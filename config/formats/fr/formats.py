"""Dates affichées partout au format 03/06/2026 (et non « 3 juin 2026 » sur certaines listes)."""
from django.conf.locale.fr.formats import *  # noqa: F401,F403

DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y H:i"
SHORT_DATE_FORMAT = "d/m/Y"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
