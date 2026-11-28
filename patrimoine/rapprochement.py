"""Rapprochement des lignes du relevé : une proposition par ligne, à valider d'un clic.

Ordre des règles :
1. règle apprise (« si le libellé contient … ») ;
2. virement entre deux comptes de l'appli (même montant de sens opposé, à 3 jours près) ;
3. nom d'un associé du compte : mouvement de trésorerie (apport ou remboursement en compte courant) ;
4. argent reçu : loyer (nom du locataire dans le libellé + montant), part CAF, paiement déjà saisi à relier ;
   argent sorti : rejet d'un paiement de loyer, échéance de prêt, facture du même montant, frais bancaires.
Certitude : « sûre » (validable en lot), « à vérifier » (un clic après lecture), « aucune » (choisir à la main).
Rien n'est enregistré sans validation ; une ligne validée peut être dérapprochée."""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch, Q

from .models import (Bail, CompteBancaire, Echeance, LigneReleve, ModePaiement, PieceComptable, Pret,
                     RegleRapprochement, Versement, normaliser)

SURE, VERIFIER, AUCUNE = "sure", "verifier", "aucune"
MOTS_VIDES = {"MME", "MR", "MLLE", "MONSIEUR", "MADAME", "M", "OU", "ET", "LES", "DES", "DE", "DU", "LA", "LE", "SCI", "SARL",
              "VIR", "VIREMENT", "SEPA", "PRLV", "INST", "RECU", "EMIS", "LOYER", "LOYERS", "REF", "CB"}
FRAIS = ("COMMISSION", "COMMISSIONS", "FRAIS", "COTIS", "COTISATION", "AGIOS", "CENET", "TENUE DE COMPTE", "ABONNEMENT")
DEUX = Decimal("0.01")


def _e(m):
    """1234.5 → « 1 234,50 € »."""
    return f"{m:,.2f} €".replace(",", " ").replace(".", ",")


@dataclass
class Proposition:
    nature: str = ""
    certitude: str = AUCUNE
    titre: str = "Rien trouvé"
    pourquoi: str = ""
    bail: object = None
    parts: list = field(default_factory=list)  # [(échéance, part)]
    mode: str = ModePaiement.VIREMENT
    versement: object = None  # paiement déjà saisi à relier, ou à annuler (rejet)
    rejet: bool = False
    piece: object = None
    pret: object = None
    associe: str = ""
    bien: object = None
    rubrique: str = ""
    fournisseur: str = ""
    regle: object = None
    choix: list = field(default_factory=list)  # plusieurs baux possibles : à choisir
    ventilation: list = field(default_factory=list)  # virement du notaire : [(prix ou remboursement, montant)]

    @property
    def validable(self):
        return self.certitude != AUCUNE and not self.choix


def _mots(texte):
    return set(normaliser(texte).split())


class Contexte:
    """Tout ce qu'il faut pour proposer sans requête par ligne."""

    def __init__(self, compte):
        self.compte = compte
        p = compte.proprietaire
        non_payees = Echeance.objects.exclude(statut=Echeance.Statut.PAYE).order_by("periode", "nature").prefetch_related("paiements")
        self.baux = list(Bail.objects.filter(lot__bien__proprietaire=p).select_related("lot__bien")
                         .prefetch_related("locataires", "garants", Prefetch("echeances", queryset=non_payees, to_attr="non_payees")))
        for b in self.baux:
            b.noms = set()
            for l in b.locataires.all():
                b.noms |= {m for m in _mots(l.nom) if len(m) >= 3 and m not in MOTS_VIDES}
            for g in b.garants.all():
                b.noms |= {m for m in _mots(getattr(g, "nom", "")) if len(m) >= 3 and m not in MOTS_VIDES}
        relies = LigneReleve.objects.exclude(versement=None).values("versement_id")
        self.versements = list(Versement.objects.filter(bail__in=self.baux, annule=False).exclude(pk__in=relies))
        self.versements_relies = list(Versement.objects.filter(bail__in=self.baux, annule=False, pk__in=relies))
        par_pk = {b.pk: b for b in self.baux}
        for v in self.versements + self.versements_relies:
            v.bail = par_pk[v.bail_id]  # le même objet que dans self.baux : noms et loyers non soldés déjà chargés
        pieces_reliees = LigneReleve.objects.exclude(piece=None).values("piece_id")
        self.pieces = list(PieceComptable.objects.filter(bien__proprietaire=p, montant__isnull=False, piece_mere=None)
                           .exclude(pk__in=pieces_reliees).select_related("bien"))
        self.prets = list(Pret.objects.filter(Q(proprietaire=p) | Q(bien__proprietaire=p)).distinct())
        self._echeances_pret = {}
        self.regles = list(RegleRapprochement.objects.filter(compte=compte).select_related("bail__lot__bien", "bien", "pret"))
        autres = CompteBancaire.objects.exclude(pk=compte.pk)
        self.autres_lignes = list(LigneReleve.objects.filter(compte__in=autres).select_related("compte"))
        self.biens = list(p.biens.prefetch_related("immobilisations"))

    def bail(self, pk):
        return next((b for b in self.baux if b.pk == pk), None)

    def echeances_pret(self, pret):
        if pret.pk not in self._echeances_pret:
            self._echeances_pret[pret.pk] = [(e["date"], (e["mensualite"] + e["assurance"]).quantize(DEUX)) for e in pret.echeancier()]
        return self._echeances_pret[pret.pk]


def proposer(ligne, ctx):
    for regle in ctx.regles:
        if regle.correspond(ligne):
            p = _depuis_regle(ligne, regle, ctx)
            if p:
                return p
    p = _virement_interne(ligne, ctx) or _associe(ligne, ctx)
    if p:
        return p
    if ligne.montant > 0:
        return _vente(ligne, ctx) or _loyer(ligne, ctx) or _interets(ligne) or Proposition()
    return _rejet(ligne, ctx) or _pret(ligne, ctx) or _facture(ligne, ctx) or _frais(ligne, ctx) or Proposition()


def _depuis_regle(ligne, regle, ctx):
    pourquoi = f"règle apprise : le libellé contient « {regle.motif} »"
    A = RegleRapprochement.Action
    if regle.action == A.LOYER and regle.bail and ligne.montant > 0:
        p = _loyer_du_bail(ligne, ctx.bail(regle.bail_id) or regle.bail, True)
        p.pourquoi = pourquoi
        return p
    if regle.action == A.TRESORERIE:
        return Proposition("tresorerie", SURE, f"{ligne.sens_tresorerie} en compte courant : {regle.associe or 'associé'}",
                           pourquoi, associe=regle.associe, regle=regle)
    if regle.action == A.DEPENSE and regle.bien:
        return Proposition("depense", SURE, f"Dépense « {regle.fournisseur or ligne.libelle} » – {regle.bien}", pourquoi,
                           bien=regle.bien, rubrique=regle.rubrique, fournisseur=regle.fournisseur, regle=regle)
    if regle.action == A.PRET and regle.pret:
        return Proposition("pret", SURE, f"Échéance du prêt {regle.pret}", pourquoi, pret=regle.pret, regle=regle)
    if regle.action == A.IGNORER:
        return Proposition("ignore", SURE, "Ignorer (hors gestion)", pourquoi, regle=regle)
    return None


def _virement_interne(ligne, ctx):
    for autre in ctx.autres_lignes:
        if autre.montant == -ligne.montant and abs((autre.date - ligne.date).days) <= 3:
            return Proposition("tresorerie", SURE, f"Virement {'depuis' if ligne.montant > 0 else 'vers'} {autre.compte.nom}",
                               "même montant de sens opposé sur un autre de vos comptes", associe=f"Virement interne – {autre.compte.nom}")
    return None


def _associe(ligne, ctx):
    mots = set(ligne.texte.split())
    for nom in ctx.compte.liste_associes:
        if set(nom.split()) <= mots:
            return Proposition("tresorerie", SURE, f"{ligne.sens_tresorerie} en compte courant : {nom.title()}",
                               "nom d'un associé du compte dans le libellé ; ni recette ni charge", associe=nom.title())
    return None


def _baux_nommes(ligne, ctx):
    mots = set(ligne.texte.split())
    return [b for b in ctx.baux if b.noms & mots]


def _mode(ligne):
    mots = set(ligne.texte.split())
    if mots & {"CAF", "APL", "ALLOCATION", "ALLOCATIONS", "CNAF"}:
        return ModePaiement.CAF
    if mots & {"PRLV", "PRELEVEMENT"}:
        return ModePaiement.PRELEVEMENT
    if mots & {"CHQ", "CHEQUE", "REMISE"}:
        return ModePaiement.CHEQUE
    return ModePaiement.VIREMENT


def _loyer_du_bail(ligne, bail, nomme):
    """Proposition de loyer pour un bail donné : montant comparé aux loyers non soldés, du plus ancien au plus récent."""
    m = ligne.montant
    mode = _mode(ligne)
    caf = " (part CAF)" if mode == ModePaiement.CAF else ""
    qui = f"{', '.join(str(l) for l in bail.locataires.all())} – {bail.lot}"
    restes = [(e, e.total - e.remise - sum((p.montant for p in e.paiements.all()), Decimal(0))) for e in getattr(bail, "non_payees", [])]
    restes = [(e, r) for e, r in restes if r > 0]
    base = "nom reconnu" if nomme else "nom absent du libellé"
    if not restes:
        return Proposition("loyer", VERIFIER, f"Avance{caf} : {qui}", f"{base} ; aucun loyer à payer, le montant reste en avance",
                           bail=bail, parts=[], mode=mode)
    mois_ligne = ligne.date.replace(day=1)
    exacts = [(e, r) for e, r in restes if r == m]
    if exacts:
        e = min(exacts, key=lambda x: abs((x[0].periode - mois_ligne).days))[0]
        proche = abs((e.periode - mois_ligne).days) <= 62 or e == restes[0][0]
        return Proposition("loyer", SURE if nomme and proche else VERIFIER, f"Loyer {e.periode:%m/%Y}{caf} : {qui}",
                           f"{base} + montant exact", bail=bail, parts=[(e, m)], mode=mode)
    cumul, parts = Decimal(0), []
    for e, r in restes:
        cumul += r
        parts.append((e, r))
        if cumul == m and len(parts) > 1:
            mois = " + ".join(f"{e.periode:%m/%Y}" for e, _ in parts)
            return Proposition("loyer", VERIFIER, f"Loyers {mois}{caf} : {qui}", f"{base} ; montant = {len(parts)} loyers non soldés",
                               bail=bail, parts=parts, mode=mode)
        if cumul > m:
            break
    parts = [(e, p) for e, p in Versement.repartition_proposee(bail, m) if p > 0] if bail.pk else []
    premier, reste_premier = restes[0]
    if m < reste_premier:
        return Proposition("loyer", VERIFIER, f"Loyer {premier.periode:%m/%Y} partiel{caf} : {qui}",
                           f"{base} ; {_e(m)} reçus sur {_e(reste_premier)} dus, le reste restera à payer", bail=bail, parts=parts, mode=mode)
    avance = m - sum((p for _, p in parts), Decimal(0))
    mois = " + ".join(f"{e.periode:%m/%Y}" for e, _ in parts)
    return Proposition("loyer", VERIFIER, f"Loyers {mois}{caf} : {qui}",
                       f"{base} ; {'avance gardée : ' + _e(avance) if avance > 0 else 'dernier loyer en partie'}",
                       bail=bail, parts=parts, mode=mode)


def _loyer(ligne, ctx):
    nommes = _baux_nommes(ligne, ctx)
    for v in ctx.versements:  # paiement déjà saisi à la main : on le relie au lieu d'en créer un second
        if v.montant == ligne.montant and abs((v.date - ligne.date).days) <= 20 and (not nommes or v.bail in nommes):
            qui = ", ".join(str(l) for l in v.bail.locataires.all()) or str(v.bail)
            return Proposition("loyer", SURE if nommes else VERIFIER, f"Déjà saisi : paiement du {v.date:%d/%m/%Y} de {qui}",
                               "même montant, date proche : on le relie, sans doublon", bail=v.bail, versement=v)
    actifs = [b for b in nommes if b.actif] or nommes
    if len(actifs) == 1:
        return _loyer_du_bail(ligne, actifs[0], True)
    if len(actifs) > 1:
        p = _loyer_du_bail(ligne, actifs[0], True)
        p.certitude, p.choix = VERIFIER, actifs
        p.pourquoi = "plusieurs baux à ce nom : choisissez le bon"
        return p
    candidats = [b for b in ctx.baux if any(e.reste_du == ligne.montant for e in getattr(b, "non_payees", []))]
    if len(candidats) == 1:
        return _loyer_du_bail(ligne, candidats[0], False)
    if len(candidats) > 1:
        p = Proposition("loyer", VERIFIER, f"{len(candidats)} locataires doivent {_e(ligne.montant)}", "même montant, nom absent du libellé",
                        choix=candidats)
        return p
    return None


def _interets(ligne):
    if {"INTERETS", "INTERET", "REMUNERATION"} & set(ligne.texte.split()):
        return Proposition("ignore", VERIFIER, "Intérêts du compte : ignorer", "produit financier, hors loyers")
    return None


def vente_du_bien(ligne, bien, certitude=VERIFIER, pourquoi=""):
    """Virement du notaire pour la vente de ce bien : réparti entre le prix et les remboursements de l'acquéreur
    saisis sur la fiche du bien (le prix est le reste du virement)."""
    from . import vente

    parts, ecart = vente.ventilation(bien, ligne.montant)
    notes = []
    if len(parts) == 1:
        notes.append("aucun remboursement de l'acquéreur saisi sur la fiche du bien")
    if ecart:
        notes.append(f"écart de {_e(ecart)} avec le prix saisi (prêt remboursé ou frais retenus par le notaire ?)")
    return Proposition("vente", certitude, f"Virement du notaire : vente de {bien}", " ; ".join([x for x in [pourquoi] + notes if x]),
                       bien=bien, ventilation=parts)


def _vente(ligne, ctx):
    """Argent reçu peu avant ou après la date de vente d'un bien : prix + remboursements, ou libellé du notaire."""
    from . import vente

    proches = vente.biens_vendus_proches(ctx.biens, ligne.date)
    if not proches:
        return None
    notaire = bool(vente.MOTS_NOTAIRE & set(ligne.texte.split()))
    exacts = [b for b in proches if vente.prix_connu(b) is not None and vente.prix_connu(b) + b.total_remboursements == ligne.montant]
    if len(exacts) == 1:
        return vente_du_bien(ligne, exacts[0], SURE if notaire else VERIFIER, "prix de vente + remboursements de l'acquéreur")
    if notaire and len(proches) == 1:
        return vente_du_bien(ligne, proches[0], VERIFIER, "virement d'un notaire près de la date de vente")
    return None


def _rejet(ligne, ctx):
    mots = set(ligne.texte.split())
    if not mots & {"REJET", "IMPAYE", "RETOUR", "ANNULATION"}:
        return None
    for v in ctx.versements_relies + ctx.versements:
        if v.montant == -ligne.montant and v.bail.noms & mots:
            return Proposition("loyer", VERIFIER, f"Rejet du paiement du {v.date:%d/%m/%Y} ({_e(v.montant)})",
                               "le paiement sera annulé, ses loyers redeviennent à payer", bail=v.bail, versement=v, rejet=True)
    return None


def _pret(ligne, ctx):
    m = -ligne.montant
    texte = ligne.texte
    evoque = bool({"PRET", "ECH", "ECHEANCE", "CREDIT", "EMPRUNT"} & set(texte.split()))
    for pret in ctx.prets:
        ref = normaliser(pret.reference)
        if ref and ref in texte:
            return Proposition("pret", SURE, f"Échéance du prêt {pret.banque} n° {pret.reference}", "n° de prêt dans le libellé", pret=pret)
    for pret in ctx.prets:
        for d, montant in ctx.echeances_pret(pret):
            if montant == m and abs((d - ligne.date).days) <= 10:
                return Proposition("pret", SURE if evoque else VERIFIER, f"Échéance du {d:%d/%m/%Y} du prêt {pret.banque} {pret.reference}".strip(),
                                   "montant de l'échéancier du prêt", pret=pret)
    return None


def _facture(ligne, ctx):
    m = -ligne.montant
    mots = set(ligne.texte.split())
    candidates = [p for p in ctx.pieces if p.montant == m and (p.date is None or ligne.date - timedelta(days=150) <= p.date <= ligne.date + timedelta(days=15))
                  and (p.date_paiement is None or p.date_paiement == ligne.date or p.date_paiement == p.date)]
    if not candidates:
        return None
    nommees = [p for p in candidates if {w for w in _mots(p.fournisseur) if len(w) >= 3 and w not in MOTS_VIDES} & mots]
    p = (nommees or candidates)[0]
    return Proposition("facture", SURE if len(nommees) == 1 else VERIFIER,
                       f"Facture {p.fournisseur or p.libelle} {_e(p.montant)}" + (f" du {p.date:%d/%m/%Y}" if p.date else "") + f" – {p.bien}",
                       "fournisseur + montant" if nommees else "même montant, fournisseur absent du libellé", piece=p)


def _frais(ligne, ctx):
    if not any(f in ligne.texte for f in FRAIS):
        return None
    bien = ctx.biens[0] if len(ctx.biens) == 1 else None
    return Proposition("depense", VERIFIER if bien else AUCUNE, "Frais bancaires : créer une dépense" + (f" – {bien}" if bien else " (choisir le bien)"),
                       "mot-clé de frais bancaires", bien=bien, fournisseur=ctx.compte.banque or "Banque", rubrique="frais_bancaires")


# --- application -----------------------------------------------------------------------------------------------------
def rubrique_depense(bien, rubrique=""):
    """Codification d'une dépense créée depuis le relevé : rubrique (LMNP, société) ou ligne 2072/2044."""
    if bien.comptabilite in ("lmnp", "societe"):
        return {"rubrique": rubrique if rubrique in PieceComptable.LIBELLES_RUBRIQUES else "autre"}
    if bien.est_maison:
        return {"rubrique": rubrique if rubrique in PieceComptable.LIBELLES_MAISON else "autre"}
    lignes = dict(PieceComptable.Ligne.choices)
    return {"ligne": rubrique if rubrique in lignes else "6"}


@transaction.atomic
def appliquer(ligne, p):
    """Enregistre la proposition validée. Les dates viennent du relevé : c'est la date du paiement, donc la date fiscale
    en trésorerie (2044, 2072) ; en engagement (IS, BIC), elle solde le loyer ou la facture déjà comptés."""
    ligne.nature = p.nature
    ligne.statut = LigneReleve.Statut.RAPPROCHEE
    if p.nature == "loyer":
        if p.rejet and p.versement:
            p.versement.annuler(f"Rejet bancaire du {ligne.date:%d/%m/%Y}")
            ligne.versement, ligne.cree = p.versement, False
        elif p.versement:
            ligne.versement, ligne.cree = p.versement, False
        else:
            v = Versement.objects.create(bail=p.bail, date=ligne.date, montant=ligne.montant, mode=p.mode,
                                         notes=f"Relevé : {ligne.libelle}"[:255])
            v.repartir(p.parts)
            ligne.versement, ligne.cree = v, True
    elif p.nature == "facture":
        ligne.piece, ligne.cree = p.piece, False
        ligne.date_paiement_avant = p.piece.date_paiement
        p.piece.date_paiement = ligne.date
        p.piece.save(update_fields=["date_paiement"])
    elif p.nature == "depense":
        piece = PieceComptable.objects.create(bien=p.bien, date=ligne.date, date_paiement=ligne.date,
                                              fournisseur=(p.fournisseur or ligne.libelle)[:150], libelle=ligne.libelle[:200],
                                              montant=-ligne.montant, notes="Créée depuis le relevé de banque",
                                              **rubrique_depense(p.bien, p.rubrique))
        ligne.piece, ligne.cree = piece, True
    elif p.nature == "pret":
        ligne.pret = p.pret
    elif p.nature == "tresorerie":
        ligne.associe = p.associe[:120]
    elif p.nature == "vente":
        from . import vente

        # trésorerie (2044, 2072) : les remboursements comptent l'année où le notaire les verse
        for piece in vente.pieces(p.bien):
            if not piece.est_verrouillee():
                piece.date_paiement = ligne.date
                piece.save(update_fields=["date_paiement"])
        ligne.bien = p.bien
        ligne.note = vente.texte_ventilation(p.ventilation)[:255]
    elif p.nature == "ignore":
        ligne.statut = LigneReleve.Statut.IGNOREE
    ligne.save()
    return ligne


@transaction.atomic
def derapprocher(ligne):
    """Remet la ligne « à traiter » et défait ce que sa validation avait créé."""
    if ligne.versement_id:
        v = ligne.versement
        if ligne.cree:
            v.delete()  # ses parts partent avec lui : les loyers redeviennent à payer
        elif v.annule and v.motif_annulation.startswith("Rejet bancaire"):
            v.annule, v.motif_annulation = False, ""
            v.save(update_fields=["annule", "motif_annulation"])
    if ligne.nature == "vente" and ligne.bien_id:
        from . import vente

        for piece in vente.pieces(ligne.bien):
            if piece.date_paiement == ligne.date and not piece.est_verrouillee():
                piece.date_paiement = None
                piece.save(update_fields=["date_paiement"])
    if ligne.piece_id:
        if ligne.cree:
            ligne.piece.delete()
        else:
            ligne.piece.date_paiement = ligne.date_paiement_avant
            ligne.piece.save(update_fields=["date_paiement"])
    LigneReleve.objects.filter(pk=ligne.pk).update(statut=LigneReleve.Statut.A_TRAITER, nature="", versement=None, piece=None, pret=None,
                                                   bien=None, note="", associe="", cree=False, date_paiement_avant=None)


class PieceRefusee(Exception):
    pass


def a_justifier(ligne):
    """Sortie d'argent rapprochée d'une facture ou d'une dépense qui n'a pas encore son justificatif."""
    return ligne.montant < 0 and ligne.statut == LigneReleve.Statut.RAPPROCHEE and bool(ligne.piece_id) and not ligne.piece.fichier


def peut_joindre(ligne):
    """📎 possible : sortie déjà reliée à une facture, ou sortie encore à traiter (la dépense est alors créée)."""
    if ligne.montant >= 0:
        return False
    return bool(ligne.piece_id) if ligne.statut == LigneReleve.Statut.RAPPROCHEE else ligne.statut == LigneReleve.Statut.A_TRAITER


def _lire_justificatif(fichier, bien):
    """Fournisseur et codification lus dans le PDF (comme l'import des factures) ; {} pour une photo ou un PDF scanné."""
    from . import compta
    from .lecture_factures import analyser, texte_pdf

    if not fichier.name.lower().endswith(".pdf"):
        return {}
    par_rubrique = bien.comptabilite in compta.PAR_RUBRIQUE
    lu = analyser(texte_pdf(fichier), par_rubrique=par_rubrique)
    lu.update(compta.code_du_fournisseur(lu.get("fournisseur"), par_rubrique))
    return lu


@transaction.atomic
def joindre_piece(ligne, fichier, bien=None):
    """📎 Joint le justificatif à une sortie d'argent du relevé et renvoie la facture.
    - ligne déjà rapprochée d'une facture ou d'une dépense : le fichier va dans cette facture ;
    - ligne à traiter : si la proposition est une facture déjà saisie, elle est rapprochée ; sinon la dépense est créée
      (montant et date du relevé, fournisseur et codification lus dans le PDF), avec le fichier joint.
    Le même fichier déjà enregistré comme facture non reliée : la ligne est rapprochée de cette facture."""
    if not peut_joindre(ligne):
        raise PieceRefusee("on ne joint une pièce qu'à une sortie d'argent reliée à une facture ou encore à traiter")
    empreinte = PieceComptable.calculer_empreinte(fichier)
    proprietaire = ligne.compte.proprietaire
    if ligne.statut == LigneReleve.Statut.RAPPROCHEE:
        piece = ligne.piece
    else:
        deja = PieceComptable.objects.filter(empreinte=empreinte, bien__proprietaire=proprietaire, lignes_releve__isnull=True).first()
        if deja:
            appliquer(ligne, Proposition("facture", VERIFIER, f"Facture {deja}", piece=deja))
            return deja
        ctx = Contexte(ligne.compte)
        p = proposer(ligne, ctx)
        if p.nature == "facture" and p.piece and p.piece.fichier:
            raise PieceRefusee(f"cette sortie correspond à la facture {p.piece}, qui a déjà son justificatif : validez-la")
        if p.nature == "facture" and p.piece:
            appliquer(ligne, p)
            piece = p.piece
        else:
            bien = bien or p.bien or (ctx.biens[0] if len(ctx.biens) == 1 else None)
            if bien is None:
                raise PieceRefusee("choisissez le bien de cette dépense")
            lu = _lire_justificatif(fichier, bien)
            regle = p.regle if p.nature == "depense" else None
            appliquer(ligne, Proposition("depense", VERIFIER, "Dépense", bien=bien,
                                         fournisseur=(regle and p.fournisseur) or lu.get("fournisseur") or p.fournisseur,
                                         rubrique=(regle and p.rubrique) or lu.get("rubrique") or lu.get("ligne") or p.rubrique))
            piece = ligne.piece
            d = lu.get("date")
            if d and ligne.date - timedelta(days=366) <= d <= ligne.date:
                piece.date = d  # date de la facture (engagement) ; la date de paiement reste celle du relevé
    if piece.fichier:
        raise PieceRefusee(f"la facture {piece} a déjà son justificatif")
    piece.fichier, piece.empreinte = fichier, empreinte
    piece.save(update_fields=["fichier", "empreinte", "date"])
    return piece


def importer(compte, nom, contenu):
    """Lit le fichier et ajoute les lignes nouvelles ; renvoie (ImportReleve, releve lu)."""
    from collections import Counter

    from . import releves
    from .models import ImportReleve

    lu = releves.lire(nom, contenu, compte.chiffres_iban)
    ok, controle = lu.controle
    imp = ImportReleve.objects.create(compte=compte, fichier=nom[:255], format=lu.format, nb_lignes=len(lu.lignes),
                                      date_debut=min((l.date for l in lu.lignes), default=None),
                                      date_fin=max((l.date for l in lu.lignes), default=None),
                                      solde_debut=lu.solde_debut, solde_fin=lu.solde_fin, controle=controle)
    vus, nouvelles = Counter(), 0
    existantes = set(LigneReleve.objects.filter(compte=compte).values_list("empreinte", flat=True))
    for l in lu.lignes:
        cle = (l.date, l.montant, normaliser(l.libelle))
        empreinte = LigneReleve.calculer_empreinte(compte.pk, l.date, l.montant, l.libelle, vus[cle])
        vus[cle] += 1
        if empreinte in existantes:
            continue
        LigneReleve.objects.create(compte=compte, releve=imp, date=l.date, libelle=l.libelle[:255], detail=l.detail,
                                   montant=l.montant, empreinte=empreinte)
        existantes.add(empreinte)
        nouvelles += 1
    imp.nb_nouvelles = nouvelles
    imp.save(update_fields=["nb_nouvelles"])
    return imp, ok


def allers_retours(lignes):
    """{ligne.pk: ligne opposée} : même montant de sens opposé sur le même compte à 60 jours près (renflouement puis retour)."""
    paires, pris = {}, set()
    tri = sorted(lignes, key=lambda l: (l.date, l.pk))
    for i, a in enumerate(tri):
        if a.pk in pris:
            continue
        for b in tri[i + 1:]:
            if b.pk not in pris and b.compte_id == a.compte_id and b.montant == -a.montant and (b.date - a.date).days <= 60:
                paires[a.pk], paires[b.pk] = b, a
                pris |= {a.pk, b.pk}
                break
    return paires


def comptes_courants(proprietaire):
    """Apports, remboursements et solde par associé (mouvements de trésorerie validés, virements internes exclus)."""
    soldes = {}
    for l in LigneReleve.objects.filter(compte__proprietaire=proprietaire, nature="tresorerie").exclude(associe__startswith="Virement interne"):
        s = soldes.setdefault(l.associe or "Associé", {"apports": Decimal(0), "remboursements": Decimal(0)})
        if l.montant > 0:
            s["apports"] += l.montant
        else:
            s["remboursements"] -= l.montant
    return [{"associe": a, **s, "solde": s["apports"] - s["remboursements"]} for a, s in sorted(soldes.items())]


def fin_exercice(proprietaire, annee):
    """Engagement (IS, BIC) : au 31/12, loyers de l'année pas encore reçus (créances) et factures de l'année payées après
    (dettes)."""
    from datetime import date

    fin = date(annee, 12, 31)
    creances = []
    for e in (Echeance.objects.filter(bail__lot__bien__proprietaire=proprietaire, periode__year=annee)
              .select_related("bail__lot").prefetch_related("paiements", "bail__locataires")):
        recu = sum((p.montant for p in e.paiements.all() if p.date <= fin), Decimal(0))
        reste = e.total - e.remise - recu
        if reste > 0:
            creances.append((e, reste))
    dettes = list(PieceComptable.objects.filter(bien__proprietaire=proprietaire, date__lte=fin, date__year=annee, date_paiement__gt=fin,
                                                montant__isnull=False).select_related("bien").order_by("date"))
    return {"annee": annee, "creances": creances, "total_creances": sum((r for _, r in creances), Decimal(0)),
            "dettes": dettes, "total_dettes": sum((p.montant for p in dettes), Decimal(0))}

