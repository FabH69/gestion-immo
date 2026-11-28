"""Vente d'un bien : remboursements de l'acquéreur (prorata de taxe foncière, de copropriété) et virement du notaire.
Toutes les données ci-dessous sont fictives."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from . import annexe2072, compta, rapprochement, vente
from .models import AnneeCloturee, Bien, CompteBancaire, LigneReleve, Lot, PieceComptable, Proprietaire

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.proprio = Proprietaire.objects.create(nom="Fabien Test", type=Proprietaire.Type.NOM_PROPRE)
        self.bien = Bien.objects.create(proprietaire=self.proprio, nom="Rue des Tilleuls", adresse="2 rue", code_postal="69000",
                                        ville="Lyon", regime_fiscal=Bien.RegimeFiscal.NU)
        Lot.objects.create(bien=self.bien, designation="Apt 2e")
        # charges de l'année payées par le vendeur
        PieceComptable.objects.create(bien=self.bien, date=date(2026, 10, 15), montant=D("1200"), ligne="12", fournisseur="Impôts",
                                      libelle="Taxe foncière 2026")
        PieceComptable.objects.create(bien=self.bien, date=date(2026, 4, 1), montant=D("900"), ligne="13", fournisseur="Syndic",
                                      libelle="Provisions copro")

    def enregistrer(self, bien=None, **vente):
        """Enregistre la fiche du bien comme depuis l'écran (avec la zone de vente)."""
        bien = bien or self.bien
        donnees = {"proprietaire": bien.proprietaire_id, "nom": bien.nom, "adresse": bien.adresse, "code_postal": bien.code_postal,
                   "ville": bien.ville, "regime_fiscal": bien.regime_fiscal, "option_fiscale": "reel",
                   "type_habitat": bien.type_habitat or "", "regime_juridique": bien.regime_juridique or "",
                   "_save": "Enregistrer"}
        lots = list(bien.lots.all())
        donnees.update({"lots-TOTAL_FORMS": len(lots), "lots-INITIAL_FORMS": len(lots), "lots-MIN_NUM_FORMS": 0, "lots-MAX_NUM_FORMS": 1000})
        for i, lot in enumerate(lots):
            donnees.update({f"lots-{i}-id": lot.pk, f"lots-{i}-bien": bien.pk, f"lots-{i}-designation": lot.designation,
                            f"lots-{i}-type": lot.type})
        donnees.update({k: ("on" if v is True else "" if v in (None, False) else v) for k, v in vente.items()})
        donnees = {k: v for k, v in donnees.items() if v != ""}
        return self.client.post(f"/admin/patrimoine/bien/{bien.pk}/change/", donnees, follow=True)


class SaisieRemboursementsTests(Base):
    def test_zone_sur_la_fiche_du_bien(self):
        r = self.client.get(f"/admin/patrimoine/bien/{self.bien.pk}/change/")
        self.assertContains(r, "Remboursements de l&#x27;acquéreur")
        self.assertContains(r, "remboursements-vente")
        self.assertContains(r, "patrimoine/vente.js")
        self.assertContains(r, 'name="remb_taxe_fonciere"')

    def test_remboursements_deduits_des_charges_2044(self):
        r = self.enregistrer(vendu=True, date_vente="2026-11-20", prix_vente="185000", remb_taxe_fonciere="140.50",
                             remb_copro="95")
        self.assertEqual(r.status_code, 200)
        self.bien.refresh_from_db()
        self.assertEqual(self.bien.remb_taxe_fonciere, D("140.50"))
        tf = PieceComptable.objects.get(bien=self.bien, remboursement_vente="tf")
        self.assertEqual((tf.montant, tf.ligne, tf.date), (D("-140.50"), "12", date(2026, 11, 20)))
        copro = PieceComptable.objects.get(bien=self.bien, remboursement_vente="copro")
        self.assertEqual((copro.montant, copro.ligne), (D("-95.00"), "13"))
        col = annexe2072.colonne(self.bien, 2026)["valeurs"]
        self.assertEqual(col["12"], D("1059.50"))  # 1200 − 140,50
        self.assertEqual(col["13"], D("805.00"))   # 900 − 95
        self.assertEqual(col.get("1", D(0)), D(0))  # pas un loyer
        self.assertEqual(col.get("3", D(0)), D(0))  # ni une recette
        # le récapitulatif 2044 lit les mêmes montants
        self.assertIn("Rue des Tilleuls", str(compta.recap_2044(2026)))

    def test_pas_dans_le_prix_de_vente(self):
        self.enregistrer(vendu=True, date_vente="2026-11-20", prix_vente="185000", remb_taxe_fonciere="140.50")
        self.bien.refresh_from_db()
        self.assertEqual(vente.prix_connu(self.bien), D("185000"))
        r = self.client.get(f"/admin/patrimoine/proprietaire/{self.proprio.pk}/change/")
        self.assertContains(r, "Vendu le 20/11/2026")
        self.assertContains(r, "185")

    def test_modifier_puis_effacer(self):
        self.enregistrer(vendu=True, date_vente="2026-11-20", remb_taxe_fonciere="140")
        self.enregistrer(vendu=True, date_vente="2026-11-20", remb_taxe_fonciere="150")
        self.assertEqual(PieceComptable.objects.get(remboursement_vente="tf").montant, D("-150"))
        self.enregistrer(vendu=True, date_vente="2026-11-20")
        self.assertFalse(PieceComptable.objects.filter(remboursement_vente="tf").exists())

    def test_vente_annulee_supprime_les_remboursements(self):
        self.enregistrer(vendu=True, date_vente="2026-11-20", remb_copro="80")
        self.enregistrer(remb_copro="80")  # case décochée, date effacée
        self.assertFalse(PieceComptable.objects.exclude(remboursement_vente="").exists())

    def test_sans_date_de_vente_rien_n_est_compte(self):
        r = self.enregistrer(vendu=True, remb_taxe_fonciere="140")
        self.assertContains(r, "indiquez la date de vente")
        self.assertFalse(PieceComptable.objects.exclude(remboursement_vente="").exists())

    def test_autre_remboursement_a_classer(self):
        r = self.enregistrer(vendu=True, date_vente="2026-11-20", remb_autres="60", remb_autres_libelle="fioul de la cuve")
        p = PieceComptable.objects.get(remboursement_vente="autre")
        self.assertTrue(p.a_codifier)
        self.assertIn("fioul de la cuve", p.libelle)
        self.assertContains(r, "à classer")

    def test_annee_cloturee_non_modifiee(self):
        self.enregistrer(vendu=True, date_vente="2026-11-20", remb_taxe_fonciere="140")
        AnneeCloturee.objects.create(annee=2026)
        r = self.enregistrer(vendu=True, date_vente="2026-11-20", remb_taxe_fonciere="999")
        self.assertContains(r, "non modifié")
        self.assertEqual(PieceComptable.objects.get(remboursement_vente="tf").montant, D("-140"))

    def test_lmnp_rubriques(self):
        self.bien.regime_fiscal = Bien.RegimeFiscal.LMNP
        self.bien.save()
        self.enregistrer(vendu=True, date_vente="2026-11-20", remb_taxe_fonciere="140", remb_copro="95")
        tf = PieceComptable.objects.get(remboursement_vente="tf")
        copro = PieceComptable.objects.get(remboursement_vente="copro")
        self.assertEqual((tf.rubrique, tf.ligne, tf.montant), ("taxe_fonciere", "", D("-140")))
        self.assertEqual((copro.rubrique, copro.montant), ("copro", D("-95")))
        d = compta.detail_bien(self.bien, 2026, 12)
        self.assertEqual(sum(c["montant"] for c in d["taxe_fonciere"].values()), D("-140"))


class VirementNotaireTests(Base):
    def setUp(self):
        super().setUp()
        self.enregistrer(vendu=True, date_vente="2026-11-20", prix_vente="185000", remb_taxe_fonciere="140.50", remb_copro="95")
        self.bien.refresh_from_db()
        self.compte = CompteBancaire.objects.create(proprietaire=self.proprio, nom="Compte perso", mode=CompteBancaire.Mode.RELEVE)

    def ligne(self, libelle, montant, jour=date(2026, 11, 22)):
        return LigneReleve.objects.create(compte=self.compte, date=jour, libelle=libelle, montant=D(montant),
                                          empreinte=LigneReleve.calculer_empreinte(self.compte.pk, jour, D(montant), libelle))

    def proposer(self, ligne):
        return rapprochement.proposer(ligne, rapprochement.Contexte(self.compte))

    def test_virement_global_reparti(self):
        l = self.ligne("VIR SCP MARTIN NOTAIRES", "185235.50")
        p = self.proposer(l)
        self.assertEqual((p.nature, p.certitude, p.bien), ("vente", rapprochement.SURE, self.bien))
        self.assertEqual(p.ventilation[0], ("Prix de vente", D("185000.00")))
        self.assertIn(("Prorata de taxe foncière", D("140.50")), p.ventilation)
        rapprochement.appliquer(l, p)
        l.refresh_from_db()
        self.assertEqual((l.nature, l.bien), ("vente", self.bien))
        self.assertIn("Prix de vente 185 000,00 €", l.note)
        for piece in vente.pieces(self.bien):
            self.assertEqual(piece.date_paiement, date(2026, 11, 22))
        # pas un loyer
        from .models import Versement
        self.assertFalse(Versement.objects.exists())
        rapprochement.derapprocher(l)
        self.assertTrue(all(p.date_paiement is None for p in vente.pieces(self.bien)))

    def test_virement_net_du_pret_a_verifier(self):
        p = self.proposer(self.ligne("VIREMENT ETUDE DUPONT NOTAIRE", "60235.50"))
        self.assertEqual((p.nature, p.certitude), ("vente", rapprochement.VERIFIER))
        self.assertEqual(p.ventilation[0], ("Prix de vente", D("60000.00")))
        self.assertIn("écart", p.pourquoi)

    def test_loin_de_la_vente_pas_propose(self):
        p = self.proposer(self.ligne("VIR SCP MARTIN NOTAIRES", "185235.50", jour=date(2025, 1, 5)))
        self.assertNotEqual(p.nature, "vente")

    def test_choix_manuel_depuis_l_ecran(self):
        l = self.ligne("VIR 123456", "185000")
        r = self.client.get(f"/loyers/releve-de-banque/?compte={self.compte.pk}&onglet=aucune")
        self.assertContains(r, "le virement du notaire (vente d'un bien)")
        self.client.post(f"/loyers/releve-de-banque/?compte={self.compte.pk}",
                         {"ligne": l.pk, "action": "choisir", "nature": "vente", "bien_vendu": self.bien.pk})
        l.refresh_from_db()
        self.assertEqual((l.statut, l.nature, l.bien), (LigneReleve.Statut.RAPPROCHEE, "vente", self.bien))
        self.assertIn("Prix de vente 184 764,50 €", l.note)
