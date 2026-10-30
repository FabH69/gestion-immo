from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Bail, Bien, Echeance, Locataire, Lot, Paiement, Proprietaire


class EcheanceTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt 1")
        self.bail = Bail.objects.create(
            lot=self.lot, type=Bail.Type.NU, date_debut=date(2026, 9, 15), loyer_hc=Decimal("600"), charges=Decimal("50"), jour_echeance=5
        )
        self.bail.locataires.add(Locataire.objects.create(nom="Martin"))

    def test_generation_idempotente(self):
        self.assertEqual(Echeance.generer_pour_mois(date(2026, 10, 1)), 1)
        self.assertEqual(Echeance.generer_pour_mois(date(2026, 10, 1)), 0)
        e = Echeance.objects.get()
        self.assertEqual(e.total, Decimal("650"))
        self.assertEqual(e.date_exigibilite, date(2026, 10, 5))

    def test_pas_d_echeance_avant_debut_ni_apres_fin(self):
        self.assertEqual(Echeance.generer_pour_mois(date(2026, 8, 1)), 0)
        self.bail.date_fin = date(2026, 10, 31)
        self.bail.save()
        self.assertEqual(Echeance.generer_pour_mois(date(2026, 11, 1)), 0)

    def test_statut_suit_les_paiements(self):
        Echeance.generer_pour_mois(date(2026, 10, 1))
        e = Echeance.objects.get()
        p = Paiement.objects.create(echeance=e, date=date(2026, 10, 5), montant=Decimal("300"))
        e.refresh_from_db()
        self.assertEqual(e.statut, Echeance.Statut.PARTIEL)
        Paiement.objects.create(echeance=e, date=date(2026, 10, 10), montant=Decimal("350"))
        e.refresh_from_db()
        self.assertEqual(e.statut, Echeance.Statut.PAYE)
        p.delete()
        e.refresh_from_db()
        self.assertEqual(e.statut, Echeance.Statut.PARTIEL)

    def test_pdf_quittance(self):
        Echeance.generer_pour_mois(date(2026, 10, 1))
        e = Echeance.objects.get()
        Paiement.objects.create(echeance=e, date=date(2026, 10, 5), montant=e.total)
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get(f"/echeances/{e.pk}/document.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn("quittance-2026-10", r["Content-Disposition"])
