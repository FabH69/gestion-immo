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

    # loyers repris d'un ancien suivi : comptés pour les mois sans paiement enregistré dans l'appli
    from .models import EncaissementHistorique
    deja = {(p.echeance.bail.lot_id, p.date.month) for p in paiements}
    historiques = EncaissementHistorique.objects.filter(mois__year=annee).select_related("lot__bien__proprietaire")
    if proprietaire:
        historiques = historiques.filter(lot__bien__proprietaire=proprietaire)
    for h in historiques:
        if (h.lot_id, h.mois.month) in deja:
            continue
        ligne = par_lot[h.lot]
        ligne["mois"][h.mois.month - 1] += h.montant
        ligne["total"] += h.montant
        ligne["loyers_hc"] += h.montant - h.charges
        ligne["charges"] += h.charges

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


def endettement(a_date, proprietaire=None):
    """Capital restant dû par prêt et par emprunteur à une date donnée, avec les intérêts de l'année."""
    from .models import Pret

    prets = Pret.objects.select_related("proprietaire", "bien")
    if proprietaire:
        prets = prets.filter(proprietaire=proprietaire)
    cles = ("montant", "mensualite", "interets", "restant")
    blocs, total = {}, dict.fromkeys(cles, Decimal(0))
    for pret in prets:
        restant = pret.capital_restant_du(a_date)
        ligne = {
            "pret": pret,
            "montant": pret.montant,
            "mensualite": pret.mensualite_totale if restant > 0 else Decimal(0),
            "interets": pret.cumul_annee(a_date.year)["interets"],
            "restant": restant,
        }
        bloc = blocs.setdefault(pret.proprietaire_id, {"proprietaire": pret.proprietaire, "lignes": [], **dict.fromkeys(cles, Decimal(0))})
        bloc["lignes"].append(ligne)
        for c in cles:
            bloc[c] += ligne[c]
            total[c] += ligne[c]
    for bloc in blocs.values():
        # Personne physique : prêts de la résidence principale séparés du locatif, avec un sous-total chacun.
        rp = [l for l in bloc["lignes"] if l["pret"].bien and l["pret"].bien.residence_principale]
        if rp:
            locatif = [l for l in bloc["lignes"] if l not in rp]
            bloc["groupes"] = [{"libelle": libelle, "lignes": lignes, **{c: sum((l[c] for l in lignes), Decimal(0)) for c in cles}}
                               for libelle, lignes in (("Locatif", locatif), ("Résidence principale", rp)) if lignes]
            bloc["lignes"] = locatif + rp
        else:
            bloc["groupes"] = [{"libelle": "", "lignes": bloc["lignes"]}]
    return {"blocs": list(blocs.values()), "total": total, "date": a_date, "annee": a_date.year}
