from decimal import Decimal

from django.core.management.base import BaseCommand

from commandes.models import Commande, Joueur, Ligne


class Command(BaseCommand):
    help = "Crée quelques joueurs et une commande fictive pour essayer le site."

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
        c, _ = Commande.objects.get_or_create(
            nom="Commande démo",
            defaults={"prix_boite": Decimal("7.50"), "taux_commission": Decimal("10"),
                      "lieu_remise": "au club samedi 10h-12h"},
        )
        for j, nb in zip(joueurs, [2, 1, 4, 3]):
            Ligne.objects.get_or_create(commande=c, joueur=j, defaults={"nb_boites": nb})
        self.stdout.write(self.style.SUCCESS("Données de démo créées."))
