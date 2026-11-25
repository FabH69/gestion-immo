from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import TestCase
from openpyxl import load_workbook

from .models import Bail, Bien, EncaissementHistorique, Echeance, Locataire, Lot, Proprietaire, Versement


class CompleterImpayesTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI T", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Thibaudiere", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Maison")
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2023, 1, 1), date_fin=date(2023, 6, 30),
                                        loyer_hc=Decimal("550"), jour_echeance=5)
        self.bail.locataires.add(Locataire.objects.create(nom="BOUCHET", prenom="Charlotte"))
        for mois in (1, 2, 5):  # mars et avril : aucun loyer dans l'appli
            Echeance.objects.create(bail=self.bail, periode=date(2023, mois, 1), loyer_hc=550, date_exigibilite=date(2023, mois, 5))
        EncaissementHistorique.objects.create(lot=self.lot, mois=date(2023, 3, 1), montant=Decimal("550"))

    def _fichier(self):
        r = self.client.get("/impayes/completer/loyers-a-completer.xlsx")
        self.assertEqual(r.status_code, 200)
        return load_workbook(BytesIO(r.content))

    def test_fichier_liste_les_loyers_dus_et_les_mois_manquants(self):
        f = self._fichier()["Impayés"]
        lignes = [r for r in f.iter_rows(min_row=3, values_only=True)]
        self.assertEqual([l[3] for l in lignes], ["01/2023", "02/2023", "03/2023", "04/2023", "05/2023", "06/2023"])
        mars = lignes[2]
        self.assertTrue(mars[0].startswith("B"))
        self.assertEqual(mars[6], 550)  # pré-rempli depuis l'ancien suivi
        self.assertIsNone(lignes[0][6])

    def test_apercu_puis_enregistrement(self):
        classeur = self._fichier()
        f = classeur["Impayés"]
        f["F3"], f["G3"] = date(2023, 1, 6), 550           # janvier payé
        f["F4"], f["G4"], f["H4"] = "10/02/2023", "300", "Chèque"  # février partiel
        sortie = BytesIO()
        classeur.save(sortie)
        sortie.seek(0)
        sortie.name = "impayes.xlsx"
        r = self.client.post("/impayes/completer/", {"fichier": sortie})
        self.assertContains(r, "Aperçu : 3 paiements reçus")
        self.assertContains(r, "Partiel : reste 250,00 € dû")
        self.assertFalse(Versement.objects.exists())  # rien avant de valider
        r = self.client.post("/impayes/completer/", {"etape": "valider"})
        self.assertRedirects(r, "/impayes/completer/", fetch_redirect_response=False)
        self.assertEqual(Versement.objects.count(), 3)
        statuts = {e.periode.month: e.statut for e in Echeance.objects.all()}
        self.assertEqual(statuts, {1: "paye", 2: "partiel", 3: "paye", 5: "a_payer"})
        self.assertEqual(Versement.objects.get(mode="cheque").montant, Decimal("300"))
        self.assertFalse(EncaissementHistorique.objects.exists())  # remplacé par le paiement reçu
        # réimporter le même fichier : janvier et mars, déjà payés, sont ignorés
        sortie.seek(0)
        r = self.client.post("/impayes/completer/", {"fichier": sortie})
        self.assertContains(r, "Déjà payé : ignoré")

    def test_erreurs(self):
        classeur = self._fichier()
        f = classeur["Impayés"]
        f["F3"] = "01/01/2023"  # montant manquant
        f["A4"], f["F4"], f["G4"] = "X9", "02/02/2023", 10
        sortie = BytesIO()
        classeur.save(sortie)
        sortie.seek(0)
        sortie.name = "impayes.xlsx"
        r = self.client.post("/impayes/completer/", {"fichier": sortie})
        self.assertContains(r, "a été enregistré : 2 lignes")
        self.assertContains(r, "Montant reçu manquant")

    def test_bouton_sur_la_page_des_relances(self):
        self.assertContains(self.client.get("/impayes/relancer/"), "Compléter avec Excel")
