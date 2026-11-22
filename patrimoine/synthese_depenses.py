"""Comptabilité › Évolution des dépenses : les dépenses de plusieurs années, rangées par nature, avec l'écart d'une année
sur l'autre.

Les biens en revenus fonciers (2044, 2072) sont codés sur les lignes du cerfa, ceux en LMNP ou en société sur des
rubriques : chaque ligne et chaque rubrique est rattachée à une nature commune (assurance, travaux, taxe foncière…) pour
pouvoir tout additionner. Les montants sont ceux des déclarations : annexe 2072 (forfait de 20 €, intérêts des prêts,
régularisation de copropriété retranchée) et récapitulatif par rubrique (réservations, échéancier des prêts, estimations).
Le capital des prêts et les dépenses non déductibles sont montrés à part, sous le total.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from . import compta, dossiers
from .models import Bien, PieceComptable

ZERO = Decimal("0.00")

# (code, libellé, lignes du cerfa 2072 / 2044, rubriques LMNP / société)
NATURES = [
    ("gestion", "Gestion, honoraires, commissions", {"6", "7"}, {"commission", "comptable"}),
    ("menage", "Ménage et consommables", set(), {"menage", "consommables"}),
    ("assurance", "Assurance propriétaire (PNO)", {"8"}, {"pno"}),
    ("travaux", "Travaux, réparations, entretien", {"9"}, set()),
    ("energie", "Énergie, eau, internet", set(), {"electricite", "eau", "fibre"}),
    ("copro", "Charges de copropriété", {"13", "14"}, {"copro"}),
    ("taxes", "Taxe foncière et impôts", {"12"}, {"taxe_fonciere", "impots_taxes"}),
    ("interets", "Intérêts et assurance d'emprunt", {"17"}, {"interets_pret", "assurance_pret"}),
    ("autres", "Autres charges", {"10", "11", "15"}, {"autre"}),
]
HORS = ("hors", "Hors charges : capital des prêts, non déductible")
PAR_LIGNE = {l: code for code, _, lignes, _ in NATURES for l in lignes}
PAR_RUBRIQUE = {r: code for code, _, _, rubriques in NATURES for r in rubriques}


def annees_par_defaut():
    t = timezone.localdate().year
    return list(range(t - 3, t + 1))


def biens_du_perimetre(dossier=None, proprietaire=None, bien=None):
    """Biens retenus : ceux d'un dossier, d'un propriétaire, un seul bien, ou tous les biens qui ont un dossier."""
    if bien is not None:
        return [bien]
    liste = [dossier] if dossier else dossiers.tous()
    pks = {pk for d in liste for pk in dossiers.biens(d).values_list("pk", flat=True)}
    qs = Bien.objects.filter(pk__in=pks)
    if proprietaire is not None:
        qs = qs.filter(proprietaire=proprietaire)
    return list(qs.select_related("proprietaire").order_by("nom"))


def _actif(bien, annee):
    return not bien.vendu or (bien.date_vente and bien.date_vente.year >= annee)


def montants_bien(bien, annee):
    """{nature: montant} d'un bien pour une année, avec « hors » pour le capital des prêts et le non déductible."""
    m = defaultdict(lambda: ZERO)
    if not _actif(bien, annee):
        return m
    if bien.comptabilite in compta.PAR_RUBRIQUE:
        for rubrique, mois in compta.detail_bien(bien, annee, compta.dernier_mois(annee)).items():
            total = sum((c["montant"] for c in mois.values()), ZERO)
            m["hors" if rubrique == "pret" else PAR_RUBRIQUE.get(rubrique, "autres")] += total
    else:
        from .annexe2072 import colonne

        v = colonne(bien, annee)["valeurs"]
        for ligne, nature in PAR_LIGNE.items():
            montant = v.get(ligne, ZERO)
            m[nature] += -montant if ligne == "14" else montant  # régularisation de copropriété : à retrancher
        for pret in bien.prets.all():
            m["hors"] += pret.cumul_annee(annee)["capital"]
    non = PieceComptable.objects.filter(bien=bien, ligne="non").exclude(compta.MANQUE)
    non = non.filter(Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee))
    m["hors"] += sum((p.montant for p in non), ZERO)
    return m


def ecart(avant, apres):
    """Écart en euros et en % (None si l'année d'avant est à zéro) ; sens : « hausse », « baisse » ou « stable »."""
    diff = apres - avant
    pct = (diff / avant * 100) if avant else None
    sens = "stable" if abs(diff) < 1 else "hausse" if diff > 0 else "baisse"
    return {"montant": diff, "pct": pct, "sens": sens}


def synthese(annees, biens):
    """Tableau d'évolution : une ligne par nature (avec le détail par bien), une colonne par année, l'écart entre les deux dernières."""
    annees = sorted(annees)
    par_bien = {b.pk: {a: montants_bien(b, a) for a in annees} for b in biens}
    lignes = []
    for code, libelle, _, _ in NATURES:
        valeurs = [sum((par_bien[b.pk][a][code] for b in biens), ZERO) for a in annees]
        if not any(valeurs):
            continue
        detail = []
        for b in biens:
            vb = [par_bien[b.pk][a][code] for a in annees]
            if any(vb):
                detail.append({"bien": b, "valeurs": vb, "ecart": ecart(vb[-2], vb[-1]) if len(vb) > 1 else None})
        lignes.append({"code": code, "libelle": libelle, "valeurs": valeurs, "detail": detail,
                       "ecart": ecart(valeurs[-2], valeurs[-1]) if len(annees) > 1 else None})
    totaux = [sum((l["valeurs"][i] for l in lignes), ZERO) for i in range(len(annees))]
    hors = [sum((par_bien[b.pk][a]["hors"] for b in biens), ZERO) for a in annees]
    hausses = sorted((l for l in lignes if l["ecart"] and l["ecart"]["sens"] == "hausse"), key=lambda l: -l["ecart"]["montant"])
    baisses = sorted((l for l in lignes if l["ecart"] and l["ecart"]["sens"] == "baisse"), key=lambda l: l["ecart"]["montant"])
    en_cours = timezone.localdate().year
    return {"annees": annees, "lignes": lignes, "totaux": totaux, "hors": hors if any(hors) else None,
            "ecart_total": ecart(totaux[-2], totaux[-1]) if len(annees) > 1 else None,
            "moyenne": sum(totaux, ZERO) / len(totaux) if totaux else ZERO,
            "plus_forte_hausse": hausses[0] if hausses else None, "plus_forte_baisse": baisses[0] if baisses else None,
            "annee_partielle": en_cours if en_cours in annees else None,
            "periode_partielle": compta.MOIS[compta.dernier_mois(en_cours) - 1] if en_cours in annees else ""}
