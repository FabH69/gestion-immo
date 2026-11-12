"""Relit les liens des calendriers .ics (Airbnb, Booking…) : lancé chaque matin par la tâche quotidienne."""
from django.core.management.base import BaseCommand

from patrimoine import ical
from patrimoine.models import CalendrierICS


class Command(BaseCommand):
    help = "Met à jour les calendriers .ics qui ont un lien."

    def handle(self, *args, **options):
        for c in CalendrierICS.objects.filter(actif=True).exclude(url=""):
            ok, message = ical.synchroniser(c)
            self.stdout.write(f"{c} : {message}")
