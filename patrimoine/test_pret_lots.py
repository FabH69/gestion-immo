"""Prêt rattaché à un ou plusieurs lots : objet financé, lots financés et part de chaque lot dans les intérêts.
Toutes les données ci-dessous sont fictives."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from . import annexe2072, lcd, lmnp
from .models import Bien, Lot, Pret, PretLot, Proprietaire

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.moi = Proprietaire.objects.create(nom="Martin Paul", type=Proprietaire.Type.NOM_PROPRE)
        self.bien = Bien.objects.create(proprietaire=self.moi, nom="Les Tilleuls", adresse="3 rue", code_postal="69000",
                                        ville="Lyon", regime_fiscal="lmnp")
        self.apt = Lot.objects.create(bien=self.bien, designation="Apt 1er", surface=D("60"))
        self.studio = Lot.objects.create(bien=self.bien, designation="Studio", surface=D("40"))
        self.pret = Pret.objects.create(proprietaire=self.moi, bien=self.bien, banque="Banque fictive", reference="TRV-1",
                                        montant=D("20000"), taux=D("3"), duree_mois=120, date_premiere_echeance=date(2025, 1, 5),
                                        assurance_mensuelle=D("10"))

    def recharger(self):
        return Pret.objects.get(pk=self.pret.pk)

    def interets_2025(self):
        return self.pret.cumul_annee(2025)["interets"]


class PartsParLotTests(Base):
    def test_aucun_lot_coche_prorata_surface_de_tout_le_bien(self):
        self.assertEqual(self.pret.part_du_lot(self.apt), D("0.6"))
        self.assertEqual(self.pret.part_du_lot(self.studio), D("0.4"))
        self.assertEqual(self.pret.lots_finances_libelle, "Tout le bien")

    def test_surface_manquante_parts_egales(self):
        Lot.objects.filter(pk=self.studio.pk).update(surface=None)
        self.assertEqual(self.recharger().part_du_lot(self.apt), D("0.5"))

    def test_un_seul_lot_finance(self):
        PretLot.objects.create(pret=self.pret, lot=self.studio)
        pret = self.recharger()
        self.assertEqual(pret.part_du_lot(self.studio), 1)
        self.assertEqual(pret.part_du_lot(self.apt), 0)
        self.assertEqual(pret.lots_finances_libelle, "Studio (100 %)")

    def test_parts_saisies(self):
        PretLot.objects.create(pret=self.pret, lot=self.apt, part=D("30"))
        PretLot.objects.create(pret=self.pret, lot=self.studio, part=D("70"))
        pret = self.recharger()
        self.assertEqual(pret.part_du_lot(self.studio), D("0.7"))
        self.assertEqual(pret.lots_finances_libelle, "Apt 1er (30 %), Studio (70 %)")


class DeclarationsTests(Base):
    def test_lmnp_plus_de_double_comptage_des_interets(self):
        a = lmnp.bilan_lot(self.apt, 2025)
        s = lmnp.bilan_lot(self.studio, 2025)
        interets = lambda b: next(c["montant"] for c in b["charges"] if c["libelle"] == "Intérêts d'emprunt")
        self.assertEqual(interets(a), (self.interets_2025() * D("0.6")).quantize(D("0.01")))
        self.assertAlmostEqual(interets(a) + interets(s), self.interets_2025(), delta=D("0.01"))

    def test_lmnp_pret_travaux_du_seul_studio(self):
        PretLot.objects.create(pret=self.pret, lot=self.studio)
        a = lmnp.bilan_lot(self.apt, 2025)
        s = lmnp.bilan_lot(self.studio, 2025)
        self.assertFalse(any(c["libelle"] == "Intérêts d'emprunt" for c in a["charges"]))
        self.assertTrue(any(c["libelle"] == "Intérêts d'emprunt" and c["montant"] == self.interets_2025() for c in s["charges"]))

    def test_courte_duree_part_du_lot_dans_les_echeances(self):
        PretLot.objects.create(pret=self.pret, lot=self.apt, part=D("25"))
        PretLot.objects.create(pret=self.pret, lot=self.studio, part=D("75"))
        janvier = lcd.suivi_annuel(self.studio, 2025)[0]
        echeance = self.pret.echeancier()[0]
        self.assertEqual(janvier["pret"], ((echeance["mensualite"] + echeance["assurance"]) * D("0.75")).quantize(D("0.01")))

    def test_2072_ligne_17_inchangee_pour_le_bien(self):
        Bien.objects.filter(pk=self.bien.pk).update(regime_fiscal="nu")
        PretLot.objects.create(pret=self.pret, lot=self.studio)
        m = self.pret.montants_declares(2025)
        self.assertEqual(annexe2072.colonne(Bien.objects.get(pk=self.bien.pk), 2025)["valeurs"]["17"], m["interets"] + m["assurance"])


class EcranPretTests(Base):
    URL = "/admin/patrimoine/pret/"

    def donnees(self, **lots):
        d = {"proprietaire": self.moi.pk, "bien": self.bien.pk, "banque": "Banque fictive", "reference": "TRV-1",
             "type": "amortissable", "objet": "travaux", "montant": "20000", "taux": "3", "duree_mois": "120",
             "date_premiere_echeance": "05/01/2025", "assurance_mensuelle": "10", "taux_assurance": "", "notes": "",
             "lots_finances-TOTAL_FORMS": str(len(lots)), "lots_finances-INITIAL_FORMS": "0",
             "attestations-TOTAL_FORMS": "0", "attestations-INITIAL_FORMS": "0"}
        for i, (lot, part) in enumerate(lots.items()):
            d[f"lots_finances-{i}-lot"] = getattr(self, lot).pk
            d[f"lots_finances-{i}-part"] = part
        return d

    def test_liste_signale_les_prets_a_completer(self):
        r = self.client.get(self.URL, follow=True)
        self.assertContains(r, "1 prêt à compléter")
        self.assertContains(r, "À compléter")
        self.assertContains(self.client.get(self.URL + "?a_completer=oui"), "TRV-1")

    def test_enregistrer_objet_et_lots(self):
        r = self.client.post(f"{self.URL}{self.pret.pk}/change/", self.donnees(apt="40", studio="60"))
        self.assertEqual(r.status_code, 302, r.content.decode()[-3000:])
        pret = self.recharger()
        self.assertEqual(pret.objet, "travaux")
        self.assertEqual(pret.part_du_lot(self.studio), D("0.6"))
        self.assertNotContains(self.client.get(self.URL), "prêt à compléter")

    def test_total_des_parts_different_de_100_refuse(self):
        r = self.client.post(f"{self.URL}{self.pret.pk}/change/", self.donnees(apt="40", studio="50"))
        self.assertContains(r, "Le total des parts fait 90 % au lieu de 100 %")
        self.assertFalse(PretLot.objects.exists())

    def test_part_a_moitie_remplie_refusee(self):
        r = self.client.post(f"{self.URL}{self.pret.pk}/change/", self.donnees(apt="40", studio=""))
        self.assertContains(r, "Remplissez la part de chaque lot")

    def test_lot_d_un_autre_bien_refuse(self):
        autre = Bien.objects.create(proprietaire=self.moi, nom="Autre", adresse="x", code_postal="1", ville="y")
        self.garage = Lot.objects.create(bien=autre, designation="Garage")
        r = self.client.post(f"{self.URL}{self.pret.pk}/change/", self.donnees(garage=""))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PretLot.objects.exists())
