from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Article, Commande, Joueur, Ligne


class CommandeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("fabien", "f@example.com", "motdepasse")
        self.client.force_login(self.user)
        self.julie = Joueur.objects.create(prenom="Julie", nom="Martin", telephone="06 11 22 33 44")
        self.tom = Joueur.objects.create(prenom="Thomas", email="tom@example.com")
        self.tube = Article.objects.create(nom="Tube 4 balles", prix_vente=Decimal("7.50"), taux_commission=Decimal("10"))
        self.carton = Article.objects.create(nom="Carton 24 tubes", prix_vente=Decimal("117.60"), taux_commission=Decimal("5"))
        self.c = Commande.objects.create(nom="Novembre", lieu_remise="au club samedi")

    def test_nouvelle_commande_est_ferme(self):
        self.assertEqual(self.c.statut, Commande.Statut.FERME)

    def test_prix_et_commission_repris_de_l_article(self):
        l = Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        self.assertEqual(l.prix_unitaire, Decimal("7.50"))
        self.assertEqual(l.taux_commission, Decimal("10"))

    def test_totaux_et_commission(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.carton, quantite=1)
        Ligne.objects.create(commande=self.c, joueur=self.tom, article=self.tube, quantite=4, prix_unitaire=Decimal("7"))
        self.assertEqual(self.c.total_quantite, 7)
        self.assertEqual(self.c.nb_joueurs, 2)
        self.assertEqual(self.c.montant_total, Decimal("160.60"))  # 15 + 117,60 + 28
        self.assertEqual(self.c.commission, Decimal("10.18"))  # 1,50 + 5,88 + 2,80
        recap = {r["article"].nom: r["quantite"] for r in self.c.recap_articles()}
        self.assertEqual(recap, {"Tube 4 balles": 6, "Carton 24 tubes": 1})

    def test_ajout_ligne_par_formulaire(self):
        url = self.c.get_absolute_url()
        r = self.client.post(url, {"joueur": self.julie.pk, "article": self.tube.pk, "quantite": 3})
        self.assertRedirects(r, url)
        self.client.post(url, {"joueur": self.julie.pk, "article": self.carton.pk, "quantite": 1, "prix_unitaire": "110"})
        lignes = {l.article.nom: l for l in self.c.lignes.all()}
        self.assertEqual(lignes["Tube 4 balles"].prix_unitaire, Decimal("7.50"))
        self.assertEqual(lignes["Carton 24 tubes"].prix_unitaire, Decimal("110"))

    def test_actions_ligne(self):
        l = Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        url = reverse("balles:ligne_action", args=[l.pk])
        self.client.post(url, {"action": "paye"})
        self.client.post(url, {"action": "quantite", "quantite": "5"})
        l.refresh_from_db()
        self.assertTrue(l.paye)
        self.assertEqual(l.quantite, 5)

    def test_message_par_joueur_et_liens(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.carton, quantite=1)
        Ligne.objects.create(commande=self.c, joueur=self.tom, article=self.tube, quantite=1)
        r = self.client.get(reverse("balles:commande_messages", args=[self.c.pk]))
        self.assertContains(r, "Bonjour Julie, ta commande est arrivée : 1 × Carton 24 tubes, 2 × Tube 4 balles, soit 132,60 €")
        self.assertContains(r, "https://wa.me/33611223344?text=")
        self.assertContains(r, "mailto:tom@example.com")

    def test_joueur_prevenu_marque_toutes_ses_lignes(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.carton, quantite=1)
        self.client.post(reverse("balles:joueur_prevenu", args=[self.c.pk, self.julie.pk]))
        self.assertFalse(self.c.lignes.filter(prevenu_le__isnull=True).exists())

    def test_passage_recue_redirige_vers_messages(self):
        r = self.client.post(reverse("balles:commande_statut", args=[self.c.pk]), {"statut": "recue"})
        self.assertRedirects(r, reverse("balles:commande_messages", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.assertIsNotNone(self.c.date_reception)

    def test_pages_principales(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        for nom in ["accueil", "joueurs", "joueur_creer", "articles", "article_creer", "commande_creer"]:
            self.assertEqual(self.client.get(reverse("balles:" + nom)).status_code, 200)
        self.assertEqual(self.client.get(self.c.get_absolute_url()).status_code, 200)

    def test_creer_article(self):
        r = self.client.post(reverse("balles:article_creer"), {"nom": "Surgrip", "prix_vente": "3.00", "taux_commission": "15", "actif": "on"})
        self.assertRedirects(r, reverse("balles:articles"))
        self.assertTrue(Article.objects.filter(nom="Surgrip").exists())

    def test_dupliquer(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2, paye=True, remis=True)
        Ligne.objects.create(commande=self.c, joueur=self.tom, article=self.carton, quantite=1, prix_unitaire=Decimal("110"))
        self.c.statut = Commande.Statut.TERMINEE
        self.c.save()
        r = self.client.post(reverse("balles:commande_dupliquer", args=[self.c.pk]))
        copie = Commande.objects.exclude(pk=self.c.pk).get()
        self.assertRedirects(r, copie.get_absolute_url())
        self.assertEqual(copie.nom, "Novembre (copie)")
        self.assertEqual(copie.statut, Commande.Statut.FERME)
        self.assertEqual(copie.lieu_remise, "au club samedi")
        self.assertEqual(copie.montant_total, self.c.montant_total)
        self.assertFalse(copie.lignes.filter(paye=True).exists())
        self.assertFalse(copie.lignes.filter(remis=True).exists())
        self.assertEqual(self.c.lignes.count(), 2)

    def test_reste_a_encaisser_et_joueurs_a_livrer(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2, paye=True, remis=True)
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.carton, quantite=1)
        Ligne.objects.create(commande=self.c, joueur=self.tom, article=self.tube, quantite=1, remis=True)
        self.assertEqual(self.c.reste_a_encaisser, Decimal("125.10"))  # 117,60 + 7,50
        self.assertEqual(self.c.nb_joueurs_a_livrer, 1)
        r = self.client.get(reverse("balles:accueil"))
        self.assertContains(r, "Reste à encaisser")
        self.assertContains(r, "125,10 €")
        self.assertNotContains(r, "<th>Commission</th>")

    def test_supprimer_commande(self):
        Ligne.objects.create(commande=self.c, joueur=self.julie, article=self.tube, quantite=2)
        r = self.client.get(reverse("balles:accueil"))
        self.assertContains(r, reverse("balles:commande_supprimer", args=[self.c.pk]))
        self.assertEqual(self.client.get(reverse("balles:commande_supprimer", args=[self.c.pk])).status_code, 405)
        r = self.client.post(reverse("balles:commande_supprimer", args=[self.c.pk]))
        self.assertRedirects(r, reverse("balles:accueil"))
        self.assertFalse(Commande.objects.exists())
        self.assertFalse(Ligne.objects.exists())
        self.assertTrue(Joueur.objects.filter(pk=self.julie.pk).exists())

    def test_connexion_obligatoire(self):
        self.client.logout()
        r = self.client.get(reverse("balles:accueil"))
        self.assertEqual(r.status_code, 302)
