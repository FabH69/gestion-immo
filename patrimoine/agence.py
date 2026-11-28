"""Biens confiés à une agence de gestion.

L'agence encaisse les loyers, paie certaines charges, vire un acompte chaque mois et régularise en fin d'année avec un
récapitulatif (loyers encaissés − charges − facture de gestion − acomptes = solde).

Dans l'appli :
- les loyers d'un bien géré par une agence sont réglés par un paiement « Encaissé par l'agence » daté de leur échéance :
  ils ne sont jamais en retard ni à relancer (la relance est le travail de l'agence) et comptent dans les déclarations
  comme loyers bruts, ce que veut le fisc ;
- les virements de l'agence sur le relevé sont des acomptes : de la trésorerie, jamais des loyers (sinon double compte) ;
- le récap annuel corrige les loyers (un loyer que l'agence n'a pas encaissé redevient impayé et est signalé), crée les
  dépenses (charges payées par l'agence, honoraires de gestion en ligne 6 de la 2072 = ligne 221 de la 2044, rubrique
  « Commission » en LMNP ou en société) et rapproche le solde du virement de régularisation."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (AnneeCloturee, Bien, ChargeRecapAgence, DeclarationFaite, Echeance, LigneRecapAgence, LigneReleve,
                     ModePaiement, PieceComptable, RecapAgence, Versement)

ZERO = Decimal(0)
NOTE = "Encaissé par l'agence"


def montant(texte, defaut=None):
    """« 1 234,50 » → Decimal ; vide ou illisible → défaut."""
    t = str(texte if texte is not None else "").replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
    if not t:
        return defaut
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        return defaut


# --- loyers encaissés par l'agence ----------------------------------------------------------------------------------
def gere_par_agence(echeance):
    bien = echeance.bail.lot.bien
    return bool(bien.agence_id) and not (bien.agence_depuis and echeance.periode < bien.agence_depuis.replace(day=1))


def parts_agence(echeance):
    """Paiements « Encaissé par l'agence » de ce loyer."""
    return echeance.paiements.filter(versement__mode=ModePaiement.AGENCE, versement__annule=False)


def encaisser_par_agence(echeance, somme=None):
    """Règle le loyer par un paiement « Encaissé par l'agence » daté de son échéance (le reste dû, ou la somme donnée)."""
    reste = echeance.reste_du
    somme = reste if somme is None else min(somme, reste)
    if somme <= 0:
        return None
    agence = echeance.bail.lot.bien.agence
    v = Versement.objects.create(bail=echeance.bail, date=echeance.date_exigibilite, montant=somme, mode=ModePaiement.AGENCE,
                                 notes=f"{NOTE}{f' {agence.nom}' if agence else ''} – loyer {echeance.periode:%m/%Y}"[:255])
    v.repartir([(echeance, somme)])
    return v


def encaisser_loyers_agence(aujourdhui=None):
    """Loyers échus des biens gérés par une agence et pas encore réglés : encaissés par l'agence. Un loyer déjà repris dans
    un récap annuel n'est plus touché (le récap fait foi). Appelé chaque jour avec la création des loyers. Renvoie le nombre."""
    aujourdhui = aujourdhui or timezone.localdate()
    qs = (Echeance.objects.filter(bail__lot__bien__agence__isnull=False, date_exigibilite__lte=aujourdhui, ligne_recap_agence__isnull=True)
          .exclude(statut=Echeance.Statut.PAYE).select_related("bail__lot__bien__agence"))
    n = 0
    for e in qs:
        if gere_par_agence(e) and encaisser_par_agence(e):
            n += 1
    return n


def est_encaisse_par_agence(echeance):
    """Pour l'affichage : loyer réglé par l'agence (paiements déjà chargés si possible)."""
    return any(p.versement.mode == ModePaiement.AGENCE for p in echeance.paiements.all() if p.versement_id)


# --- relevé de banque -----------------------------------------------------------------------------------------------
def acomptes_du_releve(agence, proprietaire, annee):
    """Virements de l'agence rapprochés sur les relevés de l'année (hors virements de solde d'un récap)."""
    soldes = RecapAgence.objects.exclude(ligne_solde=None).values("ligne_solde_id")
    qs = LigneReleve.objects.filter(nature=LigneReleve.Nature.AGENCE, agence=agence, compte__proprietaire=proprietaire,
                                    date__year=annee).exclude(pk__in=soldes)
    return qs.aggregate(s=Sum("montant"))["s"] or ZERO, qs.count()


# --- récap annuel ---------------------------------------------------------------------------------------------------
def biens_du_recap(agence, proprietaire):
    return list(Bien.objects.filter(agence=agence, proprietaire=proprietaire).order_by("nom"))


def echeances_du_recap(agence, proprietaire, annee):
    qs = (Echeance.objects.filter(bail__lot__bien__agence=agence, bail__lot__bien__proprietaire=proprietaire, periode__year=annee)
          .select_related("bail__lot__bien", "ligne_recap_agence").prefetch_related("bail__locataires", "paiements__versement")
          .order_by("bail__lot__bien__nom", "bail__lot__designation", "bail__date_debut", "periode", "nature"))
    return [e for e in qs if gere_par_agence(e)]


def code_par_defaut(bien, nature):
    if bien.comptabilite in ("lmnp", "societe"):
        return "commission" if nature == ChargeRecapAgence.Nature.HONORAIRES else "autre"
    return "6"


def codes_possibles(bien):
    """[(code, libellé)] : lignes de la 2072 / 2044 ou rubriques LMNP / société."""
    if bien.comptabilite in ("lmnp", "societe"):
        return [(c, l) for c, l in PieceComptable.LIBELLES_RUBRIQUES.items() if c not in ("pret", "immobilisation")]
    return [(c, l) for c, l in PieceComptable.Ligne.choices if c not in PieceComptable.RECETTES]


@dataclass
class Bilan:
    loyers: Decimal = ZERO
    dus: Decimal = ZERO
    charges: Decimal = ZERO
    honoraires: Decimal = ZERO
    acomptes: Decimal = ZERO
    acomptes_releve: Decimal = ZERO
    nb_acomptes_releve: int = 0
    solde_annonce: Decimal = None
    non_encaisses: list = field(default_factory=list)  # [(échéance, manque)]

    @property
    def frais(self):
        return self.charges + self.honoraires

    @property
    def net(self):
        return self.loyers - self.frais

    @property
    def solde_calcule(self):
        return self.net - self.acomptes

    @property
    def ecart_solde(self):
        return None if self.solde_annonce is None else self.solde_annonce - self.solde_calcule

    @property
    def ecart_acomptes(self):
        return self.acomptes - self.acomptes_releve if self.nb_acomptes_releve else None


def bilan(recap, encaisses=None, charges=None):
    """Totaux du récap. encaisses : {pk du loyer: montant} (saisie en cours) ; charges : [ChargeRecapAgence] non enregistrées."""
    b = Bilan()
    for e in echeances_du_recap(recap.agence, recap.proprietaire, recap.annee):
        du = e.total - (e.remise or 0)
        m = (encaisses or {}).get(e.pk)
        if m is None:
            m = valeur_initiale(e)
        b.dus += du
        b.loyers += m
        if m < du:
            b.non_encaisses.append((e, du - m))
    for c in (charges if charges is not None else recap.charges.all()):
        if c.nature == ChargeRecapAgence.Nature.HONORAIRES:
            b.honoraires += c.montant
        else:
            b.charges += c.montant
    b.acomptes = recap.acomptes if recap.acomptes is not None else ZERO
    b.acomptes_releve, b.nb_acomptes_releve = acomptes_du_releve(recap.agence, recap.proprietaire, recap.annee)
    if recap.acomptes is None and b.nb_acomptes_releve:
        b.acomptes = b.acomptes_releve
    b.solde_annonce = recap.solde_annonce
    return b


def valeur_initiale(echeance):
    """Montant proposé dans la grille : celui du récap déjà saisi, sinon ce que l'agence a encaissé selon l'appli."""
    ligne = getattr(echeance, "ligne_recap_agence", None)
    if ligne is not None:
        return ligne.montant
    return sum((p.montant for p in echeance.paiements.all() if p.versement_id and p.versement.mode == ModePaiement.AGENCE
                and not p.versement.annule), ZERO) or (echeance.total - (echeance.remise or 0))


class RecapRefuse(Exception):
    pass


def _verifier_verrous(biens, annee):
    if annee in AnneeCloturee.annees():
        raise RecapRefuse(f"l'année {annee} est clôturée : déverrouillez-la d'abord dans la liste des factures")
    declares = DeclarationFaite.verrous().get(annee, {})
    for b in biens:
        if b.pk in declares:
            d = declares[b.pk]
            raise RecapRefuse(f"{d.motif} pour {b} : {d.comment_debloquer} pour appliquer le récap")


@transaction.atomic
def appliquer(recap, encaisses, charges):
    """Enregistre le récap et l'applique :
    - chaque loyer prend le montant encaissé par l'agence (0 : le paiement de l'agence est annulé, le loyer redevient impayé) ;
    - les charges et les honoraires deviennent des dépenses du bien, payées le 31/12 (refaites à chaque enregistrement) ;
    Renvoie le bilan. encaisses : {pk du loyer: montant} ; charges : [ChargeRecapAgence] non enregistrées."""
    echeances = {e.pk: e for e in echeances_du_recap(recap.agence, recap.proprietaire, recap.annee)}
    _verifier_verrous({e.bail.lot.bien for e in echeances.values()} | {c.bien for c in charges}, recap.annee)
    recap.applique_le = timezone.now()
    recap.save()
    for pk, m in encaisses.items():
        e = echeances.get(pk)
        if e is None:
            continue
        m = max(ZERO, min(m, e.total - (e.remise or 0)))
        LigneRecapAgence.objects.update_or_create(echeance=e, defaults={"recap": recap, "montant": m})
        _regler(e, m, recap)
    # dépenses : on repart de zéro pour ne jamais compter deux fois
    for ancienne in recap.charges.all():
        if ancienne.piece_id:
            PieceComptable.objects.filter(pk=ancienne.piece_id).delete()
    recap.charges.all().delete()
    fin = date(recap.annee, 12, 31)
    for c in charges:
        c.recap = recap
        code = c.code or code_par_defaut(c.bien, c.nature)
        champ = {"rubrique": code} if c.bien.comptabilite in ("lmnp", "societe") else {"ligne": code}
        libelle = c.libelle or c.get_nature_display()
        c.piece = PieceComptable.objects.create(
            bien=c.bien, date=fin, date_paiement=fin, fournisseur=recap.agence.nom[:150], libelle=libelle[:200], montant=c.montant,
            charge_confirmee=True, notes=f"Récap {recap.annee} de l'agence {recap.agence} : payé par l'agence, retenu sur les loyers", **champ)
        c.code = code
        c.save()
    return bilan(recap)


def _regler(echeance, m, recap):
    """Met le paiement de l'agence de ce loyer au montant du récap."""
    getattr(echeance, "_prefetched_objects_cache", {}).pop("paiements", None)
    parts = list(parts_agence(echeance).select_related("versement"))
    deja = sum((p.montant for p in parts), ZERO)
    if deja == m:
        return
    motif = f"Non encaissé par l'agence selon le récap {recap.annee}"
    for p in parts:
        v = p.versement
        if v.paiements.exclude(echeance=echeance).exists():
            p.delete()
        elif m == 0:
            v.annuler(motif)
        else:
            v.delete()
    echeance.refresh_from_db()
    if m > 0:
        v = encaisser_par_agence(echeance, m)
        if v and m < echeance.total:
            v.notes = f"{v.notes} – récap {recap.annee} : partiel"[:255]
            v.save(update_fields=["notes"])
