"""Biens en LMNP : recettes, charges déductibles et résultat de l'année (aide à la déclaration 2031)."""
from datetime import date
from decimal import Decimal

from .models import ChargeLMNP, EncaissementLCD, Lot, Paiement

ZERO = Decimal(0)
ABATTEMENT_MICRO = {"longue": Decimal(50), "courte": Decimal(30)}  # meublé classique / meublé de tourisme non classé (depuis 2025)


def _ligne(libelle, montant, source=""):
    return {"libelle": libelle, "montant": montant, "source": source}


def _a_un_bail_dans_l_annee(lot, annee):
    from django.db.models import Q
    from .models import Bail

    return Bail.objects.filter(lot=lot, date_debut__lte=date(annee, 12, 31)) \
        .filter(Q(date_fin__isnull=True) | Q(date_fin__gte=date(annee, 1, 1))).exists()


def _recettes_baux(lot, annee):
    """Loyers des baux : paiements enregistrés, complétés mois par mois par le suivi repris (comme l'état des loyers
    encaissés et la 2072). Sans aucun paiement ni suivi repris, estimation selon les baux."""
    from .encaissements import loyers_dus_lot
    from .models import EncaissementHistorique

    recettes = []
    paiements = list(Paiement.objects.filter(echeance__bail__lot=lot, date__year=annee))
    historiques = list(EncaissementHistorique.objects.filter(lot=lot, mois__year=annee))
    if paiements:
        payes = {p.date.month for p in paiements}
        recettes.append(_ligne("Loyers et charges encaissés", sum((p.montant for p in paiements), ZERO),
                               f"Paiements enregistrés ({_nb_mois(payes)})"))
        repris = [h for h in historiques if h.mois.month not in payes]
        if repris:
            recettes.append(_ligne("Loyers et charges encaissés (suivi repris)", sum((h.montant for h in repris), ZERO),
                                   f"Suivi repris ({_nb_mois({h.mois.month for h in repris})})"))
        return recettes
    if historiques:
        recettes.append(_ligne("Loyers et charges encaissés", sum((h.montant for h in historiques), ZERO), "Suivi repris"))
    loyers, provisions = loyers_dus_lot(lot, annee, exclure_mois={h.mois.month for h in historiques})
    if loyers:
        recettes.append(_ligne("Loyers" + (" (mois restants)" if historiques else ""), loyers, "Selon les baux"))
    if provisions:
        recettes.append(_ligne("Charges refacturées au locataire" + (" (mois restants)" if historiques else ""), provisions, "Selon les baux"))
    return recettes


def _nb_mois(mois):
    return f"{len(mois)} mois"


def _courte_duree(lot, annee):
    """Courte durée : pour chaque mois, l'encaissement saisi s'il existe, sinon les réservations importées. Les charges
    mensuelles (énergie, internet, PNO…) comptent pour toute l'année."""
    from .lcd import suivi_annuel

    saisies = {e.mois.month: e for e in EncaissementLCD.objects.filter(lot=lot, mois__year=annee)}
    mois = suivi_annuel(lot, annee)
    autres = [m for m in mois if m["mois"] not in saisies]
    somme = lambda champ: sum((getattr(e, champ) for e in saisies.values()), ZERO)
    total = lambda champ: sum((m[champ] for m in autres), ZERO)
    src_saisie = f"Suivi des encaissements ({_nb_mois(saisies)})"
    mois_resa = sum(1 for m in autres if m["brut"])
    src_resa = f"Réservations importées ({mois_resa} mois)"
    recettes, charges = [], []
    if saisies:
        recettes.append(_ligne("Locations courte durée (CA)", somme("ca"), src_saisie))
        if somme("aircover") or somme("regul"):
            recettes.append(_ligne("AirCover et régularisations", somme("aircover") + somme("regul"), src_saisie))
    if mois_resa:
        recettes.append(_ligne("Locations Airbnb / Booking (hors taxe de séjour)", total("brut") - total("taxe"), src_resa))
    if saisies:
        charges += [_ligne("Ménage", somme("menage"), src_saisie),
                    _ligne("Commission de conciergerie", somme("commission"), src_saisie),
                    _ligne("Frais", somme("frais"), src_saisie)]
    charges += [_ligne("Commissions des plateformes", total("commission") + total("frais"), "Réservations importées"),
                _ligne("Ménage, consommables, conciergerie", total("gestion"), "Frais de gestion"),
                _ligne("Charges mensuelles (énergie, internet, PNO, copropriété…)",
                       sum((m["charges"] for m in mois), ZERO), "Charges mensuelles")]
    return recettes, charges


def bilan_lot(lot, annee):
    recettes, charges = [], []
    if lot.exploitation == "courte":
        recettes, charges = _courte_duree(lot, annee)
        # logement loué avec un bail une partie de l'année (avant le passage en courte durée) : ses loyers comptent aussi
        if _a_un_bail_dans_l_annee(lot, annee):
            recettes = _recettes_baux(lot, annee) + recettes
    else:
        recettes = _recettes_baux(lot, annee)

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
