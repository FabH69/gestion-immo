"""Import de données depuis le modèle Excel (Outils › Import Excel)."""
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase

from .models import Bail, Bien, Locataire, Lot, Proprietaire

URL = "/outils/import-excel/"


def classeur(**onglets):
    """Le modèle téléchargé, rempli avec les lignes données ({onglet: [{titre de colonne: valeur}]}), en fichier envoyé."""
    from openpyxl import load_workbook

    from . import import_excel

    wb = load_workbook(BytesIO(import_excel.generer_modele()))
    for nom, lignes in onglets.items():
        feuille = wb[nom]
        titres = {str(c.value).replace(" *", ""): c.column for c in feuille[1] if c.value}
        for i, ligne in enumerate(lignes, start=import_excel.PREMIERE_LIGNE):
            for titre, valeur in ligne.items():
                feuille.cell(i, titres[titre], valeur)
    sortie = BytesIO()
    wb.save(sortie)
    return SimpleUploadedFile("mes-donnees.xlsx", sortie.getvalue())


def complet():
    return classeur(
        Biens=[{"Nom du bien": "Immeuble Hugo", "Propriétaire": "SCI Les Tilleuls", "Adresse": "12 rue Victor Hugo",
                "Code postal": 69003, "Ville": "Lyon", "Type de propriétaire": "SCI à l'IR",
                "Date d'acquisition": datetime(2020, 3, 15), "Prix d'acquisition (€)": 185000}],
        Lots=[{"Bien": "Immeuble Hugo", "Désignation du lot": "Apt 1", "Type de lot": "Appartement", "Surface (m²)": "45,5",
               "Nombre de pièces": 2, "Meublé": "Oui"},
              {"Bien": "immeuble hugo", "Désignation du lot": "Parking 3", "Type de lot": "Parking / garage"}],
        Locataires=[{"Nom": "Martin", "Prénom": "Julie", "Civilité": "Mme", "E-mail": "julie@exemple.fr",
                     "Date de naissance": "02/05/1990"},
                    {"Nom": "Durand", "Prénom": "Paul"}],
        Baux=[{"Bien": "Immeuble Hugo", "Lot": "Apt 1", "Locataire(s)": "Julie Martin; Durand", "Type de bail": "Location meublée (1 an)",
               "Date de début": datetime(2025, 9, 1), "Loyer hors charges (€)": 650, "Charges (€)": "50,50",
               "Dépôt de garantie (€)": 1300}],
    )


class ImportExcelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("fabien", "f@exemple.fr", "x")
        self.client.force_login(self.admin)

    def test_modele_telechargeable(self):
        from openpyxl import load_workbook

        r = self.client.get("/outils/import-excel/modele.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Mode d'emploi", "Biens", "Lots", "Locataires", "Baux", "Listes"])
        titres = [c.value for c in wb["Baux"][1]]
        self.assertIn("Date de début *", titres)  # obligatoire : marqué d'une *
        self.assertIn("Date de fin", titres)  # facultatif
        self.assertTrue(wb["Lots"].data_validations.dataValidation)  # listes déroulantes
        self.assertEqual(wb["Listes"].sheet_state, "hidden")
        page = self.client.get(URL).content.decode()
        self.assertIn("Télécharger le modèle", page)

    def test_modele_vide_renvoye_tel_quel(self):
        r = self.client.post(URL, {"fichier": classeur()})
        self.assertContains(r, "aucune ligne à importer")

    def test_import_complet(self):
        r = self.client.post(URL, {"fichier": complet()}, follow=True)
        self.assertContains(r, "Import réussi : 1 bien, 2 lots, 2 locataires, 1 bail.")
        bien = Bien.objects.get()
        self.assertEqual((bien.proprietaire.nom, bien.proprietaire.type), ("SCI Les Tilleuls", "sci_ir"))
        self.assertEqual(bien.code_postal, "69003")
        self.assertEqual(bien.date_acquisition, date(2020, 3, 15))
        self.assertEqual(bien.comptabilite, "foncier")  # valeur par défaut
        apt = Lot.objects.get(designation="Apt 1")
        self.assertEqual((apt.surface, apt.nb_pieces, apt.meuble), (Decimal("45.5"), 2, True))
        self.assertEqual(Lot.objects.get(designation="Parking 3").type, "parking")
        self.assertEqual(Locataire.objects.get(nom="Martin").date_naissance, date(1990, 5, 2))
        bail = Bail.objects.get()
        self.assertEqual((bail.lot, bail.type, bail.loyer_hc, bail.charges, bail.loyer_initial), (apt, "meuble", 650, Decimal("50.50"), 650))
        self.assertTrue(bail.actif)
        self.assertEqual(sorted(l.nom for l in bail.locataires.all()), ["Durand", "Martin"])

    def test_erreurs_ligne_par_ligne_et_rien_enregistre(self):
        fichier = classeur(
            Biens=[{"Nom du bien": "Immeuble Hugo", "Propriétaire": "Moi", "Adresse": "12 rue", "Code postal": "69003", "Ville": "Lyon"},
                   {"Nom du bien": "Studio", "Adresse": "3 rue", "Code postal": "69001", "Ville": "Lyon"}],  # sans propriétaire
            Lots=[{"Bien": "Immeuble Hugo", "Désignation du lot": "Apt 1", "Type de lot": "Château"}],
            Locataires=[{"Nom": "Martin", "E-mail": "pas-un-mail"}],
            Baux=[{"Bien": "Inconnu", "Locataire(s)": "Martin", "Type de bail": "Location nue (3 ans)",
                   "Date de début": "31/02/2025", "Loyer hors charges (€)": "abc"}],
        )
        r = self.client.post(URL, {"fichier": fichier})
        page = r.content.decode().lower()
        self.assertIn("rien n'a été enregistré", page)
        self.assertEqual(page.count("ce champ ne peut pas"), 0)  # une case fausse n'est signalée qu'une fois
        for attendu in ["case obligatoire vide", "« Château » n'est pas dans la liste", "date illisible « 31/02/2025 »",
                        "nombre illisible « abc »", "bien « Inconnu » introuvable", "Saisissez une adresse"]:
            self.assertIn(attendu.replace("'", "&#x27;").lower(), page)
        self.assertIn("<td class=\"n\">4</td>", page)  # 2e bien = ligne 4 d'Excel
        self.assertFalse(Bien.objects.exists() or Proprietaire.objects.exists() or Lot.objects.exists() or Locataire.objects.exists())

    def test_doublons_et_liens_vers_l_existant(self):
        proprio = Proprietaire.objects.create(nom="SCI Existante", type="sci_ir")
        bien = Bien.objects.create(proprietaire=proprio, nom="Maison Chazay", adresse="1 rue", code_postal="69380", ville="Chazay")
        lot = Lot.objects.create(bien=bien, designation="Maison", type="maison")
        Locataire.objects.create(nom="Martin", prenom="Julie")
        # le bien existe déjà : erreur ; rien d'enregistré
        r = self.client.post(URL, {"fichier": classeur(Biens=[{"Nom du bien": "maison chazay", "Propriétaire": "SCI Existante",
                                                               "Adresse": "1 rue", "Code postal": "69380", "Ville": "Chazay"}])})
        self.assertContains(r, "existe déjà dans l")
        self.assertEqual(Bien.objects.count(), 1)
        # un bail sur le bien existant (un seul lot : la colonne Lot peut rester vide) et la locataire existante
        r = self.client.post(URL, {"fichier": classeur(Baux=[{"Bien": "Maison Chazay", "Locataire(s)": "Martin Julie",
                                                              "Type de bail": "nu", "Date de début": "01/01/2024",
                                                              "Loyer hors charges (€)": 900, "Date de fin": "31/12/2024"}])}, follow=True)
        self.assertContains(r, "Import réussi : 1 bail.")
        bail = Bail.objects.get()
        self.assertEqual((bail.lot, bail.date_fin, bail.actif), (lot, date(2024, 12, 31), False))
        self.assertEqual(Locataire.objects.count(), 1)

    def test_droits_necessaires(self):
        from django.contrib.auth.models import Permission

        from . import droits
        u = User.objects.create_user("aide", password="x", is_staff=True)
        droits.appliquer(u, {"biens": droits.MODIFICATION, "locataires": droits.CONSULTATION})
        self.client.force_login(u)
        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.post(URL, {"fichier": complet()}).status_code, 403)
        self.assertFalse(Bien.objects.exists())
        u.user_permissions.set(Permission.objects.filter(codename="view_bail"))
        u = User.objects.get(pk=u.pk)
        self.client.force_login(u)
        self.assertEqual(self.client.get(URL).status_code, 403)


class ImportExcelModeTestTests(TransactionTestCase):
    """Le même import, en mode test, va dans la base de test et jamais dans la vraie."""
    databases = {"default", "essai"}

    def setUp(self):
        from . import loyers_auto
        loyers_auto._dernier_jour = {}
        self.admin = User.objects.create_superuser("fabien", "f@exemple.fr", "x")
        self.client.force_login(self.admin)

    def tearDown(self):
        from . import base_essai
        base_essai._modele_vierge().unlink(missing_ok=True)

    def test_import_dans_la_base_de_test(self):
        self.client.post("/mode-test/entrer/")
        self.assertContains(self.client.get(URL), "l'import va dans la base de test")
        r = self.client.post(URL, {"fichier": complet()}, follow=True)
        self.assertContains(r, "Import réussi")
        self.assertEqual(Bail.objects.using("essai").count(), 1)
        self.assertFalse(Bien.objects.using("default").exists())
        self.assertFalse(Locataire.objects.using("default").exists())
        # en erreur, rien n'est ajouté à la base de test non plus
        self.client.post(URL, {"fichier": complet()})
        self.assertEqual(Bien.objects.using("essai").count(), 1)
        self.client.post("/mode-test/sortir/")
        self.assertNotContains(self.client.get("/admin/patrimoine/bien/"), "Immeuble Hugo")
