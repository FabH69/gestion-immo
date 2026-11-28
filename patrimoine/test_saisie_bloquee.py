"""Verrou de saisie bien par bien (SCI à plusieurs immeubles) : « Bloquer la saisie » au-dessus d'un immeuble sur
l'annexe 2072 ou sur son dossier comptable fige ses factures et les attestations de ses prêts pour l'année ; les autres
immeubles restent modifiables. La date de transmission reste sur la déclaration de la SCI."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import AttestationPret, Bien, DeclarationFaite, PieceComptable, Pret, Proprietaire, SaisieBloquee


class SaisieBloqueeParBienTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.sci = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        commun = {"adresse": "1 rue", "code_postal": "69000", "ville": "Lyon", "comptabilite": "foncier", "regime_fiscal": "sci_ir"}
        self.a = Bien.objects.create(proprietaire=self.sci, nom="Saint Pierre", **commun)
        self.b = Bien.objects.create(proprietaire=self.sci, nom="Pollionnay", **commun)
        self.fa = PieceComptable.objects.create(bien=self.a, date=date(2025, 3, 1), libelle="Fuite", montant=Decimal("120"), ligne="9")
        self.fb = PieceComptable.objects.create(bien=self.b, date=date(2025, 3, 1), libelle="Peinture", montant=Decimal("80"), ligne="9")
        self.pret = Pret.objects.create(proprietaire=self.sci, bien=self.a, banque="B", montant=Decimal("100000"),
                                        taux=Decimal("2"), duree_mois=120, date_premiere_echeance=date(2024, 1, 1))
        self.annexe = f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025"

    def bloquer(self, bien, action="bloquer", retour=None):
        return self.client.post("/comptabilite/bloquer-bien/", {"bien": bien.pk, "annee": "2025", "action": action,
                                                               "retour": retour or self.annexe})

    def test_bouton_sur_chaque_immeuble_de_la_2072(self):
        page = self.client.get(self.annexe).content.decode()
        self.assertEqual(page.count("🔓 Bloquer la saisie"), 2)
        self.assertNotIn("🔓 Bloquer la saisie", self.client.get(self.annexe + "&format=pdf").content.decode(errors="ignore"))

    def test_bloquer_un_bien_fige_seulement_lui(self):
        r = self.bloquer(self.a)
        self.assertRedirects(r, self.annexe, fetch_redirect_response=False)
        page = self.client.get(self.annexe).content.decode()
        self.assertIn("🔒 Saisie bloquée le", page)
        self.assertEqual(page.count("🔓 Bloquer la saisie"), 1)
        self.fa.montant = Decimal("999")
        with self.assertRaisesMessage(ValidationError, "Saisie 2025 de Saint Pierre bloquée"):
            self.fa.full_clean()
        with self.assertRaises(ValidationError):  # pas de nouvelle facture 2025 sur ce bien
            PieceComptable(bien=self.a, date=date(2025, 6, 1), libelle="X", montant=Decimal("5"), ligne="9").full_clean()
        with self.assertRaises(ValidationError):
            AttestationPret(pret=self.pret, annee=2025, interets=Decimal("1500")).full_clean()
        self.fb.montant = Decimal("90")
        self.fb.full_clean()  # l'autre immeuble reste modifiable
        PieceComptable(bien=self.a, date=date(2026, 1, 5), libelle="Y", montant=Decimal("5"), ligne="9").full_clean()  # autre année
        fiche = self.client.get(f"/admin/patrimoine/piececomptable/{self.fa.pk}/change/").content.decode()
        self.assertIn("Saisie 2025 de Saint Pierre bloquée", fiche)
        self.assertIn("débloquez la saisie de ce bien", fiche)
        self.assertFalse(DeclarationFaite.objects.exists())  # aucune date de transmission

    def test_debloquer_rouvre_et_garde_l_historique(self):
        self.bloquer(self.a)
        self.bloquer(self.a)  # deuxième clic : pas de doublon
        self.assertEqual(SaisieBloquee.objects.filter(active=True).count(), 1)
        self.bloquer(self.a, "debloquer")
        s = SaisieBloquee.objects.get()
        self.assertFalse(s.active)
        self.assertIsNotNone(s.debloquee_le)
        self.fa.montant = Decimal("999")
        self.fa.full_clean()

    def test_dossier_comptable_et_carte_declarations(self):
        dossier = f"/comptabilite/dossier/2072-{self.sci.pk}/?annee=2025"
        self.assertContains(self.client.get(dossier + f"&bien={self.a.pk}"), "🔓 Bloquer la saisie")
        r = self.bloquer(self.a, retour=dossier + f"&bien={self.a.pk}")
        self.assertRedirects(r, dossier + f"&bien={self.a.pk}", fetch_redirect_response=False)
        self.assertContains(self.client.get(dossier + f"&bien={self.a.pk}"), "🔒 Saisie bloquée le")
        page = self.client.get(dossier).content.decode()
        self.assertIn("🔒 Saint Pierre", page)
        self.assertIn("1 bien sur 2 bloqué", page)
        self.assertContains(self.client.get("/comptabilite/declarations/?annee=2025"), "🔒 Saisie bloquée : Saint Pierre (1 sur 2)")

    def test_declaration_de_la_sci_reste_le_verrou_final(self):
        self.bloquer(self.a)
        self.client.post("/comptabilite/declarations/", {"action": "cocher", "annee": "2025", "formulaire": "2072",
                                                        "proprietaire": self.sci.pk, "date": "2026-05-15"})
        self.fb.montant = Decimal("90")
        with self.assertRaisesMessage(ValidationError, "déclarée le 15/05/2026"):
            self.fb.full_clean()
        self.fa.montant = Decimal("90")
        with self.assertRaisesMessage(ValidationError, "déclarée le 15/05/2026"):  # la déclaration l'emporte
            self.fa.full_clean()

    def test_retour_externe_refuse(self):
        r = self.bloquer(self.a, retour="https://exemple.com/")
        self.assertRedirects(r, self.annexe, fetch_redirect_response=False)
