"""Loyers encaissés repris d'un ancien suivi (historique) → paiements reçus.

Chaque ligne d'historique (un montant encaissé pour un lot, un mois) devient un paiement reçu du locataire en place
ce mois-là, daté du 1er du mois, et réglant ses loyers du plus ancien au plus récent à partir du premier mois repris :
130 € reçus en janvier pour un loyer de 65 € règlent janvier et février. Les loyers de ces mois sont créés s'ils
n'existent pas (jamais au-delà du mois en cours ni après la fin du bail) ; ce qui dépasse reste en avance.

Une ligne est laissée telle quelle quand aucun bail ne couvre le mois, ou quand un paiement est déjà enregistré pour
ce lot ce mois-là (les états et déclarations l'ignorent déjà dans ce cas : la convertir compterait deux fois).
Pour un mois antérieur au premier bail saisi (bail enregistré avec une date d'entrée trop tardive), l'action
« Rattacher au locataire suivant » de l'historique force le rattachement au bail qui suit. Une ligne à 0 € est
simplement retirée : rien n'a été reçu ce mois-là. Le mois d'un changement de locataire, le montant unique du mois
règle d'abord le loyer du locataire sortant (au prorata), le reste va au nouveau.

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


def _partager_changement_de_locataire(v, bail, mois, touches):
    """Mois de changement de locataire : l'ancien suivi n'a qu'un montant pour le logement (136 € du locataire sortant
    + 560 € du nouveau = 696 €). Le loyer du mois de chaque bail sorti ce mois-là est réglé d'abord, par un paiement reçu
    à son nom ; seul le reste va au nouveau locataire (sinon le surplus part en avance et décale tous ses mois)."""
    from .models import Paiement, Versement

    fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
    sortants = (bail.lot.baux.exclude(pk=bail.pk).filter(date_debut__lte=fin, date_fin__gte=mois, date_fin__lte=fin)
                .order_by("date_debut", "pk"))
    for autre in sortants:
        if v.montant <= 0:
            break
        e = _echeance(autre, mois)
        part = min(e.reste_du, v.montant)
        if part <= 0:
            continue
        sortant = Versement.objects.create(bail=autre, date=v.date, montant=part, mode=v.mode, notes=v.notes)
        Paiement.objects.create(echeance=e, versement=sortant, date=v.date, montant=part, mode=v.mode, notes=v.notes)
        touches[e.pk] = e
        v.montant -= part
        v.save(update_fields=["montant"])


def _repartir(v, bail, mois, limite, touches):
    """Le montant règle d'abord le loyer de son propre mois, puis les loyers plus anciens restés impayés (paiement en
    retard : rien en janvier, 130 € en février règlent février puis janvier), puis les mois suivants (130 € pour 65 €
    en janvier : janvier et février). Comme le bouton « Encaisser ». Le loyer n'est jamais modifié pour coller au paiement."""
    from .models import Echeance, Paiement

    _partager_changement_de_locataire(v, bail, mois, touches)
    if v.montant <= 0:  # tout était au locataire sortant
        v.delete()
        return
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


def reconstruire(aujourdhui=None, lots=None):
    """Refait la répartition des paiements déjà repris de l'historique (première version : répartition en chaîne depuis
    le premier mois, qui décalait un petit reste dû de mois en mois). Les paiements à 0 € sont retirés.
    lots : seulement ces logements (bouton « Refaire la répartition de l'historique »), après une correction des baux.
    Chaque mois repris revient d'abord en un seul paiement, rattaché au bail en place ce mois-là (un locataire ajouté
    ou une date d'entrée corrigée sont pris en compte), puis il est réparti à nouveau."""
    from .models import Versement

    limite = (aujourdhui or date.today()).replace(day=1)
    qs = Versement.objects.filter(notes__startswith=NOTE, annule=False, part_depot=0, rembourse=0)
    if lots is not None:
        qs = qs.filter(bail__lot__in=lots)
    from .models import Echeance

    ids = list(Echeance.objects.filter(paiements__versement__in=qs).distinct().values_list("pk", flat=True))
    repris = _regrouper_par_mois(list(qs.select_related("bail__lot").order_by("date", "pk")))  # par date : du plus ancien au plus récent
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
    if lots is not None:
        nettoyer_hors_bail(lots)
    return len(repris)


def nettoyer_hors_bail(lots):
    """Loyers d'un bail tombant hors de ses dates (date de fin avancée, locataire ajouté entre-temps) : retirés quand
    rien de réel ne les règle (aucun paiement, ou seulement un « Supposé réglé » d'un ancien rattrapage, qui est
    retiré avec). Le mois de départ ou d'entrée resté à payer repasse au prorata. Renvoie les loyers gardés parce
    qu'un vrai paiement les règle (à vérifier)."""
    from .models import Bail, Echeance

    suppose = Bail.PAIEMENT_SUPPOSE
    gardes = []
    for bail in Bail.objects.filter(lot__in=lots):
        debut = bail.date_debut.replace(day=1)
        hors = bail.echeances.filter(nature=Echeance.Nature.LOYER, periode__lt=debut)
        if bail.date_fin:
            hors = hors | bail.echeances.filter(nature=Echeance.Nature.LOYER, periode__gt=bail.date_fin)
        for e in hors.prefetch_related("paiements__versement"):
            paiements = list(e.paiements.all())
            if any(not (p.notes or "").startswith(suppose) for p in paiements):
                gardes.append(e)
                continue
            versements = {p.versement for p in paiements}
            e.delete()
            for v in versements:
                if not v.paiements.exists() and (v.notes or "").startswith(suppose):
                    v.delete()
        # « Supposé réglé » dont le loyer a déjà été retiré (départ saisi plus tôt) : ce n'est pas de l'argent reçu
        bail.versements.filter(notes__startswith=suppose, paiements__isnull=True).delete()
        for e in bail.echeances.filter(nature=Echeance.Nature.LOYER, paiements__isnull=True):
            e.actualiser_loyer()  # mois d'entrée ou de départ : prorata avec les dates corrigées
    return gardes


def _regrouper_par_mois(repris):
    """Un mois repris de l'historique = un montant pour le logement : les parts déjà séparées (locataire sortant /
    entrant) sont remises ensemble, sur le bail en place ce mois-là (le plus récent), avant d'être réparties à nouveau."""
    groupes = {}
    for v in repris:
        groupes.setdefault((v.bail.lot_id, v.date), []).append(v)
    gardes = []
    for (_, jour), vs in groupes.items():
        mois = jour.replace(day=1)
        bail = _bail_du_mois(vs[0].bail.lot, mois) or vs[0].bail  # mois sans bail (rattaché au suivant) : inchangé
        v = next((x for x in vs if x.bail_id == bail.pk), vs[0])
        total = sum((x.montant for x in vs), Decimal(0))
        for autre in vs:
            if autre is not v:
                autre.paiements.all().delete()
                autre.delete()
        if (v.bail_id, v.montant) != (bail.pk, total):
            v.bail, v.montant = bail, total
            v.save(update_fields=["bail", "montant"])
        gardes.append(v)
    return gardes


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
