from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Commande, Joueur, Ligne


class CommandeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("fabien", "f@example.com", "motdepasse")
        self.client.force_login(self.user)
        self.julie = Joueur.objects.create(prenom="Julie", nom="Martin", telephone="06 11 22 33 44")
        self.tom = Joueur.objects.create(prenom="Thomas", email="tom@example.com")
        self.c = Commande.objects.create(
            nom="Novembre", prix_boite=Decimal("7.50"), taux_commission=Decimal("10"),
            lieu_remise="au club samedi",
        )

    def test_totaux_et_commission(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, nb_boites=2)
        Ligne.objects.create(commande=self.c, joueur=self.tom, nb_boites=4)
        self.assertEqual(self.c.total_boites, 6)
        self.assertEqual(self.c.montant_total, Decimal("45.00"))
        self.assertEqual(self.c.commission, Decimal("4.50"))

    def test_ajout_ligne_par_formulaire(self):
        r = self.client.post(self.c.get_absolute_url(), {"joueur": self.julie.pk, "nb_boites": 3})
        self.assertRedirects(r, self.c.get_absolute_url())
        self.assertEqual(self.c.lignes.get().nb_boites, 3)

    def test_actions_ligne(self):
        l = Ligne.objects.create(commande=self.c, joueur=self.julie, nb_boites=2)
        url = reverse("balles:ligne_action", args=[l.pk])
        self.client.post(url, {"action": "paye"})
        self.client.post(url, {"action": "boites", "nb_boites": "5"})
        l.refresh_from_db()
        self.assertTrue(l.paye)
        self.assertEqual(l.nb_boites, 5)

    def test_message_et_liens(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, nb_boites=2)
        Ligne.objects.create(commande=self.c, joueur=self.tom, nb_boites=1)
        r = self.client.get(reverse("balles:commande_messages", args=[self.c.pk]))
        self.assertContains(r, "Bonjour Julie")
        self.assertContains(r, "15,00 €")
        self.assertContains(r, "https://wa.me/33611223344?text=")
        self.assertContains(r, "mailto:tom@example.com")

    def test_passage_recue_redirige_vers_messages(self):
        r = self.client.post(reverse("balles:commande_statut", args=[self.c.pk]), {"statut": "recue"})
        self.assertRedirects(r, reverse("balles:commande_messages", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.assertIsNotNone(self.c.date_reception)

    def test_pages_principales(self):
        for nom in ["accueil", "joueurs", "joueur_creer", "commande_creer"]:
            self.assertEqual(self.client.get(reverse("balles:" + nom)).status_code, 200)

    def test_connexion_obligatoire(self):
        self.client.logout()
        r = self.client.get(reverse("balles:accueil"))
        self.assertEqual(r.status_code, 302)
