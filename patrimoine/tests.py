from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

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


class DepotGarantieTests(TestCase):
    def test_solde_et_recu(self):
        from .models import MouvementDepot

        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="Apt", meuble=True)
        bail = Bail.objects.create(lot=lot, type=Bail.Type.MEUBLE, date_debut=date(2026, 1, 1), loyer_hc=Decimal("500"), depot_garantie=Decimal("1000"))
        bail.locataires.add(Locataire.objects.create(nom="Martin"))
        encaissement = MouvementDepot.objects.create(bail=bail, date=date(2026, 1, 1), montant=Decimal("1000"))
        MouvementDepot.objects.create(bail=bail, type="retenue", date=date(2027, 1, 20), montant=Decimal("150"))
        MouvementDepot.objects.create(bail=bail, type="restitution", date=date(2027, 1, 20), montant=Decimal("850"))
        self.assertEqual(bail.depot_detenu, Decimal("0"))

        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.assertEqual(self.client.get(f"/depots/{encaissement.pk}/recu.pdf").status_code, 200)
        self.assertEqual(self.client.get(f"/baux/{bail.pk}/inventaire-entree.pdf").status_code, 200)
        r = self.client.get("/admin/patrimoine/mouvementdepot/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_detenu"], Decimal("0"))


class PretTests(TestCase):
    def setUp(self):
        from .models import Pret

        self.sci = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        self.pret = Pret.objects.create(
            proprietaire=self.sci, banque="Banque", montant=Decimal("200000"), taux=Decimal("3"),
            duree_mois=240, date_premiere_echeance=date(2020, 1, 5), assurance_mensuelle=Decimal("30"),
        )

    def test_echeancier_amortissable(self):
        self.assertEqual(self.pret.mensualite_hors_assurance, Decimal("1109.20"))
        lignes = self.pret.echeancier()
        self.assertEqual(len(lignes), 240)
        self.assertEqual(lignes[0]["interets"], Decimal("500.00"))
        self.assertEqual(lignes[-1]["restant"], Decimal("0"))
        self.assertEqual(sum(l["capital"] for l in lignes), Decimal("200000"))
        self.assertEqual(self.pret.date_derniere_echeance, date(2039, 12, 5))

    def test_capital_restant_du_a_une_date(self):
        self.assertEqual(self.pret.capital_restant_du(date(2020, 1, 4)), Decimal("200000"))
        self.assertEqual(self.pret.capital_restant_du(date(2020, 1, 5)), Decimal("200000") - Decimal("609.20"))
        self.assertEqual(self.pret.capital_restant_du(date(2045, 1, 1)), Decimal("0"))

    def test_in_fine(self):
        from .models import Pret

        p = Pret.objects.create(proprietaire=self.sci, banque="B", type="in_fine", montant=Decimal("100000"), taux=Decimal("2.4"), duree_mois=120, date_premiere_echeance=date(2025, 1, 1))
        self.assertEqual(p.mensualite_hors_assurance, Decimal("200.00"))
        self.assertEqual(p.capital_restant_du(date(2034, 11, 30)), Decimal("100000"))
        self.assertEqual(p.capital_restant_du(date(2034, 12, 1)), Decimal("0"))

    def test_etat_et_exports(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get("/etats/endettement/", {"date": "2020-01-31"})
        self.assertEqual(r.context["total"]["restant"], Decimal("199390.80"))
        self.assertIn("199390,80", self.client.get("/etats/endettement/", {"date": "2020-01-31", "format": "csv"}).content.decode("utf-8-sig"))
        self.assertEqual(self.client.get("/etats/endettement/", {"format": "pdf"})["Content-Type"], "application/pdf")
        self.assertEqual(self.client.get(f"/prets/{self.pret.pk}/amortissement.pdf").status_code, 200)


class RappelsEtRevisionTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt", chaudiere_gaz=True, date_entretien_chaudiere=date(2025, 11, 20))
        self.bail = Bail.objects.create(
            lot=self.lot, type=Bail.Type.NU, date_debut=date(2024, 11, 15), loyer_hc=Decimal("600"), charges=Decimal("40"),
            irl_trimestre="T2", irl_valeur=Decimal("143.58"), date_attestation_assurance=date(2025, 11, 10),
        )
        self.bail.locataires.add(Locataire.objects.create(nom="Martin", email="martin@example.com"))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def test_prochaine_revision(self):
        self.assertEqual(self.bail.prochaine_revision(date(2025, 10, 1)), date(2025, 11, 15))
        self.bail.date_derniere_revision = date(2025, 11, 15)
        self.assertEqual(self.bail.prochaine_revision(date(2026, 10, 1)), date(2026, 11, 15))
        # Révisions jamais faites : celle de moins d'un an reste due, les plus anciennes sont perdues.
        self.bail.date_derniere_revision = None
        self.assertEqual(self.bail.prochaine_revision(date(2026, 12, 1)), date(2026, 11, 15))
        self.assertEqual(self.bail.prochaine_revision(date(2027, 12, 1)), date(2027, 11, 15))

    def test_a_faire_un_mois_avant(self):
        from .models import Rappel
        from .rappels import a_faire

        self.bail.date_derniere_revision = date(2025, 11, 15)
        self.bail.save()
        Rappel.objects.create(titre="AG", date=date(2026, 12, 1))
        titres = [i["titre"] for i in a_faire(date(2026, 10, 22))]
        self.assertIn("Réviser le loyer (IRL)", titres)
        self.assertIn("Demander l'attestation d'assurance habitation", titres)
        self.assertIn("Demander l'attestation d'entretien de la chaudière", titres)
        self.assertNotIn("AG", titres)  # au-delà de 30 jours
        self.assertEqual(a_faire(date(2026, 9, 1)), [])

    def test_bouton_fait(self):
        from .models import Rappel

        r = Rappel.objects.create(titre="Taxe foncière", date=date(2026, 10, 15), annuel=True)
        self.client.post(f"/rappels/rappel/{r.pk}/fait/")
        r.refresh_from_db()
        self.assertEqual(r.date, date(2027, 10, 15))
        self.assertFalse(r.fait)
        self.client.post(f"/rappels/assurance/{self.bail.pk}/fait/")
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.date_attestation_assurance, timezone.localdate())
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_revision_calcul_courrier_et_application(self):
        from .models import RevisionLoyer

        r = self.client.post(f"/baux/{self.bail.pk}/revision/", {"ancien_irl": "143,58", "nouvel_irl": "146,12", "trimestre": "T2 2026", "date_effet": "2026-11-15"})
        revision = RevisionLoyer.objects.get()
        self.assertEqual(revision.nouveau_loyer, Decimal("610.61"))  # 600 × 146,12 / 143,58
        page = self.client.get(r["Location"])
        self.assertIn("mailto:martin@example.com", page.content.decode())
        self.assertEqual(self.client.get(f"/revisions/{revision.pk}/lettre.pdf")["Content-Type"], "application/pdf")
        self.client.post(f"/baux/{self.bail.pk}/revision/", {"action": "appliquer", "revision": revision.pk})
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.loyer_hc, Decimal("610.61"))
        self.assertEqual(self.bail.irl_valeur, Decimal("146.12"))
        self.assertEqual(self.bail.prochaine_revision(date(2027, 1, 1)), date(2027, 11, 15))
