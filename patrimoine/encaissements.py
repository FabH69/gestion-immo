"""Suivi mensuel des encaissements d'un propriétaire : loyers issus des baux et bloc courte durée saisi à la main."""
import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from .models import Bail, EncaissementLCD, Lot

ZERO = Decimal(0)
NOMS_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _arrondi(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def lots_du_proprietaire(proprietaire, annee):
    debut = date(annee, 1, 1)
    lots = (Lot.objects.filter(bien__proprietaire=proprietaire).select_related("bien")
            .exclude(vendu=True, date_vente__lt=debut).order_by("bien__nom", "designation"))
    return [l for l in lots if l.exploitation == "courte"], [l for l in lots if l.exploitation != "courte"]


def _part_du_mois(bail, premier, dernier, entrees=()):
    """Fraction du mois couverte par le bail (entrée ou sortie en cours de mois : au prorata des jours).

    Le jour de sortie est compté, sauf si un nouveau bail commence ce jour-là dans le même logement (pas de double loyer).
    """
    debut = max(bail.date_debut, premier)
    fin = dernier
    if bail.date_fin and not bail.actif:
        sortie = bail.date_fin - timedelta(days=1) if bail.date_fin in entrees else bail.date_fin
        fin = min(sortie, dernier)
    if fin < debut:
        return ZERO
    return Decimal((fin - debut).days + 1) / Decimal(dernier.day)


def suivi(proprietaire, annee):
    lots_courte, lots_longue = lots_du_proprietaire(proprietaire, annee)
    debut, fin = date(annee, 1, 1), date(annee, 12, 31)
    baux = {lot.pk: list(Bail.objects.filter(lot=lot, date_debut__lte=fin).filter(Q(actif=True) | Q(date_fin__gte=debut) | Q(date_fin__isnull=True))
                         .prefetch_related("locataires", "mouvements_depot", "revisions")) for lot in lots_longue}
    saisies = {(e.lot_id, e.mois.month): e for e in EncaissementLCD.objects.filter(lot__in=lots_courte, mois__year=annee)}

    mois = []
    for m in range(1, 13):
        premier = date(annee, m, 1)
        dernier = date(annee, m, calendar.monthrange(annee, m)[1])
        ligne = {"numero": m, "nom": NOMS_MOIS[m - 1], "courte": [], "longue": []}
        for lot in lots_courte:
            e = saisies.get((lot.pk, m))
            ligne["courte"].append({"lot": lot, "saisie": e, "versement": e.versement if e else ZERO,
                                    "valeurs": {c: getattr(e, c) if e else None for c in EncaissementLCD.CHAMPS}})
        for lot in lots_longue:
            loyer = charges = caution = ZERO
            occupants = []
            entrees = {b.date_debut for b in baux[lot.pk]}
            for bail in baux[lot.pk]:
                part = _part_du_mois(bail, premier, dernier, entrees)
                if part:
                    loyer += _arrondi(bail.loyer_au(premier) * part)
                    charges += _arrondi(bail.charges * part)
                    occupants += [l.nom for l in bail.locataires.all()]
                caution += sum((mv.montant_signe for mv in bail.mouvements_depot.all() if premier <= mv.date <= dernier), ZERO)
            ligne["longue"].append({"lot": lot, "loyer": loyer, "charges": charges, "caution": caution, "occupants": ", ".join(occupants)})
        ligne["total_loyers"] = sum((c["loyer"] for c in ligne["longue"]), ZERO)
        ligne["total_charges"] = sum((c["charges"] for c in ligne["longue"]), ZERO)
        ligne["total_cautions"] = sum((c["caution"] for c in ligne["longue"]), ZERO)
        ligne["total_courte"] = sum((c["versement"] for c in ligne["courte"]), ZERO)
        ligne["total"] = ligne["total_loyers"] + ligne["total_charges"] + ligne["total_courte"]
        mois.append(ligne)

    totaux = {
        "courte": [{"lot": lot, "valeurs": {c: sum((l["courte"][i]["valeurs"][c] or ZERO for l in mois), ZERO) for c in EncaissementLCD.CHAMPS},
                    "versement": sum((l["courte"][i]["versement"] for l in mois), ZERO)} for i, lot in enumerate(lots_courte)],
        "longue": [{"lot": lot, **{k: sum((l["longue"][i][k] for l in mois), ZERO) for k in ("loyer", "charges", "caution")}} for i, lot in enumerate(lots_longue)],
        **{k: sum((l[k] for l in mois), ZERO) for k in ("total_loyers", "total_charges", "total_cautions", "total_courte", "total")},
    }
    return {"lots_courte": lots_courte, "lots_longue": lots_longue, "mois": mois, "totaux": totaux}


def enregistrer(proprietaire, annee, donnees):
    """Enregistre le bloc courte durée envoyé par le formulaire (champs « lcd-<lot>-<mois>-<champ> »)."""
    lots_courte, _ = lots_du_proprietaire(proprietaire, annee)
    modifies = 0
    for lot in lots_courte:
        for m in range(1, 13):
            valeurs = {}
            for champ in EncaissementLCD.CHAMPS:
                brut = (donnees.get(f"lcd-{lot.pk}-{m}-{champ}") or "").strip().replace(" ", "").replace(",", ".")
                try:
                    valeurs[champ] = Decimal(brut) if brut else ZERO
                    if champ in ("menage", "commission", "frais"):
                        valeurs[champ] = abs(valeurs[champ])  # dépenses saisies avec ou sans signe moins
                except ArithmeticError:
                    valeurs[champ] = ZERO
            existant = EncaissementLCD.objects.filter(lot=lot, mois=date(annee, m, 1)).first()
            if not any(valeurs.values()):
                if existant:
                    existant.delete()
                    modifies += 1
                continue
            if existant and all(getattr(existant, c) == v for c, v in valeurs.items()):
                continue
            EncaissementLCD.objects.update_or_create(lot=lot, mois=date(annee, m, 1), defaults=valeurs)
            modifies += 1
    return modifies


def loyers_dus_lot(lot, annee, exclure_mois=()):
    """Loyers HC et charges dus selon les baux du lot, mois par mois (mêmes règles que le suivi des encaissements)."""
    debut, fin = date(annee, 1, 1), date(annee, 12, 31)
    baux = list(Bail.objects.filter(lot=lot, date_debut__lte=fin).filter(Q(actif=True) | Q(date_fin__gte=debut) | Q(date_fin__isnull=True))
                .prefetch_related("revisions"))
    entrees = {b.date_debut for b in baux}
    loyers = charges = ZERO
    for m in range(1, 13):
        if m in exclure_mois:
            continue
        premier, dernier = date(annee, m, 1), date(annee, m, calendar.monthrange(annee, m)[1])
        for bail in baux:
            part = _part_du_mois(bail, premier, dernier, entrees)
            if part:
                loyers += _arrondi(bail.loyer_au(premier) * part)
                charges += _arrondi(bail.charges * part)
    return loyers, charges
