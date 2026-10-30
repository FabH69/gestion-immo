"""Calcul des états de synthèse (loyers encaissés par mois et par année)."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from .models import Paiement

MOIS = ["Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]
CENTIME = Decimal("0.01")


def _ligne_vide():
    return {"mois": [Decimal(0)] * 12, "total": Decimal(0), "loyers_hc": Decimal(0), "charges": Decimal(0)}


def _ajouter(cible, source):
    for i in range(12):
        cible["mois"][i] += source["mois"][i]
    for cle in ("total", "loyers_hc", "charges"):
        cible[cle] += source[cle]


def loyers_encaisses(annee, proprietaire=None):
    """Encaissements de l'année civile (date de paiement), regroupés propriétaire > bien > lot.

    Chaque paiement est réparti entre loyer HC et charges au prorata de l'échéance qu'il règle.
    """
    paiements = Paiement.objects.filter(date__year=annee).select_related("echeance__bail__lot__bien__proprietaire")
    if proprietaire:
        paiements = paiements.filter(echeance__bail__lot__bien__proprietaire=proprietaire)

    par_lot = defaultdict(_ligne_vide)
    for p in paiements:
        e = p.echeance
        ligne = par_lot[e.bail.lot]
        part_hc = (p.montant * e.loyer_hc / e.total).quantize(CENTIME, ROUND_HALF_UP) if e.total else p.montant
        ligne["mois"][p.date.month - 1] += p.montant
        ligne["total"] += p.montant
        ligne["loyers_hc"] += part_hc
        ligne["charges"] += p.montant - part_hc

    proprietaires = {}
    for lot in sorted(par_lot, key=lambda l: (l.bien.proprietaire.nom, l.bien.nom, l.designation)):
        proprio = lot.bien.proprietaire
        bloc_p = proprietaires.setdefault(proprio.pk, {"proprietaire": proprio, "biens": {}, "sous_total": _ligne_vide()})
        bloc_b = bloc_p["biens"].setdefault(lot.bien.pk, {"bien": lot.bien, "lots": [], "sous_total": _ligne_vide()})
        bloc_b["lots"].append({"lot": lot, **par_lot[lot]})
        _ajouter(bloc_b["sous_total"], par_lot[lot])
        _ajouter(bloc_p["sous_total"], par_lot[lot])

    total = _ligne_vide()
    blocs = []
    for bloc_p in proprietaires.values():
        bloc_p["biens"] = list(bloc_p["biens"].values())
        _ajouter(total, bloc_p["sous_total"])
        blocs.append(bloc_p)
    return {"annee": annee, "proprietaires": blocs, "total": total, "mois": MOIS}
