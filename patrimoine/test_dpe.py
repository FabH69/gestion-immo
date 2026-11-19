"""DPE : validité, loi Climat, vérification dans la base de l'ADEME (réseau simulé), fiche du lot, contrôles, rappels,
et vérification du DPE fourni par le vendeur dans le simulateur d'investissement."""
import json
import urllib.error
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from . import dpe
from .models import Bail, Bien, Lot, Proprietaire

NUMERO = "2369E0123456X"
LIGNE_ADEME = {  # noms de colonnes du jeu « dpe03existant »
    "numero_dpe": NUMERO, "etiquette_dpe": "E", "etiquette_ges": "C", "date_etablissement_dpe": "2023-03-15",
    "date_fin_validite_dpe": "2033-03-14", "surface_habitable_logement": 24.5, "adresse_ban": "12 Grande Rue 69600 Oullins-Pierre-Bénite",
    "code_postal_ban": "69600", "nom_commune_ban": "Oullins-Pierre-Bénite", "type_batiment": "appartement",
    "conso_5_usages_par_m2_ep": 289.4, "emission_ges_5_usages_par_m2": 12.1, "type_energie_principale_chauffage": "Électricité",
}


def reponse_ademe(*lignes):
    return {"total": len(lignes), "results": list(lignes)}


class ReglesDpeTests(SimpleTestCase):
    def test_fin_de_validite(self):
        self.assertEqual(dpe.fin_validite(date(2023, 3, 15)), date(2033, 3, 14))  # 10 ans
        self.assertEqual(dpe.fin_validite(date(2020, 5, 1)), date(2024, 12, 31))  # ancien modèle : caduc fin 2024
        self.assertEqual(dpe.fin_validite(date(2016, 5, 1)), date(2022, 12, 31))
        self.assertEqual(dpe.fin_validite(date(2024, 2, 29)), date(2034, 2, 27))
        self.assertIsNone(dpe.fin_validite(None))

    def test_loi_climat(self):
        jour = date(2026, 11, 19)
        g = dpe.loi_climat("G", "69600", jour)
        self.assertEqual(g[0]["niveau"], "rouge")
        self.assertIn("interdite depuis le 01/01/2025", g[0]["texte"])
        self.assertIn("selon la loi en vigueur", g[0]["texte"])
        self.assertIn("loyer gelé", g[1]["texte"])
        self.assertEqual(dpe.loi_climat("F", "69600", jour)[0]["niveau"], "orange")  # 2028 : dans moins de 3 ans
        self.assertEqual(dpe.loi_climat("E", "69600", jour)[0]["niveau"], "info")  # 2034
        self.assertEqual(dpe.loi_climat("D", "69600", jour), [])
        self.assertEqual(dpe.interdiction("G", "97400"), date(2028, 1, 1))  # outre-mer : calendrier décalé
        self.assertIsNone(dpe.interdiction("E", "97400"))

    def test_numero(self):
        self.assertTrue(dpe.numero_valide("2369 e012 3456x"))
        self.assertTrue(dpe.numero_valide("232AE0123456X"))
        self.assertFalse(dpe.numero_valide("12345"))
        self.assertIn("afficher-dpe/2369E0123456X", dpe.lien_observatoire("2369e0123456x"))

    def test_lecture_anciens_noms_de_colonnes(self):
        f = dpe.lire_ligne({"N°DPE": NUMERO.lower(), "Etiquette_DPE": "d", "Date_établissement_DPE": "2022-01-10",
                            "Date_fin_validité_DPE": "2032-01-09", "Surface_habitable_logement": "45,2x", "Code_postal_(BAN)": "69003"})
        self.assertEqual((f["numero"], f["classe"], f["date_fin_validite"], f["code_postal"]), (NUMERO, "D", "2032-01-09", "69003"))
        self.assertIsNone(f["surface"])


class AdemeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_trouve_dans_le_premier_jeu(self):
        with mock.patch.object(dpe, "_lire", return_value=reponse_ademe({**LIGNE_ADEME, "numero_dpe": "2369E0000000A"}, LIGNE_ADEME)) as lire:
            f = dpe.chercher(NUMERO)
        self.assertEqual((f["classe"], f["ges"], f["surface"], f["jeu"]), ("E", "C", 24.5, "dpe03existant"))
        self.assertIn("q=2369E0123456X", lire.call_args[0][0])
        with mock.patch.object(dpe, "_lire") as lire:  # en cache
            dpe.chercher(NUMERO)
        lire.assert_not_called()

    def test_jeu_renomme_puis_dpe_neuf(self):
        erreur = urllib.error.HTTPError("u", 404, "absent", {}, None)
        with mock.patch.object(dpe, "_lire", side_effect=[erreur, reponse_ademe(LIGNE_ADEME)]):
            self.assertEqual(dpe.chercher(NUMERO)["jeu"], "dpe02neuf")

    def test_introuvable(self):
        with mock.patch.object(dpe, "_lire", return_value=reponse_ademe()):
            self.assertIsNone(dpe.chercher(NUMERO))

    def test_ademe_injoignable(self):
        with mock.patch.object(dpe, "_lire", side_effect=OSError("Tunnel connection failed: 403")):
            with self.assertRaisesMessage(dpe.DpeIndisponible, "data.ademe.fr"):
                dpe.chercher(NUMERO)


class VerifierDpeTests(SimpleTestCase):
    JOUR = date(2026, 11, 19)

    def verifier(self, fiche=LIGNE_ADEME, **kwargs):
        cache.clear()
        with mock.patch.object(dpe, "_lire", return_value=reponse_ademe(fiche) if fiche else reponse_ademe()):
            return dpe.verifier(NUMERO, aujourdhui=self.JOUR, **kwargs)

    def niveaux(self, res):
        return [(a["niveau"], a["texte"][:25]) for a in res["alertes"]]

    def test_dpe_conforme(self):
        r = self.verifier(surface=24, adresse="12 Grande Rue, 69600 Oullins", classe_annoncee="E")
        self.assertTrue(r["trouve"])
        self.assertEqual(r["fin_validite"], "2033-03-14")
        self.assertEqual(r["statut"], "vert")  # seules des informations (classe E en 2034, chauffage électrique)
        self.assertTrue(all(a["niveau"] == "info" for a in r["alertes"]))

    def test_incoherences(self):
        r = self.verifier(surface=60, adresse="3 avenue Foch, 69006 Lyon", classe_annoncee="C")
        textes = " ".join(a["texte"] for a in r["alertes"])
        self.assertEqual(r["statut"], "rouge")
        self.assertIn("Classe annoncée C", textes)
        self.assertIn("pas au 69006", textes)
        self.assertIn("Surface du DPE 24,5 m²", textes)

    def test_rue_differente_meme_commune(self):
        r = self.verifier(adresse="5 rue Pasteur, 69600 Oullins")
        self.assertEqual(r["statut"], "orange")
        self.assertIn("ne ressemble pas", r["alertes"][0]["texte"])

    def test_expire_ou_bientot(self):
        r = self.verifier({**LIGNE_ADEME, "date_fin_validite_dpe": "2026-10-01"})
        self.assertEqual(r["alertes"][0]["niveau"], "rouge")
        self.assertIn("expiré", r["alertes"][0]["texte"])
        r = self.verifier({**LIGNE_ADEME, "date_fin_validite_dpe": "2027-02-01"})
        self.assertEqual(r["statut"], "orange")
        self.assertIn("signature de l'acte", r["alertes"][0]["texte"])

    def test_classe_g_et_remplace(self):
        r = self.verifier({**LIGNE_ADEME, "etiquette_dpe": "G", "numero_dpe_remplacant": "2569E0999999Z"})
        textes = " ".join(a["texte"] for a in r["alertes"])
        self.assertIn("location interdite depuis", textes)
        self.assertIn("remplacé par un DPE plus récent", textes)

    def test_introuvable_et_saisie(self):
        r = self.verifier(None)
        self.assertFalse(r["trouve"])
        self.assertEqual(r["statut"], "rouge")
        self.assertIn("n'existe pas", r["alertes"][0]["texte"])
        with self.assertRaisesMessage(dpe.DpeIndisponible, "13 caractères"):
            dpe.verifier("ABC")

    def test_petite_surface_avant_juillet_2024(self):
        r = self.verifier({**LIGNE_ADEME, "type_energie_principale_chauffage": "Gaz naturel"})
        self.assertTrue(any("moins de 40 m²" in a["texte"] for a in r["alertes"]))


class LotDpeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.aujourdhui = timezone.localdate()
        proprio = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Oullins", adresse="12 Grande Rue", code_postal="69600", ville="Oullins")
        self.lot = Lot.objects.create(bien=self.bien, designation="T1", surface=Decimal("24"), classe_dpe="D",
                                      date_dpe=date(2023, 3, 15), fin_validite_dpe=self.aujourdhui + timedelta(days=60))
        Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2024, 1, 1), loyer_hc=Decimal("500"))
        self.admin = User.objects.create_superuser("admin", "", "x")
        self.client.force_login(self.admin)

    def test_fin_de_validite_et_alertes(self):
        self.assertEqual(self.lot.dpe_fin_validite, self.aujourdhui + timedelta(days=60))  # saisie
        self.assertEqual(self.lot.dpe_alertes()[0]["niveau"], "orange")  # moins de 6 mois
        self.lot.fin_validite_dpe = None  # calculée depuis la date du DPE
        self.assertEqual(self.lot.dpe_fin_validite, date(2033, 3, 14))
        self.lot.fin_validite_dpe = self.aujourdhui - timedelta(days=1)
        self.assertIn("expiré", self.lot.dpe_alertes()[0]["texte"])

    def test_rappels_a_faire(self):
        from .rappels import a_faire

        items = [i for i in a_faire(self.aujourdhui) if i["genre"] == "dpe"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["titre"], "Faire refaire le DPE")
        Lot.objects.filter(pk=self.lot.pk).update(fin_validite_dpe=None, classe_dpe="F")
        titres = [i["titre"] for i in a_faire(self.aujourdhui) if i["genre"] == "dpe"]
        attendu = [] if dpe.interdiction("F") - self.aujourdhui > timedelta(days=365) else ["Classe F : location interdite à partir du 01/01/2028"]
        self.assertEqual(titres, attendu)
        Lot.objects.filter(pk=self.lot.pk).update(classe_dpe="G")
        self.assertIn("Classe G : location interdite depuis le 01/01/2025", [i["titre"] for i in a_faire(self.aujourdhui)])

    def test_controles(self):
        from . import controles

        def nombre(code):
            return next(r for r in controles.executer(self.admin) if r["code"] == code)["nombre"]

        self.assertEqual((nombre("lot_dpe_expire"), nombre("lot_interdit"), nombre("lot_dpe_date")), (0, 0, 0))
        Lot.objects.filter(pk=self.lot.pk).update(date_dpe=date(2020, 1, 1), fin_validite_dpe=None, classe_dpe="G")
        Lot.objects.create(bien=self.bien, designation="T2", surface=Decimal("40"), classe_dpe="C")  # DPE sans date
        self.assertEqual((nombre("lot_dpe_expire"), nombre("lot_interdit"), nombre("lot_dpe_date")), (1, 1, 1))
        self.assertContains(self.client.get("/controles/"), "Logements interdits à la location")

    def test_fiche_du_lot(self):
        r = self.client.get(reverse("admin:patrimoine_lot_change", args=[self.lot.pk]))
        self.assertContains(r, "Compléter depuis l'ADEME")
        self.assertContains(r, "Valable jusqu'au")

    def test_completer_depuis_l_ademe(self):
        url = reverse("lot_dpe_ademe", args=[self.lot.pk])
        with mock.patch.object(dpe, "_lire", return_value=reponse_ademe(LIGNE_ADEME)):
            r = self.client.post(url, {"numero_dpe": "2369 e012 3456x"})
        self.assertRedirects(r, reverse("admin:patrimoine_lot_change", args=[self.lot.pk]), fetch_redirect_response=False)
        self.lot.refresh_from_db()
        self.assertEqual((self.lot.numero_dpe, self.lot.classe_dpe, self.lot.classe_ges, self.lot.date_dpe, self.lot.fin_validite_dpe),
                         (NUMERO, "E", "C", date(2023, 3, 15), date(2033, 3, 14)))
        cache.clear()
        with mock.patch.object(dpe, "_lire", side_effect=OSError("bloqué")):
            r = self.client.post(url, {"numero_dpe": NUMERO}, follow=True)
        self.assertContains(r, "observatoire-dpe-audit.ademe.fr")
        self.assertEqual(self.client.get(url).status_code, 405)


class SimulateurDpeTests(TestCase):
    DONNEES = {"nom": "Projet", "adresse": "12 Grande Rue, 69600 Oullins", "nb_lots": "2", "lot-0-type": "t1", "lot-0-surface": "24",
               "lot-1-type": "t2", "lot-1-surface": "40", "prix": "150000"}

    def setUp(self):
        cache.clear()

    def test_verification_en_json(self):
        url = reverse("simulateurs:rentabilite_dpe")
        with mock.patch.object(dpe, "_lire", return_value=reponse_ademe(LIGNE_ADEME)):
            d = self.client.get(url, {"numero": NUMERO, "surface": "24", "adresse": self.DONNEES["adresse"], "classe": "E"}).json()
        self.assertEqual((d["trouve"], d["fiche"]["classe"]), (True, "E"))
        d = self.client.get(url, {"numero": "123"}).json()
        self.assertIn("13 caractères", d["erreur"])
        cache.clear()
        with mock.patch.object(dpe, "_lire", side_effect=OSError("bloqué")):
            d = self.client.get(url, {"numero": NUMERO}).json()
        self.assertIn("ne répond pas", d["erreur"])
        self.assertIn("afficher-dpe", d["lien"])

    def test_saisie_enregistree_et_reportee_sur_le_lot_cree(self):
        from simulateurs.forms import lire_parametres
        from simulateurs.models import SimulationInvestissement

        saisie = [{"numero": "2369e0123456x", "lot": 1, "classe_annoncee": "e",
                   "verifie": {"classe": "E", "ges": "C", "date_etablissement": "2023-03-15", "fin_validite": "2033-03-14", "statut": "vert"}},
                  {"numero": "", "lot": 2}, {"numero": "2369E0999999Z", "lot": 9, "verifie": {"statut": "piraté"}}, "n'importe quoi"]
        p = lire_parametres({**self.DONNEES, "dpe": json.dumps(saisie)})
        self.assertEqual([(d["numero"], d["lot"], d["classe_annoncee"], bool(d["verifie"])) for d in p.dpe],
                         [(NUMERO, 1, "E", True), ("2369E0999999Z", 2, "", False)])
        admin = User.objects.create_superuser("admin", "", "x")
        self.client.force_login(admin)
        r = self.client.get(reverse("simulateurs:rentabilite") + "?exemple=1")
        self.assertContains(r, "DPE fourni par le vendeur")
        pr = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        s = SimulationInvestissement.objects.create(nom="Projet", donnees=p.en_dict())
        self.assertContains(self.client.get(s.get_absolute_url()), "2369E0123456X")
        self.client.post(reverse("simulateurs:simulation_creer_bien", args=[s.pk]),
                         {"proprietaire": pr.pk, "nom": "Oullins", "adresse": "12 Grande Rue", "code_postal": "69600", "ville": "Oullins"})
        lot1, lot2 = Lot.objects.order_by("designation")
        self.assertEqual((lot1.numero_dpe, lot1.classe_dpe, lot1.fin_validite_dpe), (NUMERO, "E", date(2033, 3, 14)))
        self.assertEqual((lot2.numero_dpe, lot2.classe_dpe, lot2.date_dpe), ("2369E0999999Z", "", None))
