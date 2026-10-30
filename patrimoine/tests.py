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


class EtatLoyersTests(TestCase):
    def setUp(self):
        self.sci = Proprietaire.objects.create(nom="SCI A", type=Proprietaire.Type.SCI_IR)
        autre = Proprietaire.objects.create(nom="Nom propre", type=Proprietaire.Type.NOM_PROPRE)
        for proprio, loyer in ((self.sci, "600"), (autre, "400")):
            bien = Bien.objects.create(proprietaire=proprio, nom=f"Bien {proprio.nom}", adresse="1 rue", code_postal="69000", ville="Lyon")
            lot = Lot.objects.create(bien=bien, designation="Apt")
            bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=Decimal(loyer), charges=Decimal("50"))
        for mois in (11, 12):
            Echeance.generer_pour_mois(date(2025, mois, 1))
        for e in Echeance.objects.all():
            # Le loyer de décembre est encaissé en janvier : il compte pour l'année suivante.
            jour = date(2025, 11, 3) if e.periode.month == 11 else date(2026, 1, 4)
            Paiement.objects.create(echeance=e, date=jour, montant=e.total)
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def test_totaux_par_date_d_encaissement(self):
        from .etats import loyers_encaisses

        etat = loyers_encaisses(2025)
        self.assertEqual(etat["total"]["total"], Decimal("1100"))
        self.assertEqual(etat["total"]["mois"][10], Decimal("1100"))
        self.assertEqual(etat["total"]["loyers_hc"], Decimal("1000"))
        self.assertEqual(etat["total"]["charges"], Decimal("100"))
        self.assertEqual(loyers_encaisses(2026)["total"]["mois"][0], Decimal("1100"))

    def test_filtre_proprietaire_et_exports(self):
        r = self.client.get("/etats/loyers-encaisses/", {"annee": 2025, "proprietaire": self.sci.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total"]["total"], Decimal("650"))
        r = self.client.get("/etats/loyers-encaisses/", {"annee": 2025, "format": "csv"})
        contenu = r.content.decode("utf-8-sig")
        self.assertIn("TOTAL;;;0,00", contenu)
        self.assertIn("1100,00", contenu)
        r = self.client.get("/etats/loyers-encaisses/", {"annee": 2025, "format": "pdf"})
        self.assertEqual(r["Content-Type"], "application/pdf")


class ChangementLocataireTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt")
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2023, 1, 1), loyer_hc=Decimal("600"))

    def test_creation_genere_la_check_list_datee(self):
        from .checklists import MODELE_CHANGEMENT_LOCATAIRE
        from .models import ChangementLocataire

        c = ChangementLocataire.objects.create(lot=self.lot, date_conge=date(2026, 9, 20), date_arrivee=date(2027, 1, 1))
        self.assertEqual(c.bail_sortant, self.bail)
        self.assertEqual(c.date_depart, date(2026, 12, 20))  # préavis de 3 mois en location nue
        self.assertEqual(c.taches.count(), len(MODELE_CHANGEMENT_LOCATAIRE))
        edl = c.taches.get(libelle="Réaliser l'état des lieux de sortie")
        self.assertEqual(edl.echeance, date(2026, 12, 20))
        depot = c.taches.get(libelle="Restituer le dépôt de garantie")
        self.assertEqual(depot.echeance, date(2027, 1, 19))
        self.assertEqual(c.taches.first().phase, "depart")

    def test_report_du_depart_decale_les_taches_non_faites(self):
        from .models import ChangementLocataire

        c = ChangementLocataire.objects.create(lot=self.lot, date_depart=date(2026, 12, 20))
        faite = c.taches.get(libelle="Réaliser l'état des lieux de sortie")
        faite.faite = True
        faite.save()
        c.date_depart = date(2027, 1, 10)
        c.save()
        faite.refresh_from_db()
        self.assertEqual(faite.echeance, date(2026, 12, 20))
        self.assertIsNotNone(faite.date_faite)
        cles = c.taches.get(libelle="Récupérer toutes les clés et badges")
        self.assertEqual(cles.echeance, date(2027, 1, 10))
        self.assertIsNone(c.taches.get(libelle="Remettre les clés").echeance)  # pas de date d'arrivée

    def test_preavis_meuble_un_mois(self):
        from .models import ChangementLocataire

        self.bail.type = Bail.Type.MEUBLE
        self.bail.save()
        c = ChangementLocataire.objects.create(lot=self.lot, date_conge=date(2026, 1, 31))
        self.assertEqual(c.date_depart, date(2026, 2, 28))

    def test_date_ajustee_a_la_main_conservee(self):
        from .models import ChangementLocataire

        c = ChangementLocataire.objects.create(lot=self.lot, date_depart=date(2026, 12, 20))
        t = c.taches.get(libelle="Récupérer toutes les clés et badges")
        t.echeance = date(2026, 12, 22)
        t.save()
        c.notes = "Locataire joignable le soir"
        c.save()
        t.refresh_from_db()
        self.assertEqual(t.echeance, date(2026, 12, 22))


class DocumentsTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR, representant="M. X, gérant")
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt", nb_pieces=3, meuble=True)
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.MEUBLE, date_debut=date(2026, 9, 1), loyer_hc=Decimal("600"))
        self.bail.locataires.add(Locataire.objects.create(nom="Martin"))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def test_pieces_par_defaut(self):
        self.assertEqual(self.lot.liste_pieces(), ["Entrée", "Séjour", "Cuisine", "Chambre 1", "Chambre 2", "Salle de bains", "WC"])
        self.lot.pieces = "Pièce de vie\n\nMezzanine"
        self.assertEqual(self.lot.liste_pieces(), ["Pièce de vie", "Mezzanine"])

    def test_pdfs(self):
        from .models import EtatDesLieux

        self.assertEqual(self.bail.duree_mois, 12)
        for url in (f"/baux/{self.bail.pk}/bail.pdf", f"/baux/{self.bail.pk}/etat-des-lieux-entree.pdf"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(r["Content-Type"], "application/pdf")
        edl = EtatDesLieux.objects.create(bail=self.bail, type="sortie", date=date(2027, 8, 31), compteur_electricite="12345")
        self.assertEqual(self.client.get(f"/etats-des-lieux/{edl.pk}/document.pdf").status_code, 200)
