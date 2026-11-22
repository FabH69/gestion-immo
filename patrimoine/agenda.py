"""Agenda : bouton « Ajouter à mon agenda » d'un rappel (fichier .ics, lien Google Agenda) et abonnement automatique
(flux .ics secret par utilisateur que le téléphone ou Google Agenda relit tout seul).

Avec une heure : rendez-vous d'une heure, alerte 1 heure avant. Sans heure : événement sur la journée.
Un rappel périodique devient un événement qui se répète (tous les mois, trimestres ou ans)."""
import hashlib
from datetime import datetime, timedelta, timezone as tz
from urllib.parse import urlencode

from django.utils import timezone

DUREE_RDV = timedelta(hours=1)
REPETITION = {"mensuel": "FREQ=MONTHLY", "trimestriel": "FREQ=MONTHLY;INTERVAL=3", "annuel": "FREQ=YEARLY"}


def _debut_fin(rappel):
    """(début, fin) en UTC pour un rendez-vous, None sans heure."""
    if rappel.heure is None:
        return None
    debut = timezone.make_aware(datetime.combine(rappel.date, rappel.heure)).astimezone(tz.utc)
    return debut, debut + DUREE_RDV


def _lieu(rappel):
    bien = rappel.bien
    if not bien:
        return ""
    adresse = ", ".join(x for x in (bien.adresse.strip(), f"{bien.code_postal} {bien.ville}".strip()) if x)
    return f"{bien.nom} – {adresse}" if adresse else bien.nom


def _description(rappel, lien):
    return "\n\n".join(x for x in (rappel.notes.strip(), f"Rappel dans l'appli : {lien}" if lien else "") if x)


def _texte(valeur):
    """Échappement des textes iCalendar (RFC 5545)."""
    return (valeur.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _plier(ligne):
    """Lignes de 75 octets au plus, la suite commence par une espace."""
    morceaux, courant = [], ""
    for c in ligne:
        if len((courant + c).encode()) > 75:
            morceaux.append(courant)
            courant = " "
        courant += c
    morceaux.append(courant)
    return "\r\n".join(morceaux)


def fichier_ics(rappel, lien=""):
    utc = "%Y%m%dT%H%M%SZ"
    lignes = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Gestion immobiliere//Rappels//FR", "CALSCALE:GREGORIAN",
              "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:rappel-{rappel.pk}@gestion-immo",
              f"DTSTAMP:{timezone.now().astimezone(tz.utc):{utc}}"]
    horaire = _debut_fin(rappel)
    if horaire:
        lignes += [f"DTSTART:{horaire[0]:{utc}}", f"DTEND:{horaire[1]:{utc}}"]
    else:
        lignes += [f"DTSTART;VALUE=DATE:{rappel.date:%Y%m%d}", f"DTEND;VALUE=DATE:{rappel.date + timedelta(days=1):%Y%m%d}"]
    if rappel.periodicite in REPETITION:
        lignes.append(f"RRULE:{REPETITION[rappel.periodicite]}")
    lignes.append(f"SUMMARY:{_texte(rappel.titre)}")
    description, lieu = _description(rappel, lien), _lieu(rappel)
    if description:
        lignes.append(f"DESCRIPTION:{_texte(description)}")
    if lieu:
        lignes.append(f"LOCATION:{_texte(lieu)}")
    if horaire:
        lignes += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_texte(rappel.titre)}", "TRIGGER:-PT1H", "END:VALARM"]
    lignes += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_plier(l) for l in lignes) + "\r\n"


def lien_google(rappel, lien=""):
    horaire = _debut_fin(rappel)
    if horaire:
        dates = f"{horaire[0]:%Y%m%dT%H%M%SZ}/{horaire[1]:%Y%m%dT%H%M%SZ}"
    else:
        dates = f"{rappel.date:%Y%m%d}/{rappel.date + timedelta(days=1):%Y%m%d}"
    params = {"action": "TEMPLATE", "text": rappel.titre, "dates": dates}
    if rappel.periodicite in REPETITION:
        params["recur"] = f"RRULE:{REPETITION[rappel.periodicite]}"
    description, lieu = _description(rappel, lien), _lieu(rappel)
    if description:
        params["details"] = description
    if lieu:
        params["location"] = lieu
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


# ---------- abonnement : tous les rappels et échéances, relus tout seuls par l'agenda ----------

HORIZON_FLUX_JOURS = 365
HEURE_ALERTE = 9  # événement sur la journée : alerte à 9 h


def _alarme(titre, declencheur):
    return ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_texte(titre)}", f"TRIGGER:{declencheur}", "END:VALARM"]


def _declencheur_jours_avant(jours, avec_heure):
    """Alerte N jours avant : à l'heure du rendez-vous, ou à 9 h pour un événement sur la journée."""
    if avec_heure:
        return f"-P{jours}D"
    if jours == 0:
        return f"PT{HEURE_ALERTE}H"
    reste = 24 - HEURE_ALERTE
    return f"-P{jours - 1}DT{reste}H" if jours > 1 else f"-PT{reste}H"


def _vevent(uid, titre, jour, heure=None, periodicite="", description="", lieu="", categorie="", jours_avant=None):
    utc = "%Y%m%dT%H%M%SZ"
    lignes = ["BEGIN:VEVENT", f"UID:{uid}@gestion-immo", f"DTSTAMP:{timezone.now().astimezone(tz.utc):{utc}}"]
    if heure is not None:
        debut = timezone.make_aware(datetime.combine(jour, heure)).astimezone(tz.utc)
        lignes += [f"DTSTART:{debut:{utc}}", f"DTEND:{debut + DUREE_RDV:{utc}}"]
    else:
        lignes += [f"DTSTART;VALUE=DATE:{jour:%Y%m%d}", f"DTEND;VALUE=DATE:{jour + timedelta(days=1):%Y%m%d}",
                   "TRANSP:TRANSPARENT"]
    if periodicite in REPETITION:
        lignes.append(f"RRULE:{REPETITION[periodicite]}")
    lignes.append(f"SUMMARY:{_texte(titre)}")
    if description:
        lignes.append(f"DESCRIPTION:{_texte(description)}")
    if lieu:
        lignes.append(f"LOCATION:{_texte(lieu)}")
    if categorie:
        lignes.append(f"CATEGORIES:{_texte(categorie)}")
    # alertes : le jour même (1 h avant un rendez-vous, 9 h sinon), et N jours avant si demandé
    lignes += _alarme(titre, "-PT1H" if heure is not None else f"PT{HEURE_ALERTE}H")
    if jours_avant:
        lignes += _alarme(titre, _declencheur_jours_avant(jours_avant, heure is not None))
    lignes.append("END:VEVENT")
    return lignes


# échéances calculées que l'on peut ajouter à l'abonnement (case à cocher sur la page « Mon agenda »)
TYPES_CALCULES = [("revisions", "Révisions de loyer (IRL)"), ("assurances", "Attestations d'assurance habitation"),
                  ("chaudieres", "Entretiens de chaudière"), ("dpe", "DPE à refaire et dates d'interdiction de louer"),
                  ("loyers", "Loyers impayés"), ("entrees", "Tâches d'entrée et de sortie de locataire")]


def evenements_utilisateur(user, types=(), aujourdhui=None):
    """Ce qui part dans l'agenda, selon les droits de l'utilisateur : seulement les rappels saisis dont la case
    « 📅 dans mon agenda » est cochée, et les échéances calculées des types choisis (sur 12 mois)."""
    from . import droits
    from .models import Rappel
    from .rappels import a_faire
    from django.urls import reverse

    aujourdhui = aujourdhui or timezone.localdate()
    outils, loyers, relocation = (droits.niveau(user, c) for c in ("outils", "loyers", "relocation"))
    evenements = []
    if outils:
        for r in Rappel.objects.filter(fait=False, actif=True, dans_agenda=True).select_related("bien"):
            jours = r.prevenir_jours if r.prevenir_jours is not None else (7 if r.important else None)
            evenements.append({"uid": f"rappel-{r.pk}", "titre": ("★ " if r.important else "") + r.titre, "date": r.date,
                               "heure": r.heure, "periodicite": r.periodicite, "notes": r.notes.strip(), "lieu": _lieu(r),
                               "categorie": r.get_categorie_display(), "jours_avant": jours or None,
                               "url": reverse("admin:patrimoine_rappel_change", args=[r.pk])})
    autorises = {"loyers": loyers, "entrees": relocation}
    types = set(types) & {code for code, _ in TYPES_CALCULES}
    if not types:
        return evenements
    for i in a_faire(aujourdhui, HORIZON_FLUX_JOURS, avec_taches=bool(relocation) and "entrees" in types):
        if i["genre"] not in types or not autorises.get(i["genre"], outils):
            continue
        empreinte = hashlib.sha1(f"{i['genre']}|{i['url']}|{i['date']}|{i['titre']}".encode()).hexdigest()[:16]
        evenements.append({"uid": f"{i['genre']}-{empreinte}", "titre": ("★ " if i["important"] else "") + i["titre"],
                           "date": i["date"], "heure": i.get("heure"), "periodicite": "", "notes": i["detail"],
                           "lieu": "", "categorie": i["categorie"], "jours_avant": None, "url": i["url"]})
    return evenements


def flux_ics(user, types=(), base_url="", aujourdhui=None):
    """Calendrier complet d'un utilisateur, pour un abonnement (iPhone, Android via Google Agenda, Outlook)."""
    lignes = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Gestion immobiliere//Agenda//FR", "CALSCALE:GREGORIAN",
              "METHOD:PUBLISH", "X-WR-CALNAME:Gestion immo", "X-WR-TIMEZONE:Europe/Paris",
              "X-WR-CALDESC:Rappels et échéances de l'appli de gestion immobilière",
              "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for e in evenements_utilisateur(user, types, aujourdhui):
        lien = f"{base_url}{e['url']}" if base_url and e["url"].startswith("/") else ""
        description = "\n\n".join(x for x in (e["notes"], f"Dans l'appli : {lien}" if lien else "") if x)
        lignes += _vevent(e["uid"], e["titre"], e["date"], e["heure"], e["periodicite"],
                          description, e["lieu"], e["categorie"], e["jours_avant"])
    lignes.append("END:VCALENDAR")
    return "\r\n".join(_plier(l) for l in lignes) + "\r\n"
