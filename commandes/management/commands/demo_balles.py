from decimal import Decimal

from django.core.management.base import BaseCommand

from commandes.models import Article, Commande, Joueur, Ligne


class Command(BaseCommand):
    help = "Crée quelques joueurs, articles et une commande fictive pour essayer le module balles."

    def handle(self, *args, **options):
        joueurs = []
        for prenom, nom, tel in [
            ("Julie", "Martin", "06 11 22 33 44"),
            ("Thomas", "Bernard", "06 22 33 44 55"),
            ("Sophie", "Petit", "07 33 44 55 66"),
            ("Lucas", "Durand", ""),
        ]:
            j, _ = Joueur.objects.get_or_create(prenom=prenom, nom=nom, defaults={"telephone": tel})
            joueurs.append(j)
        tube3, _ = Article.objects.get_or_create(nom="Tube de 3 balles Pulse", defaults={"prix_vente": Decimal("4.50"), "taux_commission": Decimal("10")})
        tube4, _ = Article.objects.get_or_create(nom="Tube de 4 balles Pulse", defaults={"prix_vente": Decimal("4.90"), "taux_commission": Decimal("10")})
        carton, _ = Article.objects.get_or_create(nom="Carton 24 tubes de 4 balles", defaults={"prix_vente": Decimal("117.60"), "taux_commission": Decimal("8")})
        c, cree = Commande.objects.get_or_create(nom="Commande démo", defaults={"lieu_remise": "au club samedi 10h-12h"})
        if cree:
            for j, a, q in [(joueurs[0], tube4, 6), (joueurs[0], tube3, 2), (joueurs[1], carton, 1), (joueurs[2], tube4, 4), (joueurs[3], tube3, 3)]:
                Ligne.objects.create(commande=c, joueur=j, article=a, quantite=q)
        self.stdout.write(self.style.SUCCESS("Données de démo créées."))
