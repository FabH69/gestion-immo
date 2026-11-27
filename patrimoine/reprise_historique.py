"""Loyers encaissés repris d'un ancien suivi (historique) → paiements reçus.

Chaque ligne d'historique (un montant encaissé pour un lot, un mois) devient un paiement reçu du locataire en place
ce mois-là, daté du 1er du mois, et réglant ses loyers du plus ancien au plus récent à partir du premier mois repris :
130 € reçus en janvier pour un loyer de 65 € règlent janvier et février. Les loyers de ces mois sont créés s'ils
n'existent pas (jamais au-delà du mois en cours ni après la fin du bail) ; ce qui dépasse reste en avance.

Une ligne est laissée telle quelle quand aucun bail ne couvre le mois, ou quand un paiement est déjà enregistré pour
ce lot ce mois-là (les états et déclarations l'ignorent déjà dans ce cas : la convertir compterait deux fois).
Pour un mois antérieur au premier bail saisi (bail enregistré avec une date d'entrée trop tardive), l'action
« Rattacher au locataire suivant » de l'historique force le rattachement au bail qui suit. Une ligne à 0 € est
simplement retirée : rien n'a été reçu ce mois-là.

Pendant la reprise seulement, un mois payé presque entièrement (écart d'au plus 5 % : ancien loyer avant une révision
non saisie, arrondi) est soldé par une remise « Écart de reprise de l'historique », visible sur le loyer : le loyer
n'est pas modifié. Les paiements saisis ensuite suivent la règle stricte : seul un règlement reçu rend un loyer payé."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

NOTE = "Repris du suivi historique"
MOTIF_ECART = "Écart de reprise de l'historique"
ECART_MAX = Decimal("0.05")  # au plus 5 % du loyer du mois


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


def raisons(historiques):
    """Comme raison(h) pour une liste de lignes d'historique, en quelques requêtes au total (contrôles)."""
    from .models import Bail, Paiement

    lots = {h.lot_id for h in historiques}
    if not lots:
        return []
    payes = {(lot, d.year, d.month) for lot, d in
             Paiement.objects.filter(echeance__bail__lot_id__in=lots).values_list("echeance__bail__lot_id", "date")}
    baux = {}
    for b in Bail.objects.filter(lot_id__in=lots).prefetch_related("locataires").order_by("date_debut", "pk"):
        baux.setdefault(b.lot_id, []).append(b)
    resultat = []
    for h in historiques:
        mois = h.mois.replace(day=1)
        fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
        du_lot = baux.get(h.lot_id, [])
        if (h.lot_id, mois.year, mois.month) in payes:
            resultat.append("un paiement est déjà enregistré ce mois-là")
        elif not any(b.date_debut <= fin and not (b.date_fin and b.date_fin < mois) for b in du_lot):
            suivant = next((b for b in du_lot if b.date_debut > mois), None)
            if suivant:
                noms = ", ".join(str(l) for l in suivant.locataires.all()) or "le bail suivant"
                resultat.append(f"aucun bail ce mois-là : le bail de {noms} commence le {suivant.date_debut:%d/%m/%Y}")
            else:
                resultat.append("aucun bail ne couvre ce mois")
        else:
            resultat.append(None)
    return resultat


def _repartir(v, bail, mois, limite, touches):
    """Le montant règle d'abord le loyer de son propre mois, puis les loyers plus anciens restés impayés (paiement en
    retard : rien en janvier, 130 € en février règlent février puis janvier), puis les mois suivants (130 € pour 65 €
    en janvier : janvier et février). Comme le bouton « Encaisser ». Le loyer n'est jamais modifié pour coller au paiement."""
    from .models import Echeance, Paiement

    reste = Decimal(v.montant)

    def payer(e):
        nonlocal reste
        du = e.reste_du
        if du > 0 and reste > 0:
            part = min(du, reste)
            Paiement.objects.create(echeance=e, versement=v, date=v.date, montant=part, mode=v.mode, notes=v.notes)
            touches[e.pk] = e
            reste -= part

    if mois <= limite and not (bail.date_fin and mois > bail.date_fin):
        payer(_echeance(bail, mois))
    premier = bail.echeances.filter(nature=Echeance.Nature.LOYER).order_by("periode").values_list("periode", flat=True).first()
    periode = max(premier, bail.date_debut.replace(day=1)) if premier else mois
    while reste > 0 and periode < mois:  # mois plus anciens, y compris un mois sans rien reçu entre deux paiements
        payer(_echeance(bail, periode))
        periode = _mois_suivant(periode)
    periode = _mois_suivant(mois)
    while reste > 0 and periode <= limite and not (bail.date_fin and periode > bail.date_fin):
        payer(_echeance(bail, periode))
        periode = _mois_suivant(periode)


def _solder_ecarts(touches):
    """Une fois tout le suivi repris : un mois payé à au moins 95 % est soldé par une remise (écart de reprise)."""
    soldes = 0
    for e in touches.values():
        e.refresh_from_db()
        du = e.reste_du
        if 0 < du <= ECART_MAX * e.total and e.montant_paye > 0:
            e.remise = (e.remise or 0) + du
            e.motif_remise = MOTIF_ECART
            e.save(update_fields=["remise", "motif_remise"])
            e.mettre_a_jour_statut()
            soldes += 1
    return soldes


def _retirer_soldes(echeances):
    """Retire les remises d'écart de reprise (avant de refaire la répartition)."""
    for e in echeances.filter(motif_remise=MOTIF_ECART):
        e.remise, e.motif_remise = 0, ""
        e.save(update_fields=["remise", "motif_remise"])


def reconstruire(aujourdhui=None):
    """Refait la répartition des paiements déjà repris de l'historique (première version : répartition en chaîne depuis
    le premier mois, qui décalait un petit reste dû de mois en mois). Les paiements à 0 € sont retirés."""
    from .models import Versement

    limite = (aujourdhui or date.today()).replace(day=1)
    repris = list(Versement.objects.filter(notes__startswith=NOTE).select_related("bail").order_by("bail_id", "date", "pk"))
    from .models import Echeance

    echeances = Echeance.objects.filter(paiements__versement__in=repris).distinct()
    ids = list(echeances.values_list("pk", flat=True))
    for v in repris:
        v.paiements.all().delete()  # le statut des loyers est recalculé à chaque suppression
    echeances = Echeance.objects.filter(pk__in=ids)
    _retirer_soldes(echeances)
    for e in echeances.filter(ajustee=True, paiements__isnull=True):  # ancien ajustement automatique du loyer : annulé
        e.ajustee = False
        e.save(update_fields=["ajustee"])
    for e in echeances:
        e.actualiser_loyer()  # loyer en vigueur ce mois-là (révision ou loyer initial saisis sur le bail)
        e.mettre_a_jour_statut()
    touches = {}
    for v in repris:
        if not v.montant:
            v.delete()
            continue
        _repartir(v, v.bail, v.date.replace(day=1), limite, touches)
    _solder_ecarts(touches)
    return len(repris)


def convertir(historiques=None, aujourdhui=None, bail_suivant=False):
    """Convertit les lignes d'historique (toutes par défaut). Renvoie (nombre converti, lignes laissées).
    bail_suivant : un mois sans bail est rattaché au premier bail du lot qui commence après lui."""
    from .models import EncaissementHistorique, Paiement, Versement

    aujourdhui = aujourdhui or date.today()
    limite = aujourdhui.replace(day=1)
    lignes = list((historiques if historiques is not None else EncaissementHistorique.objects.all())
                  .select_related("lot").order_by("lot_id", "mois"))
    converties, laissees, touches = 0, [], {}
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
        _repartir(v, bail, mois, limite, touches)
        h.delete()
        converties += 1
    _solder_ecarts(touches)
    return converties, laissees
