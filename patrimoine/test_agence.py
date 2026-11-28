"""Biens confiés à une agence : loyers encaissés par l'agence, acomptes sur le relevé, récap annuel.
Toutes les données ci-dessous sont fictives."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from . import agence as ag
from . import rapprochement
from .annexe2072 import loyers_detailles
from .models import (AgenceGestion, Bail, Bien, CompteBancaire, DeclarationFaite, Echeance, LigneRecapAgence, LigneReleve,
                     Locataire, Lot, PieceComptable, Proprietaire, RecapAgence, Versement)

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.proprio = Proprietaire.objects.create(nom="Martin Paul", type=Proprietaire.Type.NOM_PROPRE)
        self.agence = AgenceGestion.objects.create(nom="Agence du Parc", mot_releve="AG DU PARC")
        self.bien = Bien.objects.create(proprietaire=self.proprio, nom="Rue des Tilleuls", adresse="3 rue", code_postal="69003",
                                        ville="Lyon", regime_fiscal=Bien.RegimeFiscal.NU, agence=self.agence)
        self.autre = Bien.objects.create(proprietaire=self.proprio, nom="Les Lilas", adresse="1 rue", code_postal="69000",
                                         ville="Lyon", regime_fiscal=Bien.RegimeFiscal.NU)
        self.bail = self._bail(self.bien, "Apt 2", "Durand", D("700"), D("50"))
        self.bail_direct = self._bail(self.autre, "Apt 1", "Bernard", D("600"), D("0"))
        for m in range(1, 13):
            Echeance.generer_pour_mois(date(2025, m, 1))
        self.compte = CompteBancaire.objects.create(proprietaire=self.proprio, nom="Compte perso", mode=CompteBancaire.Mode.RELEVE)

    def _bail(self, bien, lot, nom, loyer, charges):
        b = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation=lot), type=Bail.Type.NU, date_debut=date(2025, 1, 1),
                                loyer_hc=loyer, charges=charges, jour_echeance=5)
        b.locataires.add(Locataire.objects.create(nom=nom, prenom="Jean"))
        return b

    def e(self, mois, bail=None):
        return Echeance.objects.get(bail=bail or self.bail, periode=date(2025, mois, 1))

    def ligne(self, libelle, montant, jour):
        return LigneReleve.objects.create(compte=self.compte, date=jour, libelle=libelle, montant=D(montant),
                                          empreinte=LigneReleve.calculer_empreinte(self.compte.pk, jour, D(montant), libelle,
                                                                                   LigneReleve.objects.count()))

    def proposer(self, ligne):
        return rapprochement.proposer(ligne, rapprochement.Contexte(self.compte))


class LoyersEncaissesParAgenceTests(Base):
    def test_loyers_echus_encaisses_par_agence_a_l_echeance(self):
        n = ag.encaisser_loyers_agence(date(2025, 12, 31))
        self.assertEqual(n, 12)
        mars = self.e(3)
        self.assertEqual(mars.statut, Echeance.Statut.PAYE)
        v = Versement.objects.get(paiements__echeance=mars)
        self.assertEqual((v.mode, v.date, v.montant), ("agence", date(2025, 3, 5), D("750")))
        self.assertIn("Agence du Parc", v.notes)
        # le bien géré en direct n'est pas touché : ses loyers restent à encaisser
        self.assertEqual(self.e(3, self.bail_direct).statut, Echeance.Statut.A_PAYER)

    def test_loyer_pas_encore_echu_attend(self):
        ag.encaisser_loyers_agence(date(2025, 6, 4))
        self.assertEqual(self.e(5).statut, Echeance.Statut.PAYE)
        self.assertEqual(self.e(6).statut, Echeance.Statut.A_PAYER)

    def test_agence_depuis_respecte(self):
        self.bien.agence_depuis = date(2025, 7, 1)
        self.bien.save()
        ag.encaisser_loyers_agence(date(2025, 12, 31))
        self.assertEqual(self.e(6).statut, Echeance.Statut.A_PAYER)
        self.assertEqual(self.e(7).statut, Echeance.Statut.PAYE)

    def test_jamais_dans_les_relances(self):
        ag.encaisser_loyers_agence(date(2025, 12, 31))
        r = self.client.get("/impayes/relancer/")
        self.assertNotContains(r, "Durand")

    def test_loyers_bruts_dans_la_declaration(self):
        ag.encaisser_loyers_agence(date(2025, 12, 31))
        lignes = loyers_detailles(self.bien, 2025)
        self.assertEqual(len(lignes), 12)
        self.assertEqual(sum(l["montant"] for l in lignes), D("8400"))  # 12 × 700 hors provisions de charges

    def test_encaisser_le_mois_affiche_encaisse_par_agence(self):
        ag.encaisser_loyers_agence(date(2025, 12, 31))
        r = self.client.get("/loyers/encaisser-le-mois/?mois=2025-03")
        self.assertContains(r, "Encaissé par l&#x27;agence")

    def test_choisir_l_agence_sur_le_bien_encaisse_les_loyers_echus(self):
        self.bien.agence = None
        self.bien.save()
        data = {"proprietaire": self.proprio.pk, "nom": self.bien.nom, "adresse": "3 rue", "code_postal": "69003", "ville": "Lyon",
                "regime_fiscal": "nu", "option_fiscale": "reel", "agence": self.agence.pk, "agence_depuis": "",
                "lots-TOTAL_FORMS": 1, "lots-INITIAL_FORMS": 1, "lots-0-id": self.bail.lot.pk, "lots-0-bien": self.bien.pk,
                "lots-0-designation": "Apt 2", "lots-0-type": self.bail.lot.type or "appartement"}
        r = self.client.post(f"/admin/patrimoine/bien/{self.bien.pk}/change/", data, follow=True)
        self.assertEqual(r.status_code, 200)
        self.bien.refresh_from_db()
        self.assertEqual(self.bien.agence, self.agence)
        self.assertEqual(self.e(1).statut, Echeance.Statut.PAYE)


class ReleveAgenceTests(Base):
    def setUp(self):
        super().setUp()
        ag.encaisser_loyers_agence(date(2025, 12, 31))

    def test_acompte_reconnu_jamais_un_loyer(self):
        p = self.proposer(self.ligne("VIR AG DU PARC ACOMPTE MARS", "680", date(2025, 3, 20)))
        self.assertEqual((p.nature, p.certitude, p.agence), ("agence", rapprochement.SURE, self.agence))
        self.assertIn("Acompte", p.titre)

    def test_nom_de_l_agence_suffit(self):
        self.agence.mot_releve = ""
        self.agence.save()
        p = self.proposer(self.ligne("VIR SEPA AGENCE DU PARC", "680", date(2025, 3, 20)))
        self.assertEqual(p.nature, "agence")

    def test_paiement_de_l_agence_jamais_relie_a_une_ligne(self):
        # même montant que le loyer, mais sans le nom de l'agence : pas de « déjà saisi » sur un paiement de l'agence
        p = self.proposer(self.ligne("VIR INCONNU", "750", date(2025, 3, 6)))
        self.assertIsNone(p.versement)

    def test_valider_puis_derapprocher(self):
        l = self.ligne("VIR AG DU PARC", "680", date(2025, 3, 20))
        rapprochement.appliquer(l, self.proposer(l))
        l.refresh_from_db()
        self.assertEqual((l.nature, l.agence, l.statut), ("agence", self.agence, LigneReleve.Statut.RAPPROCHEE))
        self.assertEqual(ag.acomptes_du_releve(self.agence, self.proprio, 2025), (D("680"), 1))
        rapprochement.derapprocher(l)
        l.refresh_from_db()
        self.assertIsNone(l.agence)

    def test_solde_du_recap_reconnu(self):
        RecapAgence.objects.create(agence=self.agence, proprietaire=self.proprio, annee=2025, solde_annonce=D("312.40"))
        l = self.ligne("VIR AG DU PARC REGUL 2025", "312.40", date(2026, 1, 15))
        p = self.proposer(l)
        self.assertEqual(p.nature, "agence")
        self.assertIn("Solde du récap 2025", p.titre)
        rapprochement.appliquer(l, p)
        self.assertEqual(RecapAgence.objects.get().ligne_solde, l)
        # le solde ne compte pas dans les acomptes
        self.assertEqual(ag.acomptes_du_releve(self.agence, self.proprio, 2026), (D("0"), 0))

    def test_choix_manuel_acompte(self):
        l = self.ligne("VIREMENT DIVERS", "500", date(2025, 4, 20))
        r = self.client.post(f"/loyers/releve-de-banque/?compte={self.compte.pk}",
                             {"ligne": l.pk, "action": "choisir", "nature": "agence", "agence": self.agence.pk})
        self.assertEqual(r.status_code, 302)
        l.refresh_from_db()
        self.assertEqual((l.nature, l.agence), ("agence", self.agence))


class RecapAgenceTests(Base):
    def setUp(self):
        super().setUp()
        ag.encaisser_loyers_agence(date(2025, 12, 31))
        self.url = f"/loyers/agence/{self.agence.pk}/{self.proprio.pk}/2025/"

    def poster(self, loyers=None, charges=(), **champs):
        data = {f"loyer-{e.pk}": "750" for e in Echeance.objects.filter(bail=self.bail)}
        data.update(loyers or {})
        data["nb_charges"] = len(charges)
        for i, (nature, libelle, code, montant) in enumerate(charges):
            data.update({f"charge-{i}-bien": self.bien.pk, f"charge-{i}-nature": nature, f"charge-{i}-libelle": libelle,
                         f"charge-{i}-code": code, f"charge-{i}-montant": montant})
        data.update(champs)
        return self.client.post(self.url, data, follow=True)

    def test_page_prerempli(self):
        r = self.client.get(self.url)
        self.assertContains(r, "Récap 2025 de l&#x27;agence Agence du Parc")
        self.assertContains(r, 'value="750.00"', count=12)
        self.assertContains(r, "Honoraires de gestion")

    def test_recap_complet(self):
        # 9 000 € encaissés − 600 € de taxe foncière − 720 € d'honoraires − 7 200 € d'acomptes = 480 € de solde
        r = self.poster(charges=[("charge", "Taxe foncière", "12", "600"), ("honoraires", "Facture de gestion 2025", "6", "720")],
                        acomptes="7200", solde_annonce="480", date_solde="2026-01-15")
        self.assertContains(r, "Récap 2025 appliqué")
        self.assertNotContains(r, "Écart de")
        self.assertContains(r, "Solde juste")
        tf, honoraires = PieceComptable.objects.filter(bien=self.bien).order_by("montant")
        self.assertEqual((tf.ligne, tf.montant, tf.date_paiement, tf.fournisseur), ("12", D("600"), date(2025, 12, 31), "Agence du Parc"))
        self.assertEqual((honoraires.ligne, honoraires.montant), ("6", D("720")))

    def test_reenregistrer_ne_double_pas_les_charges(self):
        charges = [("honoraires", "Gestion", "6", "720")]
        self.poster(charges=charges, acomptes="7200")
        self.poster(charges=charges, acomptes="7200")
        self.assertEqual(PieceComptable.objects.filter(bien=self.bien).count(), 1)

    def test_loyer_non_encaisse_repasse_en_impaye(self):
        r = self.poster(loyers={f"loyer-{self.e(4).pk}": "0", f"loyer-{self.e(5).pk}": "300"}, acomptes="6500")
        self.assertContains(r, "2 loyer(s) non encaissé(s) par l&#x27;agence, repassé(s) en impayé")
        avril, mai = self.e(4), self.e(5)
        self.assertEqual(avril.statut, Echeance.Statut.A_PAYER)
        self.assertEqual(Versement.objects.get(mode="agence", annule=True).motif_annulation,
                         "Non encaissé par l'agence selon le récap 2025")
        self.assertEqual((mai.statut, mai.montant_paye), (Echeance.Statut.PARTIEL, D("300")))
        # le lendemain, l'encaissement automatique ne le repaie pas : le récap fait foi
        ag.encaisser_loyers_agence(date(2026, 1, 2))
        self.assertEqual(self.e(4).statut, Echeance.Statut.A_PAYER)
        self.assertEqual(sum(l["montant"] for l in loyers_detailles(self.bien, 2025)), D("8400") - D("700") - D("700") + D("280"))
        r = self.client.get("/impayes/relancer/")
        self.assertContains(r, "Durand")

    def test_corriger_le_recap_remet_le_loyer(self):
        self.poster(loyers={f"loyer-{self.e(4).pk}": "0"})
        self.poster()
        self.assertEqual(self.e(4).statut, Echeance.Statut.PAYE)
        self.assertEqual(LigneRecapAgence.objects.count(), 12)

    def test_ecart_de_solde_signale(self):
        r = self.poster(charges=[("honoraires", "Gestion", "6", "720")], acomptes="7200", solde_annonce="1100")  # calculé : 9 000 − 720 − 7 200 = 1 080
        self.assertContains(r, "Écart de 20,00 €")

    def test_ecart_avec_les_acomptes_du_releve(self):
        for m in range(1, 13):
            l = self.ligne("VIR AG DU PARC", "600", date(2025, m, 20))
            rapprochement.appliquer(l, self.proposer(l))
        r = self.poster(acomptes="7000")
        self.assertContains(r, "Écart de -200,00 € entre les acomptes du récap")

    def test_acomptes_repris_du_releve_si_vides(self):
        for m in range(1, 13):
            l = self.ligne("VIR AG DU PARC", "600", date(2025, m, 20))
            rapprochement.appliquer(l, self.proposer(l))
        b = ag.bilan(RecapAgence(agence=self.agence, proprietaire=self.proprio, annee=2025), charges=[])
        self.assertEqual((b.acomptes, b.solde_calcule), (D("7200"), D("1800")))

    def test_bien_lmnp_honoraires_en_commission(self):
        self.bien.regime_fiscal = Bien.RegimeFiscal.LMNP
        self.bien.save()
        self.bien.refresh_from_db()
        self.poster(charges=[("honoraires", "Gestion", "", "720")])
        self.assertEqual(PieceComptable.objects.get(bien=self.bien).rubrique, "commission")

    def test_declaration_faite_refuse(self):
        DeclarationFaite.objects.create(annee=2025, formulaire=DeclarationFaite.Formulaire.F2044, date_declaration=date(2026, 5, 20))
        r = self.poster(charges=[("honoraires", "Gestion", "6", "720")])
        self.assertContains(r, "Récap non appliqué")
        self.assertFalse(PieceComptable.objects.exists())

    def test_liste_des_agences_avec_lien_recap(self):
        r = self.client.get("/admin/patrimoine/agencegestion/")
        self.assertContains(r, "Rue des Tilleuls")
        self.assertContains(r, f"/loyers/agence/{self.agence.pk}/{self.proprio.pk}/")
