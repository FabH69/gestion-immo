"""Mail des rappels : le jour choisi sur chaque rappel (« me prévenir par mail … jours avant »), et pour un rappel ★ aussi le jour même s'il n'est pas fait.

    python manage.py rappels_par_mail [--essai]

À lancer chaque jour (PythonAnywhere › Tasks) : bash ~/gestion-immo/deploiement/tache_quotidienne.sh"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from patrimoine import mails
from patrimoine.models import Rappel


def adresse_du_site():
    hote = next((h for h in settings.ALLOWED_HOSTS if "." in h), "")
    return f"https://{hote}" if hote else ""


class Command(BaseCommand):
    help = "Envoie par mail les rappels arrivant à échéance"

    def add_arguments(self, parser):
        parser.add_argument("--essai", action="store_true", help="Affiche les mails sans les envoyer")

    def handle(self, *args, essai=False, **options):
        aujourdhui = timezone.localdate()
        reglage = mails.reglage()
        if not essai and (not reglage or not reglage.alerte_rappels):
            self.stdout.write("Mails des rappels désactivés (Paramètres › Mails).")
            return
        a_envoyer = []
        for r in Rappel.objects.filter(actif=True, fait=False).select_related("bien").order_by("date", "heure"):
            etape = r.mail_a_envoyer(aujourdhui)
            if etape:
                a_envoyer.append((r, etape))
        if not a_envoyer:
            self.stdout.write("Aucun rappel important à envoyer aujourd'hui.")
            return
        site = adresse_du_site()
        lignes = []
        for r, etape in a_envoyer:
            jours = (r.date - aujourdhui).days
            quand = ("aujourd'hui" if jours == 0 else f"en retard de {-jours} jour{'s' if -jours > 1 else ''}" if jours < 0
                     else f"dans {jours} jour{'s' if jours > 1 else ''}")
            lien = site + reverse("admin:patrimoine_rappel_change", args=[r.pk]) if site else ""
            a_heure = f" à {r.heure_texte}" if r.heure else ""
            lignes.append(f"{'★ ' if r.important else '• '}{r.titre}{' – ' + r.bien.nom if r.bien else ''} : le {r.date:%d/%m/%Y}{a_heure} ({quand})"
                          + (f"\n   {r.notes.strip()}" if r.notes.strip() else "") + (f"\n   {lien}" if lien else ""))
        n = len(a_envoyer)
        premier = a_envoyer[0][0]
        sujet = (f"Rappel : {premier.titre} le {premier.date:%d/%m}{' à ' + premier.heure_texte if premier.heure else ''}" if n == 1
                 else f"{n} rappels à traiter")
        texte = ("Bonjour,\n\n" + ("Ce rappel arrive à échéance :" if n == 1 else "Ces rappels arrivent à échéance :") + "\n\n"
                 + "\n\n".join(lignes) + "\n\nCochez-les « Fait » sur l'accueil de l'appli une fois réglés"
                 + (f" : {site}/admin/" if site else ".") + "\n")
        if essai:
            self.stdout.write(f"Sujet : {sujet}\n\n{texte}")
            return
        if mails.envoyer([reglage.adresse], sujet, texte, reglage):
            Rappel.objects.filter(pk__in=[r.pk for r, _ in a_envoyer]).update(dernier_mail=aujourdhui)
            self.stdout.write(self.style.SUCCESS(f"Mail envoyé à {reglage.adresse} ({n} rappel(s))."))
        else:
            self.stderr.write("Le mail n'a pas pu partir (vérifiez Paramètres › Mails).")
