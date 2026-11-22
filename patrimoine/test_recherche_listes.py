"""Champ « Filtrer ces … » des listes : un locataire se retrouve par son prénom autant que par son nom."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Bail, Bien, Garant, Locataire, Lot, Proprietaire


class RecherchePrenomLocataireTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="Apt 1"), type=Bail.Type.NU,
                                        date_debut=date(2025, 1, 1), loyer_hc=Decimal("600"), charges=Decimal("50"))
        self.locataire = Locataire.objects.create(nom="Martin", prenom="Camille")
        self.bail.locataires.add(self.locataire)
        autre = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="Apt 2"), type=Bail.Type.NU,
                                    date_debut=date(2025, 1, 1), loyer_hc=Decimal("500"), charges=Decimal("50"))
        autre.locataires.add(Locataire.objects.create(nom="Durand", prenom="Paul"))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def baux_trouves(self, q):
        reponse = self.client.get(reverse("admin:patrimoine_bail_changelist"), {"q": q})
        self.assertEqual(reponse.status_code, 200)
        return list(reponse.context["cl"].result_list)

    def test_baux_par_nom_prenom_et_les_deux(self):
        for q in ["Martin", "Camille", "camille", "Camille Martin", "Martin Camille"]:
            with self.subTest(q=q):
                self.assertEqual(self.baux_trouves(q), [self.bail])
        self.assertEqual(self.baux_trouves("Camille Durand"), [])

    def test_garants_par_prenom_du_locataire(self):
        garant = Garant.objects.create(nom="Leroy", prenom="Anne", locataire=self.locataire, bail=self.bail)
        reponse = self.client.get(reverse("admin:patrimoine_garant_changelist"), {"q": "Camille"})
        self.assertEqual(list(reponse.context["cl"].result_list), [garant])

    def test_listes_loyers_versements_depots_acceptent_le_prenom(self):
        for modele in ["echeance", "versement", "mouvementdepot"]:
            with self.subTest(modele=modele):
                reponse = self.client.get(reverse(f"admin:patrimoine_{modele}_changelist"), {"q": "Camille"})
                self.assertEqual(reponse.status_code, 200)
