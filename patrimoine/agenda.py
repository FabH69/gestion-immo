"""Bouton « Ajouter à mon agenda » d'un rappel : fichier .ics (iPhone, Android, Outlook) et lien Google Agenda.

Avec une heure : rendez-vous d'une heure, alerte 1 heure avant. Sans heure : événement sur la journée.
Un rappel périodique devient un événement qui se répète (tous les mois, trimestres ou ans)."""
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
