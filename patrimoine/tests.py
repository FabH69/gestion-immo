from datetime import date
from decimal import Decimal

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
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
        depot = c.taches.get(libelle="Rembourser la caution (dépôt de garantie)")
        self.assertEqual(depot.echeance, date(2027, 1, 19))
        self.assertEqual(c.taches.first().phase, "depart")

    def test_liste_par_defaut_des_reglages(self):
        from .models import ChangementLocataire, TacheParDefaut

        self.assertTrue(TacheParDefaut.objects.filter(libelle="Transmettre le reçu du dépôt de garantie", phase="arrivee").exists())
        TacheParDefaut.objects.filter(libelle__startswith="Organiser les visites").update(actif=False)
        TacheParDefaut.objects.create(phase="depart", libelle="Couper l'abonnement internet", reference="depart", decalage_jours=2)
        c = ChangementLocataire.objects.create(lot=self.lot, date_depart=date(2026, 12, 20))
        self.assertFalse(c.taches.filter(libelle__startswith="Organiser les visites").exists())
        self.assertEqual(c.taches.get(libelle="Couper l'abonnement internet").echeance, date(2026, 12, 22))

    def test_bloc_accueil_et_case_a_cocher(self):
        from .models import ChangementLocataire

        User.objects.create_superuser("admin", "a@a.fr", "x")
        self.client.login(username="admin", password="x")
        c = ChangementLocataire.objects.create(lot=self.lot, date_depart=date(2026, 12, 20))
        page = self.client.get("/admin/").content.decode()
        self.assertIn("tâche d&#x27;entrée ou de sortie", page)  # une ligne dans le résumé À faire de l'accueil
        self.assertNotIn("<h2>Logements", page)
        tache = c.taches.filter(faite=False, echeance__isnull=False).order_by("echeance").first()
        self.assertIn(tache.libelle.replace("'", "&#x27;"), self.client.get("/a-faire/?type=entrees").content.decode())
        r = self.client.post(f"/taches/{tache.pk}/faite/", {"next": "/a-faire/?type=entrees"})
        self.assertEqual(r["Location"], "/a-faire/?type=entrees")
        from .models import TacheParDefaut
        TacheParDefaut.objects.filter(libelle__startswith="Organiser les visites").update(actif=False)
        self.assertNotIn("Organiser les visites", self.client.get("/admin/patrimoine/tachepardefaut/").content.decode())
        self.assertIn("Organiser les visites", self.client.get("/admin/patrimoine/tachepardefaut/?active=toutes").content.decode())
        tache.refresh_from_db()
        self.assertTrue(tache.faite)

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
        # Changer d'emprunteur ou de date recharge la page : « Afficher » ne reste que sans JavaScript.
        self.assertContains(r, '<select name="proprietaire" onchange="this.form.submit()">', html=False)
        self.assertContains(r, '<noscript><button type="submit" class="button">Afficher</button></noscript>', html=False)
        self.assertContains(r, 'data-initiale="2020-01-31"')
        # Plus de sous-menu « Endettement à une date » ; la page reste accessible depuis la liste des prêts.
        self.assertNotContains(r, ">Endettement à une date</a>")
        self.assertContains(self.client.get("/admin/patrimoine/pret/"), 'href="/etats/endettement/"')
        self.assertContains(r, ">TOTAL</td>")  # tous les emprunteurs : total général gardé
        # Un seul emprunteur : sa ligne s'appelle « Total », sans son nom, et la ligne TOTAL disparaît (page et PDF).
        pk = self.pret.proprietaire_id
        r = self.client.get("/etats/endettement/", {"date": "2020-01-31", "proprietaire": pk})
        self.assertContains(r, '<td class="libelle" colspan="2">Total</td>', html=False)
        self.assertNotContains(r, ">TOTAL</td>")
        self.assertNotContains(r, f"Total {self.pret.proprietaire.nom}<")
        csv_un = self.client.get("/etats/endettement/", {"date": "2020-01-31", "proprietaire": pk, "format": "csv"}).content.decode("utf-8-sig")
        self.assertIn("\nTotal;", csv_un)
        self.assertNotIn("TOTAL", csv_un)
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

    def test_heure_facultative_du_rappel(self):
        from datetime import time
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        from .models import Rappel
        from .rappels import a_faire
        from .templatetags.tableau_bord import a_faire_groupes

        jour = date(2026, 12, 3)
        Rappel.objects.create(titre="Artisan", date=jour, heure=time(14, 30))
        Rappel.objects.create(titre="Visite", date=jour, heure=time(9, 0))
        Rappel.objects.create(titre="Sans heure", date=jour)
        items = [i for i in a_faire(date(2026, 12, 1)) if i["genre"] == "rappels"]
        # même jour : la journée d'abord, puis les rendez-vous par heure
        self.assertEqual([i["titre"] for i in items], ["Sans heure", "Visite", "Artisan"])
        self.assertEqual([i["heure_texte"] for i in items], ["", "9h", "14h30"])
        details = [g["detail"] for g in a_faire_groupes(items, date(2026, 12, 1))]
        self.assertEqual(details, ["le 03/12/2026, dans 2 jours", "le 03/12/2026 à 9h, dans 2 jours", "le 03/12/2026 à 14h30, dans 2 jours"])
        # formulaire : champ heure à côté de la date, facultatif
        page = self.client.get("/admin/patrimoine/rappel/add/").content.decode()
        self.assertIn('type="time"', page)
        reponse = self.client.post("/admin/patrimoine/rappel/add/", {
            "titre": "AG", "date": "10/12/2026", "heure": "18:15", "periodicite": "ponctuel", "categorie": "ag", "actif": "on"})
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(Rappel.objects.get(titre="AG").heure, time(18, 15))
        self.client.post("/admin/patrimoine/rappel/add/", {
            "titre": "Taxe", "date": "10/12/2026", "heure": "", "periodicite": "ponctuel", "categorie": "fiscal", "actif": "on"})
        self.assertIsNone(Rappel.objects.get(titre="Taxe").heure)
        self.assertIn("10/12/2026 à 18h15", self.client.get("/admin/patrimoine/rappel/").content.decode())
        # mail du rappel important
        Rappel.objects.create(titre="Signature", date=date(2026, 12, 5), heure=time(10, 0), important=True)
        sortie = StringIO()
        with mock.patch("patrimoine.management.commands.rappels_par_mail.timezone.localdate", return_value=date(2026, 12, 1)):
            call_command("rappels_par_mail", "--essai", stdout=sortie)
        self.assertIn("le 05/12/2026 à 10h (dans 4 jours)", sortie.getvalue())

    def test_rappel_dans_agenda(self):
        from datetime import time
        from urllib.parse import parse_qs, urlparse

        from .agenda import lien_google
        from .models import Rappel

        bien = self.lot.bien
        rdv = Rappel.objects.create(titre="AG, copro; salle 2", date=date(2026, 12, 10), heure=time(18, 15), bien=bien,
                                    notes="Apporter le pouvoir")
        ics = self.client.get(f"/rappels/{rdv.pk}/agenda.ics")
        self.assertEqual(ics["Content-Type"], "text/calendar; charset=utf-8")
        texte = ics.content.decode()
        self.assertIn("DTSTART:20261210T171500Z", texte)  # 18h15 à Paris en hiver
        self.assertIn("DTEND:20261210T181500Z", texte)
        self.assertIn("SUMMARY:AG\\, copro\\; salle 2", texte)
        self.assertIn("LOCATION:Immeuble – 1 rue\\, 69000 Lyon", texte)
        self.assertIn("TRIGGER:-PT1H", texte)
        self.assertIn("Apporter le pouvoir", texte)
        journee = Rappel.objects.create(titre="Taxe foncière", date=date(2026, 10, 15), periodicite=Rappel.Periodicite.ANNUEL)
        texte = self.client.get(f"/rappels/{journee.pk}/agenda.ics").content.decode()
        self.assertIn("DTSTART;VALUE=DATE:20261015", texte)
        self.assertIn("DTEND;VALUE=DATE:20261016", texte)
        self.assertIn("RRULE:FREQ=YEARLY", texte)
        self.assertNotIn("VALARM", texte)
        self.assertTrue(all(len(l.encode()) <= 75 for l in texte.split("\r\n")))
        params = parse_qs(urlparse(lien_google(rdv)).query)
        self.assertEqual(params["dates"], ["20261210T171500Z/20261210T181500Z"])
        self.assertEqual(params["text"], ["AG, copro; salle 2"])
        # boutons sur la fiche du rappel (pas sur l'ajout), lien 📅 dans la page À faire
        self.assertIn("Ajouter à mon agenda", self.client.get(f"/admin/patrimoine/rappel/{rdv.pk}/change/").content.decode())
        self.assertNotIn("Ajouter à mon agenda", self.client.get("/admin/patrimoine/rappel/add/").content.decode())
        self.assertIn(f"/rappels/{rdv.pk}/agenda.ics", self.client.get("/a-faire/", {"jours": "90"}).content.decode())
        # un utilisateur sans droit sur les rappels n'y a pas accès
        self.client.force_login(User.objects.create_user("invite", "i@example.com", "x", is_staff=True))
        self.assertEqual(self.client.get(f"/rappels/{rdv.pk}/agenda.ics").status_code, 403)

    def test_rappel_visible_tout_de_suite_et_mail_j_moins_x(self):
        from io import StringIO

        from django.core import mail
        from django.core.management import call_command
        from django.test import override_settings

        from .models import ParametresMail, Rappel
        from .rappels import a_faire

        ParametresMail.objects.create(adresse="moi@example.com", mot_de_passe="x")
        with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 11, 20)):
            reponse = self.client.post("/admin/patrimoine/rappel/add/?next=/admin/", {
                "titre": "Visite", "date": "27/11/2026", "heure": "10:00", "periodicite": "ponctuel", "categorie": "autre",
                "prevenir_jours": "2", "actif": "on"}, follow=True)
            page = reponse.content.decode()
            visite = Rappel.objects.get(titre="Visite")
            self.assertIn("Il est affiché sur l&#x27;accueil", page)
            self.assertIn("Mail de rappel le 25/11/2026", page)
            self.assertIn(f"/rappels/{visite.pk}/agenda.ics", page)
            # affiché tout de suite sur l'accueil et dans À faire, sans attendre J-2
            self.assertIn("Visite", [i["titre"] for i in a_faire(date(2026, 11, 20))])
            self.assertIn("Visite", page.split("Bonjour")[-1])
            self.assertIn("Visite", self.client.get("/a-faire/", {"type": "rappels"}).content.decode())
        sans_mail = Rappel.objects.create(titre="Sans mail", date=date(2026, 11, 27))
        self.assertIsNone(sans_mail.mail_a_envoyer(date(2026, 11, 27)))
        self.assertEqual(Rappel.objects.create(titre="Jour J", date=date(2026, 11, 27), prevenir_jours=0)
                         .mail_a_envoyer(date(2026, 11, 27)), "jour")
        commande = "patrimoine.management.commands.rappels_par_mail.timezone.localdate"
        with override_settings(ENVOI_MAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            for jour in (24, 25, 26, 27):
                with mock.patch(commande, return_value=date(2026, 11, jour)):
                    call_command("rappels_par_mail", stdout=StringIO())
        visites = [m for m in mail.outbox if "Visite" in m.body]
        self.assertEqual(len(visites), 1)  # un seul mail, le 25 (J-2), même s'il n'est pas important
        self.assertIn("Rappel : Visite le 27/11 à 10h", visites[0].subject)
        self.assertNotIn("Sans mail", "".join(m.body for m in mail.outbox))
        visite.refresh_from_db()
        self.assertEqual(visite.dernier_mail, date(2026, 11, 25))
        liste = self.client.get("/admin/patrimoine/rappel/").content.decode()
        self.assertIn(f"/rappels/{visite.pk}/agenda.ics", liste)

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

    def test_endettement_mensualite_a_la_date_choisie(self):
        from .etats import endettement
        from .models import LigneEcheancier, Pret

        proprio = Proprietaire.objects.create(nom="SCI M", type=Proprietaire.Type.SCI_IR)
        pret = Pret.objects.create(proprietaire=proprio, banque="CM", montant=Decimal("76767.16"), taux=Decimal("1.84"), duree_mois=12,
                                   date_premiere_echeance=date(2028, 1, 5), assurance_mensuelle=Decimal("10"))
        # Tableau de la banque : l'assurance baisse chaque année, la mensualité change donc selon la date.
        for rang in range(1, 13):
            LigneEcheancier.objects.create(pret=pret, rang=rang, date=date(2028, rang, 5), capital=Decimal("450"), interets=Decimal("10"),
                                           assurance=Decimal("11.88") if rang < 8 else Decimal("11.48"), restant=Decimal(70000 - rang * 450))
        ligne = endettement(date(2028, 8, 6), proprio)["blocs"][0]["lignes"][0]
        self.assertEqual(ligne["mensualite"], Decimal("471.48"))
        self.assertEqual(pret.mensualite_au(date(2028, 3, 6)), Decimal("471.88"))
        self.assertEqual(pret.mensualite_au(date(2027, 6, 1)), Decimal("471.88"))  # avant le début : première échéance
        page = self.client.get(f"/etats/endettement/?date=2028-08-06&proprietaire={proprio.pk}")
        self.assertContains(page, "471,48")
        excel = self.client.get(f"/etats/endettement/?date=2028-08-06&proprietaire={proprio.pk}&format=csv")
        self.assertIn("471,48", excel.content.decode("utf-8-sig"))

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
            self.assertEqual(r["Location"], "/admin/patrimoine/piececomptable/?compta=foncier&a_codifier=oui")
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
            self.assertContains(self.client.get("/admin/patrimoine/piececomptable/"), "À classer (1)")

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

    def test_liste_colonne_et_filtre_bien(self):
        from .models import Candidature

        autre_bien = Bien.objects.create(proprietaire=self.lot.bien.proprietaire, nom="Maison Chazay", adresse="2 rue", code_postal="69380", ville="Chazay")
        autre_lot = Lot.objects.create(bien=autre_bien, designation="Maison", surface=90, nb_pieces=4)
        Bien.objects.create(proprietaire=self.lot.bien.proprietaire, nom="Sans candidat", adresse="3 rue", code_postal="69000", ville="Lyon")
        Candidature.objects.create(lot=self.lot, statut="recue", prenom="Léa", nom="Durand", situation="cdi")
        Candidature.objects.create(lot=autre_lot, statut="recue", prenom="Tom", nom="Martin")
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        page = self.client.get("/admin/patrimoine/candidature/").content.decode()
        self.assertIn("column-bien", page)
        self.assertIn("Immeuble – Apt 2", page)
        self.assertNotIn("column-situation", page)
        self.assertNotIn("lot__bien__proprietaire", page)
        self.assertIn(f"?bien={autre_bien.pk}", page)
        self.assertNotIn("Sans candidat", page)
        filtre = self.client.get(f"/admin/patrimoine/candidature/?bien={autre_bien.pk}").content.decode()
        self.assertIn("Tom Martin", filtre)
        self.assertNotIn("Léa Durand", filtre)

    def test_retenir_et_preparer_le_bail(self):
        from urllib.parse import parse_qs, urlparse

        from .models import Candidature, ChangementLocataire, PieceCandidature

        self.lot.meuble = True
        self.lot.save()
        Annonce.objects.create(lot=self.lot, loyer_cc=Decimal("650"), charges=Decimal("50"), disponible_le=date(2026, 12, 1))
        c = Candidature.objects.create(lot=self.lot, statut="recue", prenom="Léa", nom="Durand", type_garantie="visale")
        PieceCandidature.objects.create(candidature=c, type="identite", fichier="pieces-identite/cni.pdf")
        PieceCandidature.objects.create(candidature=c, type="salaires", fichier="pieces-identite/bulletins.pdf")
        autre = Candidature.objects.create(lot=self.lot, statut="recue", nom="Martin")
        changement = ChangementLocataire.objects.create(lot=self.lot, date_arrivee=date(2026, 12, 1))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.assertContains(self.client.get(f"/admin/patrimoine/candidature/{c.pk}/change/"), "Retenir et préparer le bail")
        r = self.client.post(f"/admin/patrimoine/candidature/{c.pk}/retenir/")
        url = urlparse(r["Location"])
        self.assertEqual(url.path, "/admin/patrimoine/bail/add/")
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        locataire = Locataire.objects.get(nom="Durand")
        self.assertEqual((q["lot"], q["locataires"], q["type"], q["date_debut"]), (str(self.lot.pk), str(locataire.pk), "meuble", "01/12/2026"))
        self.assertEqual((Decimal(q["loyer_hc"]), Decimal(q["depot_garantie"]), q["type_garantie"]), (Decimal("600"), Decimal("1200"), "visale"))
        self.assertEqual(sorted(locataire.documents.values_list("type", flat=True)), ["identite", "justificatif"])
        autre.refresh_from_db()
        self.assertEqual(autre.statut, "refusee")
        self.assertTrue(changement.taches.get(libelle__startswith="Étudier les dossiers").faite)
        page = self.client.get(r["Location"]).content.decode()
        self.assertIn('value="600', page)

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

    def test_depot_suit_la_correction_du_loyer(self):
        from .models import Annonce

        a = Annonce.objects.create(lot=self.lot, loyer_cc=Decimal("610"), charges=Decimal("10"))
        self.assertEqual(a.depot_garantie, Decimal("1200"))  # meublé : 2 mois hors charges
        a.loyer_cc = Decimal("630")
        a.save()
        self.assertEqual(Annonce.objects.get().depot_garantie, Decimal("1240"))
        # un dépôt saisi à la main n'est pas écrasé
        a.depot_garantie = Decimal("900")
        a.save()
        a.loyer_cc = Decimal("650")
        a.save()
        self.assertEqual(Annonce.objects.get().depot_garantie, Decimal("900"))

    def test_page_modifier_annonce(self):
        from django.contrib.auth.models import User

        from .models import Annonce

        self.client.force_login(User.objects.create_superuser("chef", "c@exemple.fr", "x"))
        a = Annonce.objects.create(lot=self.lot, loyer_cc=Decimal("610"), charges=Decimal("10"))
        page = self.client.get(f"/admin/patrimoine/annonce/{a.pk}/change/").content.decode()
        self.assertIn("Loyer mensuel charges comprises", page)
        self.assertIn('value="610.00"', page)
        self.assertRegex(page, r'for="id_depot_garantie">Dépôt de garantie\s:')
        self.assertNotIn("Dépôt de garantie (€)", page)
        self.assertIn("conditions-annonce", page)
        self.assertIn("annonce_modele.js", page)

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
        self.assertEqual((garage.type, garage.nb_pieces), (Lot.Type.GARAGE, None))
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
        fait = self.client.get("/a-faire/?vue=fait").content.decode()
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
        self.assertIn("revision/%d/fait" % bail.pk, self.client.get("/a-faire/?type=revisions").content.decode().replace("rappels/", ""))
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

    def test_import_airbnb_dates_au_format_americain(self):
        from .models import ReservationLCD
        airbnb = ("Date,Arrivée au plus tard le,Type,Code de confirmation,Date de réservation,Date de début,Date de fin,Nuits,Voyageur,Logement,"
                  "Détails,Code de référence,Devise,Montant,Versé,Frais de service,Frais de Versement sur carte bancaire,Frais de ménage,"
                  "Frais de gestion,Frais divers,Revenus bruts,Taxes reversées par Airbnb,Année des revenus\n"
                  "02/03/2025,02/10/2025,Payout,,,,,,,,Transfert,G-X,EUR,,67.24,,,,,,,,\n"
                  "02/03/2025,,Réservation,HMR5A8FZ39,02/01/2025,02/01/2025,02/02/2025,1,Marion,Studio,,,EUR,67.24,,\"14,76\",,28.00,0.00,0.00,82.00,\"1,49\",2025\n"
                  "12/24/2025,,Réservation,HMDBD2ZTJC,11/01/2025,12/23/2025,12/25/2025,2,Pierre,Studio,,,EUR,140.01,,\"31,99\",,0.00,28.00,0.00,172.00,\"4,73\",2025\n")
        self.assertEqual(self.importer(airbnb)[1:], (2, 0, 1))
        r = ReservationLCD.objects.get(reference="HMR5A8FZ39")
        self.assertEqual((r.arrivee, r.depart, r.date_versement), (date(2025, 2, 1), date(2025, 2, 2), date(2025, 2, 3)))
        # Taxe de séjour sortie des frais Airbnb (comme le tableau Excel) : le net versé ne change pas.
        self.assertEqual((r.montant_brut, r.commission, r.taxe_sejour, r.net),
                         (Decimal("82.00"), Decimal("13.27"), Decimal("1.49"), Decimal("67.24")))

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
        # Seul le cumul est comparé : plus de colonnes pour le mois seul
        self.assertIn("cumul à fin 02/2025", page)
        self.assertNotIn('text-align:center">02/2025<', page)

        # « Charges mensuelles » se déplie par nature ; reprise du détail de l'Excel de suivi
        from .models import ChargeLCD
        ChargeLCD.objects.create(lot=self.lot, libelle="Charges", montant_mensuel=214)
        r = self.client.post("/location-courte-duree/?annee=2025", {"lot": self.lot.pk, "action": "charges_excel"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(ChargeLCD.objects.filter(lot=self.lot, libelle="Charges").exists())
        mois = lcd.suivi_annuel(self.lot, 2025)
        self.assertEqual(mois[0]["charges"], Decimal("211.96"))  # 80 + 29 + 25,99 + 11,90 + 32,90 + 32,17
        self.assertEqual(lcd.suivi_annuel(self.lot, 2026)[0]["charges"], Decimal("213.57"))  # taxe foncière 2026
        natures = {libelle: total for libelle, _, total in lcd.charges_par_nature(mois)}
        self.assertEqual((natures["Électricité"], natures["Eau"], natures["Internet (fibre)"]), (Decimal(960), Decimal(348), Decimal("311.88")))
        page = self.client.get(f"/location-courte-duree/?lot={self.lot.pk}&annee=2025").content.decode()
        self.assertIn('class="nature-charge" hidden><td>Électricité</td>', page)
        self.assertIn("Reprendre les charges de mon Excel de suivi", page)


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

    def test_fiche_sans_logement_ni_case_de_sortie(self):
        """Un bail se crée dans Baux › Nouveau bail ; un départ se déclare avec « Déclarer un départ »."""
        loc = Locataire.objects.create(nom="DUPONT", prenom="Jean")
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600)
        bail.locataires.add(loc)
        page = self.client.get(f"/admin/patrimoine/locataire/{loc.pk}/change/").content.decode()
        self.assertNotIn("Le locataire quitte le logement", page)
        self.assertNotIn("logement loué", page)
        self.assertIn(f"/admin/patrimoine/changementlocataire/add/?lot={self.lot.pk}&amp;bail_sortant={bail.pk}", page)
        self.assertNotIn("logement loué", self.client.get("/admin/patrimoine/locataire/add/").content.decode())
        r = self.client.post("/admin/patrimoine/locataire/add/", self._donnees(nom="NOUVEAU", lot=self.lot.pk))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Bail.objects.count(), 1)  # aucun bail créé depuis la fiche locataire

    def _depart(self, bail, **kwargs):
        from .models import ChangementLocataire

        c = ChangementLocataire.objects.create(lot=self.lot, bail_sortant=bail, date_depart=date(2026, 11, 15), **kwargs)
        return c, c.taches.get(code="cloturer_bail")

    def test_cloturer_le_bail(self):
        loc = Locataire.objects.create(nom="DUPONT", prenom="Jean")
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600, type_garantie="visale", detail_garantie="V123")
        bail.locataires.add(loc)
        c, tache = self._depart(bail, nouvelle_adresse="5 rue Neuve\n69003 Lyon")
        self.assertEqual(tache.libelle, "Clôturer le bail")
        self.assertTrue(bail.actif)  # rien ne change tant que la tâche n'est pas cochée
        r = self.client.post(f"/taches/{tache.pk}/faite/")
        self.assertEqual(r.status_code, 302)
        bail.refresh_from_db()
        loc.refresh_from_db()
        self.assertEqual((bail.actif, bail.date_fin), (False, date(2026, 11, 15)))
        self.assertEqual(loc.adresse_actuelle, "5 rue Neuve\n69003 Lyon")
        self.assertTrue(Rappel.objects.filter(titre__contains="Visale").exists())
        tache.refresh_from_db()
        tache.faite = False  # décochée : le bail est rouvert
        tache.save()
        bail.refresh_from_db()
        self.assertEqual((bail.actif, bail.date_fin), (True, None))

    def test_colocataire_qui_part(self):
        a, b = Locataire.objects.create(nom="A"), Locataire.objects.create(nom="B")
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=600)
        bail.locataires.add(a, b)
        page = self.client.get(f"/admin/patrimoine/changementlocataire/add/?lot={self.lot.pk}&bail_sortant={bail.pk}&locataire_sortant={a.pk}").content.decode()
        self.assertIn("Tout le bail", page)
        self.assertIn("Seulement A", page)
        self.assertRegex(page, rf'value="{a.pk}"[^>]*checked')
        c, tache = self._depart(bail, locataire_sortant=a, nouvelle_adresse="Ailleurs")
        tache.faite = True
        tache.save()
        bail.refresh_from_db()
        a.refresh_from_db()
        self.assertTrue(bail.actif)
        self.assertEqual(list(bail.locataires.all()), [b])
        self.assertEqual(a.adresse_actuelle, "Ailleurs")
        self.assertIn("B", tache.message)


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

    def test_relocation_annonces_avant_candidatures(self):
        page = self.client.get("/admin/").content.decode()
        relocation = page.split("Relocation</div>")[1]
        self.assertLess(relocation.index("<span>Annonces</span>"), relocation.index("<span>Candidatures</span>"))

    def test_menu_refondu(self):
        menu = self.client.get("/admin/").content.decode().split('id="barre-laterale"')[1].split("</aside>")[0]
        for groupe in ("Argent</div>", "Investir</div>", "Sites utiles</div>"):
            self.assertIn(groupe, menu)
        for ancien in ("Finances</div>", "Outils</div>", "<span>États</span>", "<span>Modèles</span>", "<span>Import Excel</span>"):
            self.assertNotIn(ancien, menu)
        self.assertIn("<span>Simulations</span>", menu)
        self.assertIn("<span>Plus-value</span>", menu)
        parametres = menu.split("<span>Paramètres</span>")[1].split("Sites utiles</div>")[0]
        for lien in ("Modèles location", "Sites utiles", "Import Excel", "Contrôles", "Balles", "Sauvegardes", "Mails (Gmail)",
                     "Utilisateurs", "Toutes les tables"):
            self.assertIn(lien, parametres)
        self.assertLess(menu.index("Investir</div>"), menu.index("<span>Paramètres</span>"))  # Paramètres tout en bas

    def test_menu_parametres_reste_allume(self):
        for url in ("/reglages/tables/", "/admin/patrimoine/modeledocument/", "/controles/", "/outils/import-excel/"):
            menu = self.client.get(url).content.decode().split('id="barre-laterale"')[1].split("</aside>")[0]
            self.assertIn("<span>Paramètres</span>", menu.split('menu-entree actif')[1].split("</a>")[0], url)

    def test_toutes_les_tables(self):
        page = self.client.get("/reglages/tables/").content.decode()
        for table in ("/admin/patrimoine/bien/", "/admin/patrimoine/piececomptable/", "/admin/auth/user/"):
            self.assertIn(table, page)

    def test_a_faire_hors_du_menu(self):
        menu = lambda url: self.client.get(url).content.decode().split('id="barre-laterale"')[1].split("</aside>")[0]
        self.assertNotIn("<span>À faire</span>", menu("/admin/"))
        actif = menu("/a-faire/").split('menu-entree actif')[1]
        self.assertIn("<span>Tableau de bord</span>", actif.split("</a>")[0])  # on sait où on est


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

    def test_factures_lmnp_dans_le_bilan_2031_et_l_apercu_du_dossier(self):
        from . import dossiers
        from .lmnp import bilan_lot
        from .models import ChargeLMNP, PieceComptable

        bien = self.lot.bien
        bien.regime_fiscal = "lmnp"
        bien.save()
        self.assertEqual(bien.comptabilite, "lmnp")
        for montant in ("200", "-50"):
            PieceComptable.objects.create(bien=bien, date=date(2026, 3, 5), fournisseur="EDF", libelle="Électricité",
                                          montant=Decimal(montant), rubrique="electricite")
        PieceComptable.objects.create(bien=bien, date=date(2026, 3, 5), fournisseur="Banque", libelle="Capital",
                                      montant=Decimal("900"), rubrique="pret")
        ChargeLMNP.objects.create(lot=self.lot, annee=2026, categorie="taxe_fonciere", montant=400)
        b = bilan_lot(self.lot, 2026)
        charges = {c["libelle"]: c["montant"] for c in b["charges"]}
        # les factures saisies du dossier LMNP comptent (avoir déduit), pas le capital remboursé ; la charge LMNP saisie aussi
        self.assertEqual(charges["Électricité"], Decimal("150"))
        self.assertEqual(charges["Taxe foncière"], Decimal("400"))
        self.assertNotIn("Rbt prêt (capital)", charges)
        self.assertEqual(b["total_charges"], Decimal("550"))
        d = next(x for x in dossiers.tous() if x["code"] == "2031")
        apercu = dict((l, m) for l, m, _ in dossiers.resume(d, 2026)["apercu"])
        self.assertEqual(apercu["Charges"], Decimal("550"))
        self.assertIn("Électricité", self.client.get("/lmnp/?annee=2026").content.decode())

    def test_charge_negative_affichee_en_plus(self):
        from .models import ChargeLMNP
        ChargeLMNP.objects.create(lot=self.lot, annee=2026, categorie="assurance", montant=Decimal("-30"))
        page = self.client.get("/lmnp/?annee=2026").content.decode()
        self.assertIn("+ 30,00 €", page)
        self.assertNotIn("− -30", page)

    def test_historique_repris_avant_les_baux(self):
        from .lmnp import bilan_lot
        from .models import EncaissementHistorique
        EncaissementHistorique.objects.create(lot=self.lot, mois=date(2026, 1, 1), montant=700, charges=50)
        EncaissementHistorique.objects.create(lot=self.lot, mois=date(2026, 2, 1), montant=650, charges=50, note="remise")
        b = bilan_lot(self.lot, 2026)
        # 2 mois repris (1 350 €) + 10 mois selon le bail (10 x 700 €)
        self.assertEqual(b["total_recettes"], Decimal("8350"))

    def _payer(self, mois, annee=2026, bail=None):
        bail = bail or self.lot.baux.get()
        for m in mois:
            e = Echeance.objects.create(bail=bail, periode=date(annee, m, 1), loyer_hc=650, charges=50,
                                        date_exigibilite=date(annee, m, 5))
            Paiement.objects.create(echeance=e, date=date(annee, m, 5), montant=Decimal("700"))

    def test_paiements_completes_par_le_suivi_repris(self):
        # suivi repris de janvier à septembre, paiements saisis dans l'appli d'octobre à décembre : les 12 mois comptent
        from .lmnp import bilan_lot
        from .models import EncaissementHistorique
        for m in range(1, 10):
            EncaissementHistorique.objects.create(lot=self.lot, mois=date(2026, m, 1), montant=700, charges=50)
        self._payer([10, 11, 12])
        b = bilan_lot(self.lot, 2026)
        self.assertEqual(b["total_recettes"], Decimal("8400"))
        self.assertEqual([r["source"] for r in b["recettes"]], ["Paiements enregistrés (3 mois)", "Suivi repris (9 mois)"])

    def test_mois_paye_et_repris_compte_une_seule_fois(self):
        from .lmnp import bilan_lot
        from .models import EncaissementHistorique
        EncaissementHistorique.objects.create(lot=self.lot, mois=date(2026, 10, 1), montant=700, charges=50)
        self._payer([10])
        self.assertEqual(bilan_lot(self.lot, 2026)["total_recettes"], Decimal("700"))

    def test_courte_duree_saisies_et_reservations_mois_par_mois(self):
        # Chasselay : encaissements saisis pour janvier, réservations importées pour mars → les deux comptent
        from .lmnp import bilan_lot
        from .models import EncaissementLCD, ReservationLCD
        bien = Bien.objects.create(proprietaire=self.lot.bien.proprietaire, nom="Chasselay", adresse="1 rue",
                                   code_postal="69380", ville="Chasselay")
        lot = Lot.objects.create(bien=bien, designation="Chasselay", meuble=True, lmnp=True, exploitation="courte")
        EncaissementLCD.objects.create(lot=lot, mois=date(2026, 1, 1), ca=Decimal("900"))
        ReservationLCD.objects.create(lot=lot, plateforme="airbnb", reference="HM1", arrivee=date(2026, 3, 4),
                                      depart=date(2026, 3, 6), montant_brut=Decimal("200"))
        b = bilan_lot(lot, 2026)
        self.assertEqual(b["total_recettes"], Decimal("1100"))

    def test_courte_duree_garde_les_loyers_du_bail_de_l_annee(self):
        # logement loué avec un bail jusqu'en juin puis passé en courte durée : les loyers du bail comptent
        from .lmnp import bilan_lot
        from .models import EncaissementLCD
        bail = self.lot.baux.get()
        bail.date_fin, bail.actif = date(2026, 6, 30), False
        bail.save()
        self._payer(range(1, 7))
        self.lot.exploitation = "courte"
        self.lot.save()
        EncaissementLCD.objects.create(lot=self.lot, mois=date(2026, 8, 1), ca=Decimal("1000"))
        b = bilan_lot(self.lot, 2026)
        self.assertEqual(b["total_recettes"], Decimal("5200"))
        page = self.client.get("/lmnp/?annee=2026").content.decode()
        self.assertIn("Paiements enregistrés (6 mois)", page)


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
            # Mot de passe sur la même ligne que l'adresse d'envoi, cases à cocher à libellé court
            page = self.client.get(f"/admin/patrimoine/parametresmail/{ParametresMail.objects.get().pk}/change/").content.decode()
            self.assertIn("field-adresse field-mot_de_passe", page)
            self.assertIn("Alerte à chaque candidature reçue", page)


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
        self.assertIn("<b>ATTENTE</b>", page)
        self.assertNotIn("<b>PAYE</b>", page)
        self.assertIn("<b>PAYE</b>", self.client.get("/admin/patrimoine/echeance/?reglement=tous").content.decode())


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
        self.assertContains(self.client.get("/admin/"), "Encaissé en")

    def test_bouton_encaisser(self):
        Echeance.generer_pour_mois(date(2026, 10, 1))
        e = Echeance.objects.get()
        liste = self.client.get("/admin/patrimoine/echeance/?periode__year=2026").content.decode()
        self.assertIn(f'formaction="/admin/patrimoine/echeance/{e.pk}/encaisser/"', liste)
        self.assertNotIn("form-encaisser", liste)  # pas de formulaire imbriqué dans celui de la liste (1er bouton inopérant)
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
        loyers_auto._dernier_jour = {}
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
        self.assertEqual(v["1"], Decimal("1000.00"))  # bail en provisions : hors charges
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
        self.assertNotContains(r, "Revenus fonciers")  # pas d'accès à la comptabilité
        self.assertNotContains(r, "Capital restant dû")
        menu = r.content.decode().split('id="barre-laterale"')[1].split("</aside>")[0]
        parametres = menu.split("<span>Paramètres</span>")[1]  # visible : l'import Excel suit le droit « Biens »
        self.assertIn("Import Excel", parametres)
        for reserve in ("Sauvegardes", "Mails (Gmail)", "Utilisateurs", "Modèles location", "Balles"):
            self.assertNotIn(reserve, parametres)

    def test_menu_parametres_vide_masque(self):
        from . import droits

        droits.appliquer(self.user, {"loyers": droits.MODIFICATION})
        menu = self.client.get("/admin/").content.decode().split('id="barre-laterale"')[1].split("</aside>")[0]
        self.assertIn("Toutes les tables", menu)  # seul lien sans droit particulier : la liste suit les droits
        self.assertNotIn("Sites utiles", menu)

    def test_toutes_les_tables_suit_les_droits(self):
        page = self.client.get("/reglages/tables/").content.decode()
        self.assertIn("Toutes les tables", page)
        self.assertIn("/admin/patrimoine/bien/", page)
        self.assertNotIn("/admin/patrimoine/piececomptable/", page)  # pas de droit sur la comptabilité

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


class RelancesTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_superuser("chef", "c@exemple.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI R", type=Proprietaire.Type.SCI_IR, ville="Lyon", iban="FR76 1234")
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="T2")
        self.bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2026, 1, 1), loyer_hc=Decimal("600"), charges=Decimal("50"))
        self.bail.locataires.add(Locataire.objects.create(civilite="M.", nom="DURAND", email="d@exemple.fr"))
        for mois in (8, 9, 10):
            Echeance.generer_pour_mois(date(2026, mois, 1))
        e = Echeance.objects.get(periode=date(2026, 8, 1))
        Paiement.objects.create(echeance=e, date=date(2026, 8, 3), montant=e.total)
        Paiement.objects.create(echeance=Echeance.objects.get(periode=date(2026, 9, 1)), date=date(2026, 9, 3), montant=Decimal("400"))

    def test_impayes_et_niveaux(self):
        from . import relances
        from .models import Relance

        lignes = relances.impayes(self.bail, date(2026, 11, 7))
        self.assertEqual([r for _, r in lignes], [Decimal("250"), Decimal("650")])
        self.assertEqual(relances.niveau_suggere(self.bail, lignes), Relance.Niveau.RAPPEL)
        relances.enregistrer(self.bail, Relance.Niveau.RAPPEL, lignes, Relance.Mode.MAIL)
        relances.enregistrer(self.bail, Relance.Niveau.RAPPEL, lignes, Relance.Mode.MAIL)  # pas de doublon le même jour
        self.assertEqual(self.bail.relances.count(), 1)
        self.assertEqual(relances.niveau_suggere(self.bail, lignes), Relance.Niveau.RELANCE)
        c = relances.courrier(self.bail, Relance.Niveau.MISE_EN_DEMEURE, lignes)
        self.assertIn("900,00 €", c["paragraphes"][1])
        self.assertIn("les loyers des mois de septembre 2026 et d'octobre 2026", c["paragraphes"][0])

    def test_pages(self):
        import email
        from email.policy import default

        r = self.client.get("/impayes/relancer/")
        self.assertContains(r, "Sep 2026, Oct 2026")
        self.assertContains(r, self.bail.lot.designation)
        r = self.client.get(f"/impayes/relancer/?bail={self.bail.pk}")
        self.assertContains(r, "Préparer le mail dans Outlook")
        r = self.client.get(f"/impayes/{self.bail.pk}/preparer-mail/?niveau=rappel")
        m = email.message_from_bytes(r.content, policy=default)
        self.assertEqual(m["To"], "d@exemple.fr")
        self.assertIn("Bonjour Monsieur Durand,", next(p for p in m.walk() if p.get_content_type() == "text/plain").get_content())
        self.assertEqual(len([p for p in m.walk() if p.get_content_type() == "application/pdf"]), 1)
        r = self.client.get(f"/impayes/{self.bail.pk}/lettre.pdf?niveau=mise_en_demeure")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(self.bail.relances.count(), 2)


class ActualiserAnnonceTests(TestCase):
    def test_bouton_actualiser(self):
        from django.contrib.auth.models import User

        from .models import ModeleAnnonce

        self.client.force_login(User.objects.create_superuser("chef", "c@exemple.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI A", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="B", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="T2")
        m = ModeleAnnonce.objects.create(nom="M", lot=lot, texte="Ancien texte")
        a = Annonce.objects.create(lot=lot, modele=m, loyer_cc=Decimal("600"))
        m.texte = "Nouveau texte"
        m.save()
        r = self.client.get(f"/admin/patrimoine/annonce/{a.pk}/change/")
        self.assertContains(r, "Actualiser avec le modèle")
        self.assertNotContains(r, "_addanother")
        donnees = {"lot": lot.pk, "modele": m.pk, "loyer_cc": "600", "charges": "0", "depot_garantie": str(a.depot_garantie),
                   "disponible_le": "", "points_forts": "", "titre": a.titre, "texte": a.texte, "_actualiser": "1"}
        r = self.client.post(f"/admin/patrimoine/annonce/{a.pk}/change/", donnees)
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertTrue(a.texte.startswith("Nouveau texte"))


class HeuresCreusesTests(TestCase):
    def test_plages_seulement_si_case_cochee(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="Apt"), type=Bail.Type.NU,
                                   date_debut=date(2025, 1, 1), loyer_hc=Decimal("600"),
                                   plages_heures_creuses="22h00 – 06h00\n\n12h30 – 14h30\n")
        self.assertEqual(bail.liste_heures_creuses, [])
        bail.heures_creuses = True
        self.assertEqual(bail.liste_heures_creuses, ["22h00 – 06h00", "12h30 – 14h30"])



class VocabulaireEtListesTests(TestCase):
    """Libellés en bon français, fils d'Ariane du menu, « Ne plus rappeler », colonnes des listes."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "", "x")
        self.client.force_login(self.admin)
        proprio = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Vaugneray", adresse="1 rue", code_postal="69670", ville="Vaugneray",
                                        prix_acquisition=Decimal("100000"))
        self.lot = Lot.objects.create(bien=self.bien, designation="Vaugneray")
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2023, 5, 10), loyer_hc=Decimal("560"), charges=10)

    def test_articles_et_messages(self):
        from . import libelles
        self.assertEqual(libelles.ajouter("annonce"), "Ajouter une annonce")
        self.assertEqual(libelles.ajouter("bail"), "Ajouter un bail")
        self.assertEqual(libelles.modifier("état des lieux"), "Modifier l'état des lieux")
        self.assertEqual(libelles.modifier("tâche"), "Modifier la tâche")
        self.assertEqual(libelles.ajouter("paramètres courte durée"), "Ajouter des paramètres courte durée")
        self.assertEqual(libelles.message("L’objet loyer «\u00a0Mai\u00a0» a été modifié avec succès. Vous pouvez l’éditer à nouveau ci-dessous."),
                         "Modifications enregistrées\u00a0: «\u00a0Mai\u00a0». Vous pouvez continuer ci-dessous.")
        self.assertEqual(libelles.message("Autre message"), "Autre message")

    def test_titres_boutons_et_fil_d_ariane(self):
        page = self.client.get("/admin/patrimoine/annonce/add/").content.decode()
        self.assertIn("<h1>Ajouter une annonce</h1>", page)
        self.assertNotIn("Ajout de annonce", page)
        page = self.client.get(f"/admin/patrimoine/bail/{self.bail.pk}/change/").content.decode()
        self.assertIn("Modifier le bail", page)
        self.assertIn("Ajouter un garant", page)
        self.assertNotIn('name="_addanother"', page)  # on ne crée jamais deux baux d'affilée
        self.assertNotIn("/admin/patrimoine/\">Patrimoine", page)
        liste = self.client.get("/admin/patrimoine/annonce/").content.decode()
        self.assertIn("Ajouter une annonce", liste)
        self.assertIn('name="_addanother"', self.client.get("/admin/patrimoine/rappel/add/").content.decode())

    def test_message_apres_enregistrement(self):
        reponse = self.client.post("/admin/patrimoine/siteutile/add/", {"nom": "IRL", "url": "https://www.insee.fr", "ordre": 0}, follow=True)
        textes = [str(m) for m in reponse.context["messages"]]
        self.assertTrue(any(t.startswith("Enregistrement effectué") for t in textes), textes)

    def test_ne_plus_rappeler_revision(self):
        from .rappels import a_faire
        jour = date(2026, 11, 9)
        self.assertIn("Révision", [i["categorie"] for i in a_faire(jour)])
        accueil = self.client.get("/a-faire/").content.decode()
        self.assertIn("Ne plus rappeler", accueil)
        self.client.post(f"/rappels/revision/{self.bail.pk}/ne-plus-rappeler/")
        self.bail.refresh_from_db()
        self.assertFalse(self.bail.rappel_revision)
        self.assertNotIn("Révision", [i["categorie"] for i in a_faire(jour)])
        self.assertIn("Assurance", [i["categorie"] for i in a_faire(jour)])
        self.client.post(f"/rappels/assurance/{self.bail.pk}/ne-plus-rappeler/")
        self.bail.refresh_from_db()
        self.assertFalse(self.bail.rappel_assurance)

    def test_colonnes_des_listes(self):
        biens = self.client.get("/admin/patrimoine/bien/").content.decode()
        self.assertIn("Rendement brut", biens)
        self.assertIn("6,7 %", biens)  # 560 × 12 / 100 000
        self.assertNotIn("column-date_vente", biens)
        Bien.objects.create(proprietaire=self.bien.proprietaire, nom="Vendu", adresse="2 rue", code_postal="69000", ville="Lyon",
                            vendu=True, date_vente=date(2024, 1, 1))
        self.assertTrue("column-date_vente" in self.client.get("/admin/patrimoine/bien/?vente=oui").content.decode())
        lots = self.client.get("/admin/patrimoine/lot/").content.decode()
        self.assertNotIn('<td class="field-bien_nom">Vaugneray</td>', lots)  # le nom du bien ne répète pas la désignation
        factures = self.client.get("/admin/patrimoine/piececomptable/").content.decode()
        self.assertIn("Déposer des factures", factures)
        self.assertNotIn("Annexe 2072</a></li>", factures)

    def test_dates_au_format_court(self):
        from django.utils import formats
        self.assertEqual(formats.date_format(date(2026, 6, 3)), "03/06/2026")


FACTURE_CONCIERGERIE = """Facture n° 2026-10-FAC999
Date de facturation : 05/10/2026
DESCRIPTION
Réservation du 4 au 6 septembre Airbnb
Honoraire 85.14 € x 20% = 17.03 €
Frais de ménage 28 €
Consommable 2 €
1
unité
47,03
€ TTC
Réservation du 22 au 23 septembre Booking
Honoraire 56.99 x 20% = 12.40 €
Frais de ménage 28
Consommable 2 €
1
unité
42,40
€ TTC
Réservation 15 a 20 septembre direct
Honoraire 305 € x 20% = 61 €
Frais de ménage 28 €
Consommable 2 €
1
unité
91,00
€ TTC
Conciergerie Exemple
Total TTC à régler
180,43
€"""


class ComptaEtConciergerieTests(TestCase):
    """Comptabilité par régime (menu, récapitulatif par rubrique) et contrôle de la facture de la conciergerie."""

    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        from .models import ParametresLCD, ReservationLCD

        prive = tempfile.mkdtemp()
        reglage = override_settings(PRIVE_ROOT=prive)
        reglage.enable()
        self.addCleanup(reglage.disable)
        self.addCleanup(shutil.rmtree, prive, True)
        proprio = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Chasselay", adresse="1 rue", code_postal="69380", ville="Chasselay",
                                        comptabilite="lmnp")
        self.lot = Lot.objects.create(bien=self.bien, designation="Studio", meuble=True, exploitation="courte")
        ParametresLCD.objects.create(lot=self.lot, menage_par_sejour=28, consommables_par_sejour=2, commission_gestion=20)
        ReservationLCD.objects.create(lot=self.lot, plateforme="airbnb", reference="HM1", arrivee=date(2026, 9, 4), depart=date(2026, 9, 6),
                                      montant_brut=Decimal("113.14"))
        ReservationLCD.objects.create(lot=self.lot, plateforme="booking", reference="B1", arrivee=date(2026, 9, 22), depart=date(2026, 9, 23),
                                      montant_brut=Decimal("100.00"), commission=Decimal("15.01"))
        ReservationLCD.objects.create(lot=self.lot, plateforme="airbnb", reference="HM2", arrivee=date(2026, 9, 27), depart=date(2026, 9, 29),
                                      montant_brut=Decimal("150.00"))
        self.client.force_login(User.objects.create_superuser("admin", "", "x"))
        self._pdfs = {}

    def pdf(self, texte=FACTURE_CONCIERGERIE, nom="facture-conciergerie.pdf"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils.html import escape
        from weasyprint import HTML

        if texte not in self._pdfs:  # le même PDF à chaque fois (WeasyPrint y inscrit l'heure de création)
            html = "".join(f"<p style='margin:0'>{escape(l)}</p>" for l in texte.splitlines())
            self._pdfs[texte] = HTML(string=html).write_pdf()
        return SimpleUploadedFile(nom, self._pdfs[texte], content_type="application/pdf")

    def test_lecture_et_controle(self):
        from . import conciergerie

        facture = conciergerie.lire(FACTURE_CONCIERGERIE)
        self.assertEqual((facture["numero"], facture["date"], facture["total_ttc"]), ("2026-10-FAC999", date(2026, 10, 5), Decimal("180.43")))
        self.assertEqual([(l["jour_arrivee"], l["plateforme"]) for l in facture["lignes"]], [(4, "airbnb"), (22, "booking"), (15, "direct")])
        r = conciergerie.controler(self.lot, facture)
        etats = [l["etat"] for l in r["lignes"]]
        self.assertEqual(etats, ["ok", "alerte", "alerte"])
        self.assertIn("Honoraire : 12,40 € facturés pour 56,99 € × 20 % = 11,40 €.", [n[1] for n in r["lignes"][1]["notes"]])
        self.assertIn("réservation directe", r["lignes"][2]["notes"][0][1])
        self.assertEqual([x.reference for x in r["non_facturees"]], ["HM2"])  # séjour du mois oublié sur la facture
        self.assertEqual(r["mois"], date(2026, 9, 1))
        self.assertEqual(r["totaux"]["ttc"], Decimal("180.43"))

    def test_depot_enregistrement_et_rapport(self):
        from . import compta
        from .models import PieceComptable

        r = self.client.post("/comptabilite/conciergerie/", {"compta": "lmnp", "lot": self.lot.pk, "fichier": self.pdf()})
        principale = PieceComptable.objects.get(rubrique="commission")
        self.assertRedirects(r, f"/comptabilite/conciergerie/{principale.pk}/")
        self.assertEqual(principale.montant, Decimal("90.43"))
        self.assertEqual({p.rubrique: p.montant for p in PieceComptable.objects.all()},
                         {"commission": Decimal("90.43"), "menage": Decimal("84.00"), "consommables": Decimal("6.00")})
        self.assertTrue(all(p.mois_concerne == date(2026, 9, 1) for p in PieceComptable.objects.all()))
        rapport = self.client.get(r["Location"]).content.decode()
        self.assertIn("2 anomalies", rapport)
        self.assertIn("ajoute la réservation directe", rapport)
        self.assertIn("Séjours du mois absents de la facture", rapport)
        # la même facture déposée une seconde fois : pas de doublon, retour au rapport
        r = self.client.post("/comptabilite/conciergerie/", {"compta": "lmnp", "lot": self.lot.pk, "fichier": self.pdf()})
        self.assertRedirects(r, f"/comptabilite/conciergerie/{principale.pk}/")
        self.assertEqual(PieceComptable.objects.count(), 3)
        # dans le récapitulatif, la facture remplace l'estimation du ménage de septembre
        rec = compta.recapitulatif("lmnp", 2026, par_mois=True)
        menage = next(l for g in rec["groupes"] for l in g["lignes"] if l["code"] == "menage")
        self.assertEqual(menage["cases"][8]["montant"], Decimal("84.00"))
        self.assertEqual(menage["cases"][8]["sources"], ["F"])

    def test_import_general_reconnait_la_conciergerie(self):
        from .models import PieceComptable

        r = self.client.post("/admin/patrimoine/piececomptable/import/", {"compta": "lmnp", "bien": self.bien.pk, "fichiers": [self.pdf()]})
        principale = PieceComptable.objects.get(rubrique="commission")
        self.assertRedirects(r, f"/comptabilite/conciergerie/{principale.pk}/")

    def test_menu_et_pages(self):
        foncier = Bien.objects.create(proprietaire=Proprietaire.objects.create(nom="SCI A", type=Proprietaire.Type.SCI_IR),
                                      nom="Immeuble", adresse="2 rue", code_postal="69000", ville="Lyon")
        from .models import PieceComptable
        PieceComptable.objects.create(bien=foncier, libelle="Sans ligne", montant=Decimal("10"), date=date(2026, 1, 1))
        PieceComptable.objects.create(bien=self.bien, libelle="Sans rubrique", montant=Decimal("10"), date=date(2026, 1, 1))
        page = self.client.get("/comptabilite/lmnp/?annee=2026").content.decode()
        self.assertIn("Comptabilité LMNP", page)
        self.assertIn('class="menu-entree actif"', page.split("Mes dossiers</span>")[0].rsplit("<a", 1)[1])
        self.assertIn("Réglages fiscaux", page)
        self.assertIn("Commission", page)
        self.assertIn("1 facture à classer", page)
        self.assertIn("Contrôle conciergerie", page)
        mensuel = self.client.get("/comptabilite/lmnp/?annee=2026&vue=mois").content.decode()
        self.assertIn("sept.", mensuel)
        self.assertEqual(self.client.get("/comptabilite/lmnp/?format=pdf")["Content-Type"], "application/pdf")
        factures = self.client.get("/admin/patrimoine/piececomptable/?compta=foncier").content.decode()
        self.assertIn("Factures · Revenus fonciers", factures)
        self.assertIn("Sans ligne", factures)
        self.assertNotIn("Sans rubrique", factures)
        self.assertNotIn("Effacer les filtres", factures)  # le choix du menu n'est pas un filtre à effacer
        for url in ("/comptabilite/2044/", "/comptabilite/societe/", "/comptabilite/conciergerie/?compta=lmnp",
                    "/admin/patrimoine/piececomptable/import/?compta=lmnp", "/lmnp/", "/admin/patrimoine/chargelmnp/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_droits(self):
        from . import droits

        aide = User.objects.create_user("aide", password="x", is_staff=True)
        droits.appliquer(aide, {"loyers": droits.CONSULTATION})
        self.client.force_login(aide)
        self.assertEqual(self.client.get("/comptabilite/lmnp/").status_code, 403)
        self.assertEqual(self.client.get("/comptabilite/conciergerie/").status_code, 403)


TABLEAU_CM = """Liste des remboursements
   Période         Capital           Capital de        Intérêts de      Assurance et frais   Terme remboursé
  05/06/2024         109 682,05            318,95              347,33                15,26               681,54
  05/07/2024         109 363,10            319,96              346,32                10,81               677,09
 17/10/2023         Déblocage                         0,00                    96325,00                   0,00                  0,00                   0,00
 05/11/2023       Échéance intérêt                  96325,00                     0,00                  190,54                  18,06                 208,60
"""
TABLEAU_CE = """ RANG ECHEANCE   MONTANT   CAPITAL   INTERETS   ACCESSOIRES   RESTANT DU   REPORTES   REPORT
  001     10/08/2025                        0,00                      0,00                0,00                    0,00            92 233,74                     0,00                  0,00
  014     05/06/2025          54,50       0,00       0,00        54,50       313 262,02        114,68       0,00
  015     05/07/2025       2 250,93     852,20   1 344,23        54,50       312 409,82          0,00       0,00
     TOTAL DE L'ANNEE 2025 :                                                           941,17               2 914,02
"""


class TableauxBanqueEtRappelsTests(TestCase):
    """Tableaux d'amortissement de la banque (lecture, remplacement du calcul) ; rappels ★ et mail ; CFE."""

    def setUp(self):
        self.proprio = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.bien = Bien.objects.create(proprietaire=self.proprio, nom="Chasselay", adresse="1 rue", code_postal="69380",
                                        ville="Chasselay", comptabilite="lmnp")
        self.client.force_login(User.objects.create_superuser("admin", "", "x"))

    def test_lecture_des_deux_presentations(self):
        from . import tableaux_amortissement as ta

        cm = ta.lire(TABLEAU_CM)
        self.assertEqual([l["date"] for l in cm], [date(2023, 11, 5), date(2024, 6, 5), date(2024, 7, 5)])  # déblocage ignoré
        self.assertEqual(cm[1]["restant"], Decimal("109363.10"))
        self.assertEqual((cm[0]["capital"], cm[0]["interets"], cm[0]["assurance"]), (Decimal(0), Decimal("190.54"), Decimal("18.06")))
        ce = ta.lire(TABLEAU_CE)
        self.assertEqual([l["rang"] for l in ce], [14, 15])  # ligne à zéro et totaux ignorés
        self.assertEqual((ce[1]["capital"], ce[1]["interets"], ce[1]["assurance"], ce[1]["restant"]),
                         (Decimal("852.20"), Decimal("1344.23"), Decimal("54.50"), Decimal("312409.82")))
        self.assertEqual(ta.anomalies(ce), ["le tableau se termine avec 312409.82 € restant dû (dernière page manquante ?)"])

    def test_le_tableau_de_la_banque_remplace_le_calcul(self):
        from . import tableaux_amortissement as ta
        from .models import Pret

        pret = Pret.objects.create(proprietaire=self.proprio, bien=self.bien, banque="CE", reference="X", montant=Decimal("1000"),
                                   taux=Decimal("1.2"), duree_mois=3, date_premiere_echeance=date(2026, 1, 5),
                                   capital_constate=Decimal("900"), capital_constate_le=date(2026, 2, 1))
        calcule = pret.echeancier()
        self.assertTrue(calcule)
        lignes = [{"rang": 1, "date": date(2025, 12, 20), "capital": Decimal(0), "interets": Decimal(0), "assurance": Decimal("300"), "restant": Decimal("1000")},
                  {"rang": 2, "date": date(2026, 1, 5), "capital": Decimal("495"), "interets": Decimal("5"), "assurance": Decimal("2"), "restant": Decimal("505")},
                  {"rang": 3, "date": date(2026, 2, 5), "capital": Decimal("505"), "interets": Decimal("3"), "assurance": Decimal("1"), "restant": Decimal("0")}]
        ta.enregistrer(pret, lignes)
        pret = Pret.objects.get(pk=pret.pk)
        self.assertEqual(pret.capital_restant_du(date(2025, 12, 1)), Decimal("1000"))
        self.assertEqual(pret.capital_restant_du(date(2026, 1, 10)), Decimal("505"))  # le recalage est ignoré
        self.assertEqual(pret.date_derniere_echeance, date(2026, 2, 5))
        self.assertEqual(pret.cumul_annee(2025)["assurance"], Decimal("300"))
        self.assertEqual(pret.cumul_annee(2026)["interets"], Decimal("8"))
        page = self.client.get(f"/admin/patrimoine/pret/{pret.pk}/change/").content.decode()
        self.assertIn("tableau de la banque", page)
        self.assertNotIn("Recalage sur la banque", page)
        self.assertEqual(self.client.get(f"/admin/patrimoine/pret/{pret.pk}/tableau/").status_code, 200)
        self.client.post(f"/admin/patrimoine/pret/{pret.pk}/tableau/", {"action": "calcul"})
        self.assertFalse(pret.lignes.exists())

    def test_recapitulatif_separe_interets_et_capital(self):
        from . import compta
        from . import tableaux_amortissement as ta
        from .models import Pret

        pret = Pret.objects.create(proprietaire=self.proprio, bien=self.bien, banque="CE", reference="Y", montant=Decimal("1000"),
                                   taux=Decimal("1.2"), duree_mois=2, date_premiere_echeance=date(2025, 1, 5))
        ta.enregistrer(pret, [
            {"rang": 1, "date": date(2025, 1, 5), "capital": Decimal("495"), "interets": Decimal("5"), "assurance": Decimal("2"), "restant": Decimal("505")},
            {"rang": 2, "date": date(2025, 2, 5), "capital": Decimal("505"), "interets": Decimal("3"), "assurance": Decimal("1"), "restant": Decimal("0")}])
        rec = compta.recapitulatif("lmnp", 2025)
        lignes = {l["code"]: l["total"] for g in rec["groupes"] for l in g["lignes"]}
        self.assertEqual(lignes["pret"], Decimal("1000"))
        self.assertEqual(lignes["interets_pret"], Decimal("8"))
        self.assertEqual(lignes["assurance_pret"], Decimal("3"))
        self.assertIn("Intérêts d&#x27;emprunt", self.client.get("/comptabilite/lmnp/?annee=2025").content.decode())

    def test_rappel_important_en_tete_et_mail(self):
        from io import StringIO

        from django.core import mail
        from django.core.management import call_command
        from django.test import override_settings

        from .models import ParametresMail
        from .rappels import a_faire

        jour = timezone.localdate()
        Rappel.objects.create(titre="Tondre", date=jour + timedelta_jours(2))
        cfe = Rappel.objects.create(titre="Payer la CFE", date=jour + timedelta_jours(8), important=True, prevenir_jours=10,
                                    bien=self.bien, periodicite=Rappel.Periodicite.ANNUEL)
        loin = Rappel.objects.create(titre="Payer plus tard", date=jour + timedelta_jours(20), important=True, prevenir_jours=10)
        items = a_faire(jour)
        # ★ en tête ; « me prévenir par mail 10 jours avant » ne masque plus le rappel à 20 jours (affiché dès 30 jours)
        self.assertEqual([i["titre"] for i in items], ["Payer la CFE", "Payer plus tard", "Tondre"])
        accueil = self.client.get("/admin/").content.decode()
        self.assertIn("★", accueil)
        self.assertIn(f"/rappels/rappel/{cfe.pk}/fait/", accueil)
        ParametresMail.objects.create(adresse="moi@example.com", mot_de_passe="x")
        with override_settings(ENVOI_MAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            call_command("rappels_par_mail", stdout=StringIO())
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn("Payer la CFE", mail.outbox[0].subject)
            self.assertNotIn("Payer plus tard", mail.outbox[0].body)
            call_command("rappels_par_mail", stdout=StringIO())  # déjà prévenu : pas de second mail avant le jour J
            self.assertEqual(len(mail.outbox), 1)
        loin.refresh_from_db()
        self.assertIsNone(loin.dernier_mail)

    def test_rappel_cfe_automatique(self):
        from .rappels import rappel_cfe

        r = rappel_cfe(self.bien, date(2026, 12, 20))
        self.assertEqual((r.date, r.important, r.prevenir_jours, r.periodicite), (date(2027, 12, 15), True, 10, "annuel"))
        self.assertIsNone(rappel_cfe(self.bien))  # pas de doublon
        foncier = Bien.objects.create(proprietaire=self.proprio, nom="Nu", adresse="1 rue", code_postal="69000", ville="Lyon",
                                      comptabilite="foncier")
        self.assertIsNone(rappel_cfe(foncier))

    def test_rappel_import_lcd_le_5_du_mois(self):
        from .rappels import a_faire, rappel_import_lcd

        lot = Lot.objects.create(bien=self.bien, designation="Studio", exploitation="courte")
        r = rappel_import_lcd(lot, date(2026, 11, 23))
        self.assertEqual((r.date, r.periodicite, r.prevenir_jours, r.dans_agenda, r.bien), (date(2026, 12, 5), "mensuel", 0, True, self.bien))
        self.assertIn("Chasselay – Studio", r.titre)
        self.assertEqual(rappel_import_lcd(lot, date(2026, 12, 3)), None)  # pas de doublon
        self.assertEqual(rappel_import_lcd(Lot.objects.create(bien=self.bien, designation="T2"), date(2026, 12, 3)), None)  # location classique
        self.assertEqual(rappel_import_lcd(Lot.objects.create(bien=self.bien, designation="T3", exploitation="courte"),
                                           date(2026, 12, 5)).date, date(2026, 12, 5))  # le 5 même : ce jour-là
        self.assertTrue(any(i["titre"] == r.titre for i in a_faire(date(2026, 12, 1))))
        r.marquer_fait()
        r.refresh_from_db()
        self.assertEqual(r.date, date(2027, 1, 5))  # coché : 5 du mois suivant
        lot.exploitation = "longue"
        lot.save()
        rappel_import_lcd(lot)
        r.refresh_from_db()
        self.assertFalse(r.actif)  # le lot quitte la courte durée : rappel arrêté
        lot.exploitation = "courte"
        lot.save()
        rappel_import_lcd(lot)
        r.refresh_from_db()
        self.assertTrue(r.actif)

    def test_rappel_import_lcd_depuis_la_fiche_lot(self):
        lot = Lot.objects.create(bien=self.bien, designation="Studio")
        from .admin.biens import LotAdmin
        from django.contrib.admin.sites import site
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        requete = RequestFactory().post("/")
        requete.user = User.objects.get(username="admin")
        requete.session = {}
        requete._messages = FallbackStorage(requete)
        lot.exploitation = "courte"
        LotAdmin(Lot, site).save_model(requete, lot, None, True)
        self.assertTrue(Rappel.objects.filter(bien=self.bien, titre__startswith="Importer les réservations", actif=True).exists())
        self.assertIn("le 5 de chaque mois", " ".join(str(m) for m in requete._messages))

    def test_rubrique_impots_et_taxes(self):
        from .compta import rubrique_de_charge
        from .models import PieceComptable

        self.assertEqual(rubrique_de_charge("CFE 2026"), "impots_taxes")
        self.assertEqual(rubrique_de_charge("Taxe foncière"), "taxe_fonciere")
        self.assertIn("impots_taxes", PieceComptable.LIBELLES_RUBRIQUES)


class ControlesTests(TestCase):
    """Page « Contrôles » : informations manquantes, « Ne pas contrôler », pastille du menu, mois sans facture / sans Booking."""

    def setUp(self):
        self.proprio = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.bien = Bien.objects.create(proprietaire=self.proprio, nom="Pollionnay", adresse="1 rue", code_postal="69290",
                                        ville="Pollionnay")
        self.lot = Lot.objects.create(bien=self.bien, designation="T2", meuble=False, surface=Decimal("45"), classe_dpe="D")
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=Decimal("700"),
                                        depot_garantie=Decimal("0"))
        self.user = User.objects.create_superuser("admin", "", "x")
        self.client.force_login(self.user)
        from django.core.cache import cache

        cache.clear()

    def resultat(self, code):
        from . import controles

        return next(r for r in controles.executer(self.user) if r["code"] == code)

    def test_page_et_lignes(self):
        self.assertEqual(self.resultat("bail_locataire")["nombre"], 1)  # bail sans locataire
        self.assertEqual(self.resultat("bail_depot")["nombre"], 1)
        self.assertEqual(self.resultat("lot_dpe")["nombre"], 0)
        Lot.objects.create(bien=self.bien, designation="Parking", type="parking")  # pas concerné par surface ni DPE
        self.assertEqual(self.resultat("lot_surface")["nombre"], 0)
        page = self.client.get("/controles/").content.decode()
        self.assertIn("Baux sans locataire", page)
        self.assertIn("Bloquent un document", page)
        self.assertIn(f"/admin/patrimoine/bail/{self.bail.pk}/change/", page)
        self.assertIn("✓ DPE", page)  # dans « Tout est bon »
        accueil = self.client.get("/admin/").content.decode()
        self.assertIn("/controles/", accueil)  # entrée du menu

    def test_ne_pas_controler_puis_retablir(self):
        from . import controles
        from .models import ControleIgnore

        avant = controles.nombre_a_traiter(self.user)
        r = self.client.post("/controles/ecarter/", {"code": "bail_depot", "cle": f"bail:{self.bail.pk}", "libelle": "T2"})
        self.assertRedirects(r, "/controles/#bail_depot", fetch_redirect_response=False)
        self.assertEqual(self.resultat("bail_depot")["nombre"], 0)
        self.assertEqual(self.resultat("bail_depot")["ignores"], 1)
        self.assertEqual(controles.nombre_a_traiter(self.user), avant - 1)
        self.client.post("/controles/ecarter/", {"code": "inconnu", "cle": "x"})  # code inconnu : ignoré
        self.assertEqual(ControleIgnore.objects.count(), 1)
        self.client.post("/controles/ecarter/", {"action": "retablir"})
        self.assertEqual(self.resultat("bail_depot")["nombre"], 1)

    def test_droits(self):
        from . import controles, droits

        aide = User.objects.create_user("aide", "", "x", is_staff=True)
        self.assertEqual(controles.executer(aide), [])  # aucun module : aucun contrôle
        droits.appliquer(aide, {"outils": droits.CONSULTATION, "biens": droits.CONSULTATION})
        aide = User.objects.get(pk=aide.pk)
        self.assertEqual({r["code"] for r in controles.executer(aide)}, {"lot_surface", "lot_dpe", "lot_dpe_expire", "lot_interdit", "lot_dpe_date", "chaudiere"})
        self.client.force_login(aide)
        self.assertEqual(self.client.get("/controles/").status_code, 200)
        self.client.post("/controles/ecarter/", {"code": "lot_dpe", "cle": "lot:1"})  # consultation seule : refusé
        from .models import ControleIgnore

        self.assertFalse(ControleIgnore.objects.exists())

    def test_mois_sans_facture_de_conciergerie_et_sans_booking(self):
        from . import controles
        from .models import ParametresLCD, PieceComptable, ReservationLCD

        self.bien.comptabilite = "lmnp"
        self.bien.save()
        lcd = Lot.objects.create(bien=self.bien, designation="Studio", type="studio", meuble=True, exploitation="courte",
                                 surface=Decimal("20"), classe_dpe="C")
        ParametresLCD.objects.create(lot=lcd, commission_gestion=Decimal("20"))
        for i, (arrivee, plateforme) in enumerate([(date(2026, 7, 3), "booking"), (date(2026, 8, 10), "airbnb"),
                                                   (date(2026, 9, 12), "booking"), (date(2026, 10, 2), "booking")]):
            ReservationLCD.objects.create(lot=lcd, plateforme=plateforme, reference=str(i), arrivee=arrivee,
                                          depart=arrivee + timedelta_jours(2))
        aujourdhui = date(2026, 11, 9)
        self.assertEqual(controles.conciergerie_manquante(aujourdhui), [])  # aucune facture encore : contrôle inactif
        PieceComptable.objects.create(bien=self.bien, rubrique="commission", fournisseur="Conciergerie du Lac",
                                      mois_concerne=date(2026, 7, 1))
        PieceComptable.objects.create(bien=self.bien, rubrique="commission", fournisseur="Conciergerie du Lac",
                                      mois_concerne=date(2026, 9, 1))
        manquants = controles.conciergerie_manquante(aujourdhui)
        # août manque ; octobre n'est réclamé qu'à partir du 13 novembre
        self.assertEqual([c for c, *_ in manquants], [f"conciergerie:{lcd.pk}:2026-08"])
        self.assertIn("août 2026", manquants[0][1])
        self.assertEqual(len(controles.conciergerie_manquante(date(2026, 11, 13))), 2)
        booking = controles.booking_manquant(aujourdhui)
        self.assertEqual([c for c, *_ in booking], [f"booking:{lcd.pk}:2026-08"])  # août : Airbnb seulement


class FichesTests(TestCase):
    """Fiches bail, locataire et lot : en-tête, actions, onglets, encart « En bref » ; dépôt Encaisser / Restituer."""

    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Vaugneray", adresse="1 rue", code_postal="69670", ville="Vaugneray")
        self.lot = Lot.objects.create(bien=self.bien, designation="Vaugneray", surface=Decimal("32"))
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2023, 5, 10), loyer_hc=Decimal("560"),
                                        charges=Decimal("10"), depot_garantie=Decimal("560"))
        self.loc = Locataire.objects.create(nom="DAGNELIE", prenom="Gael", email="g@example.com")
        self.bail.locataires.add(self.loc)
        self.client.force_login(User.objects.create_superuser("admin", "", "x"))

    def test_fiche_bail(self):
        page = self.client.get(f"/admin/patrimoine/bail/{self.bail.pk}/change/").content.decode()
        for texte in ("Bail de Vaugneray", "En bref", "Bail PDF", "Déclarer un départ", 'data-onglet="argent"', "570\u00a0€ charges comprises",
                      "Attestation non reçue", 'name="_continue"', f"/baux/{self.bail.pk}/depot/encaisser/"):
            self.assertIn(texte, page)
        self.assertNotIn("Enregistrer et continuer", page)
        ajout = self.client.get(f"/admin/patrimoine/bail/add/?lot={self.lot.pk}").content.decode()
        self.assertNotIn("onglets-fiche", ajout)  # création : formulaire d'un seul tenant

    def test_recherche_des_locataires_pour_un_bail(self):
        libre = Locataire.objects.create(nom="LIBRE")
        r = self.client.get("/admin/autocomplete/", {"app_label": "patrimoine", "model_name": "bail", "field_name": "locataires", "term": ""})
        ids = {int(x["id"]) for x in r.json()["results"]}
        self.assertIn(libre.pk, ids)
        self.assertNotIn(self.loc.pk, ids)  # déjà sur un bail en cours

    def test_depot_encaisser_puis_restituer(self):
        from .models import MouvementDepot

        r = self.client.post(f"/baux/{self.bail.pk}/depot/encaisser/", {"date": "2023-05-10", "montant": "560", "mode": "virement"})
        self.assertRedirects(r, f"/admin/patrimoine/bail/{self.bail.pk}/change/#argent", fetch_redirect_response=False)
        self.assertEqual(self.bail.depot_detenu, Decimal("560"))
        r = self.client.post(f"/baux/{self.bail.pk}/depot/restituer/", {"date": "2026-11-09", "montant": "500", "retenue": "100",
                                                                          "motif": "lampe", "mode": "virement"})
        self.assertEqual(r.status_code, 200)  # 600 € > 560 € détenus : refusé
        self.client.post(f"/baux/{self.bail.pk}/depot/restituer/", {"date": "2026-11-09", "montant": "460", "retenue": "100",
                                                                     "motif": "", "mode": "virement"})
        self.assertEqual(MouvementDepot.objects.count(), 1)  # retenue sans motif : refusé
        self.client.post(f"/baux/{self.bail.pk}/depot/restituer/", {"date": "2026-11-09", "montant": "460", "retenue": "100",
                                                                     "motif": "lampe cassée", "mode": "virement"})
        self.assertEqual(self.bail.depot_detenu, Decimal("0"))
        self.assertEqual(MouvementDepot.objects.filter(type="retenue").get().justification, "lampe cassée")

    def test_fiches_locataire_et_lot(self):
        page = self.client.get(f"/admin/patrimoine/locataire/{self.loc.pk}/change/").content.decode()
        self.assertIn("Présent", page)
        self.assertIn("Bail en cours : Vaugneray", page)
        self.assertIn("Contacter", page)
        page = self.client.get(f"/admin/patrimoine/lot/{self.lot.pk}/change/").content.decode()
        self.assertIn("Ouvrir le bail", page)
        self.assertIn("32\u00a0m²", page)
        self.assertNotIn('class="sous-titre-fiche"', page)  # plus de ligne d'infos sous le titre du lot
        self.assertIn("DPE à compléter", page)
        self.bail.actif = False
        self.bail.save()
        page = self.client.get(f"/admin/patrimoine/lot/{self.lot.pk}/change/").content.decode()
        self.assertIn(f"/admin/patrimoine/bail/add/?lot={self.lot.pk}", page)  # vacant : « Nouveau bail »
        self.assertIn("Vacant", page)


def timedelta_jours(n):
    from datetime import timedelta

    return timedelta(days=n)


class ComparatifNueTests(TestCase):
    """Comparatif location nue / courte durée : chiffres réels de la courte durée, hypothèses de location nue."""

    def setUp(self):
        from .models import ComparatifNue, EncaissementLCD, LigneComparatif

        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="H-INVEST", type=Proprietaire.Type.SARL_FAMILLE)
        bien = Bien.objects.create(proprietaire=proprio, nom="Villeurbanne", adresse="68 rue", code_postal="69100", ville="Villeurbanne")
        Lot.objects.create(bien=bien, designation="T1 bis")
        self.t2 = Lot.objects.create(bien=bien, designation="T2", exploitation="courte")
        EncaissementLCD.objects.create(lot=self.t2, mois=date(2025, 1, 1), ca=1000, commission=200, menage=50)
        EncaissementLCD.objects.create(lot=self.t2, mois=date(2025, 6, 1), ca=1400, commission=280, frais=20)
        self.c = ComparatifNue.objects.create(lot=self.t2, loyer_hc=600, charges=40)
        LigneComparatif.objects.create(comparatif=self.c, cote="deux", libelle="Taxe foncière (part)", montant_annuel=480)
        LigneComparatif.objects.create(comparatif=self.c, cote="nue", libelle="Relocation", montant_annuel=300, recuperable=True)

    def test_calcul_annee_complete(self):
        from . import comparatif as cmp

        p = cmp.periode(self.t2, self.c, 2025, 12)
        lignes = {l["libelle"]: l for l in p["lignes"]}
        self.assertEqual(lignes["Loyers encaissés"]["courte"], Decimal("2400"))
        self.assertEqual(lignes["Loyers encaissés"]["nue"], Decimal("6600"))  # 600 € × 11 mois (1 mois de vacance)
        self.assertEqual(lignes["Commissions (plateformes, conciergerie)"]["courte"], Decimal("-500"))
        self.assertEqual(lignes["Commissions (plateformes, conciergerie)"]["nue"], 0)  # supprimée en location nue
        self.assertEqual(lignes["GLI (assurance loyers impayés)"]["nue"], Decimal("-211.2"))  # 3 % de 640 € × 11 mois
        self.assertEqual(lignes["Taxe foncière (part)"]["courte"], lignes["Taxe foncière (part)"]["nue"])
        self.assertEqual(lignes["Relocation"]["nue"], Decimal("-25"))  # récupérable : à charge pendant la vacance seulement
        self.assertEqual(p["resultat"], {"courte": Decimal("1370"), "nue": Decimal("5883.8")})
        self.assertIsNone(p["pret"])  # bien à plusieurs logements : prêt non réparti
        self.assertEqual(p["perte_vacance"], Decimal("600"))

    def test_charges_recuperees_en_partie(self):
        from . import comparatif as cmp

        self.c.traitements = {"copro": ["partielle", 50]}
        n = cmp.location_nue(self.c, {"charges": {"copro": Decimal("1200"), "eau": Decimal("120"), "pno": Decimal("90")}}, 12)
        self.assertEqual(n["charges"], {"copro": Decimal("650"), "eau": Decimal("10"), "pno": Decimal("90")})

    def test_page_et_enregistrement(self):
        url = f"/location-courte-duree/comparatif/?lot={self.t2.pk}&annee=2026"
        page = self.client.get(url).content.decode()
        for texte in ("Hypothèses de location nue", "La location nue rapporterait", 'name="t_menage"', "Taxe foncière (part)"):
            self.assertIn(texte, page)
        donnees = {"lot": self.t2.pk, "loyer_hc": "550", "charges": "40", "vacance_mois": "2", "gli": "0", "notes": "",
                   "t_menage": "charge", "t_commission": "partielle", "p_commission": "30",
                   "lignes-TOTAL_FORMS": "3", "lignes-INITIAL_FORMS": "2", "lignes-MIN_NUM_FORMS": "0", "lignes-MAX_NUM_FORMS": "1000"}
        for i, l in enumerate(self.c.lignes.all()):
            donnees.update({f"lignes-{i}-id": l.pk, f"lignes-{i}-comparatif": self.c.pk, f"lignes-{i}-cote": l.cote,
                            f"lignes-{i}-libelle": l.libelle, f"lignes-{i}-montant_annuel": l.montant_annuel})
        donnees["lignes-1-DELETE"] = "on"
        donnees.update({"lignes-2-cote": "courte", "lignes-2-libelle": "Mobilier", "lignes-2-montant_annuel": "600"})
        r = self.client.post(url, donnees)
        self.assertEqual(r.status_code, 302)
        self.c.refresh_from_db()
        self.assertEqual((self.c.loyer_hc, self.c.vacance_mois, self.c.gli), (Decimal("550"), Decimal("2"), Decimal("0")))
        self.assertEqual(self.c.traitement("menage")[0], "charge")
        self.assertEqual(self.c.traitement("commission"), ("partielle", Decimal("30")))
        self.assertEqual(sorted(self.c.lignes.values_list("libelle", flat=True)), ["Mobilier", "Taxe foncière (part)"])
        page = self.client.get(url).content.decode()
        self.assertNotIn("GLI (assurance", page)  # GLI à 0 : ligne retirée


class UneSeuleFaconTests(TestCase):
    """Page « À faire » (une seule liste) et dépôts de garantie en lecture."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Vaugneray", adresse="1 rue", code_postal="69670", ville="Vaugneray")
        self.lot = Lot.objects.create(bien=bien, designation="Vaugneray")
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2023, 5, 10), loyer_hc=560, depot_garantie=560)

    def test_page_a_faire(self):
        Rappel.objects.create(titre="AG copropriété", date=timezone.localdate())
        page = self.client.get("/a-faire/").content.decode()
        for texte in ("AG copropriété", "Mes rappels", "Entrées et sorties", "+ Rappel"):
            self.assertIn(texte, page)
        self.assertIn('href="/a-faire/"', self.client.get("/admin/").content.decode())  # accès par le bloc À faire de l'accueil
        self.assertIn("AG copropriété", self.client.get("/a-faire/?type=rappels").content.decode())
        self.assertNotIn("AG copropriété", self.client.get("/a-faire/?type=loyers").content.decode())

    def test_depots_en_lecture(self):
        MouvementDepot.objects.create(bail=self.bail, date=date(2023, 5, 10), montant=560)
        liste = self.client.get("/admin/patrimoine/mouvementdepot/").content.decode()
        self.assertIn(f"/admin/patrimoine/bail/{self.bail.pk}/change/#argent", liste)
        self.assertEqual(self.client.get("/admin/patrimoine/mouvementdepot/add/").status_code, 403)

    def test_recherche_globale_apercu(self):
        loc = Locataire.objects.create(nom="DAGNELIE", prenom="Gael")
        self.bail.locataires.add(loc)
        Echeance.objects.filter(bail=self.bail).delete()
        mois = timezone.localdate().replace(day=1)
        Echeance.objects.create(bail=self.bail, periode=mois, loyer_hc=560, charges=10, date_exigibilite=mois)
        r = self.client.get("/recherche/", {"q": "dagn", "apercu": "1"}).json()
        self.assertEqual([g["titre"] for g in r["groupes"]], ["Locataires", "Baux", "Loyers"])
        self.assertEqual(r["groupes"][0]["lignes"][0]["detail"], "Vaugneray · bail en cours")
        self.assertIn("570 €", r["groupes"][2]["lignes"][0]["titre"])
        self.assertEqual(r["tout"], "/recherche/?q=dagn")
        liste = self.client.get("/admin/patrimoine/echeance/").content.decode()
        self.assertIn('id="recherche-globale"', liste)  # la recherche globale reste en haut des listes
        self.assertIn("Filtrer ces loyers", liste)


class EtatDesLieuxTelephoneTests(TestCase):
    """État des lieux pièce par pièce : photos horodatées, signatures, verrouillage, sortie comparée à l'entrée."""

    def setUp(self):
        import tempfile

        from django.contrib.auth.models import User
        self.dossier = tempfile.mkdtemp()
        reglage = self.settings(PRIVE_ROOT=self.dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI EDL", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(nom="Immeuble E", proprietaire=proprio, adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="T2", nb_pieces=2, meuble=True, loyer_propose=650)
        self.bail = Bail.objects.create(lot=lot, type=Bail.Type.MEUBLE, date_debut=date(2025, 1, 1), loyer_hc=600)
        self.bail.locataires.add(Locataire.objects.create(nom="DURAND", prenom="Léa"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dossier, ignore_errors=True)

    def _photo(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        tampon = io.BytesIO()
        image = Image.new("RGB", (2400, 1600), (200, 180, 150))
        exif = image.getexif()
        exif[306] = "2026:11:11 10:15:00"
        image.save(tampon, "JPEG", exif=exif)
        return SimpleUploadedFile("photo.jpg", tampon.getvalue(), content_type="image/jpeg")

    def _signer(self, edl, n_etapes):
        signature = "data:image/png;base64," + "A" * 400
        url = f"/etats-des-lieux/{edl.pk}/saisie/?etape={n_etapes - 1}"
        self.client.post(url, {"qui": "bailleur", "signature": signature})
        return self.client.post(url, {"qui": "locataire", "signature": signature})

    def test_entree_puis_sortie_comparee(self):
        from .models import ElementEDL, EtatDesLieux
        from . import edl as logique
        r = self.client.post(f"/baux/{self.bail.pk}/etat-des-lieux/entree/", {"signataire_bailleur": "SCI EDL"})
        entree = EtatDesLieux.objects.get(type="entree")
        self.assertRedirects(r, f"/etats-des-lieux/{entree.pk}/saisie/?etape=1", fetch_redirect_response=False)
        pieces = logique.pieces(entree)
        self.assertEqual([p for p, _ in pieces], ["Entrée", "Séjour", "Cuisine", "Chambre", "Salle de bains", "WC", logique.PIECE_INVENTAIRE])
        sol = pieces[0][1][0]
        page = self.client.get(f"/etats-des-lieux/{entree.pk}/saisie/?etape=1").content.decode()
        self.assertIn('capture="environment"', page)
        self.client.post(f"/etats-des-lieux/{entree.pk}/saisie/?etape=1",
                         {f"etat-{sol.pk}": "tb", f"commentaire-{sol.pk}": "Parquet neuf", f"photos-{sol.pk}": self._photo(), "action": "photos"})
        sol.refresh_from_db()
        self.assertEqual((sol.etat, sol.commentaire), ("tb", "Parquet neuf"))
        photo = sol.photos.get()
        self.assertEqual(len(photo.empreinte), 64)
        self.assertEqual(timezone.localtime(photo.prise_le).hour, 10)
        self.assertLessEqual(max(photo.image.width, photo.image.height), logique.TAILLE_PHOTO)
        self.assertTrue(photo.image.path.startswith(self.dossier))  # hors du dossier public
        n = len(logique.pieces(entree)) + 3
        r = self._signer(entree, n)
        self.assertRedirects(r, f"/etats-des-lieux/{entree.pk}/", fetch_redirect_response=False)
        entree.refresh_from_db()
        self.assertTrue(entree.signe and entree.bailleur_signe_le and entree.empreinte)
        self.assertTrue(logique.integre(entree))
        self.assertIn("Document intact", self.client.get(f"/etats-des-lieux/{entree.pk}/").content.decode())
        # verrouillé : la saisie renvoie vers le résumé, la fiche n'est plus modifiable
        self.assertRedirects(self.client.get(f"/etats-des-lieux/{entree.pk}/saisie/"), f"/etats-des-lieux/{entree.pk}/", fetch_redirect_response=False)
        self.client.post(f"/etats-des-lieux/{entree.pk}/saisie/?etape=1", {f"etat-{sol.pk}": "m"})
        sol.refresh_from_db()
        self.assertEqual(sol.etat, "tb")
        self.assertEqual(self.client.post(f"/admin/patrimoine/etatdeslieux/{entree.pk}/change/", {}).status_code, 403)
        # toute retouche se voit
        ElementEDL.objects.filter(pk=sol.pk).update(commentaire="retouché")
        self.assertFalse(logique.integre(entree))
        ElementEDL.objects.filter(pk=sol.pk).update(commentaire="Parquet neuf")
        pdf = self.client.get(f"/etats-des-lieux/{entree.pk}/document.pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")

        # sortie : même liste, état d'entrée rappelé, dégradations relevées
        self.client.post(f"/baux/{self.bail.pk}/etat-des-lieux/sortie/", {})
        sortie = EtatDesLieux.objects.get(type="sortie")
        sol_sortie = sortie.elements.get(piece="Entrée", nom="Sol")
        self.assertEqual((sol_sortie.etat_entree, sol_sortie.commentaire_entree), ("tb", "Parquet neuf"))
        self.assertEqual(sortie.elements.count(), entree.elements.count())
        page = self.client.get(f"/etats-des-lieux/{sortie.pk}/saisie/?etape=1").content.decode()
        self.assertIn("À l'entrée", page)
        self.assertIn(photo.image.name, page)
        self.client.post(f"/etats-des-lieux/{sortie.pk}/saisie/?etape=1", {f"etat-{sol_sortie.pk}": "u", f"commentaire-{sol_sortie.pk}": "Rayures"})
        self.assertEqual([e.pk for e in logique.differences(sortie)], [sol_sortie.pk])
        self.assertIn("1 élément dégradé", self.client.get(f"/etats-des-lieux/{sortie.pk}/saisie/?etape={n - 1}").content.decode())
        # reprendre la sortie depuis le bail ne crée pas un deuxième brouillon
        self.client.get(f"/baux/{self.bail.pk}/etat-des-lieux/sortie/")
        self.assertEqual(EtatDesLieux.objects.filter(type="sortie").count(), 1)

    def test_locataire_ne_signe_pas_avant_le_bailleur(self):
        from .models import EtatDesLieux
        self.client.post(f"/baux/{self.bail.pk}/etat-des-lieux/entree/", {})
        edl = EtatDesLieux.objects.get()
        n = edl.elements.values("piece").distinct().count() + 3
        self.client.post(f"/etats-des-lieux/{edl.pk}/saisie/?etape={n - 1}", {"qui": "locataire", "signature": "data:image/png;base64," + "A" * 400})
        edl.refresh_from_db()
        self.assertFalse(edl.signe)
        self.client.post(f"/etats-des-lieux/{edl.pk}/saisie/?etape={n - 1}", {"qui": "bailleur", "signature": "pas une image"})
        edl.refresh_from_db()
        self.assertFalse(edl.signature_bailleur)

    def test_ajouter_et_retirer_une_piece(self):
        from .models import EtatDesLieux
        self.client.post(f"/baux/{self.bail.pk}/etat-des-lieux/entree/", {})
        edl = EtatDesLieux.objects.get()
        url = f"/etats-des-lieux/{edl.pk}/saisie/?etape=0"
        self.client.post(url, {"action": "ajouter_piece", "nouvelle_piece": "Cave"})
        self.assertTrue(edl.elements.filter(piece="Cave").exists())
        self.client.post(url, {"action": "retirer_piece:Cave"})
        self.assertFalse(edl.elements.filter(piece="Cave").exists())
        self.assertIn("/etat-des-lieux/entree/", self.client.get(f"/admin/patrimoine/bail/{self.bail.pk}/change/").content.decode())


ICS_AIRBNB = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
DTEND;VALUE=DATE:20261116\r
DTSTART;VALUE=DATE:20261113\r
UID:aaa111@airbnb.com\r
DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/de\r
 tails/HMTEST1234\r
SUMMARY:Reserved\r
END:VEVENT\r
BEGIN:VEVENT\r
DTEND;VALUE=DATE:20261203\r
DTSTART;VALUE=DATE:20261201\r
UID:ccc333@airbnb.com\r
SUMMARY:Airbnb (Not available)\r
END:VEVENT\r
END:VCALENDAR\r
"""


class GererCourteDureeTests(TestCase):
    """Courte durée › Gérer mon bien : calendriers .ics, occupation, indicateurs."""

    def setUp(self):
        from .models import CalendrierICS
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI LCD", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(nom="Maison", proprietaire=proprio, adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Studio", exploitation="courte")
        self.cal = CalendrierICS.objects.create(lot=self.lot, nom="Airbnb", plateforme="airbnb")

    def test_lecture_et_import(self):
        from . import ical
        evenements = ical.lire(ICS_AIRBNB)
        self.assertEqual([(e["debut"], e["fin"]) for e in evenements], [(date(2026, 11, 13), date(2026, 11, 16)), (date(2026, 12, 1), date(2026, 12, 3))])
        with self.settings(USE_TZ=True):
            ical.importer(self.cal, ICS_AIRBNB)
        reserve, bloque = self.cal.evenements.order_by("debut")
        self.assertEqual((reserve.reference, reserve.bloque), ("HMTEST1234", False))
        self.assertTrue(bloque.bloque)
        with self.assertRaises(ValueError):
            ical.lire("pas un calendrier")

    def test_annulation_et_historique(self):
        from . import ical
        from .models import EvenementICS
        EvenementICS.objects.create(calendrier=self.cal, uid="ancien", debut=date(2020, 1, 1), fin=date(2020, 1, 3))
        EvenementICS.objects.create(calendrier=self.cal, uid="annule", debut=date(2099, 1, 1), fin=date(2099, 1, 3))
        ical.importer(self.cal, ICS_AIRBNB)
        uids = set(self.cal.evenements.values_list("uid", flat=True))
        self.assertIn("ancien", uids)  # séjour passé gardé
        self.assertNotIn("annule", uids)  # séjour futur disparu du calendrier = annulé
        ical.importer(self.cal, ICS_AIRBNB)
        self.assertEqual(self.cal.evenements.count(), 3)  # pas de doublon en réimportant

    def test_occupation_sans_doublon_avec_les_reservations(self):
        from . import ical
        from .models import ReservationLCD
        ical.importer(self.cal, ICS_AIRBNB)
        ReservationLCD.objects.create(lot=self.lot, plateforme="airbnb", reference="HMTEST1234", voyageur="Léa",
                                      arrivee=date(2026, 11, 13), depart=date(2026, 11, 16), montant_brut=300)
        liste = ical.sejours(self.lot, date(2026, 11, 1), date(2026, 12, 31))
        self.assertEqual([s["titre"] for s in liste], ["Léa", "Bloqué"])
        self.assertEqual(ical.nuits_occupees(liste, date(2026, 11, 1), date(2026, 12, 31)), 3)  # les dates bloquées ne comptent pas
        semaines, _ = ical.mois_calendrier(self.lot, 2026, 11)
        jours = {j["date"]: j for s in semaines for j in s}
        self.assertEqual(jours[date(2026, 11, 13)]["sejour"]["titre"], "Léa")
        self.assertIsNone(jours[date(2026, 11, 16)]["sejour"])
        self.assertEqual(jours[date(2026, 11, 16)]["gauche"]["titre"], "Léa")  # jour du départ
        k = ical.indicateurs(self.lot, date(2026, 11, 10))
        self.assertEqual((k["nuits_mois"], k["occupation_mois"]), (3, 10))
        self.assertEqual(k["prochaine_arrivee"]["titre"], "Léa")
        ReservationLCD.objects.create(lot=self.lot, plateforme="booking", reference="B1", arrivee=date(2026, 3, 1), depart=date(2026, 3, 3),
                                      montant_brut=200, commission=30, frais_service=4, taxe_sejour=6)
        k = ical.indicateurs(self.lot, date(2026, 11, 10))
        self.assertEqual((k["commissions"], k["taxe"]), (34, 6))
        self.assertAlmostEqual(float(k["commissions_pct"]), 34 * 100 / 500)

    def test_pages_et_roue_crantee(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import CalendrierICS
        page = self.client.get(f"/courte-duree/?lot={self.lot.pk}").content.decode()
        self.assertIn("Commissions plateformes", page)
        self.assertIn(f"/courte-duree/calendriers/?lot={self.lot.pk}", page)
        self.assertEqual(self.client.get(f"/courte-duree/calendrier/?lot={self.lot.pk}&mois=2026-11").status_code, 200)
        self.assertEqual(self.client.get(f"/courte-duree/calendrier/?lot={self.lot.pk}&mois=n'importe").status_code, 200)
        fichier = SimpleUploadedFile("booking.ics", ICS_AIRBNB.encode(), content_type="text/calendar")
        self.client.post("/courte-duree/calendriers/", {"lot": self.lot.pk, "action": "ajouter", "plateforme": "booking", "nom": "Booking", "fichier": fichier})
        booking = CalendrierICS.objects.get(nom="Booking")
        self.assertEqual(booking.evenements.count(), 2)
        self.client.post("/courte-duree/calendriers/", {"lot": self.lot.pk, "action": "ajouter", "plateforme": "airbnb", "url": "javascript:alert(1)"})
        self.assertEqual(CalendrierICS.objects.count(), 2)
        self.client.post("/courte-duree/calendriers/", {"lot": self.lot.pk, "action": "pause", "calendrier": booking.pk})
        booking.refresh_from_db()
        self.assertFalse(booking.actif)
        self.client.post("/courte-duree/calendriers/", {"lot": self.lot.pk, "action": "supprimer", "calendrier": booking.pk})
        self.assertFalse(CalendrierICS.objects.filter(pk=booking.pk).exists())

    def test_lien_injoignable(self):
        from unittest import mock
        from . import ical
        self.cal.url = "https://exemple.invalid/cal.ics"
        with mock.patch("patrimoine.ical.telecharger", side_effect=OSError("refusé")):
            ok, message = ical.synchroniser(self.cal)
        self.assertFalse(ok)
        self.assertIn("échec", message)
        with mock.patch("patrimoine.ical.telecharger", return_value=ICS_AIRBNB):
            ok, _ = ical.synchroniser(self.cal)
        self.assertTrue(ok)
        self.assertEqual(self.cal.evenements.count(), 2)

    def test_logement_passe_en_courte_duree_garde_ses_loyers(self):
        """Passer un lot en courte durée ne fait pas disparaître ses loyers classiques du suivi des encaissements."""
        from . import encaissements
        bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2024, 1, 1), date_fin=date(2025, 6, 30), loyer_hc=500, actif=False)
        courte, longue = encaissements.lots_du_proprietaire(self.lot.bien.proprietaire, 2025)
        self.assertIn(self.lot, courte)
        self.assertIn(self.lot, longue)
        janvier = encaissements.suivi(self.lot.bien.proprietaire, 2025)["mois"][0]
        self.assertEqual(janvier["total_loyers"], 500)
        self.assertNotIn(self.lot, encaissements.lots_du_proprietaire(self.lot.bien.proprietaire, 2026)[1])
        self.assertTrue(Bail.objects.filter(pk=bail.pk).exists())


class ModeTestTests(TransactionTestCase):
    """Bouton « Mode test » : base séparée, vierge, jamais mêlée à la vraie."""
    databases = {"default", "essai"}

    def setUp(self):
        from . import loyers_auto
        loyers_auto._dernier_jour = {}
        self.admin = User.objects.create_superuser("fabien", "f@exemple.fr", "x")
        Proprietaire.objects.create(nom="Vraie SCI", type=Proprietaire.Type.SCI_IR)
        self.client.force_login(self.admin)

    def tearDown(self):
        from . import base_essai
        base_essai._modele_vierge().unlink(missing_ok=True)

    def _entrer(self):
        r = self.client.post("/mode-test/entrer/")
        self.assertEqual(r.cookies["base"].value, "essai")
        return r

    def test_base_separee_vierge_et_reconnaissable(self):
        self._entrer()
        page = self.client.get("/admin/patrimoine/proprietaire/").content.decode()
        self.assertIn("bandeau-test", page)  # bandeau et couleurs orange
        self.assertIn("#9a3412", page)
        self.assertNotIn("Vraie SCI", page)  # vierge : aucune vraie donnée visible
        self.client.post("/admin/patrimoine/proprietaire/add/", {"nom": "SCI d'essai", "type": "sci_ir", "pourcentage_detention": "100", "biens-TOTAL_FORMS": "0", "biens-INITIAL_FORMS": "0"})
        self.assertEqual(list(Proprietaire.objects.using("essai").values_list("nom", flat=True)), ["SCI d'essai"])
        self.assertEqual(list(Proprietaire.objects.using("default").values_list("nom", flat=True)), ["Vraie SCI"])
        self.assertFalse(Echeance.objects.using("default").exists())
        # retour aux vraies données : même session, pas besoin de se reconnecter
        r = self.client.post("/mode-test/sortir/")
        self.assertEqual(r.cookies["base"].value, "")
        page = self.client.get("/admin/patrimoine/proprietaire/").content.decode()
        self.assertIn("Vraie SCI", page)
        self.assertNotIn("bandeau-test", page)
        self.assertNotIn("SCI d'essai", page)

    def test_remise_a_zero_et_demo(self):
        self._entrer()
        self.client.post("/admin/patrimoine/proprietaire/add/", {"nom": "SCI d'essai", "type": "sci_ir", "pourcentage_detention": "100", "biens-TOTAL_FORMS": "0", "biens-INITIAL_FORMS": "0"})
        self.client.post("/mode-test/remettre-a-zero/")
        self.assertFalse(Proprietaire.objects.using("essai").exists())
        self.assertEqual(Proprietaire.objects.using("default").count(), 1)
        self.client.post("/mode-test/donnees-demo/")
        self.assertTrue(Proprietaire.objects.using("essai").filter(nom="SCI Démo 1").exists())
        self.assertFalse(Proprietaire.objects.using("default").filter(nom="SCI Démo 1").exists())

    def test_ni_mail_ni_compte_modifie(self):
        from django.core import mail

        from . import mails
        from .models import ParametresMail
        self._entrer()
        reglage = ParametresMail(adresse="moi@gmail.com", mot_de_passe="secret")
        with self.settings(ENVOI_MAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"):
            from . import base_essai
            with base_essai.utiliser("essai"):
                self.assertTrue(mails.envoyer(["locataire@exemple.fr"], "Quittance", "texte", reglage_mail=reglage))
        self.assertEqual(len(mail.outbox), 1)  # gardé en mémoire, jamais envoyé
        self.client.post(f"/admin/auth/user/{self.admin.pk}/change/", {"username": "pirate"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.username, "fabien")

    def test_reserve_aux_administrateurs(self):
        from django.contrib.auth.models import Permission
        u = User.objects.create_user("aide", password="x", is_staff=True)
        u.user_permissions.set(Permission.objects.filter(codename="view_proprietaire"))
        self.client.force_login(u)
        r = self.client.post("/mode-test/entrer/")
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("base", r.cookies)
        self.assertNotIn("Mode test", self.client.get("/admin/").content.decode())


class ScissionFactureTests(TestCase):
    """Une facture de copropriété scindée : appel de fonds (ligne 13) + régularisation de l'exercice précédent (ligne 14)."""

    def setUp(self):
        from decimal import Decimal

        from .models import PieceComptable

        proprio = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon",
                                        comptabilite="foncier")
        self.autre = Bien.objects.create(proprietaire=proprio, nom="Studio", adresse="2 rue", code_postal="69000", ville="Lyon",
                                         comptabilite="foncier")
        self.piece = PieceComptable.objects.create(bien=self.bien, date=date(2026, 4, 1), fournisseur="Syndic", libelle="Appel T2",
                                                   montant=Decimal("1000.00"), ligne="13")
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.url = f"/admin/patrimoine/piececomptable/{self.piece.pk}/scinder/"

    def _poster(self, lignes):
        data = {"nb": len(lignes)}
        for i, (bien, code, montant) in enumerate(lignes):
            data.update({f"bien_{i}": bien.pk, f"code_{i}": code, f"montant_{i}": montant})
        return self.client.post(self.url, data)

    def test_bouton_dans_les_listes(self):
        from .models import PieceComptable

        PieceComptable.objects.filter(pk=self.piece.pk).update(preremplie=True)
        for suite in ("", "&a_codifier=oui"):
            self.assertContains(self.client.get("/admin/patrimoine/piececomptable/?compta=foncier" + suite), self.url)

    def test_ecran_et_somme_obligatoire(self):
        self.assertContains(self.client.get(self.url), "Reste à répartir")
        r = self._poster([(self.bien, "13", "800"), (self.bien, "14", "100")])
        self.assertContains(r, "doit être égale au total")
        self.assertEqual(self.piece.parts.count(), 0)

    def test_scinder_puis_annexe_2072(self):
        from decimal import Decimal

        from .annexe2072 import colonne

        r = self._poster([(self.bien, "13", "800,00"), (self.bien, "14", "150"), (self.autre, "13", "50")])
        self.assertEqual(r.status_code, 302)
        self.piece.refresh_from_db()
        self.assertEqual((self.piece.montant, self.piece.ligne), (Decimal("800.00"), "13"))
        parts = list(self.piece.parts.order_by("pk"))
        self.assertEqual([(p.bien, p.ligne, p.montant) for p in parts],
                         [(self.bien, "14", Decimal("150.00")), (self.autre, "13", Decimal("50.00"))])
        self.assertEqual(parts[0].fournisseur, "Syndic")
        v = colonne(self.bien, 2026)["valeurs"]
        self.assertEqual((v["13"], v["14"]), (Decimal("800.00"), Decimal("150.00")))
        self.assertEqual(colonne(self.autre, 2026)["valeurs"]["13"], Decimal("50.00"))
        # retour à deux lignes : la part en trop est supprimée
        self._poster([(self.bien, "13", "850"), (self.bien, "14", "150")])
        self.assertEqual(self.piece.parts.count(), 1)


class VirementRepartiTests(TestCase):
    """Un virement qui règle plusieurs mois, un paiement partiel, un trop-perçu gardé en avance."""

    def setUp(self):
        from .models import Versement

        self.Versement = Versement
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI V", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=bien, designation="T2")
        self.bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2026, 1, 1), loyer_hc=Decimal("750"),
                                        charges=Decimal("50"), jour_echeance=5)
        self.bail.locataires.add(Locataire.objects.create(nom="Durand", email="d@exemple.fr"))
        for mois in (9, 10, 11):
            Echeance.generer_pour_mois(date(2026, mois, 1))
        self.sept, self.oct, self.nov = Echeance.objects.order_by("periode")
        Paiement.objects.create(echeance=self.sept, date=date(2026, 9, 5), montant=Decimal("800"))

    def _envoyer(self, etape, montant, parts=None):
        donnees = {"etape": etape, "bail": self.bail.pk, "date": "2026-11-12", "montant": montant, "mode": "virement", "notes": ""}
        donnees.update(parts or {})
        return self.client.post("/admin/patrimoine/echeance/virement/", donnees)

    def test_repartition_proposee_du_plus_ancien_au_plus_recent(self):
        parts = self.Versement.repartition_proposee(self.bail, Decimal("1200"))
        self.assertEqual([(e.periode.month, p) for e, p in parts], [(10, Decimal("800")), (11, Decimal("400"))])

    def test_page_et_apercu(self):
        r = self.client.get("/admin/patrimoine/echeance/")
        self.assertContains(r, "Encaisser un virement")
        r = self._envoyer("repartir", "1600")
        self.assertContains(r, f'name="part-{self.oct.pk}" value="800,00"')
        self.assertContains(r, f'name="part-{self.nov.pk}" value="800,00"')
        self.assertEqual(self.Versement.objects.count(), 0)  # rien n'est enregistré avant de valider

    def test_un_virement_regle_deux_mois(self):
        r = self._envoyer("valider", "1600", {f"part-{self.oct.pk}": "800", f"part-{self.nov.pk}": "800,00"})
        self.assertEqual(r.status_code, 302)
        v = self.Versement.objects.get()
        self.assertEqual(v.paiements.count(), 2)
        self.assertEqual(v.avance, 0)
        for e in (self.oct, self.nov):
            e.refresh_from_db()
            self.assertEqual(e.statut, Echeance.Statut.PAYE)
            self.assertEqual(e.paiements.get().date, date(2026, 11, 12))
        # supprimer le virement retire ses deux parts et remet les loyers à payer
        v.delete()
        self.oct.refresh_from_db()
        self.assertEqual(self.oct.statut, Echeance.Statut.A_PAYER)
        self.assertEqual(Paiement.objects.count(), 1)

    def test_repartition_corrigee_et_partiel(self):
        self._envoyer("valider", "1000", {f"part-{self.oct.pk}": "800", f"part-{self.nov.pk}": "200"})
        self.nov.refresh_from_db()
        self.assertEqual(self.nov.statut, Echeance.Statut.PARTIEL)
        self.assertEqual(self.nov.reste_du, Decimal("600"))
        self.assertEqual(self.nov.document[0], "recu-partiel")
        r = self.client.get(f"/admin/patrimoine/echeance/?bail={self.bail.pk}&reglement=tous")
        self.assertContains(r, "Reçu PDF")

    def test_parts_incoherentes_refusees(self):
        r = self._envoyer("valider", "1000", {f"part-{self.oct.pk}": "900", f"part-{self.nov.pk}": "0"})
        self.assertContains(r, "entre 0 et le reste dû")
        r = self._envoyer("valider", "500", {f"part-{self.oct.pk}": "400", f"part-{self.nov.pk}": "400"})
        self.assertContains(r, "dépassent le montant reçu")
        self.assertEqual(self.Versement.objects.count(), 0)

    def test_trop_percu_garde_en_avance_puis_impute(self):
        self._envoyer("valider", "2000", {f"part-{self.oct.pk}": "800", f"part-{self.nov.pk}": "800"})
        v = self.Versement.objects.get()
        self.assertEqual(v.avance, Decimal("400"))
        Echeance.generer_pour_mois(date(2026, 12, 1))
        dec = Echeance.objects.get(periode=date(2026, 12, 1))
        self.assertEqual(dec.statut, Echeance.Statut.PARTIEL)
        self.assertEqual(dec.montant_paye, Decimal("400"))
        self.assertEqual(self.Versement.objects.get().avance, 0)
        r = self.client.get("/admin/patrimoine/versement/")
        self.assertContains(r, "Durand")

    def test_recu_paiement_partiel(self):
        Paiement.objects.create(echeance=self.oct, date=date(2026, 10, 8), montant=Decimal("300"))
        from .views.loyers import pdf_echeance

        self.oct.refresh_from_db()
        _, nom, est_quittance = pdf_echeance(self.oct)
        self.assertFalse(est_quittance)
        self.assertTrue(nom.startswith("recu-partiel-2026-10"))
        from . import mails

        texte = mails.texte_quittance(self.oct, list(self.bail.locataires.all()), "octobre 2026", False)
        self.assertIn("reçu de votre paiement partiel", texte)
        self.assertIn("Il reste 500,00 € à régler", texte)


class FacturesAnneePaiementEtClotureTests(TestCase):
    """Factures rangées par année de paiement, message de doublon avec l'année et le lien, années clôturées."""

    def setUp(self):
        from .models import PieceComptable

        sci = Proprietaire.objects.create(nom="SCI V", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=sci, nom="Vaugneray", adresse="1 rue", code_postal="69670", ville="Vaugneray",
                                        comptabilite="foncier")
        # appel de fonds daté de décembre 2024, payé en janvier 2025
        self.piece = PieceComptable.objects.create(bien=self.bien, date=date(2024, 12, 18), date_paiement=date(2025, 1, 10),
                                                   fournisseur="Syndic", libelle="Appel de fonds T1", montant=Decimal("300"), ligne="13")
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))

    def test_rangee_dans_l_annee_du_paiement(self):
        from .annexe2072 import colonne

        self.assertEqual(self.piece.annee, 2025)
        self.assertEqual(colonne(self.bien, 2025)["valeurs"].get("13"), Decimal("300"))
        self.assertFalse(colonne(self.bien, 2024)["valeurs"].get("13"))
        liste = self.client.get("/admin/patrimoine/piececomptable/?compta=foncier&annee=2025")
        self.assertEqual(list(liste.context["cl"].result_list), [self.piece])
        self.assertContains(liste, "column-payee_le")
        self.assertContains(liste, "10/01/2025")

    def test_doublon_indique_l_annee_et_le_lien(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from .models import PieceComptable

        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            self.piece.fichier = SimpleUploadedFile("appel.pdf", b"%PDF-appel")
            self.piece.empreinte = ""
            self.piece.save()
            r = self.client.post("/admin/patrimoine/piececomptable/import/",
                                 {"bien": self.bien.pk, "fichiers": [SimpleUploadedFile("copie.pdf", b"%PDF-appel")]}, follow=True)
        self.assertEqual(PieceComptable.objects.count(), 1)
        self.assertContains(r, "Cette facture est enregistrée en 2025")
        self.assertContains(r, f'href="/admin/patrimoine/piececomptable/{self.piece.pk}/change/"')
        self.assertContains(r, "?compta=foncier&amp;annee=2025")

    def test_annee_cloturee_verrouille_les_factures(self):
        from .models import AnneeCloturee, PieceComptable

        liste = "/admin/patrimoine/piececomptable/?compta=foncier&annee=2025"
        # une année ouverte renvoie vers « Déclaration effectuée le … » ; les années clôturées avant la refonte restent verrouillées
        self.assertContains(self.client.get(liste), "Déclaration effectuée le")
        self.assertNotContains(self.client.get(liste), "Année 2025 ouverte")
        AnneeCloturee.objects.create(annee=2025)
        self.assertTrue(AnneeCloturee.est_cloturee(2025))
        self.assertContains(self.client.get(liste), "Année 2025 clôturée")
        # fiche en lecture seule, suppression et scission refusées
        fiche = self.client.get(f"/admin/patrimoine/piececomptable/{self.piece.pk}/change/")
        self.assertContains(fiche, "est clôturée")
        self.client.post(f"/admin/patrimoine/piececomptable/{self.piece.pk}/change/", {"bien": self.bien.pk, "montant": "999"})
        self.client.post(f"/admin/patrimoine/piececomptable/{self.piece.pk}/delete/", {"post": "yes"})
        self.client.post("/admin/patrimoine/piececomptable/", {"action": "delete_selected", "post": "yes", "_selected_action": [self.piece.pk]})
        self.piece.refresh_from_db()
        self.assertEqual(self.piece.montant, Decimal("300"))
        # aucune nouvelle facture comptée dans l'année clôturée ; possible si payée l'année suivante
        p = PieceComptable(bien=self.bien, date=date(2025, 6, 1), montant=Decimal("50"), ligne="9")
        with self.assertRaises(Exception):
            p.full_clean()
        p.date_paiement = date(2026, 1, 5)
        p.full_clean()
        # déverrouillage : la facture redevient modifiable
        self.client.post("/admin/patrimoine/piececomptable/cloture/2025/", {"verrouiller": "0", "retour": liste})
        self.assertFalse(AnneeCloturee.est_cloturee(2025))
        self.piece.montant = Decimal("310")
        self.piece.full_clean()


class RetoursNovembreTests(TestCase):
    """Petites corrections demandées par Fabien (retours des 14 et 15 novembre)."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.sci = Proprietaire.objects.create(nom="SCI R", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=self.sci, nom="Immeuble R", adresse="1 rue", code_postal="69000", ville="Lyon")

    def test_menu_parametres_et_ordre_biens(self):
        page = self.client.get("/admin/").content.decode()
        self.assertIn("Paramètres", page)
        self.assertNotIn("Réglages</span>", page)
        self.assertLess(page.index("Propriétaires"), page.index(">Lots<"))
        self.assertLess(page.index(">Lots<"), page.index("Syndics"))

    def test_nouveau_locataire_a_venir_reste_visible(self):
        Locataire.objects.create(nom="NOUVEAU")
        page = self.client.get("/admin/patrimoine/locataire/").content.decode()
        self.assertIn("NOUVEAU", page)
        self.assertIn("À venir", page)
        self.assertNotIn("NOUVEAU", self.client.get("/admin/patrimoine/locataire/?present=non").content.decode())
        self.assertIn("NOUVEAU", self.client.get("/admin/patrimoine/locataire/?present=avenir").content.decode())

    def test_types_garage_et_place_de_parking(self):
        self.assertIn(("garage", "Garage"), Lot.Type.choices)
        self.assertIn(("parking", "Place de parking"), Lot.Type.choices)
        self.assertIn(("maison", "Maison"), Lot.Type.choices)

    def test_2072_arrondi_et_assurance_emprunteur_ligne_17(self):
        from .annexe2072 import annexe, part_personnelle
        from .lecture_factures import analyser
        from .models import PieceComptable

        PieceComptable.objects.create(bien=self.bien, date=date(2025, 3, 1), libelle="Taxe", montant=Decimal("800.50"), ligne="12")
        PieceComptable.objects.create(bien=self.bien, date=date(2025, 4, 1), libelle="Assurance", montant=Decimal("120.49"), ligne="17")
        a = annexe([self.bien], 2025)
        v = a["colonnes"][0]["valeurs"]
        self.assertEqual((v["12"], v["17"]), (Decimal("801"), Decimal("120")))
        self.assertEqual(a["colonnes"][0]["valeurs_exactes"]["12"], Decimal("800.50"))
        self.assertEqual(part_personnelle([("R5", "", "", Decimal("-1001"))], 50)[0][4], Decimal("-501"))
        texte = "Avis d'échéance - Assurance emprunteur - prêt immobilier n° 123\nMontant à payer : 45,20 €\nDate : 05/01/2025"
        self.assertEqual(analyser(texte).get("ligne"), "17")

    def test_ligne_230_toujours_retranchee_et_pret_estime_signale(self):
        from .annexe2072 import colonne
        from .models import PieceComptable, Pret

        PieceComptable.objects.create(bien=self.bien, date=date(2025, 3, 1), libelle="Appel T1", montant=Decimal("110.62"), ligne="13")
        PieceComptable.objects.create(bien=self.bien, date=date(2025, 9, 22), libelle="Régul T4", montant=Decimal("-78.12"), ligne="14")
        pret = Pret.objects.create(proprietaire=self.sci, bien=self.bien, banque="CM", reference="1", montant=Decimal("20000"),
                                   taux=Decimal("3"), duree_mois=120, date_premiere_echeance=date(2024, 1, 5),
                                   assurance_mensuelle=Decimal("8"))
        c = colonne(self.bien, 2025)
        self.assertEqual(c["valeurs"]["14"], Decimal("78.12"))
        self.assertEqual(c["valeurs"]["16"], Decimal("110.62") - Decimal("78.12"))  # 32,50 et non 188,74
        self.assertIn("estimation", c["detail"]["17"][0][0])
        pret.lignes.create(rang=1, date=date(2025, 1, 5), capital=Decimal("100"), interets=Decimal("47.18"),
                           assurance=Decimal("4.83"), restant=Decimal("19900"))
        c = colonne(self.bien, 2025)
        self.assertEqual(c["valeurs"]["17"], Decimal("52.01"))
        self.assertNotIn("estimation", c["detail"]["17"][0][0])
        pret.attestations.create(annee=2025, interets=Decimal("566.12"), assurance=Decimal("57.96"))
        c = colonne(self.bien, 2025)
        self.assertEqual(c["valeurs"]["17"], Decimal("624.08"))
        self.assertIn("attestation 2025", c["detail"]["17"][0][0])
        self.assertEqual(colonne(self.bien, 2026)["valeurs"].get("17", Decimal(0)), Decimal(0))  # autre année : le tableau
        page = self.client.get(f"/admin/patrimoine/pret/{pret.pk}/change/").content.decode()
        self.assertIn("Attestations annuelles de la banque", page)
        self.assertIn("566.12", page)

    def test_annees_anterieures_toujours_proposees(self):
        t = timezone.localdate().year
        page = self.client.get("/admin/patrimoine/piececomptable/?compta=foncier").content.decode()
        self.assertIn(f"{t - 1}", page)
        self.assertIn(f">{t - 3}<", page)
        page = self.client.get("/comptabilite/2072/").content.decode()
        self.assertIn(f">{t - 3}<", page)

    def test_piece_non_lue_et_saisie_obligatoire(self):
        from .models import PieceComptable

        piece = PieceComptable.objects.create(bien=self.bien, libelle="scan illisible")
        url = "/admin/patrimoine/piececomptable/?compta=foncier&a_codifier=oui"
        page = self.client.get(url).content.decode()
        self.assertIn("À saisir à la main", page)
        self.assertIn("1 pièce à compléter", page)
        # enregistrer la ligne sans montant ni date : refusé, avec un message clair
        donnees = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-0-id": str(piece.pk), "form-0-bien": str(self.bien.pk),
                   "form-0-date": "", "form-0-date_paiement": "", "form-0-fournisseur": "Plombier", "form-0-libelle": "scan illisible",
                   "form-0-montant": "", "form-0-ligne": "9", "_save": "Enregistrer"}
        r = self.client.post(url, donnees)
        self.assertContains(r, "Obligatoire : saisissez le montant.")
        piece.refresh_from_db()
        self.assertEqual(piece.ligne, "")
        # action « Valider » avec une saisie en cours : la saisie est enregistrée, la pièce non lue n'est pas « validée »
        donnees.update({"form-0-date": "2025-03-01", "form-0-montant": "358", "action": "valider_preremplies",
                        "_selected_action": [str(piece.pk)], "index": "0"})
        del donnees["_save"]
        r = self.client.post(url, donnees, follow=True)
        piece.refresh_from_db()
        self.assertEqual((piece.montant, piece.ligne), (Decimal("358.00"), "9"))
        self.assertContains(r, "saisie du tableau enregistrée")

    def test_rappel_retour_accueil_et_bouton_fait(self):
        accueil = self.client.get("/admin/").content.decode()
        self.assertIn("/admin/patrimoine/rappel/add/?next=/admin/", accueil)
        r = self.client.post("/admin/patrimoine/rappel/add/?next=/admin/", {
            "titre": "Note BNB Google", "date": timezone.localdate().isoformat(), "periodicite": "ponctuel", "categorie": "autre",
            "actif": "on", "notes": ""})
        self.assertEqual(r.status_code, 302, r.context["adminform"].form.errors if r.context else "")
        self.assertEqual(r["Location"], "/admin/")
        accueil = self.client.get("/admin/").content.decode()
        self.assertIn("Note BNB Google", accueil)
        self.assertIn("bouton-fait", accueil)

    def test_scripts_charges(self):
        page = self.client.get("/admin/patrimoine/locataire/").content.decode()
        for script in ("recherche_liste.js", "calcul_montants.js", "confirmer.js"):
            self.assertIn(script, page)


class RegimesFiscauxTests(TestCase):
    """Régime fiscal par bien : comptabilité déduite, formulaires, tableau récapitulatif et alertes de seuils."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.foyer = Proprietaire.objects.create(nom="Fabien & Alice", type=Proprietaire.Type.NOM_PROPRE)
        self.sci = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR, pourcentage_detention=50)
        self.h = Proprietaire.objects.create(nom="H-INVEST", type=Proprietaire.Type.SARL_FAMILLE)

    def _bien(self, proprio, nom, **kw):
        return Bien.objects.create(proprietaire=proprio, nom=nom, adresse="1 rue", code_postal="69000", ville=nom, **kw)

    def test_regime_deduit_de_l_ancienne_comptabilite(self):
        self.assertEqual(self._bien(self.foyer, "Pollionnay").regime_fiscal, "nu")
        self.assertEqual(self._bien(self.sci, "Vaugneray").regime_fiscal, "sci_ir")
        self.assertEqual(self._bien(self.foyer, "Chazay", comptabilite="lmnp").regime_fiscal, "lmnp")
        self.assertEqual(self._bien(self.h, "Villeurbanne", comptabilite="societe").regime_fiscal, "societe_ir")
        self.assertEqual(self._bien(self.foyer, "Maison", comptabilite="").regime_fiscal, "non_loue")
        # changer la comptabilité par le code change aussi le régime
        b = Bien.objects.get(nom="Pollionnay")
        b.comptabilite = "lmnp"
        b.save()
        self.assertEqual(Bien.objects.get(pk=b.pk).regime_fiscal, "lmnp")

    def test_le_regime_commande_la_comptabilite(self):
        b = self._bien(self.foyer, "Chasselay", regime_fiscal="lmp")
        self.assertEqual(b.comptabilite, "lmnp")
        b.regime_fiscal = "non_loue"
        b.save()
        self.assertEqual(Bien.objects.get(pk=b.pk).comptabilite, "")
        s = self._bien(self.sci, "Vaugneray", regime_fiscal="sci_ir", option_fiscale="micro")
        self.assertEqual(s.option_fiscale, "reel")  # une SCI est toujours au réel

    def test_2044_sans_les_biens_au_micro_foncier(self):
        from . import compta

        reel = self._bien(self.foyer, "Pollionnay", regime_fiscal="nu")
        self._bien(self.foyer, "Craponne", regime_fiscal="nu", option_fiscale="micro")
        self._bien(self.sci, "Vaugneray")
        self.assertEqual(list(compta.biens_2044()), [reel])
        self.assertEqual(compta.biens_2072().count(), 1)

    def test_2044_detail_deplie_sous_chaque_ligne(self):
        from .models import PieceComptable, Pret

        a = self._bien(self.foyer, "Pollionnay", regime_fiscal="nu")
        b = self._bien(self.foyer, "Craponne", regime_fiscal="nu")
        PieceComptable.objects.create(bien=a, date=date(2025, 1, 10), fournisseur="Syndic", libelle="Appel T1", montant=Decimal("420"), ligne="13")
        PieceComptable.objects.create(bien=b, date=date(2025, 9, 18), fournisseur="Isol", libelle="Combles", montant=Decimal("3200"), ligne="9b")
        Pret.objects.create(proprietaire=self.foyer, bien=a, banque="CM", reference="P1", montant=Decimal("20000"), taux=Decimal("3"),
                            duree_mois=120, date_premiere_echeance=date(2024, 1, 5), assurance_mensuelle=Decimal("8"))
        page = self.client.get("/comptabilite/2044/?annee=2025").content.decode()
        self.assertNotIn("Détail par bien", page)
        self.assertIn("Tout déplier", page)
        # la facture apparaît dans une sous-ligne de la ligne 229, dans la colonne de son bien, avec le lien vers la facture
        self.assertRegex(page, r'(?s)229</td>.*?<tr class="sous">.*?Syndic – Appel T1</a>.*?420,00')
        # 9 bis rangée sous la ligne 224 avec son étiquette ; prêt sans tableau signalé « estimation »
        self.assertRegex(page, r"(?s)224</td>.*?Isol – Combles</a>.*?rénovation énergétique")
        self.assertRegex(page, r"(?s)250</td>.*?Prêt CM P1.*?estimation")

    def test_2072_detail_deplie_sous_chaque_ligne(self):
        from .models import PieceComptable, Pret

        a = self._bien(self.sci, "Vaugneray")
        b = self._bien(self.sci, "Tassin")
        PieceComptable.objects.create(bien=a, date=date(2025, 1, 10), fournisseur="Syndic", libelle="Honoraires", montant=Decimal("540"), ligne="6")
        PieceComptable.objects.create(bien=b, date=date(2025, 9, 18), fournisseur="Isol", libelle="Combles", montant=Decimal("4800"), ligne="9b")
        Pret.objects.create(proprietaire=self.sci, bien=a, banque="CM", reference="S1", montant=Decimal("20000"), taux=Decimal("3"),
                            duree_mois=120, date_premiere_echeance=date(2024, 1, 5), assurance_mensuelle=Decimal("8"))
        page = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode()
        self.assertNotIn("Détail par immeuble", page)
        self.assertIn("Tout déplier", page)
        self.assertRegex(page, r'(?s)▸</button>6</td>.*?<tr class="sous">.*?Syndic – Honoraires</a>.*?540,00')
        # les travaux 9 bis apparaissent sous la ligne 9 (qui les inclut) et sous la ligne 9 bis
        self.assertRegex(page, r"(?s)▸</button>9</td>.*?Isol – Combles</a>.*?rénovation énergétique.*?▸</button>9 bis</td>.*?Isol – Combles</a>")
        self.assertRegex(page, r"(?s)▸</button>17</td>.*?Prêt CM S1.*?estimation")
        # le PDF garde le formulaire sans sous-lignes
        pdf = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025&format=pdf")
        self.assertEqual(pdf.status_code, 200)

    def test_avoir_negatif_ligne_14_garde_son_signe_dans_le_detail(self):
        from .models import PieceComptable

        a = self._bien(self.sci, "Vaugneray")
        PieceComptable.objects.create(bien=a, date=date(2025, 9, 17), fournisseur="Foncia", libelle="Régul", montant=Decimal("-81.81"), ligne="14")
        PieceComptable.objects.create(bien=a, date=date(2025, 3, 1), fournisseur="Foncia", libelle="Appel T1", montant=Decimal("500"), ligne="13")
        page = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode()
        # le détail montre le montant saisi (−81,81 €) et explique qu'il est retranché ; la ligne 14 reste positive (cerfa)
        self.assertRegex(page, r"(?s)▸</button>14</td>.*?Foncia – Régul</a>.*?avoir saisi en négatif : 81,81\xa0€ à retrancher.*?−81,81\xa0€")
        self.assertRegex(page, r"(?s)>14</td>.*?<td class=\"montant\">82\xa0€</td>")
        self.assertRegex(page, r"(?s)>16</td>.*?<td class=\"montant\">418\xa0€</td>")  # 500 − 82

    def test_montants_negatifs_dans_toutes_les_declarations(self):
        """Avoirs saisis en négatif : signe gardé dans les détails et les sous-totaux, effet juste sur les totaux."""
        from . import compta, dossiers, lmnp
        from .models import ChargeLMNP, Lot, PieceComptable

        F = lambda bien, montant, **kw: PieceComptable.objects.create(bien=bien, date=date(2025, 5, 2), fournisseur="F",
                                                                       libelle=kw.pop("libelle", "Avoir"), montant=Decimal(montant), **kw)
        # 2044 : avoir de 100 € sur des travaux de 1 000 € → ligne 224 à 900 €, détail −100,00 €
        n = self._bien(self.foyer, "Pollionnay", regime_fiscal="nu")
        F(n, "1000", ligne="9", libelle="Travaux")
        F(n, "-100", ligne="9", libelle="Avoir travaux")
        r = compta.recap_2044(2025)
        l224 = next(l for l in r["lignes"] if l["numero"] == "224")
        self.assertEqual(l224["total"], Decimal("900"))
        self.assertIn(Decimal("-100"), [d["total"] for d in l224["detail"]])
        self.assertIn("−100,00", self.client.get("/comptabilite/2044/?annee=2025").content.decode())
        # 2072 : avoir de 50 € sur une assurance de 300 € → ligne 8 à 250 €, cadre I R3 cohérent
        s = self._bien(self.sci, "Vaugneray")
        F(s, "300", ligne="8", libelle="PNO")
        F(s, "-50", ligne="8", libelle="Avoir PNO")
        page = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode()
        self.assertRegex(page, r"(?s)>8</td>.*?<td class=\"montant\">250\xa0€</td>")
        self.assertIn("−50,00", page)
        # LMNP et société : rubriques et totaux signés
        m = self._bien(self.foyer, "Chazay", regime_fiscal="lmnp")
        Lot.objects.create(bien=m, designation="T2", type="appartement")
        F(m, "200", rubrique="electricite")
        F(m, "-80", rubrique="electricite")
        ChargeLMNP.objects.create(lot=m.lots.first(), annee=2025, categorie="assurance", montant=Decimal("-30"))
        recap = compta.recapitulatif("lmnp", 2025)
        self.assertEqual(recap["total"], Decimal("90"))  # 200 − 80 − 30
        self.assertIn("120,00", self.client.get("/comptabilite/lmnp/?annee=2025").content.decode())
        bloc = next(b for b in lmnp.bilan(2025)["blocs"] if b["lot"].bien_id == m.pk)
        self.assertIn(Decimal("-30"), [c["montant"] for c in bloc["charges"]])
        h = self._bien(self.h, "Villeurbanne", regime_fiscal="societe_is")
        F(h, "500", rubrique="autre")
        F(h, "-120", rubrique="autre")
        self.assertEqual(compta.recapitulatif("societe", 2025)["total"], Decimal("380"))
        pdf = self.client.get("/comptabilite/societe/?annee=2025&format=pdf")
        self.assertEqual(pdf.status_code, 200)
        # cartes des dossiers : « charges saisies » de la 2072 avec une régularisation ligne 14 (à retrancher)
        F(s, "40", ligne="14", libelle="Régul")
        d2072 = next(d for d in dossiers.tous() if d["code"] == f"2072-{self.sci.pk}")
        self.assertEqual(dossiers.resume(d2072, 2025)["charges"], Decimal("210"))  # 300 − 50 − 40

    def test_loyers_encaisses_mois_par_mois_dans_le_detail(self):
        from .models import Bail, Echeance, Locataire, Lot, Paiement

        a = self._bien(self.sci, "Vaugneray")
        lot = Lot.objects.create(bien=a, designation="Apt 1er", type="appartement")
        bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2024, 1, 1), loyer_hc=600, charges=50)
        bail.locataires.add(Locataire.objects.create(nom="DUPONT", prenom="Jean", civilite="M."))
        for mois, jour in ((3, 4), (1, 5), (2, 3)):  # créés dans le désordre
            e = Echeance.objects.create(bail=bail, periode=date(2025, mois, 1), loyer_hc=600, charges=50,
                                        date_exigibilite=date(2025, mois, 1))
            Paiement.objects.create(echeance=e, date=date(2025, mois, jour), montant=Decimal("650"))
        page = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode()
        # une sous-ligne par loyer, avec le mois et le locataire, du 1er janvier au 31 décembre, bail en provisions : montant hors charges
        self.assertRegex(page, r"(?s)▸</button>1</td>.*?05/01/2025 · loyer de janvier 2025 · M. Jean DUPONT \(Apt 1er\) · hors provisions de charges.*?600,00"
                               r".*?03/02/2025 · loyer de février 2025.*?04/03/2025 · loyer de mars 2025")
        self.assertRegex(page, r"(?s)>1</td>.*?<td class=\"montant\">1 800\xa0€</td>")

    def test_charges_au_forfait_declarees_avec_le_loyer(self):
        from .models import Bail, Echeance, Locataire, Lot, Paiement

        a = self._bien(self.sci, "Vaugneray")
        lot = Lot.objects.create(bien=a, designation="Vaugneray", type="appartement")
        bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2024, 1, 1), loyer_hc=560, charges=10,
                                   mode_charges="forfait")
        bail.locataires.add(Locataire.objects.create(nom="DAGNELIE", prenom="Gael", civilite="M."))
        for mois in (1, 2):
            e = Echeance.objects.create(bail=bail, periode=date(2025, mois, 1), loyer_hc=560, charges=10,
                                        date_exigibilite=date(2025, mois, 1))
            Paiement.objects.create(echeance=e, date=date(2025, mois, 2), montant=Decimal("570"))
        from .annexe2072 import colonne

        self.assertEqual(colonne(a, 2025)["valeurs"]["1"], Decimal("1140"))  # forfait : charges comprises
        page = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode()
        self.assertRegex(page, r"(?s)02/01/2025 · loyer de janvier 2025 · M. Gael DAGNELIE \(Vaugneray\) · forfait de charges compris.*?570,00")
        # rappel des baux au forfait, avec un lien vers le bail qui ramène à la déclaration
        self.assertIn("Charges au forfait comprises dans la ligne 1 : 1 bail", page)
        self.assertIn(f"/admin/patrimoine/bail/{bail.pk}/change/?next=", page)
        # liste des baux : repère « forfait » et filtre par mode de règlement des charges
        liste = self.client.get("/admin/patrimoine/bail/?mode_charges=forfait").content.decode()
        self.assertIn(">forfait</span>", liste)
        # en provisions, le même bail est déclaré hors charges et sort du rappel
        bail.mode_charges = "provision"
        bail.save()
        self.assertEqual(colonne(a, 2025)["valeurs"]["1"], Decimal("1120"))
        self.assertNotIn("Charges au forfait comprises", self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode())

    def test_fiche_facture_montre_le_pdf_d_origine(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from .models import PieceComptable

        a = self._bien(self.sci, "Vaugneray")
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            p = PieceComptable.objects.create(bien=a, date=date(2025, 1, 10), fournisseur="Foncia", libelle="Appel", ligne="13",
                                              montant=Decimal("100"), fichier=SimpleUploadedFile("appel.pdf", b"%PDF-1.4 test"))
            retour = f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025"
            fiche = self.client.get(f"/admin/patrimoine/piececomptable/{p.pk}/change/?next={retour}").content.decode()
            self.assertIn("Facture d'origine", fiche)
            self.assertIn(f'class="pages-pdf" data-url="{p.fichier.url}"', fiche)
            self.assertIn("pdfjs/pdf.min.js", fiche)  # dessiné par pdf.js, quel que soit le lecteur PDF du navigateur
            # le fichier s'affiche dans le cadre (même adresse) et en ligne, pas en téléchargement
            r = self.client.get(p.fichier.url)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get("X-Frame-Options"), "SAMEORIGIN")
            self.assertNotIn("attachment", r.get("Content-Disposition", ""))
            # une part d'une facture scindée montre le PDF de la pièce mère ; sans fichier, pas de cadre
            part = PieceComptable.objects.create(bien=a, date=date(2025, 1, 10), libelle="Part", ligne="13",
                                                 montant=Decimal("40"), piece_mere=p)
            self.assertIn(p.fichier.url, self.client.get(f"/admin/patrimoine/piececomptable/{part.pk}/change/").content.decode())
            sans = PieceComptable.objects.create(bien=a, date=date(2025, 1, 10), libelle="Sans", ligne="13", montant=Decimal("5"))
            self.assertNotIn("Facture d'origine", self.client.get(f"/admin/patrimoine/piececomptable/{sans.pk}/change/").content.decode())

    def _donnees_fiche(self, url):
        """Données POST d'une fiche d'admin telle qu'affichée (champs et formulaires en ligne)."""
        ctx = self.client.get(url).context
        form = ctx["adminform"].form
        donnees = {}
        for nom, champ in form.fields.items():
            v = form.initial.get(nom, champ.initial)
            if v is None or v is False or hasattr(v, "field"):  # fichiers : rien à renvoyer
                continue
            donnees[nom] = "on" if v is True else (v.pk if hasattr(v, "pk") else v)
        for fs in ctx["inline_admin_formsets"]:
            mf = fs.formset.management_form
            for nom in mf.fields:
                donnees[f"{mf.prefix}-{nom}"] = mf.initial.get(nom, 0) if nom != "TOTAL_FORMS" else len(fs.formset.initial_forms)
            for f in fs.formset.initial_forms:
                for nom in f.fields:
                    v = f.initial.get(nom)
                    if v not in (None, False):
                        donnees[f"{f.prefix}-{nom}"] = v.pk if hasattr(v, "pk") else v
        return donnees

    def test_facture_ouverte_depuis_une_declaration_y_ramene(self):
        from .models import PieceComptable

        a = self._bien(self.sci, "Vaugneray")
        p = PieceComptable.objects.create(bien=a, date=date(2025, 1, 10), fournisseur="Syndic", libelle="Honoraires", montant=Decimal("540"), ligne="6")
        page = self.client.get(f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025").content.decode()
        retour = f"/comptabilite/2072/?sci={self.sci.pk}&annee=2025#ligne-6"
        lien = f"/admin/patrimoine/piececomptable/{p.pk}/change/?next=" + retour.replace("?", "%3F").replace("&", "%26").replace("=", "%3D").replace("#", "%23")
        self.assertIn(lien.replace("&", "&amp;"), page)
        # Enregistrer : retour à la déclaration, sur la ligne dépliée ; la nature modifiée est prise en compte
        donnees = {**self._donnees_fiche(lien), "ligne": "8"}
        reponse = self.client.post(lien, donnees)
        self.assertEqual(reponse.status_code, 302, getattr(reponse, "context", None) and reponse.context["adminform"].form.errors)
        self.assertEqual(reponse["Location"], retour)
        self.assertEqual(PieceComptable.objects.get(pk=p.pk).ligne, "8")
        # même chose depuis la 2044
        n = self._bien(self.foyer, "Pollionnay", regime_fiscal="nu")
        PieceComptable.objects.create(bien=n, date=date(2025, 3, 1), fournisseur="AXA", libelle="PNO", montant=Decimal("142"), ligne="8")
        self.assertIn("next=/comptabilite/2044/%3Fannee%3D2025%23ligne-8", self.client.get("/comptabilite/2044/?annee=2025").content.decode())
        # liste des factures ouverte avec ?next= (cases des récapitulatifs) : la liste l'ignore comme filtre
        # et la facture ouverte depuis cette liste ramène à la page d'origine
        liste = self.client.get("/admin/patrimoine/piececomptable/?compta=foncier&next=/comptabilite/2031/")
        self.assertEqual(liste.status_code, 200)
        filtres = "_changelist_filters=compta%3Dfoncier%26next%3D%2Fcomptabilite%2F2031%2F"
        reponse = self.client.post(f"/admin/patrimoine/piececomptable/{p.pk}/change/?{filtres}", donnees)
        self.assertEqual(reponse["Location"], "/comptabilite/2031/")
        # « Enregistrer et continuer » reste sur la fiche
        reponse = self.client.post(lien, {**donnees, "_continue": "1"})
        self.assertTrue(reponse["Location"].startswith(f"/admin/patrimoine/piececomptable/{p.pk}/change/"))
        # « Supprimer » garde la page d'origine, « Annuler » y ramène, et la confirmation de suppression y revient
        fiche = self.client.get(lien).content.decode()
        suite = "next=/comptabilite/2072/%3Fsci%3D{}%26annee%3D2025%23ligne-6".format(self.sci.pk)
        self.assertRegex(fiche, rf'href="/admin/patrimoine/piececomptable/{p.pk}/delete/[^"]*{suite}"')
        self.assertIn(f'href="{retour}" class="annuler"'.replace("&", "&amp;"), fiche)
        url_suppression = f"/admin/patrimoine/piececomptable/{p.pk}/delete/?{suite}"
        self.assertEqual(self.client.get(url_suppression).status_code, 200)
        reponse = self.client.post(url_suppression, {"post": "yes"})
        self.assertEqual(reponse["Location"], retour)
        self.assertFalse(PieceComptable.objects.filter(pk=p.pk).exists())

    def test_formulaire_du_bien(self):
        from . import regimes

        self.assertIn("4BE", regimes.formulaire(self._bien(self.foyer, "A", regime_fiscal="nu", option_fiscale="micro"))[0])
        self.assertIn("2044", regimes.formulaire(self._bien(self.foyer, "B", regime_fiscal="nu"))[0])
        self.assertIn("2072", regimes.formulaire(self._bien(self.sci, "C"))[0])
        self.assertIn("2031", regimes.formulaire(self._bien(self.foyer, "D", regime_fiscal="lmnp"))[0])
        self.assertIn("2065", regimes.formulaire(self._bien(self.h, "E", regime_fiscal="societe_is"))[0])

    def test_alerte_micro_foncier_au_dela_de_15000(self):
        from .models import PieceComptable
        from . import regimes

        b = self._bien(self.foyer, "Pollionnay", regime_fiscal="nu", option_fiscale="micro")
        PieceComptable.objects.create(bien=b, date=date(2025, 3, 1), montant=Decimal("16000"), ligne="3", libelle="Recette")
        s = regimes.synthese(2025)
        self.assertEqual(s["lignes"][0]["recettes"], Decimal("16000"))
        self.assertEqual(s["lignes"][0]["resultat"], Decimal("11200"))  # abattement de 30 %
        self.assertTrue(any("Micro-foncier impossible" in a["texte"] for a in s["seuils"]))

    def test_alertes_lmp_et_micro_bic(self):
        from . import regimes

        b = self._bien(self.foyer, "Chasselay", regime_fiscal="lmnp", option_fiscale="micro")
        ligne = {"bien": b, "recettes": Decimal("24000"), "par_categorie": {"non_classe": Decimal("24000")}}
        textes = " ".join(a["texte"] for a in regimes.alertes_seuils([ligne]))
        self.assertIn("Risque de passage en LMP", textes)
        self.assertIn("courte durée non classée", textes)

    def test_alertes_incoherences_de_fiche(self):
        from . import regimes

        b = self._bien(self.foyer, "Pollionnay", regime_fiscal="nu")
        Lot.objects.create(bien=b, designation="Studio", meuble=True)
        textes = " ".join(a["texte"] for a in regimes.alertes_bien(b))
        self.assertIn("meublé ou en courte durée", textes)
        Bien.objects.filter(pk=b.pk).update(regime_fiscal="lmnp", proprietaire=self.h)
        b.refresh_from_db()
        self.assertTrue(any(a["niveau"] == "rouge" for a in regimes.alertes_bien(b)))

    def test_page_et_menu(self):
        self._bien(self.foyer, "Pollionnay", regime_fiscal="nu")
        self._bien(self.sci, "Vaugneray", regime_a_verifier=True)
        page = self.client.get("/comptabilite/regimes-fiscaux/?annee=2025").content.decode()
        for texte in ("Régimes fiscaux – revenus 2025", "Pollionnay", "Déclaration 2044", "Déclaration 2072", "à vérifier",
                      "Les seuils surveillés"):
            self.assertIn(texte, page)
        self.assertIn("Réglages fiscaux", self.client.get("/admin/").content.decode())

    def test_fiche_du_bien_refuse_un_regime_incoherent(self):
        from .admin.biens import BienForm

        b = self._bien(self.sci, "Vaugneray", regime_a_verifier=True)
        donnees = {"proprietaire": self.sci.pk, "nom": "Vaugneray", "adresse": "1 rue", "code_postal": "69670", "ville": "Vaugneray",
                   "regime_fiscal": "nu", "option_fiscale": "reel"}
        form = BienForm(donnees, instance=b)
        self.assertFalse(form.is_valid())
        self.assertIn("regime_fiscal", form.errors)
        form = BienForm({**donnees, "regime_fiscal": "sci_ir"}, instance=b)
        self.assertTrue(form.is_valid(), form.errors)

    def test_courte_duree_classee_abattement_50(self):
        from .lmnp import bilan_lot

        b = self._bien(self.foyer, "Chasselay", regime_fiscal="lmnp")
        lot = Lot.objects.create(bien=b, designation="Studio", meuble=True, exploitation="courte")
        self.assertEqual(bilan_lot(lot, 2025)["abattement_micro"], Decimal(30))
        lot.tourisme_classe = True
        self.assertEqual(bilan_lot(lot, 2025)["abattement_micro"], Decimal(50))


class ListeBauxSansPeriodeTests(TestCase):
    """La liste des baux n'a plus de filtre de période : tous les baux du statut choisi s'affichent."""

    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        for i, debut in enumerate((date(2019, 3, 1), date(timezone.localdate().year, 1, 1))):
            lot = Lot.objects.create(bien=bien, designation=f"Apt {i}")
            Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=debut, loyer_hc=Decimal("500"))
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.url = reverse("admin:patrimoine_bail_changelist")

    def test_pas_de_boutons_de_periode(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "navigation-periode")
        self.assertNotContains(r, "Année dernière")
        self.assertEqual(r.context["cl"].result_count, 2)  # l'ancien bail de 2019 aussi

    def test_ancien_lien_avec_periode_redirige_sans_elle(self):
        r = self.client.get(self.url, {"date_debut__gte": "2026-01-01", "date_debut__lte": "2026-12-31", "q": "Apt"})
        self.assertRedirects(r, f"{self.url}?q=Apt", fetch_redirect_response=False)
        r = self.client.get(self.url, {"date_debut__gte": "2026-01-01"})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)

    def test_loyers_gardent_leur_periode(self):
        r = self.client.get(reverse("admin:patrimoine_echeance_changelist"))
        self.assertContains(r, "navigation-periode")


class DeclarationsTests(TestCase):
    """Comptabilité refondue (PR 42) : page Déclarations (une carte par cerfa), « Déclaration effectuée le … » qui verrouille
    la saisie de ce cerfa seulement, menu en trois entrées et année fiscale choisie une fois."""

    def setUp(self):
        from .models import PieceComptable, Pret

        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        moi = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.sci = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        self.autre_sci = Proprietaire.objects.create(nom="SCI TATA", type=Proprietaire.Type.SCI_IR)
        commun = {"adresse": "1 rue", "code_postal": "69000", "ville": "Lyon", "comptabilite": "foncier"}
        self.pollionnay = Bien.objects.create(proprietaire=moi, nom="Pollionnay", regime_fiscal="nu", **commun)
        self.immeuble = Bien.objects.create(proprietaire=self.sci, nom="Immeuble A", regime_fiscal="sci_ir", **commun)
        self.tata = Bien.objects.create(proprietaire=self.autre_sci, nom="Immeuble T", regime_fiscal="sci_ir", **commun)
        self.facture = PieceComptable.objects.create(bien=self.immeuble, date=date(2025, 3, 1), fournisseur="Plombier",
                                                     libelle="Fuite", montant=Decimal("120"), ligne="9")
        self.facture_tata = PieceComptable.objects.create(bien=self.tata, date=date(2025, 3, 1), libelle="Peinture",
                                                          montant=Decimal("80"), ligne="9")
        PieceComptable.objects.create(bien=self.immeuble, date=date(2025, 4, 1), libelle="Sans ligne", montant=Decimal("10"))
        self.pret = Pret.objects.create(proprietaire=self.sci, bien=self.immeuble, banque="B", montant=Decimal("100000"),
                                        taux=Decimal("2"), duree_mois=120, date_premiere_echeance=date(2024, 1, 1))

    def test_une_carte_par_cerfa(self):
        page = self.client.get("/comptabilite/declarations/?annee=2025").content.decode()
        for texte in ("Déclarations – revenus 2025", "Nom propre", "SCI ALFAB", "SCI TATA", "2044", "2072",
                      "1 à classer", "1 attestation manquante", "À terminer", "Prête", "Déclaration effectuée le"):
            self.assertIn(texte, page)
        self.assertIn(f"/comptabilite/2072/?sci={self.sci.pk}&amp;annee=2025", page)

    def test_declaration_verrouille_seulement_son_cerfa(self):
        from django.core.exceptions import ValidationError

        from .models import AttestationPret, DeclarationFaite

        r = self.client.post("/comptabilite/declarations/", {"action": "cocher", "annee": "2025", "formulaire": "2072",
                                                            "proprietaire": self.sci.pk, "date": "2026-05-15"})
        self.assertRedirects(r, "/comptabilite/declarations/?annee=2025", fetch_redirect_response=False)
        d = DeclarationFaite.objects.get()
        self.assertEqual(str(d), "2072 SCI ALFAB 2025 déclarée le 15/05/2026")
        self.assertContains(self.client.get("/comptabilite/declarations/?annee=2025"), "Déclarée le 15/05/2026")
        # facture de la SCI déclarée : verrouillée (fiche, enregistrement) ; l'autre SCI reste modifiable
        self.facture.montant = Decimal("999")
        with self.assertRaises(ValidationError):
            self.facture.full_clean()
        self.assertContains(self.client.get(f"/admin/patrimoine/piececomptable/{self.facture.pk}/change/"), "2072 · SCI ALFAB 2025 déclarée le 15/05/2026")
        self.facture_tata.montant = Decimal("90")
        self.facture_tata.full_clean()
        # pas de nouvelle attestation de prêt pour l'année déclarée
        with self.assertRaises(ValidationError):
            AttestationPret(pret=self.pret, annee=2025, interets=Decimal("1500")).full_clean()
        # décocher (rectificative) rouvre la saisie et garde l'historique
        self.client.post("/comptabilite/declarations/", {"action": "decocher", "annee": "2025", "declaration": d.pk})
        d.refresh_from_db()
        self.assertFalse(d.active)
        self.assertIsNotNone(d.decochee_le)
        self.facture.refresh_from_db()
        self.facture.montant = Decimal("999")
        self.facture.full_clean()
        AttestationPret(pret=self.pret, annee=2025, interets=Decimal("1500")).full_clean()
        self.assertContains(self.client.get("/comptabilite/declarations/?annee=2025"), "décochée le")

    def test_menu_en_trois_entrees(self):
        page = self.client.get("/admin/").content.decode()
        for texte in ("Mes dossiers</span>", "Toutes les factures</span>", "Réglages fiscaux</span>"):
            self.assertIn(texte, page)
        self.assertLess(page.index("Mes dossiers</span>"), page.index("Toutes les factures</span>"))
        for ancien in ("Revenus fonciers</span>", "Régimes fiscaux</span>", "Déclarations</span>", "Saisie</span>"):
            self.assertNotIn(ancien, page)
        factures = self.client.get("/admin/patrimoine/piececomptable/").content.decode()
        self.assertIn('class="menu-entree actif"', factures.split("Toutes les factures</span>")[0].rsplit("<a", 1)[1])
        for url in ("/comptabilite/2044/", "/comptabilite/", f"/comptabilite/dossier/2072-{self.sci.pk}/",
                    f"/admin/patrimoine/piececomptable/?dossier=2072-{self.sci.pk}"):
            page = self.client.get(url).content.decode()
            self.assertIn('class="menu-entree actif"', page.split("Mes dossiers</span>")[0].rsplit("<a", 1)[1], url)

    def test_annee_choisie_une_fois(self):
        self.client.get("/comptabilite/declarations/?annee=2024")
        self.assertContains(self.client.get("/comptabilite/2044/"), "revenus 2024")
        self.assertContains(self.client.get("/comptabilite/regimes-fiscaux/"), "revenus 2024")
        self.assertContains(self.client.get("/comptabilite/2072/"), "revenus 2024")


class SaisieProprietaireRetenuTests(TestCase):
    """Saisie : le propriétaire choisi dans la liste des factures limite les biens proposés à l'ajout de factures,
    et le seul bien de ce propriétaire est déjà choisi."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        commun = {"adresse": "1 rue", "code_postal": "69000", "ville": "Lyon", "comptabilite": "foncier", "regime_fiscal": "sci_ir"}
        self.alfab = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        self.tata = Proprietaire.objects.create(nom="SCI TATA", type=Proprietaire.Type.SCI_IR)
        self.a1 = Bien.objects.create(proprietaire=self.alfab, nom="Immeuble A1", **commun)
        self.a2 = Bien.objects.create(proprietaire=self.alfab, nom="Immeuble A2", **commun)
        self.t1 = Bien.objects.create(proprietaire=self.tata, nom="Immeuble T1", **commun)
        self.liste = "/admin/patrimoine/piececomptable/"
        self.ajout = "/admin/patrimoine/piececomptable/import/"

    def test_seul_bien_du_proprietaire_preselectionne(self):
        self.client.get(f"{self.liste}?bien__proprietaire__id__exact={self.tata.pk}")
        page = self.client.get(self.ajout).content.decode()
        self.assertIn(f'<option value="{self.t1.pk}" selected>', page)
        self.assertNotIn("Immeuble A1", page)
        self.assertIn("Biens de <b>SCI TATA</b>", page)
        # nouvelle facture saisie à la main : même bien
        self.assertContains(self.client.get(f"{self.liste}add/"), f'<option value="{self.t1.pk}" selected>')

    def test_plusieurs_biens_proposes_sans_choix(self):
        self.client.get(f"{self.liste}?bien__proprietaire__id__exact={self.alfab.pk}")
        page = self.client.get(self.ajout).content.decode()
        self.assertIn("Immeuble A1", page)
        self.assertIn("Immeuble A2", page)
        self.assertNotIn("Immeuble T1", page)
        self.assertIn('<option value="" selected>', page.split('name="bien"')[1].split("</select>")[0])
        # bien choisi dans le filtre : repris tel quel
        self.client.get(f"{self.liste}?bien__proprietaire__id__exact={self.alfab.pk}&bien={self.a2.pk}")
        self.assertContains(self.client.get(self.ajout), f'<option value="{self.a2.pk}" selected>')

    def test_voir_tous_les_biens_et_sans_choix(self):
        self.assertContains(self.client.get(self.ajout), "Immeuble T1")  # aucun propriétaire choisi : tous les biens
        self.client.get(f"{self.liste}?bien__proprietaire__id__exact={self.tata.pk}")
        page = self.client.get(f"{self.ajout}?proprietaire=tous").content.decode()
        self.assertIn("Immeuble A1", page)
        self.assertIn("Immeuble T1", page)


class SaisieFenetreSelectionTests(TestCase):
    """Saisie : les lignes cochées font apparaître une fenêtre Valider / Supprimer (la suppression passe par la confirmation)."""

    def setUp(self):
        from .models import PieceComptable

        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        bien = Bien.objects.create(proprietaire=Proprietaire.objects.create(nom="SCI F", type=Proprietaire.Type.SCI_IR), nom="Immeuble F",
                                   adresse="1 rue", code_postal="69000", ville="Lyon", comptabilite="foncier")
        self.piece = PieceComptable.objects.create(bien=bien, date=date(2025, 2, 5), libelle="Facture plombier", montant=Decimal("180"))

    def test_fenetre_et_actions(self):
        page = self.client.get("/admin/patrimoine/piececomptable/?a_codifier=oui").content.decode()
        self.assertIn('id="fenetre-selection"', page)
        self.assertIn('id="fenetre-selection-annuler"', page)
        self.assertIn('value="valider_preremplies"', page)
        self.assertIn('value="delete_selected"', page)

    def test_supprimer_apres_confirmation(self):
        from .models import PieceComptable

        # sans confirmation (« post »), Django affiche la page de confirmation et ne supprime rien
        self.client.post("/admin/patrimoine/piececomptable/", {"action": "delete_selected", "_selected_action": [self.piece.pk]})
        self.assertTrue(PieceComptable.objects.filter(pk=self.piece.pk).exists())
        self.client.post("/admin/patrimoine/piececomptable/", {"action": "delete_selected", "post": "yes", "_selected_action": [self.piece.pk]})
        self.assertFalse(PieceComptable.objects.filter(pk=self.piece.pk).exists())


class DossiersComptablesTests(TestCase):
    """Comptabilité par dossier (PR n° 13) : accueil « Mes dossiers », saisie limitée à un dossier, aperçu de sa déclaration."""

    def setUp(self):
        from .models import PieceComptable

        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        moi = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.alfab = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        self.tata = Proprietaire.objects.create(nom="SCI TATA", type=Proprietaire.Type.SCI_IR)
        commun = {"adresse": "1 rue", "code_postal": "69000", "ville": "Lyon", "comptabilite": "foncier"}
        Bien.objects.create(proprietaire=moi, nom="Pollionnay", regime_fiscal="nu", **commun)
        self.a = Bien.objects.create(proprietaire=self.alfab, nom="Immeuble A", regime_fiscal="sci_ir", **commun)
        self.t = Bien.objects.create(proprietaire=self.tata, nom="Immeuble T", regime_fiscal="sci_ir", **commun)
        PieceComptable.objects.create(bien=self.a, date=date(2025, 3, 1), fournisseur="Plombier ALFAB", libelle="Fuite",
                                      montant=Decimal("120"), ligne="9")
        PieceComptable.objects.create(bien=self.a, date=date(2025, 4, 1), libelle="Importée ALFAB", montant=Decimal("10"))
        PieceComptable.objects.create(bien=self.t, date=date(2025, 3, 1), fournisseur="Peintre TATA", libelle="Peinture",
                                      montant=Decimal("80"), ligne="9")
        self.code = f"2072-{self.alfab.pk}"

    def test_accueil_une_carte_par_dossier(self):
        page = self.client.get("/comptabilite/?annee=2025").content.decode()
        for texte in ("Mes dossiers comptables", "Nom propre", "SCI ALFAB", "SCI TATA", "1 à saisir", "À jour",
                      "1 facture importée", "Importée ALFAB", f"/comptabilite/dossier/{self.code}/", "Voir la 2072"):
            self.assertIn(texte, page)

    def test_dossier_ne_montre_que_ses_factures(self):
        page = self.client.get(f"/comptabilite/dossier/{self.code}/?annee=2025").content.decode()
        self.assertIn("Plombier ALFAB", page)
        self.assertNotIn("Peintre TATA", page)
        self.assertIn("R5 · Revenu net", page)  # aperçu de la 2072
        self.assertIn("Déclaration effectuée le", page)
        self.assertEqual(self.client.get("/comptabilite/dossier/2072-999/").status_code, 404)
        liste = self.client.get(f"/admin/patrimoine/piececomptable/?dossier={self.code}&annee=2025").content.decode()
        self.assertIn("Factures · SCI ALFAB", liste)
        self.assertIn("Fuite", liste)
        self.assertNotIn("Peinture", liste)
        self.assertIn(f"/admin/patrimoine/piececomptable/import/?dossier={self.code}", liste)

    def test_dossier_sans_filtre_proprietaire_et_bandeau(self):
        liste = self.client.get(f"/admin/patrimoine/piececomptable/?dossier={self.code}&annee=2025").content.decode()
        self.assertNotIn("bien__proprietaire__id__exact", liste)  # le dossier fixe déjà le propriétaire
        self.assertIn(f'href="/comptabilite/dossier/{self.code}/?annee=2025">vue d&#x27;ensemble du dossier SCI ALFAB', liste.replace("'", "&#x27;"))
        toutes = self.client.get("/admin/patrimoine/piececomptable/?annee=2025").content.decode()
        self.assertIn("bien__proprietaire__id__exact", toutes)
        self.assertIn('href="/comptabilite/declarations/?annee=2025">Déclarations', toutes)

    def test_filtre_bien_dans_le_dossier(self):
        from .models import PieceComptable

        b2 = Bien.objects.create(proprietaire=self.alfab, nom="Garage B", regime_fiscal="sci_ir", adresse="2 rue", code_postal="69000",
                                 ville="Lyon", comptabilite="foncier")
        PieceComptable.objects.create(bien=b2, date=date(2025, 5, 1), fournisseur="Serrurier garage", libelle="Porte",
                                      montant=Decimal("50"), ligne="9")
        url = f"/comptabilite/dossier/{self.code}/?annee=2025"
        page = self.client.get(url).content.decode()
        self.assertIn("Tous les biens", page)
        self.assertIn("Serrurier garage", page)
        self.assertIn("Plombier ALFAB", page)
        page = self.client.get(f"{url}&bien={b2.pk}").content.decode()
        self.assertIn("Serrurier garage", page)
        self.assertNotIn("Plombier ALFAB", page)
        self.assertIn("Garage B seulement", page)  # aperçu de ce bien seulement
        self.assertIn(f"&amp;bien={b2.pk}", page)  # la saisie s'ouvre filtrée sur ce bien

    def test_ajout_dans_le_dossier(self):
        page = self.client.get(f"/admin/patrimoine/piececomptable/import/?dossier={self.code}").content.decode()
        self.assertIn(f'<option value="{self.a.pk}" selected>', page)  # seul bien du dossier, déjà choisi
        self.assertNotIn("Immeuble T", page)
        self.assertIn("Biens du dossier <b>SCI ALFAB</b>", page)

    def test_cocher_depuis_le_dossier_y_revient(self):
        from .models import DeclarationFaite

        retour = f"/comptabilite/dossier/{self.code}/?annee=2025"
        r = self.client.post("/comptabilite/declarations/", {"action": "cocher", "annee": "2025", "formulaire": "2072",
                                                            "proprietaire": self.alfab.pk, "date": "2026-05-15", "retour": retour})
        self.assertRedirects(r, retour, fetch_redirect_response=False)
        self.assertTrue(DeclarationFaite.objects.filter(proprietaire=self.alfab, annee=2025, active=True).exists())
        self.assertContains(self.client.get(retour), "Déclarée le 15/05/2026")


class EvolutionDepensesTests(TestCase):
    """Comptabilité › Évolution des dépenses : dépenses par nature sur plusieurs années, écart d'une année sur l'autre."""

    def setUp(self):
        from .models import PieceComptable

        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        moi = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.alfab = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        commun = {"adresse": "1 rue", "code_postal": "69000", "ville": "Lyon"}
        self.a = Bien.objects.create(proprietaire=self.alfab, nom="Immeuble A", regime_fiscal="sci_ir", **commun)
        self.s = Bien.objects.create(proprietaire=moi, nom="Studio LMNP", regime_fiscal="lmnp", **commun)
        Lot.objects.create(bien=self.a, designation="Apt 1", type=Lot.Type.APPARTEMENT)
        P = PieceComptable.objects.create
        for annee, montant in ((2023, "600"), (2024, "800")):
            P(bien=self.a, date=date(annee, 2, 1), libelle="PNO", montant=Decimal(montant), ligne="8")
            P(bien=self.s, date=date(annee, 2, 1), libelle="PNO studio", montant=Decimal("100"), rubrique="pno")
        P(bien=self.a, date=date(2024, 5, 1), libelle="Taxe", montant=Decimal("1000"), ligne="12")
        P(bien=self.a, date=date(2024, 6, 1), libelle="Régul copro", montant=Decimal("50"), ligne="14")
        P(bien=self.a, date=date(2024, 6, 1), libelle="Copro", montant=Decimal("300"), ligne="13")
        P(bien=self.a, date=date(2024, 7, 1), libelle="Mobilier", montant=Decimal("400"), ligne="non")
        P(bien=self.a, date=date(2024, 8, 1), libelle="Pas encore saisie", montant=Decimal("999"))  # à classer : ignorée

    def test_natures_et_ecarts(self):
        from . import synthese_depenses as sd

        r = sd.synthese([2023, 2024], sd.biens_du_perimetre())
        lignes = {l["code"]: l for l in r["lignes"]}
        # 2072 (ligne 8) et LMNP (rubrique pno) additionnés dans la même nature
        self.assertEqual(lignes["assurance"]["valeurs"], [Decimal("700"), Decimal("900")])
        self.assertEqual(lignes["assurance"]["ecart"]["montant"], Decimal("200"))
        self.assertEqual(lignes["assurance"]["ecart"]["sens"], "hausse")
        self.assertEqual(len(lignes["assurance"]["detail"]), 2)
        self.assertIsNone(lignes["taxes"]["ecart"]["pct"])  # rien l'année d'avant : « nouveau »
        self.assertEqual(lignes["copro"]["valeurs"][1], Decimal("250"))  # régularisation retranchée
        self.assertEqual(lignes["gestion"]["valeurs"], [Decimal("20"), Decimal("20")])  # forfait de 20 € par local
        self.assertEqual(r["hors"][1], Decimal("400"))  # non déductible, hors total
        self.assertEqual(r["totaux"][1], Decimal("900") + Decimal("1000") + Decimal("250") + Decimal("20"))

    def test_page_et_filtres(self):
        url = reverse("evolution_depenses")
        page = self.client.get(f"{url}?debut=2023&fin=2024").content.decode()
        for texte in ("Évolution des dépenses", "Assurance propriétaire (PNO)", "Taxe foncière et impôts", "Studio LMNP",
                      "Total des dépenses", "Hors charges", "nouveau", "Plus forte hausse"):
            self.assertIn(texte, page)
        page = self.client.get(f"{url}?dossier=2072-{self.alfab.pk}&debut=2023&fin=2024").content.decode()
        self.assertNotIn("Studio LMNP</td>", page)
        page = self.client.get(f"{url}?bien={self.s.pk}&debut=2023&fin=2024").content.decode()
        self.assertNotIn("Taxe foncière et impôts", page)
        self.assertIn("Évolution des dépenses", self.client.get(reverse("compta_accueil")).content.decode())

    def test_annees_par_defaut_sans_annee_en_cours(self):
        t = timezone.localdate().year
        page = self.client.get(reverse("evolution_depenses")).content.decode()
        self.assertIn(f"<option selected>{t - 4}</option>", page)
        self.assertIn(f"<option selected>{t - 1}</option>", page)


class BlocComptaAccueilTests(TestCase):
    """Tableau de bord : bloc « Comptabilité à faire » avec un lien vers le dossier concerné, masqué quand tout est à jour."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@example.com", "x")
        self.client.force_login(self.admin)
        self.alfab = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=self.alfab, nom="Immeuble A", regime_fiscal="sci_ir", adresse="1 rue",
                                        code_postal="69000", ville="Lyon", comptabilite="foncier")
        self.code = f"2072-{self.alfab.pk}"

    def _accueil(self, annee=None):
        if annee:
            self.client.get(f"/comptabilite/?annee={annee}")  # l'année fiscale est gardée en session
        return self.client.get("/admin/").content.decode()

    def test_a_jour_quand_rien_a_faire(self):
        page = self._accueil(timezone.localdate().year)
        self.assertIn("Comptabilité à faire", page)
        self.assertIn("Rien à faire", page)

    def test_factures_a_saisir_renvoient_au_dossier(self):
        from .models import PieceComptable

        PieceComptable.objects.create(bien=self.bien, date=date(2025, 4, 1), libelle="Importée", montant=Decimal("10"))
        PieceComptable.objects.create(bien=self.bien, libelle="Pas encore lue")
        page = self._accueil(timezone.localdate().year)
        self.assertIn("Comptabilité à faire", page)
        self.assertIn("2 factures à saisir", page)
        self.assertIn(f"dossier={self.code}&amp;a_codifier=oui", page)
        self.assertIn(reverse("compta_accueil"), page)

    def test_bien_sans_dossier(self):
        from .models import PieceComptable

        moi = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        sans = Bien.objects.create(proprietaire=moi, nom="Garage", regime_fiscal="nu", option_fiscale="micro", adresse="2 rue",
                                   code_postal="69000", ville="Lyon")
        PieceComptable.objects.create(bien=sans, libelle="Importée")
        page = self._accueil(timezone.localdate().year)
        self.assertIn("Bien hors dossier", page)
        self.assertIn(reverse("regimes_fiscaux"), page)

    def test_attestation_manquante_annee_terminee_non_declaree(self):
        from .models import DeclarationFaite, Pret

        annee = timezone.localdate().year - 1
        p = Pret.objects.create(proprietaire=self.alfab, bien=self.bien, banque="B", type="in_fine", montant=Decimal("100000"),
                                taux=Decimal("2.4"), duree_mois=120, date_premiere_echeance=date(annee, 1, 1))
        page = self._accueil(annee)
        self.assertIn("1 attestation manquante", page)
        self.assertIn(reverse("admin:patrimoine_pret_change", args=[p.pk]) + "?next=", page)
        DeclarationFaite.objects.create(annee=annee, formulaire="2072", proprietaire=self.alfab, date_declaration=date(annee + 1, 5, 15))
        self.assertNotIn("attestation manquante", self._accueil())
        # année en cours : les attestations n'arrivent qu'en janvier, pas de rappel
        self.assertNotIn("attestation manquante", self._accueil(annee + 1))

    def test_cache_sans_droit_compta(self):
        from . import droits
        from .models import PieceComptable

        PieceComptable.objects.create(bien=self.bien, libelle="Importée")
        user = User.objects.create_user("aide", password="x", is_staff=True)
        droits.appliquer(user, {"loyers": droits.MODIFICATION})
        self.client.force_login(user)
        self.assertNotIn("Comptabilité à faire", self.client.get("/admin/").content.decode())


class FacturesAjouterRecurrentesTests(TestCase):
    """Factures et dépenses : menu « + Ajouter », dépenses récurrentes, colonne « Déclaration », règles par fournisseur."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        self.alfab = Proprietaire.objects.create(nom="SCI ALFAB", type=Proprietaire.Type.SCI_IR)
        self.societe = Proprietaire.objects.create(nom="H-INVEST", type=Proprietaire.Type.SCI_IR)
        commun = {"adresse": "1 rue", "code_postal": "69000", "ville": "Lyon"}
        self.a = Bien.objects.create(proprietaire=self.alfab, nom="Immeuble A", regime_fiscal="sci_ir", comptabilite="foncier", **commun)
        self.h = Bien.objects.create(proprietaire=self.societe, nom="Bureaux H", comptabilite="societe", **commun)
        self.liste = "/admin/patrimoine/piececomptable/"

    def test_menu_ajouter(self):
        page = self.client.get(self.liste).content.decode()
        self.assertIn('class="menu-ajouter"', page)
        for texte in ("Déposer des factures", "Saisir sans justificatif", "Dépense récurrente",
                      "/admin/patrimoine/piececomptable/import/", "/admin/patrimoine/piececomptable/add/",
                      "/admin/patrimoine/depenserecurrente/add/"):
            self.assertIn(texte, page)
        self.assertNotIn("Saisie sans fichier", page)

    def test_colonne_declaration_hors_dossier_seulement(self):
        from .models import PieceComptable

        PieceComptable.objects.create(bien=self.a, date=date(2025, 3, 1), fournisseur="Plombier", libelle="Fuite",
                                      montant=Decimal("120"), ligne="9")
        PieceComptable.objects.create(bien=self.h, date=date(2025, 3, 1), fournisseur="EDF", libelle="Électricité",
                                      montant=Decimal("60"), rubrique="electricite")
        page = self.client.get(self.liste).content.decode()
        self.assertIn("column-declaration", page)
        self.assertIn("2072 · SCI ALFAB", page)
        self.assertIn("Société · H-INVEST", page)
        self.assertIn(f"/comptabilite/dossier/2072-{self.alfab.pk}/", page)
        dossier = self.client.get(f"{self.liste}?dossier=2072-{self.alfab.pk}").content.decode()
        self.assertNotIn("column-declaration", dossier)

    def test_depense_recurrente_cree_les_echeances_passees(self):
        from .models import DepenseRecurrente, PieceComptable

        r = DepenseRecurrente.objects.create(bien=self.a, fournisseur="Allianz", libelle="Assurance PNO", montant=Decimal("26"),
                                             ligne="8", frequence="mensuelle", premiere_echeance=date(2026, 1, 31))
        self.assertEqual(r.generer(date(2026, 4, 15)), 3)  # 31/01, 28/02 (jour ramené), 31/03
        self.assertEqual(sorted(r.pieces.values_list("date", flat=True)), [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])
        p = r.pieces.first()
        self.assertEqual((p.ligne, p.montant, p.preremplie, p.fournisseur), ("8", Decimal("26.00"), False, "Allianz"))
        self.assertEqual(r.generer(date(2026, 4, 15)), 0)  # pas de doublon
        r.pieces.filter(date=date(2026, 2, 28)).delete()
        self.assertEqual(r.generer(date(2026, 4, 30)), 1)  # 30/04 seulement : la facture supprimée ne revient pas
        self.assertEqual(r.prochaine(date(2026, 4, 30)), date(2026, 5, 31))
        r.active = False
        r.save()
        self.assertEqual(r.generer(date(2026, 12, 31)), 0)
        self.assertEqual(PieceComptable.objects.filter(recurrence=r).count(), 3)

    def test_depense_recurrente_respecte_la_declaration_et_la_fin(self):
        from .models import DeclarationFaite, DepenseRecurrente

        DeclarationFaite.objects.create(annee=2025, formulaire="2072", proprietaire=self.alfab, date_declaration=date(2026, 5, 15))
        r = DepenseRecurrente.objects.create(bien=self.a, fournisseur="Trésor public", libelle="Taxe foncière", montant=Decimal("900"),
                                             ligne="12", frequence="annuelle", premiere_echeance=date(2025, 10, 15),
                                             fin=date(2027, 10, 15))
        self.assertEqual(r.generer(date(2030, 1, 1)), 2)  # 2025 déclarée : rien ; 2026 et 2027 ; rien après la fin
        self.assertEqual(sorted(d.year for d in r.pieces.values_list("date", flat=True)), [2026, 2027])

    def test_depense_recurrente_controle_et_ecran(self):
        from django.core.exceptions import ValidationError

        from .models import DepenseRecurrente

        r = DepenseRecurrente(bien=self.h, fournisseur="EDF", montant=Decimal("60"), premiere_echeance=date(2026, 1, 5))
        with self.assertRaises(ValidationError):
            r.full_clean()  # bien en société : rubrique obligatoire
        r.rubrique = "electricite"
        r.full_clean()
        r.save()
        page = self.client.get("/admin/patrimoine/depenserecurrente/").content.decode()
        self.assertIn("EDF", page)
        self.assertIn("Prochaine échéance", page)
        self.assertTrue(r.pieces.exists())  # l'écran crée les échéances passées
        self.assertEqual(self.client.get("/admin/patrimoine/depenserecurrente/add/").status_code, 200)
        factures = self.client.get(f"{self.liste}?a_codifier=recurrentes").content.decode()
        self.assertIn("Récurrentes (1)", factures)
        self.assertIn("Créée automatiquement", "".join(r.pieces.values_list("notes", flat=True)))

    def test_ajout_par_l_ecran_cree_les_factures(self):
        from .models import DepenseRecurrente

        r = self.client.post("/admin/patrimoine/depenserecurrente/add/", {
            "bien": self.a.pk, "fournisseur": "Foncia", "montant": "150", "libelle": "Appel de fonds", "ligne": "13", "rubrique": "",
            "frequence": "trimestrielle", "premiere_echeance": "2026-01-01", "fin": "", "active": "on"}, follow=True)
        self.assertEqual(r.status_code, 200)
        d = DepenseRecurrente.objects.get()
        self.assertGreaterEqual(d.pieces.count(), 1)
        self.assertContains(r, "créée")

    def test_regle_par_fournisseur(self):
        from . import compta
        from .models import PieceComptable

        # EDF déjà classée en Électricité dans une société, en ligne 12 côté revenus fonciers
        PieceComptable.objects.create(bien=self.h, date=date(2026, 1, 5), fournisseur="EDF", libelle="Janv.",
                                      montant=Decimal("60"), rubrique="electricite")
        PieceComptable.objects.create(bien=self.a, date=date(2026, 1, 5), fournisseur="Foncia", libelle="Syndic",
                                      montant=Decimal("60"), ligne="6")
        PieceComptable.objects.create(bien=self.a, date=date(2026, 2, 5), fournisseur="Foncia", libelle="Proposée",
                                      montant=Decimal("60"), ligne="9", preremplie=True)  # pas encore vérifiée : ignorée
        self.assertEqual(compta.code_du_fournisseur("edf", True), {"rubrique": "electricite"})
        self.assertEqual(compta.code_du_fournisseur("EDF", False), {})
        self.assertEqual(compta.code_du_fournisseur("FONCIA", False), {"ligne": "6"})
        self.assertEqual(compta.code_du_fournisseur("", False), {})

        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive), \
                mock.patch("patrimoine.lecture_factures.texte_pdf", return_value="Foncia appel"), \
                mock.patch("patrimoine.lecture_factures.analyser", return_value={"fournisseur": "Foncia", "ligne": "9"}):
            self.client.post("/admin/patrimoine/piececomptable/import/", {"bien": self.a.pk, "fichiers": [SimpleUploadedFile("f.pdf", b"%PDF-x")]})
        p = PieceComptable.objects.order_by("-pk").first()
        self.assertEqual((p.fournisseur, p.ligne, p.preremplie), ("Foncia", "6", True))  # la règle l'emporte, reste à vérifier
