"""Calendriers .ics des plateformes (Airbnb, Booking…) : lecture, mise à jour, occupation jour par jour et indicateurs du logement."""
import calendar
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone as tz
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import CalendrierICS, EvenementICS, ReservationLCD

TAILLE_MAX = 2_000_000  # un calendrier .ics fait quelques dizaines de Ko


def _deplier(texte):
    """Les lignes longues d'un .ics continuent sur la suivante, qui commence par une espace."""
    return re.sub(r"\r?\n[ \t]", "", texte).splitlines()


def _date_ics(valeur):
    valeur = valeur.strip()
    if re.fullmatch(r"\d{8}", valeur):
        return date(int(valeur[:4]), int(valeur[4:6]), int(valeur[6:8]))
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", valeur)
    if m:
        moment = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        if m.group(3):
            moment = timezone.localtime(moment.replace(tzinfo=tz.utc))
        return moment.date()
    raise ValueError(f"date illisible : {valeur}")


def _texte(valeur):
    return valeur.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def lire(texte):
    """Liste des événements d'un .ics : uid, début, fin (jour de départ), intitulé, description."""
    if "BEGIN:VCALENDAR" not in texte[:2000]:
        raise ValueError("ce n'est pas un calendrier .ics")
    evenements, courant = [], None
    for ligne in _deplier(texte):
        if ligne == "BEGIN:VEVENT":
            courant = {}
        elif ligne == "END:VEVENT" and courant is not None:
            if "DTSTART" in courant:
                debut = _date_ics(courant["DTSTART"])
                fin = _date_ics(courant["DTEND"]) if "DTEND" in courant else debut + timedelta(days=1)
                uid = courant.get("UID") or f"{debut:%Y%m%d}-{fin:%Y%m%d}"
                evenements.append({"uid": uid[:255], "debut": debut, "fin": max(fin, debut + timedelta(days=1)),
                                   "resume": _texte(courant.get("SUMMARY", ""))[:255], "description": _texte(courant.get("DESCRIPTION", ""))})
            courant = None
        elif courant is not None and ":" in ligne:
            cle, valeur = ligne.split(":", 1)
            courant.setdefault(cle.split(";", 1)[0].upper(), valeur)
    return evenements


def _bloque(plateforme, resume):
    """Airbnb distingue « Reserved » et « Not available » (dates bloquées) ; Booking écrit « CLOSED - Not available » partout."""
    resume = resume.lower()
    if plateforme == CalendrierICS.Plateforme.AIRBNB:
        return "not available" in resume or "indisponible" in resume
    return "bloqu" in resume


def _reference(description):
    m = re.search(r"/details/([A-Z0-9]{6,})", description) or re.search(r"(?:R[ée]servation|Reservation)\s*(?:n°|#|ID)?\s*:?\s*([A-Z0-9]{6,})", description)
    return m.group(1)[:40] if m else ""


@transaction.atomic
def importer(calendrier, texte):
    """Remplace les périodes du calendrier par celles du fichier ; les séjours passés absents du fichier sont gardés (historique)."""
    evenements = lire(texte)
    vus = set()
    for e in evenements:
        vus.add(e["uid"])
        EvenementICS.objects.update_or_create(calendrier=calendrier, uid=e["uid"], defaults={
            "debut": e["debut"], "fin": e["fin"], "resume": e["resume"], "reference": _reference(e["description"]),
            "bloque": _bloque(calendrier.plateforme, e["resume"])})
    aujourdhui = timezone.localdate()
    supprimes, _ = calendrier.evenements.exclude(uid__in=vus).filter(fin__gte=aujourdhui).delete()
    calendrier.derniere_synchro = timezone.now()
    calendrier.dernier_message = f"{len(evenements)} période{'s' if len(evenements) > 1 else ''} lue{'s' if len(evenements) > 1 else ''}" + (
        f", {supprimes} annulée{'s' if supprimes > 1 else ''}" if supprimes else "")
    calendrier.save(update_fields=["derniere_synchro", "dernier_message"])
    return len(evenements), supprimes


def telecharger(url):
    requete = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (gestion locative ; calendrier)"})
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        contenu = reponse.read(TAILLE_MAX + 1)
    if len(contenu) > TAILLE_MAX:
        raise ValueError("fichier trop gros pour un calendrier")
    return contenu.decode("utf-8", errors="replace")


def synchroniser(calendrier):
    """Relit le lien du calendrier. Renvoie (ok, message)."""
    if not calendrier.url:
        return False, "pas de lien : importez le fichier .ics"
    try:
        n, supprimes = importer(calendrier, telecharger(calendrier.url))
    except Exception as erreur:  # réseau coupé, site refusé par l'hébergeur, fichier illisible…
        if "Tunnel connection failed" in str(erreur) or "403" in str(erreur):
            message = ("échec de la mise à jour : l'hébergeur bloque ce site (compte PythonAnywhere gratuit). "
                       "Importez le fichier .ics à la main, ou passez à un compte payant")[:255]
        else:
            message = f"échec de la mise à jour : {erreur}"[:255]
        CalendrierICS.objects.filter(pk=calendrier.pk).update(dernier_message=message)
        return False, message
    return True, calendrier.dernier_message


# ---------------------------------------------------------------------------------------------------------------
# Occupation jour par jour : réservations importées (avec montants) et périodes des calendriers .ics (dates seules)
# ---------------------------------------------------------------------------------------------------------------

def sejours(lot, debut, fin):
    """Séjours qui touchent [debut, fin[ : réservations importées d'abord, puis périodes .ics qui ne les doublonnent pas."""
    resultat = []
    reservations = list(ReservationLCD.objects.filter(lot=lot, annulee=False, depart__gt=debut, arrivee__lt=fin))
    references = {r.reference for r in reservations}
    for r in reservations:
        resultat.append({"debut": r.arrivee, "fin": r.depart, "plateforme": r.plateforme, "titre": r.voyageur or r.get_plateforme_display(),
                         "source": r.get_plateforme_display(), "bloque": False, "montant": r.montant_brut, "reference": r.reference, "nuits": r.nuits})
    for e in EvenementICS.objects.filter(calendrier__lot=lot, calendrier__actif=True, fin__gt=debut, debut__lt=fin).select_related("calendrier"):
        if e.reference and e.reference in references:
            continue
        if not e.bloque and any(r.arrivee == e.debut and r.depart == e.fin for r in reservations):
            continue
        resultat.append({"debut": e.debut, "fin": e.fin, "plateforme": e.calendrier.plateforme, "titre": "Bloqué" if e.bloque else e.calendrier.nom,
                         "source": e.calendrier.nom, "bloque": e.bloque, "montant": None, "reference": e.reference, "nuits": e.nuits})
    resultat.sort(key=lambda s: (s["debut"], s["bloque"]))
    return resultat


def nuits_occupees(liste, debut, fin):
    """Nombre de nuits louées (hors dates bloquées) dans [debut, fin[, sans compter deux fois une nuit."""
    nuits = set()
    for s in liste:
        if s["bloque"]:
            continue
        jour = max(s["debut"], debut)
        while jour < min(s["fin"], fin):
            nuits.add(jour)
            jour += timedelta(days=1)
    return len(nuits)


def mois_calendrier(lot, annee, mois):
    """Grille du mois (semaines du lundi au dimanche) : pour chaque jour, le séjour en cours, l'arrivée et le départ."""
    premier = date(annee, mois, 1)
    dernier = date(annee, mois, calendar.monthrange(annee, mois)[1])
    debut_grille = premier - timedelta(days=premier.weekday())
    fin_grille = dernier + timedelta(days=6 - dernier.weekday())
    liste = sejours(lot, debut_grille, fin_grille + timedelta(days=1))
    aujourdhui = timezone.localdate()
    semaines, jour = [], debut_grille
    while jour <= fin_grille:
        semaine = []
        for _ in range(7):
            occupe = [s for s in liste if s["debut"] <= jour < s["fin"]]
            veille = jour - timedelta(days=1)
            avant = [s for s in liste if s["debut"] <= veille < s["fin"]]
            droite = occupe[0] if occupe else None
            semaine.append({
                "date": jour, "dans_mois": jour.month == mois, "aujourdhui": jour == aujourdhui, "passe": jour < aujourdhui,
                "sejour": droite, "gauche": avant[0] if avant else None,
                "etiquette": droite is not None and (droite["debut"] == jour or jour.weekday() == 0),
                "arrivee": next((s for s in liste if s["debut"] == jour), None),
                "depart": next((s for s in liste if s["fin"] == jour), None),
            })
            jour += timedelta(days=1)
        semaines.append(semaine)
    return semaines, [s for s in liste if s["fin"] > premier and s["debut"] <= dernier]


def indicateurs(lot, aujourdhui=None):
    """Chiffres de la page d'accueil : occupation du mois et des 30 prochains jours, revenus, prix moyen, prochains séjours."""
    from .lcd import suivi_annuel, totaliser

    aujourdhui = aujourdhui or timezone.localdate()
    premier = aujourdhui.replace(day=1)
    jours_mois = calendar.monthrange(aujourdhui.year, aujourdhui.month)[1]
    fin_mois = premier + timedelta(days=jours_mois)
    dans_30 = aujourdhui + timedelta(days=30)
    liste = sejours(lot, premier - timedelta(days=60), max(fin_mois, dans_30) + timedelta(days=120))
    mois = suivi_annuel(lot, aujourdhui.year)
    precedente = suivi_annuel(lot, aujourdhui.year - 1)
    cumul, cumul_n1 = totaliser(mois, aujourdhui.month), totaliser(precedente, aujourdhui.month)
    nuits_mois = nuits_occupees(liste, premier, fin_mois)
    nuits_30 = nuits_occupees(liste, aujourdhui, dans_30)
    en_cours = next((s for s in liste if s["debut"] <= aujourdhui < s["fin"] and not s["bloque"]), None)
    a_venir = [s for s in liste if s["debut"] > aujourdhui and not s["bloque"]][:6]
    ecart = (cumul["net"] - cumul_n1["net"]) / abs(cumul_n1["net"]) * 100 if cumul_n1["net"] else None
    commissions = cumul["commission"] + cumul["frais"]
    pourcent = lambda x: x / cumul["brut"] * 100 if cumul["brut"] else None  # noqa: E731
    ce_mois = dict(mois[aujourdhui.month - 1])
    ce_mois["commissions"] = ce_mois["commission"] + ce_mois["frais"]
    ce_mois["commissions_pct"] = ce_mois["commissions"] / ce_mois["brut"] * 100 if ce_mois["brut"] else None
    ce_mois["taxe_pct"] = ce_mois["taxe"] / ce_mois["brut"] * 100 if ce_mois["brut"] else None
    return {
        "occupation_mois": round(Decimal(nuits_mois) / jours_mois * 100), "nuits_mois": nuits_mois, "jours_mois": jours_mois,
        "occupation_30": round(Decimal(nuits_30) / 30 * 100), "nuits_30": nuits_30,
        "brut_mois": mois[aujourdhui.month - 1]["brut"], "net_mois": mois[aujourdhui.month - 1]["net"],
        "cumul": cumul, "cumul_n1": cumul_n1, "ecart_cumul": ecart,
        "en_cours": en_cours, "a_venir": a_venir, "prochaine_arrivee": a_venir[0] if a_venir else None,
        "libres_30": 30 - nuits_30,
        "mois": ce_mois, "commissions": commissions, "commissions_pct": pourcent(commissions),
        "taxe": cumul["taxe"], "taxe_pct": pourcent(cumul["taxe"]),
    }
