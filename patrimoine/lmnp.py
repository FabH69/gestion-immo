"""Biens en LMNP : recettes, charges déductibles et résultat de l'année (aide à la déclaration 2031)."""
from datetime import date
from decimal import Decimal

from .models import ChargeLMNP, EncaissementLCD, Lot, Paiement

ZERO = Decimal(0)
ABATTEMENT_MICRO = {"longue": Decimal(50), "courte": Decimal(30)}  # meublé classique / meublé de tourisme non classé (depuis 2025)


def _ligne(libelle, montant, source=""):
    return {"libelle": libelle, "montant": montant, "source": source}


def bilan_lot(lot, annee):
    recettes, charges = [], []
    if lot.exploitation == "courte":
        saisies = list(EncaissementLCD.objects.filter(lot=lot, mois__year=annee))
        if saisies:
            somme = lambda champ: sum((getattr(e, champ) for e in saisies), ZERO)
            recettes.append(_ligne("Locations courte durée (CA)", somme("ca"), "Suivi des encaissements"))
            if somme("aircover") or somme("regul"):
                recettes.append(_ligne("AirCover et régularisations", somme("aircover") + somme("regul"), "Suivi des encaissements"))
            charges += [_ligne("Ménage", somme("menage"), "Suivi des encaissements"),
                        _ligne("Commission de conciergerie", somme("commission"), "Suivi des encaissements"),
                        _ligne("Frais", somme("frais"), "Suivi des encaissements")]
        else:
            from .lcd import suivi_annuel, totaliser
            t = totaliser(suivi_annuel(lot, annee))
            recettes.append(_ligne("Locations Airbnb / Booking (hors taxe de séjour)", t["brut"] - t["taxe"], "Réservations importées"))
            charges += [_ligne("Commissions des plateformes", t["commission"] + t["frais"], "Réservations importées"),
                        _ligne("Ménage, consommables, conciergerie", t["gestion"], "Frais de gestion"),
                        _ligne("Charges mensuelles (énergie, internet, PNO, copropriété…)", t["charges"], "Charges mensuelles")]
    else:
        paiements = Paiement.objects.filter(echeance__bail__lot=lot, date__year=annee)
        if paiements.exists():
            recettes.append(_ligne("Loyers et charges encaissés", sum((p.montant for p in paiements), ZERO), "Paiements enregistrés"))
        else:
            from .encaissements import loyers_dus_lot
            from .models import EncaissementHistorique
            historiques = list(EncaissementHistorique.objects.filter(lot=lot, mois__year=annee))
            if historiques:
                recettes.append(_ligne("Loyers et charges encaissés", sum((h.montant for h in historiques), ZERO), "Suivi repris"))
            loyers, provisions = loyers_dus_lot(lot, annee, exclure_mois={h.mois.month for h in historiques})
            if loyers:
                recettes.append(_ligne("Loyers" + (" (mois restants)" if historiques else ""), loyers, "Selon les baux"))
            if provisions:
                recettes.append(_ligne("Charges refacturées au locataire" + (" (mois restants)" if historiques else ""), provisions, "Selon les baux"))

    seul_lot = lot.bien.comptabilite == "lmnp" and not lot.bien.lots.exclude(pk=lot.pk).filter(
        pk__in=lots_lmnp().values("pk")).exists()
    if seul_lot:
        # bien suivi en comptabilité LMNP : mêmes charges que le récapitulatif LMNP (factures saisies, charges LMNP,
        # réservations, prêts, estimation des charges mensuelles), sans le capital remboursé qui n'est pas une charge
        charges = _charges_du_recapitulatif(lot.bien, annee)
        manuelles = list(ChargeLMNP.objects.filter(lot=lot, annee=annee, categorie=ChargeLMNP.Categorie.AMORTISSEMENT))
        return _resultat(lot, recettes, charges, manuelles)

    interets = assurance = ZERO
    for pret in lot.bien.prets.all():
        for e in pret.echeancier():
            if e["date"].year == annee:
                interets += e["interets"]
                assurance += e["assurance"] or ZERO
    if interets:
        charges.append(_ligne("Intérêts d'emprunt", interets, "Prêts"))
    if assurance:
        charges.append(_ligne("Assurance et frais d'emprunt", assurance, "Prêts"))

    manuelles = list(ChargeLMNP.objects.filter(lot=lot, annee=annee))
    for c in manuelles:
        if c.categorie != ChargeLMNP.Categorie.AMORTISSEMENT:
            charges.append(_ligne(c.get_categorie_display() + (f" ({c.libelle})" if c.libelle else ""), c.montant, "Saisie"))
    return _resultat(lot, recettes, charges, manuelles)


SOURCES = {"F": "Factures et charges saisies", "R": "Réservations et frais de gestion", "P": "Prêts",
           "E": "Estimation (charges mensuelles)"}


def _charges_du_recapitulatif(bien, annee):
    from .compta import detail_bien
    from .models import PieceComptable

    d = detail_bien(bien, annee, 12)
    charges = []
    for _, rubriques in PieceComptable.RUBRIQUES:
        for code, nom in rubriques:
            if code == "pret" or code not in d:
                continue
            cases = d[code].values()
            sources = sorted(set().union(*(c["sources"] for c in cases)))
            charges.append(_ligne(nom, sum((c["montant"] for c in cases), ZERO), ", ".join(SOURCES[x] for x in sources)))
    return charges


def _resultat(lot, recettes, charges, manuelles):
    charges = [c for c in charges if c["montant"]]
    amortissements = sum((c.montant for c in manuelles if c.categorie == ChargeLMNP.Categorie.AMORTISSEMENT), ZERO)

    total_recettes = sum((r["montant"] for r in recettes), ZERO)
    total_charges = sum((c["montant"] for c in charges), ZERO)
    resultat = total_recettes - total_charges
    # les amortissements ne peuvent pas créer de déficit : l'excédent se reporte sans limite de durée
    amort_deduit = min(amortissements, max(resultat, ZERO))
    # courte durée : 30 % si le meublé de tourisme n'est pas classé, 50 % s'il l'est (comme le meublé classique)
    abattement = ABATTEMENT_MICRO["courte" if lot.exploitation == "courte" and not lot.tourisme_classe else "longue"]
    return {
        "lot": lot, "recettes": recettes, "charges": charges, "total_recettes": total_recettes, "total_charges": total_charges,
        "resultat": resultat, "amortissements": amortissements, "amort_deduit": amort_deduit, "amort_reporte": amortissements - amort_deduit,
        "resultat_fiscal": resultat - amort_deduit, "abattement_micro": abattement,
        "micro_imposable": (total_recettes * (100 - abattement) / 100).quantize(Decimal("1")),
    }


def lots_lmnp():
    """Lots des biens au régime LMNP ou LMP, et lots cochés « exploité en LMNP »."""
    from django.db.models import Q

    return Lot.objects.filter(Q(lmnp=True) | Q(bien__regime_fiscal__in=("lmnp", "lmp"))).distinct() \
        .select_related("bien__proprietaire").order_by("bien__nom", "designation")


def bilan(annee, lot_id=None):
    """Bilan de tous les biens en LMNP, ou d'un seul (lot_id)."""
    lots = lots_lmnp()
    if lot_id:
        lots = lots.filter(pk=lot_id)
    blocs = [bilan_lot(l, annee) for l in lots]
    total = {k: sum((b[k] for b in blocs), ZERO) for k in ("total_recettes", "total_charges", "resultat", "amortissements", "amort_deduit", "amort_reporte", "resultat_fiscal", "micro_imposable")}
    return {"blocs": blocs, "total": total}
