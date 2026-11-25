"""Loyers encaissés repris d'un ancien suivi (historique) → paiements reçus.

Chaque ligne d'historique (un montant encaissé pour un lot, un mois) devient un paiement reçu du locataire en place
ce mois-là, daté du 1er du mois, et réglant ses loyers du plus ancien au plus récent à partir du premier mois repris :
130 € reçus en janvier pour un loyer de 65 € règlent janvier et février. Les loyers de ces mois sont créés s'ils
n'existent pas (jamais au-delà du mois en cours ni après la fin du bail) ; ce qui dépasse reste en avance.

Une ligne est laissée telle quelle quand aucun bail ne couvre le mois, ou quand un paiement est déjà enregistré pour
ce lot ce mois-là (les états et déclarations l'ignorent déjà dans ce cas : la convertir compterait deux fois).
Pour un mois antérieur au premier bail saisi (bail enregistré avec une date d'entrée trop tardive), l'action
« Rattacher au locataire suivant » de l'historique force le rattachement au bail qui suit. Une ligne à 0 € est
simplement retirée : rien n'a été reçu ce mois-là."""
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


def _bail_suivant(lot, mois):
    return lot.baux.filter(date_debut__gt=mois).order_by("date_debut").first()


def raison(h):
    """Pourquoi une ligne d'historique ne peut pas être reprise automatiquement (None si elle peut l'être)."""
    from .models import Paiement

    mois = h.mois.replace(day=1)
    fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
    if Paiement.objects.filter(echeance__bail__lot_id=h.lot_id, date__gte=mois, date__lte=fin).exists():
        return "un paiement est déjà enregistré ce mois-là"
    if _bail_du_mois(h.lot, mois) is None:
        suivant = _bail_suivant(h.lot, mois)
        if suivant:
            noms = ", ".join(str(l) for l in suivant.locataires.all()) or "le bail suivant"
            return f"aucun bail ce mois-là : le bail de {noms} commence le {suivant.date_debut:%d/%m/%Y}"
        return "aucun bail ne couvre ce mois"
    return None


TOLERANCE = Decimal("0.95")


def _repartir(v, bail, mois, limite):
    """Le montant règle d'abord le loyer de son propre mois, puis les suivants (130 € pour 65 € : ce mois et le suivant).
    Le suivi repris dit ce qui a été payé : un mois encore sans paiement réglé un peu moins que le loyer calculé
    (révision non saisie, arrondi : au moins 95 %) prend le montant réellement payé, au lieu de laisser un petit reste dû
    qui décalerait tous les mois suivants."""
    from .models import Paiement

    reste, periode, premier = Decimal(v.montant), mois, True
    while reste > 0 and periode <= limite and not (bail.date_fin and periode > bail.date_fin):
        e = _echeance(bail, periode)
        if premier and e.total > 0 and TOLERANCE * e.total <= reste < e.total and not e.paiements.exists():
            ratio = reste / e.total
            e.loyer_hc = (e.loyer_hc * ratio).quantize(Decimal("0.01"))
            e.charges = reste - e.loyer_hc
            e.ajustee = True
            e.save(update_fields=["loyer_hc", "charges", "ajustee"])
        premier = False
        du = e.reste_du
        if du > 0:
            part = min(du, reste)
            Paiement.objects.create(echeance=e, versement=v, date=v.date, montant=part, mode=v.mode, notes=v.notes)
            reste -= part
        periode = _mois_suivant(periode)


def reconstruire(aujourdhui=None):
    """Refait la répartition des paiements déjà repris de l'historique (première version : répartition en chaîne depuis
    le premier mois, qui décalait un petit reste dû de mois en mois). Les paiements à 0 € sont retirés."""
    from .models import Versement

    limite = (aujourdhui or date.today()).replace(day=1)
    repris = list(Versement.objects.filter(notes__startswith=NOTE).select_related("bail").order_by("bail_id", "date", "pk"))
    for v in repris:
        v.paiements.all().delete()  # le statut des loyers est recalculé à chaque suppression
    for v in repris:
        if not v.montant:
            v.delete()
            continue
        _repartir(v, v.bail, v.date.replace(day=1), limite)
    return len(repris)


def convertir(historiques=None, aujourdhui=None, bail_suivant=False):
    """Convertit les lignes d'historique (toutes par défaut). Renvoie (nombre converti, lignes laissées).
    bail_suivant : un mois sans bail est rattaché au premier bail du lot qui commence après lui."""
    from .models import EncaissementHistorique, Paiement, Versement

    aujourdhui = aujourdhui or date.today()
    limite = aujourdhui.replace(day=1)
    lignes = list((historiques if historiques is not None else EncaissementHistorique.objects.all())
                  .select_related("lot").order_by("lot_id", "mois"))
    converties, laissees = 0, []
    for h in lignes:
        mois = h.mois.replace(day=1)
        fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
        if Paiement.objects.filter(echeance__bail__lot=h.lot, date__gte=mois, date__lte=fin).exists():
            laissees.append((h, "un paiement est déjà enregistré ce mois-là"))
            continue
        if not h.montant:
            h.delete()  # rien reçu ce mois-là : pas de paiement à créer
            converties += 1
            continue
        bail = _bail_du_mois(h.lot, mois) or (_bail_suivant(h.lot, mois) if bail_suivant else None)
        if bail is None:
            laissees.append((h, raison(h)))
            continue
        v = Versement.objects.create(bail=bail, date=mois, montant=h.montant, notes=(f"{NOTE} – {h.note}" if h.note else NOTE)[:255])
        _repartir(v, bail, mois, limite)
        h.delete()
        converties += 1
    return converties, laissees
