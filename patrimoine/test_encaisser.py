"""Une seule action « Encaisser » : paiement prérempli, trop-perçu, reliquat, CAF, annulation, dépôt de garantie,
régularisation des charges, remise et remboursement de l'avance."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Bail, Bien, Echeance, Locataire, Lot, MouvementDepot, Paiement, Proprietaire, Versement

URL = "/admin/patrimoine/echeance/virement/"


class EncaisserTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI E", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.bail = Bail.objects.create(lot=Lot.objects.create(bien=bien, designation="T2"), type=Bail.Type.NU,
                                        date_debut=date(2026, 1, 1), loyer_hc=Decimal("750"), charges=Decimal("50"), jour_echeance=5)
        self.bail.locataires.add(Locataire.objects.create(nom="Durand"))
        for mois in (10, 11):
            Echeance.generer_pour_mois(date(2026, mois, 1))
        self.oct, self.nov = Echeance.objects.order_by("periode")

    def encaisser(self, montant, parts, depot="", mode="virement", echeance=None, suivre=False):
        donnees = {"etape": "valider", "bail": self.bail.pk, "date": "2026-11-12", "montant": montant, "mode": mode, "notes": "",
                   "part_depot": depot}
        if echeance:
            donnees["echeance"] = echeance.pk
        donnees.update({f"part-{e.pk}": p for e, p in parts})
        return self.client.post(URL, donnees, follow=suivre)

    def statut(self, e):
        e.refresh_from_db()
        return e.statut

    def test_bouton_prerempli_loyer_choisi_d_abord(self):
        r = self.client.get(f"{URL}?bail={self.bail.pk}&echeance={self.nov.pk}&montant=800.00&next=/admin/")
        self.assertContains(r, "le loyer de novembre 2026")
        # le loyer choisi passe avant le plus ancien
        self.assertContains(r, f'name="part-{self.nov.pk}" value="800,00"')
        self.assertContains(r, f'name="part-{self.oct.pk}" value="0,00"')
        self.assertContains(r, 'name="next" value="/admin/"')
        self.assertEqual(Versement.objects.count(), 0)  # rien n'est enregistré avant « Valider »
        r = self.client.post(URL, {"etape": "valider", "bail": self.bail.pk, "echeance": self.nov.pk, "next": "/admin/",
                                   "date": "2026-11-12", "montant": "800", "mode": "virement", "notes": "",
                                   f"part-{self.nov.pk}": "800", f"part-{self.oct.pk}": "0"})
        self.assertRedirects(r, "/admin/", fetch_redirect_response=False)
        self.assertEqual(self.statut(self.nov), Echeance.Statut.PAYE)
        self.assertEqual(self.statut(self.oct), Echeance.Statut.A_PAYER)

    def test_somme_superieure_regle_deux_loyers_puis_avance(self):
        self.encaisser("2000", [(self.oct, "800"), (self.nov, "800")])
        self.assertEqual(self.statut(self.oct), Echeance.Statut.PAYE)
        self.assertEqual(self.statut(self.nov), Echeance.Statut.PAYE)
        self.assertEqual(Versement.objects.get().avance, Decimal("400"))

    def test_somme_inferieure_laisse_un_reliquat_comble_ensuite(self):
        self.encaisser("500", [(self.oct, "500"), (self.nov, "0")])
        self.assertEqual(self.statut(self.oct), Echeance.Statut.PARTIEL)
        self.assertEqual(self.oct.reste_du, Decimal("300"))
        parts = Versement.repartition_proposee(self.bail, Decimal("800"))
        self.assertEqual([(e.periode.month, p) for e, p in parts], [(10, Decimal("300")), (11, Decimal("500"))])

    def test_caf_puis_locataire(self):
        self.encaisser("350", [(self.oct, "350")], mode="caf")
        self.encaisser("450", [(self.oct, "450")])
        self.assertEqual(self.statut(self.oct), Echeance.Statut.PAYE)
        self.assertEqual(sorted(p.mode for p in self.oct.paiements.all()), ["caf", "virement"])

    def test_annuler_un_paiement_remet_le_loyer_a_payer(self):
        self.encaisser("800", [(self.oct, "800")])
        v = Versement.objects.get()
        r = self.client.post(f"/admin/patrimoine/versement/{v.pk}/change/", {"notes": "", "rembourse": "0", "annule": "on",
                             "motif_annulation": "", "paiements-TOTAL_FORMS": "0", "paiements-INITIAL_FORMS": "0"})
        self.assertContains(r, "Indiquez pourquoi")  # motif obligatoire
        r = self.client.post(f"/admin/patrimoine/versement/{v.pk}/change/", {
            "notes": "", "rembourse": "0", "annule": "on", "motif_annulation": "Chèque sans provision",
            "paiements-TOTAL_FORMS": "1", "paiements-INITIAL_FORMS": "1", "paiements-0-id": v.paiements.get().pk,
            "paiements-0-versement": v.pk})
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertTrue(v.annule)
        self.assertEqual(v.avance, 0)
        self.assertEqual(self.statut(self.oct), Echeance.Statut.A_PAYER)
        self.assertContains(self.client.get("/admin/patrimoine/versement/"), "Annulé")
        # une avance annulée ne s'impute plus sur les loyers suivants
        Echeance.generer_pour_mois(date(2026, 12, 1))
        self.assertEqual(Echeance.objects.get(periode=date(2026, 12, 1)).montant_paye, 0)

    def test_depot_de_garantie_dans_le_premier_paiement(self):
        self.encaisser("1550", [(self.oct, "800"), (self.nov, "0")], depot="750")
        v = Versement.objects.get()
        self.assertEqual(v.avance, 0)
        depot = MouvementDepot.objects.get()
        self.assertEqual((depot.montant, depot.type, depot.versement), (Decimal("750"), "encaissement", v))
        self.assertEqual(self.statut(self.oct), Echeance.Statut.PAYE)
        r = self.encaisser("100", [(self.nov, "0")], depot="200")
        self.assertContains(r, "ne peut pas dépasser")

    def test_regularisation_des_charges_a_part(self):
        r = self.client.get(f"/admin/patrimoine/echeance/add/?nature=regularisation&loyer_hc=0&bail={self.bail.pk}")
        self.assertContains(r, "Régularisation des charges")
        regul = Echeance.objects.create(bail=self.bail, nature=Echeance.Nature.REGULARISATION, periode=date(2026, 11, 1),
                                        loyer_hc=0, charges=Decimal("120"), date_exigibilite=date(2026, 11, 30))
        # même mois que le loyer : pas de conflit, et la génération des loyers ne la touche pas
        Echeance.generer_pour_mois(date(2026, 11, 1))
        self.assertEqual(Echeance.objects.filter(periode=date(2026, 11, 1)).count(), 2)
        regul.refresh_from_db()
        self.assertEqual(regul.charges, Decimal("120"))
        self.assertContains(self.client.get(f"/admin/patrimoine/echeance/?bail={self.bail.pk}"), "Régularisation des charges")
        self.encaisser("120", [(self.oct, "0"), (self.nov, "0"), (regul, "120")])
        self.assertEqual(self.statut(regul), Echeance.Statut.PAYE)

    def test_remise_solde_sans_encaissement(self):
        self.encaisser("500", [(self.oct, "500")])
        self.oct.refresh_from_db()
        self.oct.remise, self.oct.motif_remise = Decimal("300"), "Geste commercial (dégât des eaux)"
        self.oct.save()
        self.oct.mettre_a_jour_statut()
        self.assertEqual(self.statut(self.oct), Echeance.Statut.PAYE)
        self.assertEqual(self.oct.reste_du, 0)
        self.assertEqual(self.oct.document[0], "recu-partiel")  # pas de quittance pour 800 € : seuls 500 € ont été payés
        liste = self.client.get(f"/admin/patrimoine/echeance/?bail={self.bail.pk}&reglement=tous")
        self.assertContains(liste, "Soldé (remise)")
        self.assertNotContains(liste, ">Quittance<")

    def test_rembourser_le_trop_percu(self):
        self.encaisser("1000", [(self.oct, "800"), (self.nov, "0")])
        v = Versement.objects.get()
        base = {"notes": "", "paiements-TOTAL_FORMS": "1", "paiements-INITIAL_FORMS": "1",
                "paiements-0-id": v.paiements.get().pk, "paiements-0-versement": v.pk}
        r = self.client.post(f"/admin/patrimoine/versement/{v.pk}/change/", {**base, "rembourse": "300"})
        self.assertContains(r, "200")  # on ne rembourse que l'avance non utilisée
        r = self.client.post(f"/admin/patrimoine/versement/{v.pk}/change/", {**base, "rembourse": "200"})
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.avance, 0)
        self.assertIsNotNone(v.date_remboursement)
        Echeance.generer_pour_mois(date(2026, 12, 1))
        self.assertEqual(Echeance.objects.get(periode=date(2026, 12, 1)).montant_paye, 0)

    def test_tableau_de_bord_et_relances_proposent_encaisser(self):
        from .rappels import a_faire
        from .templatetags.tableau_bord import a_faire_groupes

        groupes = a_faire_groupes(a_faire(date(2026, 11, 25)), date(2026, 11, 25))
        loyers = next(g for g in groupes if g["categorie"] == "Loyer")
        self.assertIn("?retard=oui", loyers["encaisser"])  # deux loyers : la liste, chaque ligne a son bouton
        self.assertContains(self.client.get("/impayes/relancer/"), "Encaisser")
        self.assertContains(self.client.get(f"/impayes/relancer/?bail={self.bail.pk}"), f"{URL}?bail={self.bail.pk}&amp;montant=1600.00")

    def test_tout_encaisser_cree_des_paiements_recus(self):
        self.client.post("/admin/patrimoine/echeance/", {"action": "marquer_paye", "_selected_action": [self.oct.pk, self.nov.pk],
                                                         "reglement": "tous"})
        self.assertEqual(Versement.objects.count(), 2)
        self.assertFalse(Paiement.objects.filter(versement__isnull=True).exists())


class RepriseHistoriqueTests(TestCase):
    """Loyers encaissés (historique) → paiements reçus, y compris un montant qui couvre deux mois."""

    def setUp(self):
        from .models import EncaissementHistorique

        self.H = EncaissementHistorique
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI G", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Saint Pierre", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Garage Saint Pierre", type=Lot.Type.PARKING)
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 6, 1), loyer_hc=Decimal("65"), jour_echeance=1)
        self.bail.locataires.add(Locataire.objects.create(nom="Johannel"))

    def test_un_montant_qui_couvre_deux_mois(self):
        from .reprise_historique import convertir

        self.H.objects.create(lot=self.lot, mois=date(2026, 1, 1), montant=Decimal("130"))
        self.H.objects.create(lot=self.lot, mois=date(2026, 3, 1), montant=Decimal("65"), note="virement mars")
        n, laissees = convertir(aujourdhui=date(2026, 11, 25))
        self.assertEqual((n, laissees), (2, []))
        self.assertFalse(self.H.objects.exists())
        janvier, mars = Versement.objects.order_by("date")
        self.assertEqual((janvier.date, janvier.montant, janvier.avance), (date(2026, 1, 1), Decimal("130"), 0))
        self.assertEqual(sorted(p.echeance.periode.month for p in janvier.paiements.all()), [1, 2])
        self.assertEqual([p.echeance.periode.month for p in mars.paiements.all()], [3])
        self.assertIn("virement mars", mars.notes)
        for mois in (1, 2, 3):
            self.assertEqual(Echeance.objects.get(periode=date(2026, mois, 1)).statut, Echeance.Statut.PAYE)
        self.assertFalse(Echeance.objects.filter(periode=date(2026, 4, 1)).exists())  # pas de loyer inventé sans historique
        self.assertContains(self.client.get("/admin/patrimoine/versement/"), "Johannel")

    def test_lignes_laissees(self):
        from .reprise_historique import convertir

        self.H.objects.create(lot=self.lot, mois=date(2025, 1, 1), montant=Decimal("65"))  # avant le bail
        Echeance.generer_pour_mois(date(2026, 10, 1))
        e = Echeance.objects.get()
        v = Versement.objects.create(bail=self.bail, date=date(2026, 10, 3), montant=Decimal("65"))
        v.repartir([(e, Decimal("65"))])
        self.H.objects.create(lot=self.lot, mois=date(2026, 10, 1), montant=Decimal("65"))  # déjà saisi dans l'appli
        n, laissees = convertir(aujourdhui=date(2026, 11, 25))
        self.assertEqual(n, 0)
        self.assertEqual(sorted(r for _, r in laissees), ["aucun bail ce mois-là : le bail de Johannel commence le 01/06/2025", "un paiement est déjà enregistré ce mois-là"])
        self.assertEqual(self.H.objects.count(), 2)

    def test_saisie_dans_l_historique_devient_un_paiement_recu(self):
        r = self.client.post("/admin/patrimoine/encaissementhistorique/add/",
                             {"lot": self.lot.pk, "mois": "01/02/2026", "montant": "65", "charges": "0", "note": ""})
        self.assertRedirects(r, "/admin/patrimoine/versement/", fetch_redirect_response=False)
        self.assertEqual(Versement.objects.get().paiements.get().echeance.periode, date(2026, 2, 1))


class RepriseAvantDateEntreeTests(TestCase):
    """Bail saisi avec une date d'entrée postérieure à l'historique (cas du garage) et mois à 0 €."""

    def setUp(self):
        from .models import EncaissementHistorique

        self.H = EncaissementHistorique
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI G", type=Proprietaire.Type.SCI_IR)
        bien = Bien.objects.create(proprietaire=proprio, nom="Saint Pierre", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=bien, designation="Garage Saint Pierre", type=Lot.Type.PARKING)
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2026, 8, 1), loyer_hc=Decimal("65"), jour_echeance=1)
        self.bail.locataires.add(Locataire.objects.create(nom="JOHANNEL", prenom="Fabrice"))
        for mois, montant in [(1, "130"), (3, "130"), (5, "130"), (7, "130"), (8, "65"), (9, "0")]:
            self.H.objects.create(lot=self.lot, mois=date(2026, mois, 1), montant=Decimal(montant))

    def test_reprise_automatique_puis_rattachement(self):
        from .reprise_historique import convertir

        n, laissees = convertir(aujourdhui=date(2026, 11, 25))
        self.assertEqual(n, 2)  # août converti, septembre à 0 € retiré sans paiement
        self.assertEqual(Versement.objects.get().montant, Decimal("65"))
        self.assertEqual(len(laissees), 4)
        self.assertIn("JOHANNEL commence le 01/08/2026", laissees[0][1])
        liste = self.client.get("/admin/patrimoine/encaissementhistorique/").content.decode()
        self.assertIn("commence le 01/08/2026", liste)
        # action « Rattacher au locataire suivant du lot et reprendre »
        self.client.post("/admin/patrimoine/encaissementhistorique/",
                         {"action": "reprendre_bail_suivant", "_selected_action": list(self.H.objects.values_list("pk", flat=True))})
        self.assertFalse(self.H.objects.exists())
        self.assertEqual(Versement.objects.filter(bail=self.bail).count(), 5)
        janvier = Versement.objects.get(date=date(2026, 1, 1))
        self.assertEqual(sorted(p.echeance.periode.month for p in janvier.paiements.all()), [1, 2])
        for mois in range(1, 9):
            self.assertEqual(Echeance.objects.get(periode=date(2026, mois, 1)).statut, Echeance.Statut.PAYE, mois)

    def test_reconstruire_regle_chaque_mois_et_retire_les_zero(self):
        """Première version : un mois payé 646,99 € sur un loyer calculé à 650 € laissait 3,01 € dû, comblés par le mois
        suivant, et ainsi de suite (« 696,99 € » sur un loyer de 700 €)."""
        from .reprise_historique import NOTE, reconstruire

        bail = Bail.objects.create(lot=Lot.objects.create(bien=self.lot.bien, designation="Chazay"), type=Bail.Type.MEUBLE,
                                   date_debut=date(2026, 1, 1), loyer_hc=Decimal("650"), charges=Decimal("50"), jour_echeance=5)
        sept = Echeance.objects.create(bail=bail, periode=date(2026, 9, 1), loyer_hc=650, charges=50, date_exigibilite=date(2026, 9, 5))
        octo = Echeance.objects.create(bail=bail, periode=date(2026, 10, 1), loyer_hc=650, charges=50, date_exigibilite=date(2026, 10, 5))
        v9 = Versement.objects.create(bail=bail, date=date(2026, 9, 1), montant=Decimal("696.99"), notes=NOTE)
        v9.repartir([(sept, Decimal("696.99"))])
        v10 = Versement.objects.create(bail=bail, date=date(2026, 10, 1), montant=Decimal("700"), notes=f"{NOTE} – BOUVET")
        v10.repartir([(sept, Decimal("3.01")), (octo, Decimal("696.99"))])  # l'ancienne répartition en chaîne
        zero = Versement.objects.create(bail=self.bail, date=date(2026, 9, 1), montant=0, notes=NOTE)
        reconstruire(aujourdhui=date(2026, 11, 25))
        self.assertFalse(Versement.objects.filter(pk=zero.pk).exists())
        sept.refresh_from_db()
        octo.refresh_from_db()
        self.assertEqual((sept.total, sept.statut), (Decimal("696.99"), Echeance.Statut.PAYE))  # le loyer de septembre = ce qui a été payé
        self.assertEqual((octo.montant_paye, octo.statut), (Decimal("700"), Echeance.Statut.PAYE))
        self.assertEqual([p.echeance for p in v10.paiements.all()], [octo])
