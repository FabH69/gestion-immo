from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .models import Annonce, Bail, Bien, Echeance, Locataire, Lot, MouvementDepot, Paiement, Proprietaire, Rappel


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

        r = Rappel.objects.create(titre="Taxe foncière", date=date(2026, 10, 15), periodicite=Rappel.Periodicite.ANNUEL)
        self.client.post(f"/rappels/rappel/{r.pk}/fait/")
        r.refresh_from_db()
        self.assertEqual(r.date, date(2027, 10, 15))
        self.assertFalse(r.fait)
        self.client.post(f"/rappels/assurance/{self.bail.pk}/fait/")
        self.bail.refresh_from_db()
        self.assertEqual(self.bail.date_attestation_assurance, timezone.localdate())
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_pret_recale_sur_capital_constate(self):
        from .models import Pret

        proprio = Proprietaire.objects.create(nom="SCI R", type=Proprietaire.Type.SCI_IR)
        pret = Pret.objects.create(proprietaire=proprio, banque="CM", montant=Decimal("70400"), taux=Decimal("1.80"), duree_mois=180,
                                   date_premiere_echeance=date(2016, 7, 5))
        calcule = pret.capital_restant_du(date(2026, 11, 7))
        fin = pret.date_derniere_echeance
        pret.capital_constate, pret.capital_constate_le = Decimal("24378.87"), date(2026, 11, 7)
        pret.save()
        self.assertNotEqual(calcule, Decimal("24378.87"))
        self.assertEqual(pret.capital_restant_du(date(2026, 11, 7)), Decimal("24378.87"))
        self.assertEqual(pret.capital_restant_du(date(2026, 10, 7)), pret.capital_restant_du(date(2026, 10, 7)))
        lignes = pret.echeancier()
        self.assertEqual(lignes[-1]["restant"], 0)
        self.assertGreater(pret.date_derniere_echeance, fin)  # capital plus élevé : même mensualité, prêt allongé
        self.assertEqual(lignes[-2]["mensualite"], pret.mensualite_hors_assurance)

    def test_endettement_residence_principale_a_part(self):
        from .etats import endettement
        from .models import Pret

        proprio = Proprietaire.objects.create(nom="M. et Mme", type=Proprietaire.Type.NOM_PROPRE)
        rp = Bien.objects.create(proprietaire=proprio, nom="Résidence principale", adresse="89 av", code_postal="69290", ville="Grézieu",
                                 residence_principale=True)
        loc = Bien.objects.create(proprietaire=proprio, nom="Pollionnay", adresse="1 rue", code_postal="69290", ville="Pollionnay")
        for bien, montant in ((rp, 82000), (loc, 70000)):
            Pret.objects.create(proprietaire=proprio, bien=bien, banque="CM", montant=Decimal(montant), taux=Decimal("2"), duree_mois=120,
                                date_premiere_echeance=date(2020, 1, 5))
        bloc = next(b for b in endettement(date(2026, 11, 7))["blocs"] if b["proprietaire"] == proprio)
        self.assertEqual([g["libelle"] for g in bloc["groupes"]], ["Locatif", "Résidence principale"])
        self.assertEqual(bloc["groupes"][1]["montant"], Decimal(82000))
        page = self.client.get("/etats/endettement/?date=2026-11-07")
        self.assertContains(page, "Sous-total résidence principale")

    def test_import_factures_en_masse_puis_codification(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from .annexe2072 import colonne
        from .models import PieceComptable

        sci = Proprietaire.objects.create(nom="SCI X", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=sci, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            self.assertEqual(self.client.get("/admin/patrimoine/piececomptable/import/").status_code, 200)
            r = self.client.post("/admin/patrimoine/piececomptable/import/", {"bien": bien.pk, "fichiers": [
                SimpleUploadedFile("facture_plombier.pdf", b"%PDF-1"), SimpleUploadedFile("taxe.pdf", b"%PDF-2"),
                SimpleUploadedFile("virus.exe", b"x")]})
            self.assertEqual(r.status_code, 302, r.context["form"].errors if r.context and "form" in r.context else r.content[:300])
            self.assertEqual(r["Location"], "/admin/patrimoine/piececomptable/?a_codifier=oui")
            self.assertEqual(PieceComptable.objects.count(), 2)
            self.assertTrue(all(p.a_codifier for p in PieceComptable.objects.all()))
            colonne(bien, 2026)  # les pièces à codifier sont ignorées sans erreur
            liste = self.client.get("/admin/patrimoine/piececomptable/?a_codifier=oui")
            self.assertContains(liste, 'name="form-0-montant"')
            pieces = list(liste.context["cl"].result_list)
            donnees = {"form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "2", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000", "_save": "Enregistrer"}
            for n, p in enumerate(pieces):
                donnees.update({f"form-{n}-id": p.pk, f"form-{n}-bien": bien.pk, f"form-{n}-date": "2026-03-10", f"form-{n}-date_paiement": "",
                                f"form-{n}-fournisseur": "Plombier", f"form-{n}-libelle": p.libelle, f"form-{n}-montant": "150",
                                f"form-{n}-ligne": "9" if n == 0 else ""})
            r = self.client.post("/admin/patrimoine/piececomptable/?a_codifier=oui", donnees)
            self.assertEqual(r.status_code, 302)
            self.assertEqual(PieceComptable.objects.filter(ligne="9", montant=150).count(), 1)
            self.assertEqual(sum(1 for p in PieceComptable.objects.all() if p.a_codifier), 1)
            self.assertContains(self.client.get("/admin/patrimoine/piececomptable/"), "À codifier (1)")

    def test_lecture_automatique_facture_pdf(self):
        import io
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from weasyprint import HTML

        from .lecture_factures import analyser
        from .models import PieceComptable

        r = analyser("Dupont Plomberie SARL\nFACTURE N° 41\nDate de facture : 14/03/2026\nRemplacement chauffe-eau\nTotal HT 450,00 €\nTotal TTC 495,00 €")
        self.assertEqual((r["date"], r["montant"], r["fournisseur"], r["ligne"]), (date(2026, 3, 14), Decimal("495.00"), "Dupont Plomberie SARL", "9"))
        r = analyser("DIRECTION GÉNÉRALE DES FINANCES PUBLIQUES\nAVIS D'IMPÔT 2026 TAXE FONCIÈRE\nDate : 31/08/2026\nMontant à payer : 1 234,00 €")
        self.assertEqual((r["montant"], r["fournisseur"], r["ligne"]), (Decimal("1234.00"), "Impôts (DGFiP)", "12"))
        self.assertEqual(analyser(""), {})

        sci = Proprietaire.objects.create(nom="SCI L", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=sci, nom="Imm", adresse="1 rue", code_postal="69000", ville="Lyon")
        pdf = io.BytesIO()
        HTML(string="<p>Dupont Plomberie SARL</p><p>Date de facture : 14/03/2026</p><p>Réparation fuite</p><p>Total TTC : 495,00 €</p>").write_pdf(pdf)
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            self.client.post("/admin/patrimoine/piececomptable/import/", {"bien": bien.pk, "fichiers": [SimpleUploadedFile("f.pdf", pdf.getvalue())]})
            piece = PieceComptable.objects.get()
            self.assertEqual((piece.montant, piece.ligne, piece.date), (Decimal("495.00"), "9", date(2026, 3, 14)))
            self.assertTrue(piece.preremplie and piece.a_codifier)  # à vérifier avant de compter dans l'annexe
            self.client.post("/admin/patrimoine/piececomptable/", {"action": "valider_preremplies", "_selected_action": [piece.pk]})
            piece.refresh_from_db()
            self.assertFalse(piece.a_codifier)
            # même PDF importé une seconde fois : refusé comme doublon
            r = self.client.post("/admin/patrimoine/piececomptable/import/", {"bien": bien.pk, "fichiers": [SimpleUploadedFile("copie.pdf", pdf.getvalue())]}, follow=True)
            self.assertEqual(PieceComptable.objects.count(), 1)
            self.assertContains(r, "déjà présente")

    def test_etat_encaisse_montant_cliquable(self):
        from .models import Echeance, Paiement

        e = Echeance.objects.create(bail=self.bail, periode=date(2026, 2, 1), loyer_hc=600, charges=50, date_exigibilite=date(2026, 2, 5))
        Paiement.objects.create(echeance=e, date=date(2026, 2, 6), montant=650)
        page = self.client.get("/etats/loyers-encaisses/?annee=2026")
        self.assertContains(page, f"lot={self.bail.lot_id}&amp;annee=2026&amp;mois=2")
        r = self.client.get(f"/etats/loyers-encaisses/detail/?lot={self.bail.lot_id}&annee=2026&mois=2")
        self.assertEqual(r["Location"], f"/admin/patrimoine/echeance/{e.pk}/change/")
        e2 = Echeance.objects.create(bail=self.bail, periode=date(2026, 3, 1), loyer_hc=600, charges=50, date_exigibilite=date(2026, 3, 5))
        Paiement.objects.create(echeance=e2, date=date(2026, 2, 27), montant=650)
        r = self.client.get(f"/etats/loyers-encaisses/detail/?lot={self.bail.lot_id}&annee=2026&mois=2")
        liste = self.client.get(r["Location"])
        self.assertEqual(liste.status_code, 200)
        self.assertEqual(liste.context["cl"].result_count, 2)

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


class CandidatureEtGarantTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt 2", surface=40, nb_pieces=2)

    def test_formulaire_public_puis_creation_du_locataire(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from .models import Candidature

        c = Candidature.objects.create(lot=self.lot, type_garantie="garant")  # type de caution choisi avant l'envoi
        url = f"/candidature/{c.jeton}/"
        page = self.client.get(url)  # sans connexion
        self.assertContains(page, "Second garant")
        self.assertContains(page, 'name="adresse_code_postal"')
        base = {"prenom": "Léa", "nom": "Durand", "email": "lea@example.com", "telephone": "0600000000", "adresse_rue": "3 rue Neuve",
                "adresse_code_postal": "69003", "adresse_ville": "Lyon", "situation": "cdi", "revenus_mensuels": "2400",
                "consentement": "on", "garants-TOTAL_FORMS": "2", "garants-INITIAL_FORMS": "0", "garants-MIN_NUM_FORMS": "0",
                "garants-MAX_NUM_FORMS": "2"}
        r = self.client.post(url, base)
        self.assertContains(r, "Ce champ est obligatoire")  # le premier garant est demandé
        garant = {"garants-0-prenom": "Paul", "garants-0-nom": "Durand", "garants-0-date_naissance": "1965-04-02",
                  "garants-0-lieu_naissance": "Lyon", "garants-0-adresse_rue": "5 rue Haute", "garants-0-code_postal": "69005",
                  "garants-0-ville": "Lyon", "garants-0-lien": "père", "garants-0-piece_identite_numero": "X12345",
                  "garants-0-piece_identite_fichier": SimpleUploadedFile("cni.pdf", b"%PDF-1.4 test", content_type="application/pdf")}
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            r = self.client.post(url, {**base, "type_garantie": "visale", **garant})
            self.assertContains(r, "Merci Léa")
            c.refresh_from_db()
            self.assertEqual((c.statut, c.type_garantie), ("recue", "garant"))  # le choix du propriétaire ne change pas
            self.assertEqual(c.adresse_complete, "3 rue Neuve\n69003 Lyon")
            self.assertEqual(c.taux_effort(Decimal("800")), 33)
            locataire = c.creer_locataire()
            self.assertEqual(locataire.adresse_actuelle, "3 rue Neuve\n69003 Lyon")
            g = locataire.garants.get()
            self.assertEqual((g.prenom, g.nom, g.lien, g.piece_identite_numero), ("Paul", "Durand", "père", "X12345"))
            self.assertEqual(g.adresse, "5 rue Haute\n69005 Lyon")
            fichier = g.piece_identite_fichier.url
            self.assertTrue(fichier.startswith("/prive/pieces-identite/"))
            self.assertEqual(self.client.get(fichier).status_code, 302)  # pas connecté : vers la connexion
            self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
            self.assertEqual(b"".join(self.client.get(fichier).streaming_content), b"%PDF-1.4 test")
            self.assertEqual(self.client.get("/prive/../config/settings.py").status_code, 404)
            self.client.logout()
        self.assertContains(self.client.get(url), "clôturé")
        self.assertEqual(self.client.get("/candidature/inconnu/").status_code, 404)

    def test_acte_de_cautionnement(self):
        from .models import Garant

        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2026, 9, 1), loyer_hc=Decimal("700"), charges=Decimal("50"))
        locataire = Locataire.objects.create(nom="Durand")
        bail.locataires.add(locataire)
        g = Garant.objects.create(locataire=locataire, bail=bail, nom="Durand", prenom="Paul", duree_mois=36)
        self.assertEqual(g.montant_garanti(), Decimal("27000"))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.assertEqual(self.client.get(f"/garants/{g.pk}/acte-de-cautionnement.pdf")["Content-Type"], "application/pdf")
        self.assertEqual(self.client.get(f"/baux/{bail.pk}/bail.pdf").status_code, 200)


class AnnonceTests(TestCase):
    def setUp(self):
        import tempfile

        self.media = self.settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.media.enable()
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Résidence", adresse="8 av. Gare", code_postal="35000", ville="Rennes")
        self.lot = Lot.objects.create(bien=bien, designation="Studio 4", type=Lot.Type.STUDIO, surface=Decimal("24"), nb_pieces=1, meuble=True,
                                      classe_dpe="D", classe_ges="B", energie_min=480, energie_max=690, energie_annee=2023)

    def tearDown(self):
        self.media.disable()

    def test_texte_genere_avec_mentions_obligatoires(self):
        from .models import Annonce

        a = Annonce.objects.create(lot=self.lot, loyer_cc=Decimal("560"), charges=Decimal("50"), depot_garantie=Decimal("1020"),
                                   points_forts="Proche gare\nCalme")
        self.assertEqual(a.titre, "Studio 1 pièce meublé 24 m² – Rennes")
        for attendu in ("560 € par mois charges comprises", "dont 50 € de charges", "Dépôt de garantie : 1020 €", "Location meublée",
                        "surface habitable 24 m²", "Classe énergie : D", "Classe climat : B", "entre 480 € et 690 € par an",
                        "1er janvier 2023", "- Proche gare"):
            self.assertIn(attendu, a.texte)
        self.assertEqual(a.mentions_manquantes(), [])
        a.texte = "Texte retouché"
        a.save()
        self.assertEqual(Annonce.objects.get().texte, "Texte retouché")

    def test_photos_zip(self):
        import io as _io
        import zipfile as _zip

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from .models import PhotoLot

        for i, legende in enumerate(["Séjour", "Cuisine"]):
            tampon = _io.BytesIO()
            Image.new("RGB", (40, 30), "white").save(tampon, "JPEG")
            PhotoLot.objects.create(lot=self.lot, ordre=i, legende=legende, image=SimpleUploadedFile(f"p{i}.jpg", tampon.getvalue()))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get(f"/lots/{self.lot.pk}/photos.zip")
        self.assertEqual(_zip.ZipFile(_io.BytesIO(r.content)).namelist(), ["01-sejour.jpg", "02-cuisine.jpg"])


class ImportAirtableTests(TestCase):
    def test_import_lots(self):
        import os
        import tempfile

        from django.core.management import call_command

        contenu = (
            "\ufeffAppartement,Adresse_Appart,CP_Appart,Ville_Appart,Etage,Tags,Superficie,Copro,Nb_Pièce,Propriétaire,PDL,Record ID,Statut,Type_Chauffage,Type_eau,Location 4\n"
            "Immeuble A,\"1 rue Test\n\",69000,Lyon,1er étage,Appartement,40,Syndic X,2,SCI TEST,123,rec1,,Individuel,,A1\n"
            "Immeuble A,1 rue Test,69000,Lyon,Rez de chaussée,Garage,,Syndic X,1,SCI TEST,,rec2,,,,G1\n"
            "Vieux,2 rue Test,69000,Lyon,,Appartement,30,,1,SCI TEST,,rec3,Vendu,,,\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(contenu)
        try:
            call_command("importer_airtable_lots", f.name, stdout=open(os.devnull, "w"))
            self.assertEqual(Lot.objects.count(), 0)  # simulation par défaut
            call_command("importer_airtable_lots", f.name, "--appliquer", stdout=open(os.devnull, "w"))
            call_command("importer_airtable_lots", f.name, "--appliquer", stdout=open(os.devnull, "w"))  # idempotent
        finally:
            os.unlink(f.name)
        self.assertEqual(Lot.objects.count(), 2)
        self.assertEqual(Bien.objects.get().syndic.nom, "Syndic X")
        self.assertEqual(Proprietaire.objects.get().type, Proprietaire.Type.SCI_IR)
        garage = Lot.objects.get(ref_import="rec2")
        self.assertEqual((garage.type, garage.nb_pieces), (Lot.Type.PARKING, None))
        self.assertEqual(Lot.objects.get(ref_import="rec1").pdl, "123")


class ImportAirtableCompletTests(TestCase):
    def test_import_locataires_locations_garants(self):
        import os
        import tempfile
        from django.core.management import call_command

        tables = {
            "appartements": "Appartement,Adresse_Appart,CP_Appart,Ville_Appart,Tags,Propriétaire,Record ID,Statut,Location 4\n"
                            "Imm,1 rue Test,69000,Lyon,Appartement,SCI TEST,rec1,,A1\nImm,1 rue Test,69000,Lyon,Appartement,SCI TEST,rec2,,A2\n",
            "proprietaires": "Propriétaire,Adresse_Propr,CP_Prop,Ville_Prop,SIRET,Type Fiscalité\nSCI TEST,2 rue X,69001,Lyon,123 456 789 00012,IS\n",
            "locataires": "Nom_loc,Prénom_loc,Civilité_Loc,Tel_Loc,Date_naissance_loc,ID\n"
                          "DUPONT,Karine,Mme,06-01-02-03-04,9/10/1987,recA\nDUPONT ,Christelle,Mme,,,recB\nDUPONT copy,Karine,Mme,,,recC\n",
            "locations": "Appartement,Date_début_Bail,Date_fin_Bail,Locataire,Loyer,Charges,Durée_Bail,IRL_Ref,IRL_Ref_Période,type Location,Dépot Garantie,Statut Dépot Gar,Type Garantie\n"
                         "A1,1/3/2020,1/3/2023,\"DUPONT,DUPONT copy\",\"500,00\",\"10,00\",3,\"138,61\",1er trimestre 2023,Nue,\"500,00\",Encaissé,Visale\n"
                         "A2,1/3/2024,1/3/2025,DUPONT ,\"600,00\",\"0,00\",1,,,Meublée,,,\n",
            "garants": "Nom_Garant,Prénom_Garant,Nom_Garant2,Prénom_Garant2,CP_Garant,Ville_Garant,Locataire,Location\nMARTIN,Paul,MARTIN,Anne,38000,GRENOBLE,DUPONT ,A2\n",
        }
        chemins = {}
        for table, contenu in tables.items():
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
                f.write(contenu)
            chemins[table] = f.name
        options = [x for table, chemin in chemins.items() for x in (f"--{table}", chemin)]
        try:
            call_command("importer_airtable", *options, stdout=open(os.devnull, "w"))
            self.assertEqual(Bail.objects.count(), 0)  # simulation par défaut
            for _ in range(2):  # idempotent
                call_command("importer_airtable", *options, "--appliquer", stdout=open(os.devnull, "w"))
        finally:
            for chemin in chemins.values():
                os.unlink(chemin)

        self.assertEqual(Proprietaire.objects.get().type, Proprietaire.Type.SCI_IS)
        self.assertEqual(Locataire.objects.count(), 2)
        karine = Locataire.objects.get(prenom="Karine")
        self.assertEqual((karine.civilite, karine.telephone), ("Mme", "06 01 02 03 04"))
        a1 = Bail.objects.get(lot__designation="A1")
        self.assertEqual(list(a1.locataires.all()), [karine])
        self.assertIsNone(a1.date_fin)  # reconduit tacitement
        self.assertEqual((a1.irl_trimestre, a1.irl_valeur, a1.depot_detenu), ("T1", Decimal("138.61"), Decimal("500")))
        a2 = Bail.objects.get(lot__designation="A2")
        self.assertEqual((a2.type, a2.locataires.get().prenom, a2.lot.meuble), (Bail.Type.MEUBLE, "Christelle", True))
        self.assertEqual(sorted(a2.garants.values_list("prenom", flat=True)), ["Anne", "Paul"])
        self.assertEqual((a1.type_garantie, a2.type_garantie), ("visale", "garant"))


class TableauDeBordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "", "x")
        self.client.force_login(self.user)

    def test_filtre_a_faire_et_deja_fait(self):
        from .models import JournalFait, Rappel
        rappel = Rappel.objects.create(titre="AG copropriété", date=timezone.localdate())
        page = self.client.get("/admin/").content.decode()
        self.assertIn("AG copropriété", page)
        self.assertNotIn("Authentification", page)
        self.assertNotIn("Loyers encaissés par mois et par année</a></th>", page)
        self.client.post(f"/rappels/rappel/{rappel.pk}/fait/")
        self.assertNotIn("AG copropriété", self.client.get("/admin/").content.decode())
        fait = self.client.get("/admin/?vue=fait").content.decode()
        self.assertIn("AG copropriété", fait)
        self.assertEqual(JournalFait.objects.count(), 1)

    def test_cocher_revision(self):
        from datetime import timedelta
        proprio = Proprietaire.objects.create(nom="SCI X", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="B", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="A1")
        debut = timezone.localdate() - timedelta(days=360)
        bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=debut, loyer_hc=500)
        echeance = bail.prochaine_revision(timezone.localdate())
        self.assertIn("revision/%d/fait" % bail.pk, self.client.get("/admin/").content.decode().replace("rappels/", ""))
        self.client.post(f"/rappels/revision/{bail.pk}/fait/")
        bail.refresh_from_db()
        self.assertEqual(bail.date_derniere_revision, echeance)
        self.assertNotIn("Réviser le loyer", self.client.get("/admin/").content.decode())


class PretAssuranceIncluseTests(TestCase):
    def test_echeance_constante_assurance_sur_capital_restant(self):
        # Tableau Caisse d'Épargne : 92 233,74 € à 3,13 % + assurance 0,64 %, échéance constante de 547,80 €.
        from .models import Pret
        proprio = Proprietaire.objects.create(nom="X", type=Proprietaire.Type.NOM_PROPRE)
        pret = Pret(proprietaire=proprio, banque="CE", montant=Decimal("92233.74"), taux=Decimal("3.13"),
                    taux_assurance=Decimal("0.64"), duree_mois=240, date_premiere_echeance=date(2025, 10, 10))
        self.assertEqual(pret.mensualite_totale, Decimal("547.80"))
        premiere = pret.echeancier()[0]
        self.assertEqual((premiere["capital"], premiere["interets"], premiere["assurance"]), (Decimal("258.03"), Decimal("240.58"), Decimal("49.19")))
        self.assertEqual(pret.capital_restant_du(date(2026, 12, 10)), Decimal("88276.98"))


class ListesPresentVenduTests(TestCase):
    def test_colonnes_present_et_vendu(self):
        self.client.force_login(User.objects.create_superuser("admin", "", "x"))
        proprio = Proprietaire.objects.create(nom="SCI X", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="B", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="A1")
        parti, present = Locataire.objects.create(nom="PARTI"), Locataire.objects.create(nom="PRESENT")
        Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2020, 1, 1), date_fin=date(2023, 6, 30), loyer_hc=500, actif=False).locataires.add(parti)
        Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2023, 7, 1), loyer_hc=500).locataires.add(present)
        page = self.client.get("/admin/patrimoine/locataire/?present=non").content.decode()
        self.assertIn("PARTI", page)
        self.assertIn("Parti", page)
        self.assertNotIn("PRESENT", page)
        Lot.objects.create(bien=bien, designation="Vieux", vendu=True, date_vente=date(2024, 3, 1))
        page = self.client.get("/admin/patrimoine/lot/?vente=oui").content.decode()
        self.assertIn("Vieux", page)
        self.assertNotIn(">A1<", page)


class PlusValueTests(TestCase):
    def test_calcul_avec_forfaits(self):
        from .plus_value import Simulation
        r = Simulation(date_acquisition=date(2010, 1, 1), prix_acquisition=Decimal("200000"),
                       date_cession=date(2026, 6, 1), prix_cession=Decimal("300000")).calculer()
        # 200 000 + 7,5 % (15 000) + 15 % (30 000) = 245 000 ; plus-value 55 000 ; 16 ans de détention
        self.assertEqual((r["pv_brute"], r["annees"]), (Decimal("55000"), 16))
        self.assertEqual((r["abattement_ir"], r["pv_ir"], r["impot_ir"]), (Decimal("66"), Decimal("18700"), Decimal("3553")))
        self.assertEqual((r["abattement_ps"], r["pv_ps"], r["impot_ps"]), (Decimal("18.15"), Decimal("45018"), Decimal("7743")))
        self.assertEqual((r["surtaxe"], r["total"]), (Decimal("0"), Decimal("11296")))

    def test_abattements_et_surtaxe(self):
        from .plus_value import abattement_ir, abattement_ps, surtaxe
        self.assertEqual([abattement_ir(n) for n in (5, 6, 21, 22)], [0, 6, 96, 100])
        self.assertEqual([abattement_ps(n) for n in (5, 21, 22, 23, 30)], [0, Decimal("26.40"), 28, 37, 100])
        self.assertEqual(surtaxe(50000), 0)
        self.assertEqual(surtaxe(55000), Decimal("850"))  # 2 % × 55 000 − (60 000 − 55 000) / 20
        self.assertEqual(surtaxe(80000), Decimal("1600"))
        self.assertEqual(surtaxe(300000), Decimal("18000"))

    def test_lmnp_moins_de_5_ans_et_page(self):
        from .plus_value import Simulation
        r = Simulation(date_acquisition=date(2023, 1, 1), prix_acquisition=Decimal("100000"), date_cession=date(2026, 6, 1),
                       prix_cession=Decimal("110000"), amortissements_lmnp=Decimal("6000")).calculer()
        self.assertEqual((r["travaux"], r["pv_brute"], r["abattement_ir"]), (Decimal("0"), Decimal("8500"), Decimal("0")))
        self.client.force_login(User.objects.create_superuser("admin", "", "x"))
        page = self.client.get("/simulations/plus-value/?date_acquisition=2010-01-01&prix_acquisition=200000&date_cession=2026-06-01&prix_cession=300+000").content.decode()
        self.assertIn("11", page)
        self.assertIn("Total à payer", page)


class LocationCourteDureeTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="X", type=Proprietaire.Type.NOM_PROPRE)
        bien = Bien.objects.create(proprietaire=proprio, nom="Chasselay", adresse="1 rue", code_postal="69380", ville="Chasselay")
        self.lot = Lot.objects.create(bien=bien, designation="Chasselay", meuble=True)

    def importer(self, contenu, nom="export.csv"):
        import io
        from . import lcd
        return lcd.importer(self.lot, io.BytesIO(contenu.encode("utf-8")), nom)

    def test_import_booking_et_airbnb_sans_doublon(self):
        from .models import ParametresLCD, ReservationLCD
        booking = ("Type,Reference number,Check-in,Checkout,Guest name,Reservation status,Currency,Payment status,Tourism tax,Amount,Commission,Payments Service Fee,Net,Payout date\n"
                   "Reservation,111,2025-01-30,2025-02-02,Jean,ok,EUR,by_booking,-2.69,138.10,-20.31,-1.90,113.20,3 Feb 2025\n")
        airbnb = ("Date;Type;Code de confirmation;Date de début;Date de fin;Nuits;Voyageur;Logement;Montant;Frais de service;Revenus bruts;Taxes de séjour\n"
                  "05/02/2025;Réservation;HMABC;04/02/2025;06/02/2025;2;Marie;Chasselay;\"180,00\";\"30,00\";\"210,00\";\n"
                  "06/02/2025;Versement;;;;;;;180,00;;;\n")
        self.assertEqual(self.importer(booking)[:2], ("booking", 1))
        self.assertEqual(self.importer(airbnb)[1:], (1, 0, 1))
        self.assertEqual(self.importer(airbnb)[1:], (0, 1, 1))  # réimport : mise à jour
        r = ReservationLCD.objects.get(reference="HMABC")
        self.assertEqual((r.plateforme, r.arrivee, r.nuits, r.montant_brut, r.commission), ("airbnb", date(2025, 2, 4), 2, Decimal("210.00"), Decimal("30.00")))
        self.assertEqual(ReservationLCD.objects.get(reference="111").net, Decimal("113.20"))

        from . import lcd
        ParametresLCD.objects.create(lot=self.lot, menage_par_sejour=28)
        mois = lcd.suivi_annuel(self.lot, 2025)
        # Booking du 30/01 au 02/02 : 2 nuits en janvier, 1 en février (prorata du montant)
        self.assertEqual((mois[0]["nuits"], mois[1]["nuits"]), (2, 3))
        self.assertEqual(round(mois[0]["brut"], 2), Decimal("92.07"))
        self.assertEqual((mois[0]["sejours"], mois[0]["gestion"], mois[1]["gestion"]), (1, Decimal("28"), Decimal("28")))

        self.client.force_login(User.objects.create_superuser("admin", "", "x"))
        page = self.client.get(f"/location-courte-duree/?lot={self.lot.pk}&annee=2025&mois=2").content.decode()
        self.assertIn("Comparaison avec 2024", page)
        self.assertIn("Taux de remplissage", page)


class PeriodiciteRappelsTests(TestCase):
    def test_mensuel_et_desactivation(self):
        from .models import Rappel
        from .rappels import a_faire
        jour = date(2026, 10, 31)
        r = Rappel.objects.create(titre="Relever le compteur", date=jour, periodicite=Rappel.Periodicite.MENSUEL)
        r.marquer_fait()
        self.assertEqual((r.date, r.fait), (date(2026, 11, 30), False))
        self.assertIn("Relever le compteur", [i["titre"] for i in a_faire(jour)])
        r.actif = False
        r.save()
        self.assertNotIn("Relever le compteur", [i["titre"] for i in a_faire(jour)])

    def test_rappel_assurance_desactivable(self):
        from .rappels import a_faire
        proprio = Proprietaire.objects.create(nom="X", type=Proprietaire.Type.NOM_PROPRE)
        bien = Bien.objects.create(proprietaire=proprio, nom="B", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="A1")
        bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2025, 11, 1), loyer_hc=500, revision_annuelle=False)
        self.assertIn("Assurance", [i["categorie"] for i in a_faire(date(2026, 10, 31))])
        bail.rappel_assurance = False
        bail.save()
        self.assertNotIn("Assurance", [i["categorie"] for i in a_faire(date(2026, 10, 31))])


class FicheLocataireTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI Fiche", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(nom="Immeuble F", proprietaire=proprio, adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt 1", meuble=True, loyer_propose=650)

    def _donnees(self, **extra):
        d = {"nom": "DUPONT", "prenom": "Jean", "garants-TOTAL_FORMS": "0", "garants-INITIAL_FORMS": "0", "documents-TOTAL_FORMS": "0", "documents-INITIAL_FORMS": "0"}
        d.update(extra)
        return d

    def test_rattachement_cree_un_bail(self):
        r = self.client.post("/admin/patrimoine/locataire/add/", self._donnees(lot=self.lot.pk, date_entree="2026-11-01"))
        self.assertEqual(r.status_code, 302, r.content.decode()[:3000])
        bail = Bail.objects.get(lot=self.lot)
        self.assertEqual(bail.type, Bail.Type.MEUBLE)
        self.assertEqual(bail.date_debut, date(2026, 11, 1))
        self.assertEqual(list(bail.locataires.values_list("nom", flat=True)), ["DUPONT"])

    def test_rattachement_au_bail_existant(self):
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600)
        self.client.post("/admin/patrimoine/locataire/add/", self._donnees(lot=self.lot.pk))
        self.assertEqual(Bail.objects.count(), 1)
        self.assertEqual(bail.locataires.count(), 1)

    def test_sortie_cree_les_rappels(self):
        loc = Locataire.objects.create(nom="DUPONT", prenom="Jean")
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600, type_garantie="visale", detail_garantie="V123")
        bail.locataires.add(loc)
        MouvementDepot.objects.create(bail=bail, date=date(2025, 1, 1), montant=600)
        page = self.client.get(f"/admin/patrimoine/locataire/{loc.pk}/change/").content.decode()
        self.assertIn("Le locataire quitte le logement", page)
        self.assertIn("Documents", page)
        r = self.client.post(f"/admin/patrimoine/locataire/{loc.pk}/change/", self._donnees(
            lot=self.lot.pk, sortie="on", date_sortie="2026-11-15", nouvelle_rue="5 rue Neuve", nouveau_code_postal="69003", nouvelle_ville="Lyon"))
        self.assertEqual(r.status_code, 302, r.content.decode()[:3000])
        loc.refresh_from_db()
        self.assertEqual(loc.adresse_actuelle, "5 rue Neuve\n69003 Lyon")
        bail.refresh_from_db()
        self.assertFalse(bail.actif)
        self.assertEqual(bail.date_fin, date(2026, 11, 15))
        titres = list(Rappel.objects.values_list("titre", flat=True))
        self.assertTrue(any("dépôt de garantie" in t and "600.00" in t for t in titres), titres)
        self.assertTrue(any("Visale" in t for t in titres), titres)
        self.assertEqual(Rappel.objects.get(titre__contains="dépôt").date, date(2026, 12, 15))

    def test_sortie_sans_depot_ni_visale(self):
        loc = Locataire.objects.create(nom="MARTIN")
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600, type_garantie="garant")
        bail.locataires.add(loc)
        self.client.post(f"/admin/patrimoine/locataire/{loc.pk}/change/", self._donnees(nom="MARTIN", lot=self.lot.pk, sortie="on", date_sortie="2026-11-15"))
        self.assertFalse(Bail.objects.get().actif)
        self.assertEqual(Rappel.objects.count(), 0)

    def test_colocataire_qui_part(self):
        a, b = Locataire.objects.create(nom="A"), Locataire.objects.create(nom="B")
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600)
        bail.locataires.add(a, b)
        self.client.post(f"/admin/patrimoine/locataire/{a.pk}/change/", self._donnees(nom="A", prenom="", lot=self.lot.pk, sortie="on", date_sortie="2026-11-15"))
        bail.refresh_from_db()
        self.assertTrue(bail.actif)
        self.assertEqual(list(bail.locataires.all()), [b])


class RecuDepotWordTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR, adresse="2 chemin du Bois", code_postal="69290", ville="Pollionnay")
        bien = Bien.objects.create(proprietaire=proprio, nom="B", adresse="3 rue X", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="Apt 1")
        self.bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600, depot_garantie=Decimal("1200"))
        self.bail.locataires.add(Locataire.objects.create(civilite="Mme", prenom="Léa", nom="DUPONT"))

    def _texte(self, reponse):
        import io, re, zipfile
        xml = zipfile.ZipFile(io.BytesIO(reponse.content)).read("word/document.xml").decode()
        return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))

    def test_recu_depuis_le_bail(self):
        texte = self._texte(self.client.get(f"/baux/{self.bail.pk}/recu-depot-garantie.docx"))
        self.assertIn("Je soussigné SCI ALFAB, bailleur du bien situé 3 rue X 69000 Lyon", texte)
        self.assertIn("la somme de 1 200 euros", texte)
        self.assertIn("Madame Léa DUPONT", texte)
        self.assertIn("à Pollionnay", texte)
        self.assertNotIn("{", texte)

    def test_recu_depuis_un_encaissement(self):
        m = MouvementDepot.objects.create(bail=self.bail, date=date(2025, 1, 1), montant=Decimal("1150.50"))
        self.assertIn("1 150,50 euros", self._texte(self.client.get(f"/depots/{m.pk}/recu.docx")))
        page = self.client.get("/admin/patrimoine/mouvementdepot/").content.decode()
        self.assertIn("Mme Léa DUPONT", page)
        self.assertIn("SCI ALFAB", page)


class MenusNovembreTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI M", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Pollionnay", adresse="1 rue", code_postal="69290", ville="Pollionnay")
        self.lot = Lot.objects.create(bien=self.bien, designation="Pollionnay")

    def test_historique_revisions(self):
        from .models import RevisionLoyer
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2022, 7, 23), loyer_hc=Decimal("550"))
        self.assertEqual(bail.loyer_initial, Decimal("550"))
        RevisionLoyer.objects.create(bail=bail, date_effet=date(2024, 7, 23), ancien_loyer=550, ancien_irl=Decimal("140.59"),
                                     nouvel_irl=Decimal("145.17"), trimestre="T2 2024", nouveau_loyer=Decimal("567.92"), appliquee=True)
        lignes = bail.historique_revisions(date(2026, 11, 1))
        self.assertEqual([l["anniversaire"].year for l in lignes], [2023, 2024, 2025, 2026])
        self.assertEqual([l["statut"] for l in lignes], ["manquee", "appliquee", "manquee", "a_faire"])
        self.assertAlmostEqual(float(lignes[1]["pourcentage"]), 3.26, places=2)
        page = self.client.get(f"/admin/patrimoine/bail/{bail.pk}/change/").content.decode()
        self.assertIn("Non appliquée", page)
        self.assertIn("+3,26 %", page)
        self.assertIn("Loyer initial", page)

    def test_locataires_actifs_par_defaut(self):
        parti, present = Locataire.objects.create(nom="PARTI"), Locataire.objects.create(nom="PRESENT")
        Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2020, 1, 1), loyer_hc=500, actif=False).locataires.add(parti)
        Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2023, 1, 1), loyer_hc=500).locataires.add(present)
        page = self.client.get("/admin/patrimoine/locataire/").content.decode()
        self.assertIn("PRESENT", page)
        self.assertNotIn("PARTI", page)
        self.assertIn("PARTI", self.client.get("/admin/patrimoine/locataire/?present=tous").content.decode())

    def test_import_syndics(self):
        import os, tempfile
        from django.core.management import call_command
        from .models import Syndic
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("Nom,Adresse,Ville_Corpo,Nom Gestionnaire,Mail Gestionnaire,Tel Gestionnaire,Appartement,URL Espcae\n"
                    "MERCIER Immobilier,4 Bld Jules Favre 69006,Lyon,ARCHIER David,d@x.fr,04-82-83-70-01,Pollionnay,https://m.fr/\n")
        try:
            call_command("importer_airtable", "--copros", f.name, "--appliquer", stdout=open(os.devnull, "w"))
        finally:
            os.unlink(f.name)
        s = Syndic.objects.get()
        self.assertEqual((s.adresse, s.code_postal, s.ville, s.contact_telephone), ("4 Bld Jules Favre", "69006", "Lyon", "04 82 83 70 01"))
        self.bien.refresh_from_db()
        self.assertEqual(self.bien.syndic, s)
        self.assertIn("MERCIER", self.client.get("/admin/patrimoine/syndic/").content.decode())

    def test_sites_utiles_dans_le_menu(self):
        page = self.client.get("/admin/").content.decode()
        self.assertIn("Sites utiles", page)
        self.assertIn("anil.org", page)  # créé par la migration
        self.assertNotIn("Ajouter un site utile", page)  # bouton retiré de l'accueil, reste dans le menu
        self.assertIn("+ Ajouter un site", page)


class SuiviEncaissementsTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.proprio = Proprietaire.objects.create(nom="H-INVEST", type=Proprietaire.Type.SARL_FAMILLE)
        bien = Bien.objects.create(proprietaire=self.proprio, nom="Villeurbanne", adresse="68 rue", code_postal="69100", ville="Villeurbanne")
        self.t1 = Lot.objects.create(bien=bien, designation="T1 bis", meuble=True)
        self.t2 = Lot.objects.create(bien=bien, designation="T2", exploitation="courte")
        a, b = Locataire.objects.create(nom="GAFSAOUI"), Locataire.objects.create(nom="MALGUID")
        Bail.objects.create(lot=self.t1, type=Bail.Type.MEUBLE, date_debut=date(2025, 7, 10), date_fin=date(2026, 3, 20), actif=False,
                            loyer_hc=700, charges=70).locataires.add(a)
        nouveau = Bail.objects.create(lot=self.t1, type=Bail.Type.MEUBLE, date_debut=date(2026, 3, 20), loyer_hc=700, charges=70)
        nouveau.locataires.add(b)
        MouvementDepot.objects.create(bail=nouveau, date=date(2026, 3, 20), montant=1400)

    def test_prorata_et_cautions(self):
        from .encaissements import suivi
        mars = suivi(self.proprio, 2026)["mois"][2]["longue"][0]
        self.assertEqual(mars["loyer"], Decimal("429.03") + Decimal("270.97"))  # 19 jours Gafsaoui, 12 jours Malguid (pas de double loyer le 20)
        self.assertEqual(mars["caution"], Decimal("1400"))
        self.assertIn("GAFSAOUI", mars["occupants"])

    def test_saisie_courte_duree(self):
        from .models import EncaissementLCD
        url = "/finances/encaissements/"
        r = self.client.post(url, {"proprietaire": self.proprio.pk, "annee": 2026, f"lcd-{self.t2.pk}-6-ca": "1605,65",
                                   f"lcd-{self.t2.pk}-6-menage": "-39", f"lcd-{self.t2.pk}-6-commission": "321.13", f"lcd-{self.t2.pk}-6-frais": "105"})
        self.assertEqual(r.status_code, 302)
        e = EncaissementLCD.objects.get()
        self.assertEqual((e.mois, e.menage, e.versement), (date(2026, 6, 1), Decimal("39"), Decimal("1140.52")))
        page = self.client.get(f"{url}?proprietaire={self.proprio.pk}&annee=2026").content.decode()
        self.assertIn("1140,52", page)
        self.assertIn("T2 · courte durée", page)

    def test_baux_en_cours_par_defaut(self):
        page = self.client.get("/admin/patrimoine/bail/").content.decode()
        self.assertIn("MALGUID", page)
        self.assertNotIn("GAFSAOUI", page)


class LmnpTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="Fabien & Alice", type=Proprietaire.Type.NOM_PROPRE)
        bien = Bien.objects.create(proprietaire=proprio, nom="Chazay", adresse="7 place", code_postal="69380", ville="Chazay")
        self.lot = Lot.objects.create(bien=bien, designation="Chazay", meuble=True, lmnp=True)
        Bail.objects.create(lot=self.lot, type=Bail.Type.MEUBLE, date_debut=date(2025, 1, 1), loyer_hc=650, charges=50).locataires.add(Locataire.objects.create(nom="BOUVET"))

    def test_bilan(self):
        from .models import ChargeLMNP
        ChargeLMNP.objects.create(lot=self.lot, annee=2026, categorie="taxe_fonciere", montant=400)
        ChargeLMNP.objects.create(lot=self.lot, annee=2026, categorie="amortissement", montant=9000)
        from .lmnp import bilan_lot
        b = bilan_lot(self.lot, 2026)
        self.assertEqual(b["total_recettes"], Decimal("8400"))
        self.assertEqual(b["resultat"], Decimal("8000"))
        self.assertEqual((b["amort_deduit"], b["amort_reporte"], b["resultat_fiscal"]), (Decimal("8000"), Decimal("1000"), Decimal("0")))
        page = self.client.get("/lmnp/?annee=2026").content.decode()
        self.assertIn("Résultat fiscal", page)
        self.assertIn("Taxe foncière", page)

    def test_historique_repris_avant_les_baux(self):
        from .lmnp import bilan_lot
        from .models import EncaissementHistorique
        EncaissementHistorique.objects.create(lot=self.lot, mois=date(2026, 1, 1), montant=700, charges=50)
        EncaissementHistorique.objects.create(lot=self.lot, mois=date(2026, 2, 1), montant=650, charges=50, note="remise")
        b = bilan_lot(self.lot, 2026)
        # 2 mois repris (1 350 €) + 10 mois selon le bail (10 x 700 €)
        self.assertEqual(b["total_recettes"], Decimal("8350"))


class EncaissementHistoriqueEtatTests(TestCase):
    def test_mois_sans_paiement_repris_dans_l_etat(self):
        from .etats import loyers_encaisses
        from .models import EncaissementHistorique
        proprio = Proprietaire.objects.create(nom="SCI A", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Lozanne", adresse="1 rue", code_postal="69380", ville="Lozanne")
        lot = Lot.objects.create(bien=bien, designation="Loz1", vendu=True)
        EncaissementHistorique.objects.create(lot=lot, mois=date(2019, 3, 1), montant=510)
        EncaissementHistorique.objects.create(lot=lot, mois=date(2019, 4, 1), montant=520, charges=20)
        etat = loyers_encaisses(2019)
        self.assertEqual(etat["total"]["total"], Decimal("1030"))
        self.assertEqual(etat["total"]["mois"][2], Decimal("510"))
        self.assertEqual(etat["total"]["charges"], Decimal("20"))


class CompteursEtDepotTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Apt", pdl="12345678901234", meuble=True)
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def test_index_du_bail_repris_dans_l_etat_des_lieux(self):
        from .models import EtatDesLieux
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.MEUBLE, date_debut=date(2026, 9, 1), loyer_hc=600,
                                   index_elec_entree="HP 1200 / HC 800", index_eau_froide_entree="345")
        self.assertEqual(bail.pdl_electricite, "12345678901234")
        edl = EtatDesLieux.objects.create(bail=bail, type="entree", date=date(2026, 9, 1), compteur_gaz="9876")
        self.assertEqual((edl.compteur_electricite, edl.compteur_eau_froide), ("HP 1200 / HC 800", "345"))
        bail.refresh_from_db()
        self.assertEqual(bail.index_gaz_entree, "9876")  # saisi dans l'état des lieux, repris dans le bail
        sortie = EtatDesLieux.objects.create(bail=bail, type="sortie", date=date(2027, 8, 31), compteur_electricite="HP 3000")
        bail.refresh_from_db()
        self.assertEqual((bail.index_elec_sortie, bail.index_elec_entree), ("HP 3000", "HP 1200 / HC 800"))
        self.assertEqual(self.client.get(f"/baux/{bail.pk}/etat-des-lieux-entree.pdf").status_code, 200)
        page = self.client.get(f"/admin/patrimoine/bail/{bail.pk}/change/").content.decode()
        self.assertIn("Relevés des compteurs", page)
        self.assertIn("depot_garantie.js", page)

    def test_depot_plafonne(self):
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2026, 9, 1), loyer_hc=600)
        self.assertEqual(bail.depot_maximum, 600)
        bail.type = Bail.Type.MEUBLE
        self.assertEqual(bail.depot_maximum, 1200)
        from .admin import BailForm
        donnees = {"lot": self.lot.pk, "type": "nu", "destination": "habitation", "date_debut": "2026-09-01", "loyer_hc": "600",
                   "loyer_initial": "600", "charges": "0", "depot_garantie": "900", "jour_echeance": "1", "mode_charges": "aucune",
                   "irl_trimestre": "", "type_garantie": ""}
        form = BailForm(donnees)
        self.assertFalse(form.is_valid())
        self.assertIn("600.00", str(form.errors["depot_garantie"]))
        form = BailForm({**donnees, "type": "meuble"})
        self.assertNotIn("depot_garantie", form.errors)


class SauvegardeTests(TransactionTestCase):  # hors transaction, comme en production
    def test_creation_rotation_et_telechargement(self):
        import tempfile
        import zipfile
        from pathlib import Path

        from django.test import override_settings

        from . import sauvegarde
        with tempfile.TemporaryDirectory() as tmp, override_settings(SAUVEGARDES_ROOT=Path(tmp) / "s", MEDIA_ROOT=Path(tmp) / "m",
                                                                   PRIVE_ROOT=Path(tmp) / "p"):
            (Path(tmp) / "p" / "pieces-identite").mkdir(parents=True)
            (Path(tmp) / "p" / "pieces-identite" / "cni.pdf").write_bytes(b"%PDF")
            for i in range(9):
                f = sauvegarde.creer()
                import os
                os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))
            self.assertEqual(len(sauvegarde.liste()), 7)
            with zipfile.ZipFile(sauvegarde.liste()[0]) as z:
                self.assertIn("db.sqlite3", z.namelist())
                self.assertIn("prive/pieces-identite/cni.pdf", z.namelist())
            nom = sauvegarde.liste()[0].name
            self.assertEqual(self.client.get(f"/reglages/sauvegardes/{nom}").status_code, 302)  # connexion obligatoire
            self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
            self.assertContains(self.client.get("/reglages/sauvegardes/"), nom)
            r = self.client.post("/reglages/sauvegardes/")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self.client.get(r["Location"]).status_code, 200)
            self.assertEqual(self.client.get("/reglages/sauvegardes/../db.sqlite3").status_code, 404)
            # automatique : rien si la dernière a moins de 24 h
            sauvegarde._derniere_verification = 0
            self.assertIsNone(sauvegarde.si_necessaire())


class JustificatifsEtMailsTests(TestCase):
    def test_justificatifs_et_mails(self):
        import tempfile

        from django.core import mail
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from .models import Candidature, ParametresMail
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="Apt 3")
        ParametresMail.objects.create(adresse="proprio@gmail.com", mot_de_passe="abcd efgh ijkl mnop", nom_expediteur="Fabien")
        self.assertEqual(ParametresMail.objects.get().mot_de_passe, "abcdefghijklmnop")
        c = Candidature.objects.create(lot=lot, type_garantie="visale", email="lea@example.com")
        base = {"prenom": "Léa", "nom": "Durand", "email": "lea@example.com", "telephone": "06", "adresse_rue": "3 rue",
                "adresse_code_postal": "69003", "adresse_ville": "Lyon", "situation": "cdi", "revenus_mensuels": "2400",
                "consentement": "on", "garants-TOTAL_FORMS": "2", "garants-INITIAL_FORMS": "0", "garants-MIN_NUM_FORMS": "0",
                "garants-MAX_NUM_FORMS": "2"}
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive, ENVOI_MAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            r = self.client.post(f"/candidature/{c.jeton}/", {**base, "piece-salaires": [SimpleUploadedFile("virus.exe", b"x")]})
            self.assertContains(r, "formats acceptés")
            r = self.client.post(f"/candidature/{c.jeton}/", {**base, "piece-salaires": [SimpleUploadedFile("p1.pdf", b"%PDF"), SimpleUploadedFile("p2.pdf", b"%PDF")],
                                                              "piece-identite": [SimpleUploadedFile("cni.jpg", b"jpg")]})
            self.assertContains(r, "Merci Léa")
            self.assertEqual(c.pieces.count(), 3)
            self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["lea@example.com", "proprio@gmail.com"])
            self.assertIn("Justificatifs déposés : 3", next(m.body for m in mail.outbox if m.to == ["proprio@gmail.com"]))
            self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
            page = self.client.get(f"/admin/patrimoine/candidature/{c.pk}/change/")
            self.assertContains(page, "cni.jpg")
            self.assertContains(page, "Envoyer le lien par mail")
            self.client.post(f"/candidatures/{c.pk}/envoyer-lien/", {"email": "autre@example.com"})
            self.assertEqual(mail.outbox[-1].to, ["autre@example.com"])
            self.assertIn(f"/candidature/{c.jeton}/", mail.outbox[-1].body)
            self.assertIn("Compléter mon dossier", mail.outbox[-1].alternatives[0][0])
            eml = self.client.get(f"/candidatures/{c.pk}/preparer-mail/")
            self.assertEqual(eml["Content-Type"], "message/rfc822")
            self.assertIn(b"X-Unsent: 1", eml.content)
            self.assertIn(b"To: autre@example.com", eml.content)
            self.assertIn(b"text/html", eml.content)
            self.client.post("/reglages/mails/test/")
            self.assertEqual(mail.outbox[-1].subject, "Test – Gestion immobilière")
            self.assertEqual(self.client.get("/admin/patrimoine/parametresmail/").status_code, 302)


class EcheanceApresRevisionTests(TestCase):
    def test_echeance_suit_le_loyer_revise(self):
        from .models import RevisionLoyer
        proprio = Proprietaire.objects.create(nom="Nom propre", type=Proprietaire.Type.NOM_PROPRE)
        bien = Bien.objects.create(proprietaire=proprio, nom="Pollionnay", adresse="1 rue", code_postal="69290", ville="Pollionnay")
        bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="Apt"), type=Bail.Type.NU,
                                   date_debut=date(2022, 7, 23), loyer_hc=Decimal("578"))
        Echeance.generer_pour_mois(date(2026, 11, 1))  # générée avant la révision
        r = RevisionLoyer.objects.create(bail=bail, date_effet=date(2026, 8, 1), ancien_loyer=Decimal("578"), ancien_irl=Decimal("145.17"),
                                         nouvel_irl=Decimal("148.37"), trimestre="T2 2026", nouveau_loyer=Decimal("590"))
        r.appliquer()
        self.assertEqual(bail.echeances.get(periode=date(2026, 11, 1)).loyer_hc, Decimal("590"))
        # un loyer modifié à la main sans révision est repris à la génération suivante
        Bail.objects.filter(pk=bail.pk).update(loyer_hc=Decimal("595"), date_derniere_revision=None)
        RevisionLoyer.objects.all().delete()
        Echeance.generer_pour_mois(date(2026, 11, 1))
        self.assertEqual(bail.echeances.get(periode=date(2026, 11, 1)).loyer_hc, Decimal("595"))


class FiltreLoyersTests(TestCase):
    def test_non_regles_par_defaut(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        for nom in ("Paye", "Attente"):
            bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation=nom), type=Bail.Type.NU, date_debut=date(2026, 1, 1), loyer_hc=500)
            bail.locataires.add(Locataire.objects.create(nom=nom.upper()))
        Echeance.generer_pour_mois(date(2026, 10, 1))
        e = Echeance.objects.get(bail__lot__designation="Paye")
        Paiement.objects.create(echeance=e, date=date(2026, 10, 2), montant=e.total)
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        page = self.client.get("/admin/patrimoine/echeance/").content.decode()
        self.assertIn("ATTENTE", page)
        self.assertNotIn("PAYE", page)
        self.assertIn("PAYE", self.client.get("/admin/patrimoine/echeance/?reglement=tous").content.decode())


class ModifierLoyersTests(TestCase):
    def test_action_modifier(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="A"), type=Bail.Type.NU, date_debut=date(2026, 1, 1), loyer_hc=500)
        bail.locataires.add(Locataire.objects.create(nom="DUPONT"))
        Echeance.generer_pour_mois(date(2026, 10, 1))
        Echeance.generer_pour_mois(date(2026, 11, 1))
        e1, e2 = Echeance.objects.order_by("periode")
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        url = "/admin/patrimoine/echeance/"
        self.assertIn("Modifier", self.client.get(url).content.decode())
        r = self.client.post(url, {"action": "modifier", "_selected_action": [e1.pk]})
        self.assertRedirects(r, f"/admin/patrimoine/echeance/{e1.pk}/change/", fetch_redirect_response=False)
        r = self.client.post(url, {"action": "modifier", "_selected_action": [e1.pk, e2.pk]})
        self.assertContains(r, "Appliquer aux 2 échéances")
        self.client.post(url, {"action": "modifier", "_selected_action": [e1.pk, e2.pk], "appliquer": "1", "loyer_hc": "520", "charges": ""})
        e1.refresh_from_db(); e2.refresh_from_db()
        self.assertEqual((e1.loyer_hc, e2.loyer_hc), (520, 520))
        self.assertEqual(e1.charges, 0)


class BienVenduTests(TestCase):
    def test_bien_vendu_masque_a_la_location(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        vendu = Bien.objects.create(proprietaire=proprio, nom="Vendu", adresse="1 rue", code_postal="69000", ville="Lyon")
        garde = Bien.objects.create(proprietaire=proprio, nom="Gardé", adresse="2 rue", code_postal="69000", ville="Lyon")
        lot_vendu = Lot.objects.create(bien=vendu, designation="Appart vendu")
        Lot.objects.create(bien=garde, designation="Appart gardé")
        bail = Bail.objects.create(lot=lot_vendu, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=500)
        admin_bien = site._registry[Bien]
        requete = RequestFactory().post("/")
        vendu.date_vente = date(2026, 6, 30)
        admin_bien.save_model(requete, vendu, None, True)
        lot_vendu.refresh_from_db()
        self.assertTrue(vendu.vendu)
        self.assertEqual((lot_vendu.vendu, lot_vendu.date_vente), (True, date(2026, 6, 30)))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        page = self.client.get("/admin/patrimoine/candidature/add/").content.decode()
        self.assertIn("Appart gardé", page)
        self.assertNotIn("Appart vendu", page)
        # la fiche d'un ancien bail garde son logement
        self.assertIn("Appart vendu", self.client.get(f"/admin/patrimoine/bail/{bail.pk}/change/").content.decode())
        # liste des biens : non vendus par défaut, vendus sur demande
        liste = self.client.get("/admin/patrimoine/bien/").content.decode()
        self.assertIn("Gardé", liste)
        self.assertNotIn(">Vendu<", liste)
        self.assertIn(">Vendu<", self.client.get("/admin/patrimoine/bien/?vente=oui").content.decode())
        self.assertNotIn("Appart vendu", self.client.get("/admin/patrimoine/lot/").content.decode())
        # décocher annule la vente des lots
        vendu.vendu, vendu.date_vente = False, None
        admin_bien.save_model(requete, vendu, None, True)
        lot_vendu.refresh_from_db()
        self.assertFalse(lot_vendu.vendu)


class ProrataEtAjustementTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="A"), type=Bail.Type.MEUBLE,
                                        date_debut=date(2026, 9, 16), loyer_hc=Decimal("600"), charges=Decimal("60"))

    def test_premier_mois_au_prorata(self):
        Echeance.generer_pour_mois(date(2026, 9, 1))
        Echeance.generer_pour_mois(date(2026, 10, 1))
        sept, octo = Echeance.objects.order_by("periode")
        self.assertEqual((sept.loyer_hc, sept.charges), (Decimal("300.00"), Decimal("30.00")))  # 15 jours sur 30
        self.assertEqual((octo.loyer_hc, octo.charges), (Decimal("600"), Decimal("60")))

    def test_correction_manuelle_conservee(self):
        Echeance.generer_pour_mois(date(2026, 10, 1))
        Echeance.generer_pour_mois(date(2026, 11, 1))
        e, e2 = Echeance.objects.order_by("periode")
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.client.post("/admin/patrimoine/echeance/", {"action": "modifier", "_selected_action": [e.pk, e2.pk], "appliquer": "1", "loyer_hc": "550"})
        e.refresh_from_db()
        self.assertTrue(e.ajustee)
        Echeance.generer_pour_mois(date(2026, 10, 1))  # nouvelle génération : la correction reste
        e.refresh_from_db()
        self.assertEqual(e.loyer_hc, Decimal("550"))


class NouveauLookTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="A"), type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=500)
        self.bail.locataires.add(Locataire.objects.create(nom="DUPONT", prenom="Jean"))

    def test_pages_principales(self):
        Echeance.generer_pour_mois(timezone.localdate().replace(day=1))
        for url in ["/admin/", "/admin/patrimoine/echeance/", "/admin/patrimoine/locataire/", "/admin/patrimoine/lot/",
                    "/admin/patrimoine/bail/", "/recherche/?q=dupont", "/admin/patrimoine/echeance/?retard=oui"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get("/recherche/?q=dup"), "DUPONT")
        self.assertContains(self.client.get("/admin/"), "Loyers de")

    def test_bouton_encaisser(self):
        Echeance.generer_pour_mois(date(2026, 10, 1))
        e = Echeance.objects.get()
        self.client.post(f"/admin/patrimoine/echeance/{e.pk}/encaisser/")
        e.refresh_from_db()
        self.assertEqual(e.statut, Echeance.Statut.PAYE)
        self.assertEqual(e.paiements.get().montant, 500)


class AFaireTests(TestCase):
    def test_loyer_en_retard_et_assurance_recente(self):
        from patrimoine.rappels import a_faire
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Chazay", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="Chazay")
        self.assertEqual(str(lot), "Chazay")
        Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2004, 3, 1), loyer_hc=500)
        Echeance.generer_pour_mois(date(2026, 10, 1))
        items = a_faire(date(2026, 11, 5))
        self.assertIn("Loyer de 10/2026 non réglé : 500,00 €", [i["titre"] for i in items])
        assurance = next(i for i in items if i["categorie"] == "Assurance")
        self.assertEqual(assurance["date"], date(2026, 3, 1))  # pas le 01/03/2005


class ModelesWordTests(TestCase):
    def test_modeles_remplis_depuis_le_bail(self):
        import io
        import tempfile
        import zipfile

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from .models import Garant, ModeleDocument
        from .word import MODELES
        proprio = Proprietaire.objects.create(nom="Fabien & Alice HUNAULT", type=Proprietaire.Type.NOM_PROPRE, adresse="89 avenue Marcel Mérieux",
                                              code_postal="69290", ville="Grézieu-la-Varenne")
        bien = Bien.objects.create(proprietaire=proprio, nom="Chazay", adresse="7 place du marché", code_postal="69380", ville="Chazay d'Azergues")
        lot = Lot.objects.create(bien=bien, designation="Chazay", meuble=True, surface=Decimal("28.28"), nb_pieces=2)
        bail = Bail.objects.create(lot=lot, type=Bail.Type.MEUBLE, date_debut=date(2026, 6, 3), loyer_hc=650, charges=50, depot_garantie=650,
                                   irl_trimestre="T1", irl_valeur=Decimal("146.60"), indice_reference="IRL T1 2026 : 146,60", index_elec_entree="HP 1200 / HC 800")
        loc = Locataire.objects.create(civilite="Mme", nom="BOUVET", prenom="Lucie", date_naissance=date(1993, 3, 21), lieu_naissance="Mayenne")
        bail.locataires.add(loc)
        Garant.objects.create(bail=bail, locataire=loc, civilite="M.", nom="Bouvet", prenom="Rodolphe", adresse="2 La Petite Marcillerie")
        Garant.objects.create(bail=bail, locataire=loc, civilite="Mme", nom="Meziere", prenom="Isabelle", adresse="2 La Petite Marcillerie")
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

        def texte(reponse):
            with zipfile.ZipFile(io.BytesIO(reponse.content)) as z:
                import re
                return re.sub(r"<[^>]+>", "", z.read("word/document.xml").decode())

        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            modeles = {}
            for nom, type_doc, fichier in [("Bail meublé", "bail_meuble", "bail_meuble.docx"), ("Acte", "caution", "acte_cautionnement.docx"),
                                           ("EDL", "edl", "etat_des_lieux.docx"), ("Bail nu", "bail_nu", "bail_meuble.docx")]:
                m = ModeleDocument(nom=nom, type=type_doc)
                m.fichier.save(fichier, ContentFile((MODELES / fichier).read_bytes()))
                modeles[type_doc] = m
            page = self.client.get(f"/admin/patrimoine/bail/{bail.pk}/change/").content.decode()
            self.assertIn("Mes modèles Word", page)
            self.assertIn("EDL – entrée", page)
            self.assertNotIn("Bail nu", page)  # pas proposé pour un bail meublé
            t = texte(self.client.get(f"/modeles/{modeles['bail_meuble'].pk}/bail/{bail.pk}/"))
            self.assertIn("dépôt de garantie d’un montant de 650 €", t)
            self.assertIn("Monsieur Rodolphe BOUVET et Madame Isabelle MEZIERE se porte(nt) garant de Madame Lucie BOUVET", t)
            self.assertIn("146,60 au 1er trimestre 2026", t)
            self.assertNotIn("{", t.split("CONTRAT TYPE")[0])
            t = texte(self.client.get(f"/modeles/{modeles['caution'].pk}/bail/{bail.pk}/"))
            self.assertIn("Loyer : 650 euros (six-cent-cinquante euros) ; charges : 50 euros (cinquante euros)", t)
            self.assertIn("Monsieur Rodolphe BOUVET, demeurant 2 La Petite Marcillerie ET Madame Isabelle MEZIERE", t)
            t = texte(self.client.get(f"/modeles/{modeles['edl'].pk}/bail/{bail.pk}/?edl=entree"))
            self.assertIn("☒ ENTRÉE", t)
            self.assertIn("03/06/2026", t)
            self.assertIn("1200", t)
            self.assertIn("800", t)
            page = self.client.get(f"/admin/patrimoine/modeledocument/{modeles['edl'].pk}/change/").content.decode()
            self.assertIn("{Index_HP_Entrée}", page)
            self.assertContains(self.client.get(f"/modeles/champs/?bail={bail.pk}"), "six-cent-cinquante euros")


class ZoneTendueEtConditionsTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI TATA", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Thibaudière", adresse="39 rue de la Thibaudière", code_postal="69007", ville="Lyon",
                                        type_habitat="collectif", regime_juridique="copropriete", interphone=True)
        self.lot = Lot.objects.create(bien=self.bien, designation="T1", surface=19, nb_pieces=1, mode_chauffage="individuel",
                                      clause_particuliere="WC de type broyeur.")
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2026, 2, 1), loyer_hc=600, loyer_initial=600, charges=20)
        self.bail.locataires.add(Locataire.objects.create(nom="VU", prenom="Alain", civilite="M."))

    def _texte(self, valeurs):
        import io, re, zipfile
        from .word import remplir_docx
        xml = zipfile.ZipFile(io.BytesIO(remplir_docx("bail_nu.docx", valeurs))).read("word/document.xml").decode()
        return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))

    def test_paragraphes_conditionnels_et_cases(self):
        from .champs import valeurs
        t = self._texte(valeurs(self.bail))
        self.assertNotIn("LOYER EN ZONE TENDUE", t)
        self.assertNotIn("GARANT SOLIDAIRE", t)
        self.assertIn("WC de type broyeur.", t)
        self.assertIn("☒ Immeuble collectif", t)
        self.assertIn("☒ Copropriété", t)
        self.assertIn("☒ Interphone", t)
        self.assertIn("☐ Ascenseur", t)
        self.assertNotIn("{", t)
        self.bien.zone_tendue = self.bien.encadrement_loyers = True
        self.bien.save()
        self.bail.loyer_reference, self.bail.loyer_reference_majore, self.bail.complement_loyer = 16.5, 19.8, 223.8
        self.bail.complement_loyer_motif = "local supplémentaire"
        self.bail.save()
        self.bail.refresh_from_db()
        t = self._texte(valeurs(self.bail))
        self.assertIn("LOYER EN ZONE TENDUE", t)
        self.assertIn("19,80 €/m²", t)
        self.assertIn("376,20 €", t)  # loyer de base = 600 - 223,80
        self.assertNotIn("LOYER DU PRÉCÉDENT LOCATAIRE", t)
        self.assertNotIn("{", t)

    def test_controle_loyer_de_reference_majore(self):
        from .admin import BailForm
        self.bien.encadrement_loyers = True
        self.bien.save()
        donnees = {"lot": self.lot.pk, "locataires": [l.pk for l in self.bail.locataires.all()], "type": "nu", "date_debut": "2026-02-01",
                   "loyer_initial": "600", "loyer_hc": "600", "charges": "20", "depot_garantie": "600", "jour_echeance": 1, "mode_charges": "forfait",
                   "destination": "habitation", "loyer_reference_majore": "19.80"}
        form = BailForm(data=donnees, instance=self.bail)
        self.assertFalse(form.is_valid())
        self.assertIn("loyer_reference_majore", form.errors)
        form = BailForm(data={**donnees, "complement_loyer": "223.80"}, instance=self.bail)
        self.assertFalse(form.is_valid())
        self.assertIn("complement_loyer_motif", form.errors)
        form = BailForm(data={**donnees, "complement_loyer": "223.80", "complement_loyer_motif": "local supplémentaire"}, instance=self.bail)
        self.assertNotIn("loyer_reference_majore", form.errors)
        self.assertNotIn("complement_loyer_motif", form.errors)


class LocatairePartiTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="A")
        self.ancien = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2022, 7, 1), loyer_hc=Decimal("600"))
        for m in range(1, 6):
            e = Echeance.objects.create(bail=self.ancien, periode=date(2026, m, 1), loyer_hc=Decimal("600"), charges=0,
                                        date_exigibilite=date(2026, m, 1))
            if m in (1, 2, 3):
                Paiement.objects.create(echeance=e, date=e.date_exigibilite, montant=600,
                                        notes="Repris du suivi Excel" if m == 3 else "Supposé réglé (rattrapage 2026)")

    def test_depart_retire_les_loyers_suivants(self):
        self.ancien.date_fin, self.ancien.actif = date(2026, 1, 15), False
        self.ancien.save()
        restants = list(self.ancien.echeances.order_by("periode"))
        # janvier au prorata, février / avril / mai retirés, mars réellement encaissé : gardé (pas de locataire suivant)
        self.assertEqual([e.periode.month for e in restants], [1, 3])
        self.assertEqual(restants[0].loyer_hc, Decimal("290.32"))
        self.assertEqual(restants[0].paiements.get().montant, Decimal("290.32"))

    def test_nouveau_bail_cloture_le_precedent(self):
        nouveau = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2026, 3, 1), loyer_hc=Decimal("650"))
        self.ancien.refresh_from_db()
        self.assertEqual((self.ancien.actif, self.ancien.date_fin), (False, date(2026, 2, 28)))
        self.assertEqual([e.periode.month for e in self.ancien.echeances.order_by("periode")], [1, 2])
        self.assertEqual(list(nouveau.echeances.values_list("periode", flat=True)), [date(2026, 3, 1)])

    def test_filtres_en_haut(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get("/admin/patrimoine/bail/")
        self.assertContains(r, 'class="filtres-haut"')
        self.assertNotContains(r, "Type de bail")
        self.assertNotContains(r, 'id="changelist-filter"')
        self.assertContains(r, 'class="titre-liste"')

    def test_garants_actifs_par_defaut(self):
        from .models import Garant
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        loc = Locataire.objects.create(nom="ANCIEN")
        self.ancien.locataires.add(loc)
        Garant.objects.create(bail=self.ancien, locataire=loc, nom="GARANTACTIF")
        self.ancien.date_fin, self.ancien.actif = date(2026, 1, 31), False
        self.ancien.save()
        self.assertNotContains(self.client.get("/admin/patrimoine/garant/"), "GARANTACTIF")
        self.assertContains(self.client.get("/admin/patrimoine/garant/?actif=tous"), "GARANTACTIF")

    def test_bail_ne_propose_que_les_locataires_sans_bail(self):
        from .admin import BailForm
        loge = Locataire.objects.create(nom="LOGE")
        self.ancien.locataires.add(loge)
        libre = Locataire.objects.create(nom="LIBRE")
        self.assertEqual(list(BailForm().fields["locataires"].queryset), [libre])
        self.assertEqual(set(BailForm(instance=self.ancien).fields["locataires"].queryset), {loge, libre})

    def test_contacter(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        loc = Locataire.objects.create(nom="DURAND", email="d@example.com")
        self.ancien.locataires.add(loc)
        self.assertContains(self.client.get("/admin/patrimoine/locataire/"), "Contacter")
        r = self.client.get(f"/admin/patrimoine/locataire/{loc.pk}/contacter/")
        self.assertContains(r, "Relance de loyer impayé")
        self.assertContains(r, "05/2026")  # loyer impayé repris dans le message de relance


class LoyersAutomatiquesTests(TestCase):
    def test_loyers_du_mois_crees_a_la_premiere_visite(self):
        from django.test import override_settings

        from . import loyers_auto

        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="A"), type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=500)
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        loyers_auto._dernier_jour = None
        with override_settings(LOYERS_AUTO=True):
            r = self.client.get("/admin/patrimoine/echeance/")
            self.client.get("/admin/")
        self.assertEqual(Echeance.objects.filter(periode=timezone.localdate().replace(day=1)).count(), 1)
        self.assertNotContains(r, "Générer ")


class NavigationPeriodeTests(TestCase):
    def test_boutons_et_periode_libre(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="A"), type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=500)
        bail.locataires.add(Locataire.objects.create(nom="DUPONT"))
        Echeance.generer_pour_mois(date(2026, 3, 1))
        Echeance.generer_pour_mois(date(2026, 5, 1))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get("/admin/patrimoine/echeance/?reglement=tous")
        for libelle in ("Ce mois", "Mois dernier", "3 derniers mois", "Cette année", "Période…"):
            self.assertContains(r, libelle)
        r = self.client.get("/admin/patrimoine/echeance/?reglement=tous&periode__gte=2026-03-01&periode__lte=2026-03-31")
        self.assertEqual(r.context["cl"].result_count, 1)
        self.assertContains(r, "periode-libre\" open")


class ReductionTemporaireTests(TestCase):
    def test_reduction_deduite_au_prorata(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="A"), type=Bail.Type.NU, date_debut=date(2025, 1, 1),
                                   loyer_hc=Decimal("600"), charges=Decimal("50"))
        for m in (1, 2, 3, 4):
            Echeance.generer_pour_mois(date(2026, m, 1))
        Paiement.objects.create(echeance=bail.echeances.get(periode=date(2026, 1, 1)), date=date(2026, 1, 5), montant=650)
        bail.reduction_montant, bail.reduction_debut, bail.reduction_fin = Decimal("100"), date(2026, 1, 1), date(2026, 3, 15)
        bail.save()
        loyers = {e.periode.month: e.loyer_hc for e in bail.echeances.all()}
        self.assertEqual(loyers[1], Decimal("600"))  # déjà réglé : inchangé
        self.assertEqual(loyers[2], Decimal("500.00"))
        self.assertEqual(loyers[3], Decimal("551.61"))  # 15 jours sur 31 réduits
        self.assertEqual(loyers[4], Decimal("600"))
        self.assertEqual(bail.echeances.get(periode=date(2026, 2, 1)).charges, Decimal("50"))


class Annexe2072Tests(TestCase):
    def setUp(self):
        from .models import PieceComptable, Pret

        self.sci = Proprietaire.objects.create(nom="SCI TEST", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=self.sci, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot_a, _ = Lot.objects.create(bien=self.bien, designation="A"), Lot.objects.create(bien=self.bien, designation="B")
        bail = Bail.objects.create(lot=lot_a, type=Bail.Type.NU, date_debut=date(2024, 1, 1), loyer_hc=Decimal("500"), charges=Decimal("50"))
        for m in (1, 2):
            Echeance.generer_pour_mois(date(2025, m, 1))
            Paiement.objects.create(echeance=bail.echeances.get(periode=date(2025, m, 1)), date=date(2025, m, 5), montant=550)
        PieceComptable.objects.create(bien=self.bien, date=date(2025, 3, 1), libelle="Taxe foncière", montant=Decimal("800"), ligne="12")
        PieceComptable.objects.create(bien=self.bien, date=date(2024, 12, 20), date_paiement=date(2025, 1, 10), libelle="Chaudière",
                                      montant=Decimal("1200"), ligne="9b")
        PieceComptable.objects.create(bien=self.bien, date=date(2025, 5, 1), libelle="Mobilier", montant=Decimal("300"), ligne="non")
        PieceComptable.objects.create(bien=self.bien, date=date(2024, 5, 1), libelle="Autre année", montant=Decimal("99"), ligne="6")

    def test_calcul(self):
        from .annexe2072 import colonne

        v = colonne(self.bien, 2025)["valeurs"]
        self.assertEqual(v["1"], Decimal("1000.00"))
        self.assertEqual(v["7"], Decimal("40"))  # 2 locaux
        self.assertEqual(v["9"], Decimal("1200"))
        self.assertEqual(v["9b"], Decimal("1200"))
        self.assertEqual(v["12"], Decimal("800"))
        self.assertEqual(v["6"], Decimal("0"))
        self.assertEqual(v["16"], Decimal("2040"))
        self.assertEqual(v["18"], Decimal("-1040.00"))

    def test_pages(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025")
        self.assertContains(r, "Immeuble")
        self.assertContains(r, "Taxe foncière")
        r = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025&format=pdf")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(self.client.get("/admin/patrimoine/piececomptable/").status_code, 200)
        self.assertEqual(self.client.get("/admin/patrimoine/piececomptable/add/").status_code, 200)
        self.assertContains(self.client.get("/admin/patrimoine/mouvementdepot/"), "Restituées")

    def test_cadre_i_et_part_personnelle(self):
        self.sci.pourcentage_detention = 50
        self.sci.save()
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        r = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025")
        cadre = {code: (montant, part) for code, _, _, montant, part in r.context["cadre_i"]}
        self.assertEqual(cadre["R1"], (Decimal("1000.00"), Decimal("500.00")))
        self.assertEqual(cadre["R2"][1], Decimal("600.00"))
        self.assertEqual(cadre["R5"], (Decimal("-1040.00"), Decimal("-520.00")))
        self.assertContains(r, "Votre part (50 %)")


class EnvoyerQuittanceTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI Q", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="Studio")
        self.bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2026, 1, 1), loyer_hc=Decimal("600"), charges=Decimal("50"))
        self.bail.locataires.add(Locataire.objects.create(civilite="Mme", nom="DUPONT", prenom="Léa", email="lea@exemple.fr"))
        for mois in (9, 10):
            Echeance.generer_pour_mois(date(2026, mois, 1))
        self.payee = Echeance.objects.get(periode=date(2026, 9, 1))
        Paiement.objects.create(echeance=self.payee, date=date(2026, 9, 3), montant=self.payee.total)

    def test_page_choix(self):
        r = self.client.get("/quittances/envoyer/")
        self.assertContains(r, "DUPONT Léa")
        r = self.client.get(f"/quittances/envoyer/?bail={self.bail.pk}")
        self.assertContains(r, "Septembre 2026")
        self.assertContains(r, "Octobre 2026 – à payer")
        self.assertContains(r, f"/echeances/{self.payee.pk}/preparer-mail/")  # dernier mois payé par défaut

    def test_mail_outlook(self):
        import email

        r = self.client.get(f"/echeances/{self.payee.pk}/preparer-mail/")
        from email.policy import default

        m = email.message_from_bytes(r.content, policy=default)
        self.assertEqual(m["To"], "lea@exemple.fr")
        self.assertEqual(m["X-Unsent"], "1")
        self.assertIn("Quittance de loyer", m["Subject"])
        pieces = [p for p in m.walk() if p.get_content_type() == "application/pdf"]
        self.assertEqual(len(pieces), 1)
        texte = next(p for p in m.walk() if p.get_content_type() == "text/plain").get_content()
        self.assertIn("Bonjour Mme Dupont", texte)
        self.assertIn("du mois de septembre 2026 pour le logement de Lyon.", texte)
        self.assertTrue(texte.rstrip().endswith("Bien cordialement,"))


class DroitsTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from . import droits

        self.admin = User.objects.create_superuser("chef", "c@exemple.fr", "x")
        self.user = User.objects.create_user("aide", password="x", is_staff=True)
        droits.appliquer(self.user, {"biens": droits.CONSULTATION, "loyers": droits.MODIFICATION})
        proprio = Proprietaire.objects.create(nom="SCI D", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.client.force_login(self.user)

    def test_niveaux(self):
        from . import droits

        n = droits.niveaux(self.user)
        self.assertEqual(n["biens"], droits.CONSULTATION)
        self.assertEqual(n["loyers"], droits.MODIFICATION)
        self.assertEqual(n["compta"], droits.AUCUN)

    def test_consultation_sans_modification(self):
        self.assertEqual(self.client.get("/admin/patrimoine/bien/").status_code, 200)
        r = self.client.get(f"/admin/patrimoine/bien/{self.bien.pk}/change/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="_save"')  # fiche en lecture seule
        self.assertEqual(self.client.post(f"/admin/patrimoine/bien/{self.bien.pk}/change/", {"nom": "X"}).status_code, 403)
        self.assertEqual(self.client.post("/admin/patrimoine/bien/", {"action": "delete_selected", "_selected_action": [self.bien.pk]}).status_code, 403)
        self.assertEqual(self.client.get("/admin/patrimoine/bien/add/").status_code, 403)

    def test_modules_interdits(self):
        self.assertEqual(self.client.get("/admin/patrimoine/piececomptable/").status_code, 403)
        self.assertEqual(self.client.get("/comptabilite/2072/").status_code, 403)
        self.assertEqual(self.client.get("/etats/endettement/").status_code, 403)
        self.assertEqual(self.client.get("/reglages/sauvegardes/").status_code, 403)
        self.assertEqual(self.client.get("/admin/auth/user/").status_code, 403)
        self.assertEqual(self.client.get("/balles/").status_code, 403)
        self.assertEqual(self.client.get("/quittances/envoyer/").status_code, 200)  # loyers : autorisé

    def test_menu_et_tableau_de_bord(self):
        r = self.client.get("/admin/")
        self.assertContains(r, "Biens et lots")
        self.assertContains(r, "Créer une quittance")
        self.assertNotContains(r, "Comptabilité SCI")
        self.assertNotContains(r, "Capital restant dû")
        self.assertNotContains(r, "Réglages")

    def test_ecran_utilisateurs(self):
        from django.contrib.auth.models import User

        from . import droits

        self.client.force_login(self.admin)
        donnees = {"username": "compta", "password1": "Motdepasse-tres-long-1", "password2": "Motdepasse-tres-long-1",
                   "first_name": "", "last_name": "", "email": "", "droit_compta": "modification", "droit_etats": "consultation"}
        r = self.client.post("/admin/auth/user/add/", donnees)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["adminform"].form.errors)
        u = User.objects.get(username="compta")
        self.assertTrue(u.is_staff)
        self.assertEqual(droits.niveau(u, "compta"), droits.MODIFICATION)
        self.assertEqual(droits.niveau(u, "etats"), droits.CONSULTATION)
        self.assertEqual(droits.niveau(u, "biens"), droits.AUCUN)
        r = self.client.get(f"/admin/auth/user/{u.pk}/change/")
        self.assertContains(r, "Droits par module")
        self.assertContains(r, 'value="modification" class="choix-droit" id="id_droit_compta_2" checked')


class ModeleAnnonceTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_superuser("chef", "c@exemple.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI M", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Vaugneray", adresse="1 rue", code_postal="69670", ville="Vaugneray")
        self.lot = Lot.objects.create(bien=bien, designation="T2", surface=Decimal("32"))
        from .models import ModeleAnnonce

        self.modele = ModeleAnnonce.objects.create(nom="Annonce Vaugneray", lot=self.lot,
                                                   texte="Particulier loue T2 en plein centre\nEntièrement rénové")

    def test_texte_docx(self):
        import io
        import zipfile

        from .word import texte_docx

        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Ligne 1 &amp; plus</w:t></w:r></w:p>'
                                            '<w:p><w:r><w:t>Ligne 2</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(texte_docx(tampon), "Ligne 1 & plus\nLigne 2")

    def test_modele_par_defaut_et_texte(self):
        r = self.client.get(f"/admin/patrimoine/annonce/modele-du-lot/?lot={self.lot.pk}")
        self.assertEqual(r.json()["modele"], self.modele.pk)
        r = self.client.get(f"/admin/patrimoine/annonce/add/?lot={self.lot.pk}")
        self.assertContains(r, f'<option value="{self.modele.pk}" selected>')
        a = Annonce.objects.create(lot=self.lot, modele=self.modele, loyer_cc=Decimal("560"), charges=Decimal("10"))
        self.assertTrue(a.texte.startswith("Particulier loue T2 en plein centre\nEntièrement rénové\n\nLoyer : 560"))
        self.assertIn("Classe énergie", a.texte)
        sans = Annonce.objects.create(lot=self.lot, loyer_cc=Decimal("560"))
        self.assertTrue(sans.texte.startswith("À louer"))


class AnnoncePdfTests(TestCase):
    def test_pdf(self):
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_superuser("chef", "c@exemple.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI P", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="B", adresse="1 rue", code_postal="69000", ville="Lyon")
        a = Annonce.objects.create(lot=Lot.objects.create(bien=bien, designation="T2"), loyer_cc=Decimal("600"))
        r = self.client.get(f"/annonces/{a.pk}/annonce.pdf")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertContains(self.client.get("/admin/patrimoine/annonce/"), "Générer le PDF")
