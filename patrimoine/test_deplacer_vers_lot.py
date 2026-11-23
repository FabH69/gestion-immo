"""Commande deplacer_vers_lot : rattacher un bien saisi à part (ex. un garage) à un lot d'un autre bien."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import (Bail, Bien, EncaissementHistorique, Echeance, Locataire, Lot, Paiement, PieceComptable,
                     Proprietaire, Rappel)


def lancer(*args):
    sortie = StringIO()
    call_command("deplacer_vers_lot", *args, stdout=sortie)
    return sortie.getvalue()


class DeplacerVersLotTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR)
        commun = dict(proprietaire=proprio, adresse="1 rue", code_postal="69000", ville="Lyon")
        self.garage = Bien.objects.create(nom="Garage Saint Pierre", **commun)
        self.saint_pierre = Bien.objects.create(nom="Saint Pierre", **commun)
        self.lot_ancien = Lot.objects.create(bien=self.garage, designation="Garage", type=Lot.Type.GARAGE,
                                             surface=Decimal("15"), code_porte="1234")
        self.apt = Lot.objects.create(bien=self.saint_pierre, designation="Appartement")
        self.lot_garage = Lot.objects.create(bien=self.saint_pierre, designation="Garage", type=Lot.Type.GARAGE)
        self.bail = Bail.objects.create(lot=self.lot_ancien, type=Bail.Type.NU, date_debut=date(2025, 1, 1),
                                        loyer_hc=Decimal("80"), charges=Decimal("0"), jour_echeance=5)
        self.bail.locataires.add(Locataire.objects.create(nom="Durand"))
        Echeance.generer_pour_mois(date(2026, 10, 1))
        Paiement.objects.create(echeance=Echeance.objects.get(), date=date(2026, 10, 5), montant=Decimal("80"))
        EncaissementHistorique.objects.create(lot=self.lot_ancien, mois=date(2024, 12, 1), montant=Decimal("80"))
        self.facture = PieceComptable.objects.create(bien=self.garage, date=date(2026, 3, 5), fournisseur="Mairie",
                                                     libelle="Taxe foncière", montant=Decimal("120"), rubrique="taxe_fonciere")
        Rappel.objects.create(titre="Révision garage", date=date(2027, 1, 1), bien=self.garage)

    def args(self, *plus):
        return ["--source", "Garage Saint Pierre", "--cible", "saint pierre", *plus]

    def test_sans_option_liste_les_biens(self):
        sortie = lancer()
        self.assertIn(f"[{self.saint_pierre.pk}] Saint Pierre", sortie)
        self.assertIn(f"lot [{self.lot_garage.pk}] Garage", sortie)

    def test_simulation_ne_modifie_rien(self):
        sortie = lancer(*self.args())
        self.assertIn("SIMULATION", sortie)
        self.assertIn("Durand", sortie)
        self.assertIn("1 loyer(s), 1 paiement(s)", sortie)
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.lot, self.lot_ancien)
        self.assertEqual(PieceComptable.objects.get().bien, self.garage)

    def test_appliquer_deplace_tout_et_garde_l_ancien_bien_vide(self):
        sortie = lancer(*self.args("--appliquer"))
        self.assertIn("Terminé", sortie)
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.lot, self.lot_garage)  # le garage est trouvé tout seul parmi les lots
        self.assertEqual(self.bail.echeances.get().paiements.count(), 1)
        self.assertEqual(EncaissementHistorique.objects.get().lot, self.lot_garage)
        self.assertEqual(PieceComptable.objects.get().bien, self.saint_pierre)
        self.assertEqual(Rappel.objects.get().bien, self.saint_pierre)
        self.lot_garage.refresh_from_db()
        self.assertEqual(self.lot_garage.surface, Decimal("15"))  # champs vides complétés
        self.assertEqual(self.lot_garage.code_porte, "1234")
        self.assertTrue(Bien.objects.filter(pk=self.garage.pk).exists())  # ancien bien gardé, vide
        self.assertFalse(self.lot_ancien.baux.exists())

    def test_relancer_ne_fait_rien(self):
        lancer(*self.args("--appliquer"))
        self.assertIn("Rien à déplacer", lancer(*self.args("--appliquer")))

    def test_lot_cible_par_designation_ou_numero(self):
        lancer(*self.args("--lot-cible", str(self.apt.pk), "--appliquer"))
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.lot, self.apt)

    def test_doublon_de_mois_bloque_tout(self):
        EncaissementHistorique.objects.create(lot=self.lot_garage, mois=date(2024, 12, 1), montant=Decimal("80"))
        with self.assertRaises(CommandError):
            lancer(*self.args("--appliquer"))
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.lot, self.lot_ancien)

    def test_bien_introuvable(self):
        with self.assertRaises(CommandError):
            lancer("--source", "Garage inconnu", "--cible", "Saint Pierre")

    def test_proprietaires_differents_avertit(self):
        autre = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.SCI_IR)
        self.saint_pierre.proprietaire = autre
        self.saint_pierre.save()
        self.assertIn("Propriétaires différents", lancer(*self.args()))
