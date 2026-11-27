"""Reprise d'un FEC : lecture du fichier, correspondance des comptes, simulation, enregistrement et annulation."""
import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import fec
from .models import (AnneeCloturee, Bien, EncaissementHistorique, Immobilisation, ImportFEC, Lot, PieceComptable, Pret, Proprietaire,
                     SoldeFEC)

ENTETE = ("JournalCode|JournalLib|EcritureNum|EcritureDate|CompteNum|CompteLib|CompAuxNum|CompAuxLib|PieceRef|PieceDate|"
          "EcritureLib|Debit|Credit|EcritureLet|DateLet|ValidDate|Montantdevise|Idevise")


def ligne(j, num, jour, compte, lib, debit="", credit="", aux="", aux_lib="", ref="", libelle=""):
    jl = {"AN": "A nouveaux", "HA": "Achats", "BQ": "Banque", "OD": "Opérations diverses"}[j]
    return "|".join([j, jl, num, jour, compte, lib, aux, aux_lib, ref, jour, libelle or lib, debit, credit, "", "", jour, "", ""])


LIGNES = [
    ligne("AN", "1", "20250101", "21100000", "Terrain", "30000,00", "0,00"),
    ligne("AN", "1", "20250101", "21310000", "Constructions", "150000,00", "0,00"),
    ligne("AN", "1", "20250101", "21840000", "Mobilier", "7000,00", "0,00"),
    ligne("AN", "1", "20250101", "28131000", "Amortissements constructions", "0,00", "6000,00"),
    ligne("AN", "1", "20250101", "28184000", "Amortissements mobilier", "0,00", "2000,00"),
    ligne("AN", "1", "20250101", "16400000", "Emprunt Crédit Agricole", "0,00", "120000,00"),
    ligne("AN", "1", "20250101", "10100000", "Capital", "0,00", "1000,00"),
    ligne("AN", "1", "20250101", "45500000", "Compte courant associé", "0,00", "58000,00"),
    ligne("HA", "2", "20250310", "60610000", "Electricité", "120,50", "0,00", ref="F-123", libelle="EDF mars"),
    ligne("HA", "2", "20250310", "40100000", "Fournisseurs", "0,00", "120,50", "EDF", "EDF", "F-123", "EDF mars"),
    ligne("HA", "3", "20250402", "63512000", "Taxes foncières", "900,00", "0,00", libelle="Taxe foncière 2025"),
    ligne("HA", "3", "20250402", "40100000", "Fournisseurs", "0,00", "900,00", "TRESOR", "Trésor public"),
    ligne("HA", "4", "20250515", "61600000", "Primes d'assurance", "250,00", "0,00", libelle="PNO 2025"),
    ligne("HA", "4", "20250515", "40100000", "Fournisseurs", "0,00", "250,00", "AXA", "AXA"),
    ligne("BQ", "5", "20250105", "51200000", "Banque", "800,00", "0,00", libelle="Loyer janvier"),
    ligne("BQ", "5", "20250105", "70600000", "Loyers", "0,00", "800,00", libelle="Loyer janvier"),
    ligne("BQ", "6", "20250205", "51200000", "Banque", "800,00", "0,00", libelle="Loyer février"),
    ligne("BQ", "6", "20250205", "70600000", "Loyers", "0,00", "800,00", libelle="Loyer février"),
    ligne("BQ", "7", "20250630", "66110000", "Intérêts des emprunts", "1500,00", "0,00"),
    ligne("BQ", "7", "20250630", "51200000", "Banque", "0,00", "1500,00"),
    ligne("OD", "8", "20251231", "68112000", "Dotations aux amortissements", "4000,00", "0,00"),
    ligne("OD", "8", "20251231", "28131000", "Amortissements constructions", "0,00", "3000,00"),
    ligne("OD", "8", "20251231", "28184000", "Amortissements mobilier", "0,00", "1000,00"),
]
FEC = (ENTETE + "\r\n" + "\r\n".join(LIGNES) + "\r\n").encode("utf-8")
NOM = "123456789FEC20251231.txt"


class LectureFECTests(TestCase):
    def test_lit_le_fec_a_barres_verticales(self):
        lu = fec.lire(FEC, NOM)
        self.assertEqual(len(lu["ecritures"]), len(LIGNES))
        self.assertEqual(lu["siren"], "123456789")
        self.assertEqual(lu["date_cloture"], date(2025, 12, 31))
        e = lu["ecritures"][8]
        self.assertEqual((e["compte"], e["debit"], e["date"]), ("60610000", Decimal("120.50"), date(2025, 3, 10)))

    def test_lit_le_fec_a_tabulations_en_windows_1252(self):
        lu = fec.lire(FEC.decode().replace("|", "\t").encode("cp1252"), "export.txt")
        self.assertEqual(lu["separateur"], "tabulation")
        self.assertEqual(lu["ecritures"][10]["compte_lib"], "Taxes foncières")

    def test_variante_montant_et_sens(self):
        texte = ("JournalCode\tJournalLib\tEcritureNum\tEcritureDate\tCompteNum\tCompteLib\tCompAuxNum\tCompAuxLib\tPieceRef\t"
                 "PieceDate\tEcritureLib\tMontant\tSens\tEcritureLet\tDateLet\tValidDate\tMontantdevise\tIdevise\n"
                 "HA\tAchats\t1\t20250310\t6061\tEDF\t\t\t\t\tEDF\t1 120,50\tD\t\t\t\t\t\n"
                 "HA\tAchats\t1\t20250310\t401\tFournisseurs\t\t\t\t\tEDF\t1 120,50\tC\t\t\t\t\t\n")
        lu = fec.lire(texte.encode(), "x.txt")
        self.assertEqual([(e["debit"], e["credit"]) for e in lu["ecritures"]],
                         [(Decimal("1120.50"), 0), (0, Decimal("1120.50"))])

    def test_refuse_un_fichier_qui_n_est_pas_un_fec(self):
        with self.assertRaisesMessage(fec.ErreurFEC, "Colonnes manquantes"):
            fec.lire("Date|Montant\n01/01/2025|12\n".encode(), "x.txt")
        with self.assertRaisesMessage(fec.ErreurFEC, "Séparateur"):
            fec.lire(b"Date;Montant\n", "x.csv")

    def test_ligne_illisible_signalee_sans_bloquer(self):
        lu = fec.lire(FEC + ligne("HA", "9", "2025-99-99", "606", "X", "1,00", "0").encode(), NOM)
        self.assertEqual(len(lu["ecritures"]), len(LIGNES))
        self.assertIn("date illisible", lu["erreurs"][0])

    def test_analyse_equilibre_et_resultat(self):
        a = fec.analyse(fec.lire(FEC, NOM)["ecritures"])
        self.assertEqual(a["desequilibres"], [])
        self.assertEqual(a["total_debit"], a["total_credit"])
        self.assertEqual(a["produits"], Decimal("1600"))
        self.assertEqual(a["charges"], Decimal("6770.50"))

    def test_ecriture_desequilibree(self):
        a = fec.analyse(fec.lire(FEC + ligne("HA", "9", "20250601", "606", "X", "10,00", "0").encode(), NOM)["ecritures"])
        self.assertEqual(len(a["desequilibres"]), 1)


@override_settings(PRIVE_ROOT=tempfile.mkdtemp())
class RepriseFECTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.PRIVE_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.sci = Proprietaire.objects.create(nom="SCI Tilleuls", type=Proprietaire.Type.SCI_IS)
        self.bien = Bien.objects.create(proprietaire=self.sci, nom="Tilleuls", adresse="1 rue", code_postal="69001", ville="Lyon")
        self.lot = Lot.objects.create(bien=self.bien, designation="T3", type="appartement")
        self.code = f"societe-{self.sci.pk}"
        self.url = f"/comptabilite/dossier/{self.code}/reprise-fec/"

    def _deposer(self, contenu=FEC, nom=NOM):
        r = self.client.post(self.url, {"fichier": SimpleUploadedFile(nom, contenu, content_type="text/plain")})
        return r, ImportFEC.objects.first()

    def _formulaire(self, imp, **modifs):
        d = {"charges": "on", "recettes": "on", "annee_reprise": "2026"}
        for compte, c in imp.correspondances.items():
            d[f"c_{compte}"], d[f"b_{compte}"] = c["cible"], c["bien"]
        for compte, r in imp.reglages_immos.items():
            p = f"i_{compte}_"
            d.update({f"{p}designation": r["designation"], f"{p}categorie": r["categorie"], f"{p}bien": r["bien"],
                      f"{p}mise_en_service": r["mise_en_service"], f"{p}duree": str(r["duree"])})
            if r["inclure"]:
                d[f"{p}inclure"] = "on"
        d.update(modifs)
        return d

    def test_onglet_reprise_fec_dans_le_dossier(self):
        r = self.client.get(f"/comptabilite/dossier/{self.code}/immobilisations/")
        self.assertContains(r, "Reprise FEC")
        self.assertContains(self.client.get(self.url), "Déposer un FEC")

    def test_depot_propose_la_correspondance(self):
        r, imp = self._deposer()
        self.assertRedirects(r, f"{self.url}{imp.pk}/")
        c = imp.correspondances
        self.assertEqual(c["60610000"]["cible"], "rubrique:electricite")
        self.assertEqual(c["63512000"]["cible"], "rubrique:taxe_fonciere")
        self.assertEqual(c["61600000"]["cible"], "rubrique:pno")
        self.assertEqual(c["66110000"]["cible"], "rubrique:interets_pret")
        self.assertEqual(c["68112000"]["cible"], "ignorer")
        self.assertEqual(c["70600000"]["cible"], "recette")
        self.assertEqual(c["60610000"]["bien"], f"b{self.bien.pk}")
        self.assertNotIn("51200000", c)  # banque : solde de bilan, pas une charge

    def test_immobilisations_proposees_avec_cumul_et_duree(self):
        _, imp = self._deposer()
        i = imp.reglages_immos
        self.assertEqual(set(i), {"21100000", "21310000", "21840000"})
        self.assertEqual((i["21310000"]["categorie"], i["21310000"]["duree"], i["21310000"]["cumul"]), ("gros_oeuvre", 50, "9000.00"))
        self.assertEqual((i["21840000"]["categorie"], i["21840000"]["duree"], i["21840000"]["cumul"]), ("mobilier", 7, "3000.00"))
        self.assertEqual(i["21310000"]["mise_en_service"], "2023-01-01")  # 9 000 € amortis à 3 000 € par an
        self.assertTrue(i["21310000"]["mes_estimee"])
        self.assertEqual((i["21100000"]["categorie"], i["21100000"]["duree"]), ("terrain", 0))

    def test_simulation_n_enregistre_rien(self):
        _, imp = self._deposer()
        r = self.client.get(f"{self.url}{imp.pk}/")
        self.assertContains(r, "Simulation : ce qui sera créé")
        s = r.context["s"]
        self.assertEqual(s["nb_factures"], 4)
        self.assertEqual(s["total_factures"], Decimal("2770.50"))
        self.assertEqual((s["nb_loyers"], s["total_loyers"]), (2, Decimal("1600")))
        self.assertEqual(s["nb_immos"], 3)
        self.assertEqual(s["dotation_reprise"], Decimal("4000"))
        self.assertEqual(s["emprunts_fec"], Decimal("120000"))
        self.client.post(f"{self.url}{imp.pk}/", self._formulaire(imp, action="simuler"))
        self.assertFalse(PieceComptable.objects.exists())
        self.assertFalse(Immobilisation.objects.exists())

    def test_enregistrer_puis_annuler(self):
        _, imp = self._deposer()
        r = self.client.post(f"{self.url}{imp.pk}/", self._formulaire(imp, action="enregistrer"))
        self.assertRedirects(r, f"{self.url}{imp.pk}/")
        imp.refresh_from_db()
        self.assertEqual(imp.etat, ImportFEC.Etat.IMPORTE)
        edf = PieceComptable.objects.get(rubrique="electricite")
        self.assertEqual((edf.bien, edf.montant, edf.fournisseur, edf.numero_facture, edf.date_paiement),
                         (self.bien, Decimal("120.50"), "EDF", "F-123", date(2025, 3, 10)))
        self.assertEqual(PieceComptable.objects.count(), 4)
        self.assertEqual(list(EncaissementHistorique.objects.order_by("mois").values_list("mois", "montant")),
                         [(date(2025, 1, 1), Decimal("800")), (date(2025, 2, 1), Decimal("800"))])
        constructions = Immobilisation.objects.get(categorie="gros_oeuvre")
        self.assertEqual((constructions.valeur, constructions.amortissements_anterieurs, constructions.annee_reprise, constructions.origine),
                         (Decimal("150000"), Decimal("9000"), 2026, "fec"))
        self.assertTrue(SoldeFEC.objects.filter(compte="16400000", credit=Decimal("120000")).exists())
        # registre : dotation 2026 recalculée par l'appli
        r = self.client.get(f"/comptabilite/dossier/{self.code}/immobilisations/?annee=2026")
        self.assertEqual(r.context["total"]["dotation"], Decimal("4000"))
        # annulation
        self.client.post(f"{self.url}{imp.pk}/annuler/")
        imp.refresh_from_db()
        self.assertEqual(imp.etat, ImportFEC.Etat.ANNULE)
        self.assertFalse(PieceComptable.objects.exists() or Immobilisation.objects.exists() or EncaissementHistorique.objects.exists()
                         or SoldeFEC.objects.exists())

    def test_meme_fichier_refuse_apres_import(self):
        _, imp = self._deposer()
        self.client.post(f"{self.url}{imp.pk}/", self._formulaire(imp, action="enregistrer"))
        r = self.client.post(self.url, {"fichier": SimpleUploadedFile(NOM, FEC)}, follow=True)
        self.assertContains(r, "déjà été importé")
        self.assertEqual(ImportFEC.objects.count(), 1)

    def test_annee_cloturee_non_reprise(self):
        AnneeCloturee.objects.create(annee=2025)
        _, imp = self._deposer()
        self.client.post(f"{self.url}{imp.pk}/", self._formulaire(imp, action="enregistrer"))
        self.assertFalse(PieceComptable.objects.exists())
        imp.refresh_from_db()
        self.assertEqual(imp.resume["verrouillees"], 4)

    def test_facture_deja_saisie_pas_en_double(self):
        PieceComptable.objects.create(bien=self.bien, date=date(2025, 3, 10), montant=Decimal("120.50"), rubrique="electricite")
        _, imp = self._deposer()
        s = fec.simuler(imp, fec.dossiers.trouver(self.code))
        self.assertEqual((s["nb_factures"], s["nb_doublons"]), (3, 1))

    def test_correspondance_modifiee_et_compte_ignore(self):
        _, imp = self._deposer()
        self.client.post(f"{self.url}{imp.pk}/", self._formulaire(imp, action="enregistrer", **{
            "c_61600000": "ignorer", "c_60610000": "rubrique:eau", "i_21840000_duree": "10"}))
        self.assertFalse(PieceComptable.objects.filter(rubrique="pno").exists())
        self.assertTrue(PieceComptable.objects.filter(rubrique="eau").exists())
        self.assertEqual(Immobilisation.objects.get(categorie="mobilier").duree, 10)

    def test_interets_ignores_si_le_bien_a_un_pret(self):
        Pret.objects.create(proprietaire=self.sci, bien=self.bien, banque="CA", montant=Decimal("150000"), taux=Decimal("2"),
                            duree_mois=240, date_premiere_echeance=date(2023, 2, 1))
        _, imp = self._deposer()
        self.assertEqual(imp.correspondances["66110000"]["cible"], "ignorer")

    def test_correspondance_reprise_du_dernier_import(self):
        _, imp = self._deposer()
        self.client.post(f"{self.url}{imp.pk}/", self._formulaire(imp, action="enregistrer", c_61600000="rubrique:autre"))
        _, imp2 = self._deposer(FEC + b"\r\n", "123456789FEC20261231.txt")
        self.assertNotEqual(imp2.pk, imp.pk)
        self.assertEqual(imp2.correspondances["61600000"]["cible"], "rubrique:autre")

    def test_plusieurs_biens_d_apres_le_libelle(self):
        autre = Bien.objects.create(proprietaire=self.sci, nom="Brotteaux", adresse="2 rue", code_postal="69006", ville="Lyon")
        Lot.objects.create(bien=autre, designation="T2", type="appartement")
        contenu = FEC.decode().replace("PNO 2025", "PNO Brotteaux 2025").encode()
        _, imp = self._deposer(contenu)
        self.assertEqual(imp.correspondances["61600000"]["bien"], "auto")
        imp.options["bien_defaut"] = f"b{self.bien.pk}"
        s = fec.simuler(imp, fec.dossiers.trouver(self.code))
        pno = next(p for p in s["pieces"] if p["compte"] == "61600000")
        self.assertEqual(pno["bien"], autre)
        edf = next(p for p in s["pieces"] if p["compte"] == "60610000")
        self.assertEqual(edf["bien"], self.bien)  # pas de nom de bien : bien choisi par défaut

    def test_sci_ir_codee_en_lignes_2072_sans_immobilisations(self):
        sci_ir = Proprietaire.objects.create(nom="SCI Alfab", type=Proprietaire.Type.SCI_IR)
        b = Bien.objects.create(proprietaire=sci_ir, nom="Vaugneray", adresse="1 rue", code_postal="69670", ville="Vaugneray")
        Lot.objects.create(bien=b, designation="Maison", type="maison")
        r = self.client.post(f"/comptabilite/dossier/2072-{sci_ir.pk}/reprise-fec/", {"fichier": SimpleUploadedFile(NOM, FEC)})
        imp = ImportFEC.objects.get(dossier=f"2072-{sci_ir.pk}")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(imp.correspondances["61600000"]["cible"], "ligne:8")
        self.assertEqual(imp.correspondances["63512000"]["cible"], "ligne:12")
        self.assertEqual(imp.reglages_immos, {})

    def test_abandonner_un_import_en_preparation(self):
        _, imp = self._deposer()
        self.client.post(f"{self.url}{imp.pk}/annuler/")
        self.assertFalse(ImportFEC.objects.exists())
