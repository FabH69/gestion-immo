from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Bail, Bien, Echeance, Locataire, Lot, MouvementDepot, Paiement, Proprietaire, Rappel


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
        from .models import Candidature

        c = Candidature.objects.create(lot=self.lot)
        url = f"/candidature/{c.jeton}/"
        self.assertEqual(self.client.get(url).status_code, 200)  # sans connexion
        r = self.client.post(url, {"prenom": "Léa", "nom": "Durand", "email": "lea@example.com", "telephone": "0600000000",
                                   "situation": "cdi", "revenus_mensuels": "2400", "type_garantie": "garant"})
        self.assertContains(r, "Indiquez le nom de la personne")
        r = self.client.post(url, {"prenom": "Léa", "nom": "Durand", "email": "lea@example.com", "telephone": "0600000000",
                                   "situation": "cdi", "revenus_mensuels": "2400", "type_garantie": "garant",
                                   "garant_nom": "Paul Durand", "garant_lien": "père", "consentement": "on"})
        self.assertContains(r, "Merci Léa")
        c.refresh_from_db()
        self.assertEqual(c.statut, "recue")
        self.assertEqual(c.taux_effort(Decimal("800")), 33)
        locataire = c.creer_locataire()
        garant = locataire.garants.get()
        self.assertEqual((garant.prenom, garant.nom, garant.lien), ("Paul", "Durand", "père"))
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
        self.assertIn("30/06/2023", page)
        self.assertNotIn("PRESENT", page)
        Lot.objects.create(bien=bien, designation="Vieux", vendu=True, date_vente=date(2024, 3, 1))
        page = self.client.get("/admin/patrimoine/lot/?vendu__exact=1").content.decode()
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
        d = {"nom": "DUPONT", "prenom": "Jean", "garants-TOTAL_FORMS": "0", "garants-INITIAL_FORMS": "0"}
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
        r = self.client.post(f"/admin/patrimoine/locataire/{loc.pk}/change/", self._donnees(lot=self.lot.pk, sortie="on", date_sortie="2026-11-15"))
        self.assertEqual(r.status_code, 302, r.content.decode()[:3000])
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
        self.assertIn("Je soussigné SCI ALFAB, bailleur du bien situé au 3 rue X 69000 Lyon", texte)
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
