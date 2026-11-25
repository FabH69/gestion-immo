"""Loyers encaissés repris d'un ancien suivi (historique) → paiements reçus.

Chaque ligne d'historique (un montant encaissé pour un lot, un mois) devient un paiement reçu du locataire en place
ce mois-là, daté du 1er du mois, et réglant ses loyers du plus ancien au plus récent à partir du premier mois repris :
130 € reçus en janvier pour un loyer de 65 € règlent janvier et février. Les loyers de ces mois sont créés s'ils
n'existent pas (jamais au-delà du mois en cours ni après la fin du bail) ; ce qui dépasse reste en avance.

Une ligne est laissée telle quelle quand aucun bail ne couvre le mois, ou quand un paiement est déjà enregistré pour
ce lot ce mois-là (les états et déclarations l'ignorent déjà dans ce cas : la convertir compterait deux fois)."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

NOTE = "Repris du suivi historique"


def _mois_suivant(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _bail_du_mois(lot, mois):
    fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
    return (lot.baux.filter(date_debut__lte=fin).exclude(date_fin__lt=mois).order_by("-date_debut").first())


def _echeance(bail, periode):
    from .models import Echeance

    e = bail.echeances.filter(periode=periode, nature=Echeance.Nature.LOYER).first()
    if e:
        return e
    fin = periode.replace(day=calendar.monthrange(periode.year, periode.month)[1])
    loyer, charges = bail.montants_du_mois(periode)
    return Echeance.objects.create(bail=bail, periode=periode, nature=Echeance.Nature.LOYER, loyer_hc=loyer, charges=charges,
                                   date_exigibilite=periode.replace(day=min(bail.jour_echeance, fin.day)))


def convertir(historiques=None, aujourdhui=None):
    """Convertit les lignes d'historique (toutes par défaut). Renvoie (nombre converti, lignes laissées)."""
    from .models import EncaissementHistorique, Paiement, Versement

    aujourdhui = aujourdhui or date.today()
    limite = aujourdhui.replace(day=1)
    lignes = list((historiques if historiques is not None else EncaissementHistorique.objects.all())
                  .select_related("lot").order_by("lot_id", "mois"))
    debut_par_bail, converties, laissees = {}, 0, []
    for h in lignes:
        mois = h.mois.replace(day=1)
        fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
        if Paiement.objects.filter(echeance__bail__lot=h.lot, date__gte=mois, date__lte=fin).exists():
            laissees.append((h, "un paiement est déjà enregistré ce mois-là"))
            continue
        bail = _bail_du_mois(h.lot, mois)
        if bail is None:
            laissees.append((h, "aucun bail ne couvre ce mois"))
            continue
        v = Versement.objects.create(bail=bail, date=mois, montant=h.montant, notes=(f"{NOTE} – {h.note}" if h.note else NOTE)[:255])
        # du plus ancien loyer non soldé (depuis le premier mois repris de ce bail) au plus récent
        debut = debut_par_bail.setdefault(bail.pk, max(mois, bail.date_debut.replace(day=1)))
        reste, periode = Decimal(h.montant), debut
        while reste > 0 and periode <= limite and not (bail.date_fin and periode > bail.date_fin):
            e = _echeance(bail, periode)
            du = e.reste_du
            if du > 0:
                part = min(du, reste)
                Paiement.objects.create(echeance=e, versement=v, date=v.date, montant=part, mode=v.mode, notes=v.notes)
                reste -= part
            periode = _mois_suivant(periode)
        h.delete()
        converties += 1
    return converties, laissees
