from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from .calculs import notaire, pret
from .forms import FraisNotaireForm, PretForm
from .registre import SIMULATEURS, disponibles


class FraisNotaireCalculTests(SimpleTestCase):
    def test_emoluments_bareme_par_tranches(self):
        # 6 500 × 3,870 % + 10 500 × 1,596 % + 43 000 × 1,064 % + 190 000 × 0,799 %
        self.assertEqual(notaire.emoluments_ht(D(250000)), D("2394.75"))
        self.assertEqual(notaire.emoluments_ht(D(5000)), D("193.50"))
        self.assertEqual(notaire.emoluments_ht(D(17000)), D("419.13"))

    def test_ancien_departement_a_5_pourcent(self):
        r = notaire.calculer(D(250000), notaire.ANCIEN, "69")
        self.assertEqual([l.montant for l in r.droits], [D(12500), D(3000), D(296)])
        self.assertEqual(r.total_droits, D(15796))
        self.assertEqual([l.montant for l in r.notaire], [D("2394.75"), D("478.95"), D("250.00"), D("1200.00")])
        self.assertEqual(r.total, D("20119.70"))
        self.assertEqual(r.pourcentage, D("8.05"))
        self.assertEqual(r.cout_total, D("270119.70"))

    def test_departement_reste_a_4_5_et_indre(self):
        self.assertEqual(notaire.taux_departemental("06"), D("4.50"))
        self.assertEqual(notaire.taux_departemental("36"), D("3.80"))
        self.assertEqual(notaire.taux_departemental("75"), D("5.00"))
        self.assertEqual(notaire.calculer(D(100000), notaire.ANCIEN, "36").droits[0].montant, D(3800))

    def test_primo_accedant_reste_a_4_5(self):
        r = notaire.calculer(D(250000), notaire.ANCIEN, "69", primo_accedant=True)
        self.assertEqual(r.droits[0].montant, D(11250))
        self.assertEqual(r.droits[2].montant, D(267))  # 2,37 % de 11 250 = 266,625
        self.assertIn("primo", r.droits[0].detail)
        # Indre : déjà sous 4,5 %, le primo-accédant garde 3,80 %
        self.assertEqual(notaire.taux_departemental("36", primo_accedant=True), D("3.80"))

    def test_mobilier_retire_de_la_base(self):
        r = notaire.calculer(D(200000), notaire.ANCIEN, "69", mobilier=D(10000))
        self.assertEqual(r.base, D(190000))
        self.assertEqual(r.droits[0].montant, D(9500))
        self.assertEqual(r.prix, D(200000))

    def test_neuf_taxe_reduite_sur_le_prix_hors_tva(self):
        r = notaire.calculer(D(300000), notaire.NEUF, "69")
        self.assertEqual([l.montant for l in r.droits], [D(1750), D(41)])
        self.assertEqual(r.notaire[0].montant, D("2394.75"))  # émoluments sur 250 000 € HT
        self.assertLess(r.pourcentage, D(3))

    def test_csi_minimum(self):
        r = notaire.calculer(D(10000), notaire.ANCIEN, "69", formalites=0)
        self.assertEqual(r.notaire[2].montant, D(15))

    def test_tous_les_departements_ont_un_nom(self):
        self.assertEqual(len(notaire.DEPARTEMENTS), 101)
        self.assertTrue(set(notaire.TAUX_DEPARTEMENTAUX) <= set(notaire.NOMS_DEPARTEMENTS))


class PretCalculTests(SimpleTestCase):
    def test_mensualite(self):
        self.assertEqual(pret.mensualite(D(200000), D("3.4"), 240), D("1149.67"))
        self.assertEqual(pret.mensualite(D(12000), D(0), 12), D("1000.00"))

    def test_tableau_rembourse_tout_le_capital(self):
        s = pret.simuler(D(200000), D("3.4"), 240)
        self.assertEqual(len(s.echeances), 240)
        self.assertEqual(sum(e.capital for e in s.echeances), D(200000))
        self.assertEqual(s.echeances[-1].restant, D(0))
        self.assertEqual(s.echeances[0].interets, D("566.67"))
        self.assertEqual(s.cout_interets, sum(e.mensualite for e in s.echeances) - D(200000))
        self.assertEqual(len(s.par_annee()), 20)
        self.assertEqual(s.par_annee()[-1]["restant"], D(0))

    def test_assurance_sur_capital_initial_fixe(self):
        s = pret.simuler(D(200000), D("3.4"), 240, D("0.30"))
        self.assertEqual(s.assurance_mensuelle, D("50.00"))
        self.assertEqual(s.cout_assurance, D(12000))
        self.assertEqual(s.mensualite_totale, D("1199.67"))

    def test_assurance_sur_capital_restant_degressive(self):
        s = pret.simuler(D(200000), D("3.4"), 240, D("0.30"), pret.ASSURANCE_RESTANT)
        self.assertEqual(s.echeances[0].assurance, D("50.00"))
        self.assertLess(s.echeances[-1].assurance, D(1))
        self.assertLess(s.cout_assurance, D(12000))

    def test_taeg(self):
        # Sans frais ni assurance : le taux actuariel du taux nominal (3,4 % → 3,45 %)
        self.assertEqual(pret.simuler(D(200000), D("3.4"), 240).taeg, D("3.45"))
        avec = pret.simuler(D(200000), D("3.4"), 240, D("0.30"), frais=D(3000))
        self.assertGreater(avec.taeg, D("3.8"))
        self.assertLess(avec.taeg, D("4.2"))
        self.assertEqual(avec.cout_total, avec.cout_interets + D(12000) + D(3000))

    def test_taux_zero(self):
        s = pret.simuler(D(12000), D(0), 12)
        self.assertEqual(s.cout_interets, D(0))
        self.assertEqual(s.taeg, D(0))

    def test_taux_endettement(self):
        s = pret.simuler(D(200000), D("3.4"), 240, D("0.30"))
        self.assertEqual(s.taux_endettement(D(4000)), D("29.99"))
        self.assertEqual(s.taux_endettement(D(4000), D(300)), D("37.49"))
        self.assertIsNone(s.taux_endettement(None))


class FormulairesTests(SimpleTestCase):
    def test_montants_a_la_francaise(self):
        f = FraisNotaireForm({"prix": "250 000 €", "type_bien": "ancien", "departement": "69", "mobilier": "1 500,50"})
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["prix"], D(250000))
        self.assertEqual(f.cleaned_data["mobilier"], D("1500.50"))
        self.assertEqual(f.cleaned_data["formalites"], notaire.FORMALITES_PAR_DEFAUT)

    def test_erreurs(self):
        f = FraisNotaireForm({"prix": "abc", "type_bien": "ancien", "departement": "99"})
        self.assertFalse(f.is_valid())
        self.assertIn("prix", f.errors)
        self.assertIn("departement", f.errors)
        f = PretForm({"capital": "100000", "taux": "3,5", "duree_annees": "50", "mode_assurance": "initial", "frais": "200000"})
        self.assertFalse(f.is_valid())
        self.assertIn("duree_annees", f.errors)
        self.assertIn("frais", f.errors)


class PagesPubliquesTests(TestCase):
    """Les simulateurs s'ouvrent sans compte : ils servent de vitrine."""

    def test_accueil_liste_les_simulateurs(self):
        rep = self.client.get(reverse("simulateurs:accueil"))
        self.assertEqual(rep.status_code, 200)
        for s in SIMULATEURS:
            self.assertContains(rep, escape(s.titre))
        self.assertContains(rep, "Bientôt")
        self.assertContains(rep, "Se connecter")

    def test_chaque_simulateur_disponible_a_sa_page(self):
        for s in disponibles():
            rep = self.client.get(reverse(s.nom_url))
            self.assertEqual(rep.status_code, 200, s.slug)
            self.assertContains(rep, escape(s.titre))

    def test_frais_de_notaire(self):
        rep = self.client.get(reverse("simulateurs:frais-de-notaire"),
                              {"prix": "250000", "type_bien": "ancien", "departement": "69", "formalites": "1200"})
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(rep.context["r"].total, D("20119.70"))
        self.assertContains(rep, "Rhône")
        self.assertContains(rep, "?capital=270120")  # lien vers le simulateur de prêt

    def test_frais_de_notaire_erreur(self):
        rep = self.client.get(reverse("simulateurs:frais-de-notaire"), {"prix": "", "type_bien": "ancien", "departement": "69"})
        self.assertIsNone(rep.context["r"])
        self.assertContains(rep, "class=\"erreur\"")

    def test_pret(self):
        rep = self.client.get(reverse("simulateurs:pret-immobilier"),
                              {"capital": "200 000", "taux": "3,4", "duree_annees": "20", "taux_assurance": "0,3",
                               "mode_assurance": "initial", "revenus": "4000"})
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(rep.context["r"].mensualite_totale, D("1199.67"))
        self.assertEqual(rep.context["endettement"], D("29.99"))
        self.assertContains(rep, "Tableau d'amortissement")

    def test_pret_pre_rempli_depuis_le_notaire(self):
        rep = self.client.get(reverse("simulateurs:pret-immobilier"), {"capital": "270120"})
        self.assertIsNone(rep.context["r"])
        self.assertContains(rep, 'value="270120"')

    def test_utilisateur_connecte_retrouve_son_espace_et_le_menu(self):
        user = User.objects.create_superuser("fabien", "f@example.com", "x")
        self.client.force_login(user)
        rep = self.client.get(reverse("simulateurs:accueil"))
        self.assertContains(rep, "Mon espace")
        self.assertNotContains(rep, "Découvrir l'application")
        rep = self.client.get(reverse("admin:index"))
        self.assertContains(rep, reverse("simulateurs:frais-de-notaire"))


# =====================================================================================================================
# Module investisseur
# =====================================================================================================================
from datetime import date  # noqa: E402
from unittest import mock  # noqa: E402

from . import dvf  # noqa: E402
from .calculs import investissement as inv  # noqa: E402
from .forms import departement_de, lire_parametres, nombre  # noqa: E402
from .models import SimulationInvestissement  # noqa: E402


def sans_travaux(p):
    for l in p.lots:
        l.travaux = 0
    return p


class InvestissementCalculTests(SimpleTestCase):
    def test_exemple_retrouve_les_chiffres_de_l_excel(self):
        # Excel « Simulateur invest-Oullins » : emprunt 284 741 €, mensualité 1 755,63 €, intérêts an 1 11 781,72 €,
        # cash-flow mensuel avant impôt 46,75 €, rentabilité brute ≈ 8,1 %
        r = inv.simuler(inv.exemple())
        self.assertAlmostEqual(r.emprunt, 284741, delta=1)
        self.assertAlmostEqual(r.mensualite, 1755.63, delta=0.01)
        self.assertAlmostEqual(r.interets_an1, 11781.72, delta=0.01)
        self.assertAlmostEqual(r.cash_flow_avant_impot, 46.75, delta=0.01)
        self.assertAlmostEqual(r.loyers_an, 26280)
        self.assertAlmostEqual(r.renta_brute, 0.0811, delta=0.0005)

    def test_micro_foncier_refuse_au_dela_de_15000(self):
        r = inv.simuler(inv.exemple())
        nu_micro = next(g for g in r.regimes if g.id == "nu-micro")
        self.assertFalse(nu_micro.eligible)
        self.assertNotEqual(r.meilleur.id, "nu-micro")

    def test_lmnp_reel_les_amortissements_effacent_le_benefice(self):
        r = inv.simuler(sans_travaux(inv.exemple()))
        lmnp = next(g for g in r.regimes if g.id == "lmnp-reel")
        self.assertEqual(lmnp.base, 0)
        self.assertEqual(lmnp.impot, 0)
        self.assertGreater(lmnp.report, 0)
        self.assertEqual(r.meilleur.id, "lmnp-reel")

    def test_nu_reel_impot_marginal_a_30_pourcent(self):
        r = inv.simuler(sans_travaux(inv.exemple()))
        nu = next(g for g in r.regimes if g.id == "nu-reel")
        # base ≈ 9 847 € à la TMI de 30 % + 17,2 % de prélèvements sociaux
        self.assertAlmostEqual(nu.impot, nu.base * 0.472, delta=5)

    def test_travaux_deduits_en_location_nue(self):
        # Oullins : 83 000 € de travaux déduits l'année 1 → déficit foncier imputé (10 700 €) et le reste reporté
        r = inv.simuler(inv.exemple())
        nu = next(g for g in r.regimes if g.id == "nu-reel")
        self.assertEqual(nu.base, -10700)
        self.assertLess(nu.impot, 0)  # économie d'impôt
        self.assertGreater(nu.report, 60000)
        self.assertIn("Travaux déduits", nu.detail)

    def test_deficit_foncier_plafonne_a_10700(self):
        base, report = inv._foncier_reel(L=5000, C=30000, I=4000)
        self.assertEqual(base, -10700)
        self.assertAlmostEqual(report, 29000 - 10700)

    def test_impot_revenu_quotient_familial(self):
        self.assertEqual(inv.parts_fiscales("couple", 2), (2, 3))
        self.assertEqual(inv.parts_fiscales("seul", 3), (1, 3))
        self.assertEqual(inv.impot_revenu(10000, "seul", 0), 0)
        # 30 000 € seul : (29 579 – 11 600) × 11 % + (30 000 – 29 579) × 30 %
        self.assertAlmostEqual(inv.impot_revenu(30000, "seul", 0), 17979 * 0.11 + 421 * 0.30, places=2)
        # plafonnement : l'avantage des enfants ne dépasse pas 1 807 € par demi-part
        sans, avec = inv.impot_revenu(300000, "couple", 0), inv.impot_revenu(300000, "couple", 2)
        self.assertAlmostEqual(sans - avec, 1807 * 2, places=2)

    def test_prix_cible_cash_flow_nul(self):
        p = inv.exemple()
        prix = inv.prix_cible(p, 0, "avant")
        self.assertEqual(prix % 100, 0)
        self.assertGreaterEqual(inv.simuler(p, prix).cash_flow_avant_impot, 0)
        self.assertLess(inv.simuler(p, prix + 200).cash_flow_avant_impot, 0)
        self.assertIsNone(inv.prix_cible(p, 5000, "avant"))  # hors d'atteinte

    def test_courbe_decroissante(self):
        c = inv.courbe(inv.exemple())
        self.assertEqual(len(c["prix"]), 31)
        self.assertTrue(all(a > b for a, b in zip(c["series"]["avant"], c["series"]["avant"][1:])))
        self.assertTrue(all(v is None for v in c["series"]["nu-micro"]))

    def test_lots_mixtes_ajoutent_votre_repartition(self):
        p = inv.exemple()
        p.lots[2].meuble = False
        r = inv.simuler(p)
        self.assertEqual(r.regimes[0].id, "mixte")
        self.assertTrue(r.lots_mixtes)

    def test_sci_is_et_associes(self):
        p = sans_travaux(inv.exemple())
        p.associes = [inv.Associe(50, 72000, "couple", 2), inv.Associe(50, 15000, "seul", 0)]
        r = inv.simuler(p)
        sci_ir = next(g for g in r.regimes if g.id == "sci-ir")
        nu = next(g for g in r.regimes if g.id == "nu-reel")
        self.assertLess(sci_ir.impot, nu.impot)  # le second associé est imposé à 11 %
        sci_is = next(g for g in r.regimes if g.id == "sci-is")
        self.assertEqual(sci_is.impot, 0)  # amortissements : pas de bénéfice

    def test_detail_par_lot_au_prorata_de_la_surface(self):
        r = inv.simuler(inv.exemple())
        self.assertAlmostEqual(sum(l.part_prix for l in r.lignes_lots), 190000)
        self.assertAlmostEqual(r.lignes_lots[0].part_prix, 190000 * 24 / 68)

    def test_scenario_loyers_actuels(self):
        p = inv.exemple()
        p.scenario = "actuel"
        self.assertEqual(inv.simuler(p).loyers_mois, 0)  # lots vacants
        p.lots[0].loue, p.lots[0].loyer_actuel = True, 500
        self.assertEqual(inv.simuler(p).loyers_mois, 500)

    def test_chiffrage_quatre_modes_et_imprevus(self):
        detail = {"imprevus": 10, "lignes": [
            {"poste": "Électricité", "mode": "m2", "prix": 90},                       # 90 × 24 m² = 2 160
            {"poste": "Fenêtres", "mode": "unite", "prix": 900, "quantite": 2},       # 1 800
            {"poste": "Cuisine", "mode": "forfait", "prix": 5000},                    # 5 000
            {"poste": "Salle de bain", "mode": "devis", "devis": 7400, "prix": 6000},  # 7 400 sur devis
            {"poste": "Véranda", "mode": "forfait", "prix": 1000, "agrandissement": True},
        ]}
        t = inv.total_chiffrage(detail, 24)
        self.assertEqual(t.estime, 2160 + 1800 + 5000 + 1000)
        self.assertEqual(t.devis, 7400)
        self.assertAlmostEqual(t.imprevus, 996)  # 10 % des postes estimés seulement
        self.assertAlmostEqual(t.total, 9960 + 7400 + 996)
        self.assertEqual(t.agrandissement, 1000)
        self.assertEqual(t.par_poste["Salle de bain"], (0, 7400))

    def test_chiffrage_remplace_le_montant_du_lot_et_parties_communes(self):
        p = inv.exemple()
        p.lots[0].travaux_detail = {"imprevus": 0, "lignes": [{"poste": "Peinture", "mode": "m2", "prix": 30}]}
        p.travaux_communs = {"imprevus": 0, "lignes": [{"poste": "Toiture", "mode": "m2", "prix": 150, "quantite": 80},
                                                       {"poste": "Extension", "mode": "devis", "devis": 6800,
                                                        "agrandissement": True}]}
        r = inv.simuler(p)
        communs = 12000 + 6800
        self.assertEqual(r.travaux_communs, communs)
        self.assertEqual(r.travaux, 30 * 24 + 27000 + 29000 + communs)
        self.assertEqual(r.travaux_deductibles, r.travaux - 6800)
        # parties communes réparties au prorata de la surface
        self.assertAlmostEqual(r.lignes_lots[0].travaux, 720 + communs * 24 / 68)
        self.assertAlmostEqual(sum(l.travaux for l in r.lignes_lots), r.travaux)
        self.assertEqual(r.recap_travaux["Toiture"], (12000, 0))
        self.assertEqual(r.recap_travaux["Saisi sans détail"], (56000, 0))
        self.assertAlmostEqual(r.travaux_sur_devis, 6800)

    def test_chiffrage_par_defaut_selon_le_niveau(self):
        leger, lourd = inv.chiffrage_par_defaut(niveau=0), inv.chiffrage_par_defaut(niveau=2)
        self.assertLess(inv.total_chiffrage(leger, 30).total, inv.total_chiffrage(lourd, 30).total)
        self.assertEqual(inv.chiffrage_par_defaut(communs=True)["lignes"][0]["poste"], "Toiture")

    def test_aller_retour_json(self):
        p = inv.exemple()
        self.assertEqual(inv.Parametres.depuis_dict(p.en_dict()), p)


class InvestissementSaisieTests(SimpleTestCase):
    def test_nombres_a_la_francaise(self):
        self.assertEqual(nombre("190 000"), 190000)
        self.assertEqual(nombre("4,2 %"), 4.2)
        self.assertEqual(nombre("1 200 €"), 1200)
        self.assertEqual(nombre("abc", 7), 7)

    def test_departement_du_code_postal(self):
        self.assertEqual(departement_de("12 rue X, 69600 Oullins"), "69")
        self.assertEqual(departement_de("97400 Saint-Denis"), "974")
        self.assertEqual(departement_de("20000 Ajaccio"), "2A")
        self.assertEqual(departement_de("sans code"), "")

    def test_lire_parametres(self):
        p = lire_parametres({"nom": "Test", "adresse": "1 rue X, 06000 Nice", "nb_lots": "2", "lot-0-type": "t1",
                             "lot-0-surface": "25,5", "lot-0-meuble": "1", "lot-1-type": "inconnu", "prix": "150 000",
                             "taux": "3,5", "assoc-0-parts": "60", "assoc-1-parts": "40", "assoc-1-revenu": "20000"})
        self.assertEqual(len(p.lots), 2)
        self.assertEqual(p.lots[0].surface, 25.5)
        self.assertTrue(p.lots[0].meuble)
        self.assertFalse(p.lots[1].meuble)
        self.assertEqual(p.lots[1].type, "t2")
        self.assertEqual(p.departement, "06")
        self.assertEqual((p.prix, p.taux, p.duree), (150000, 3.5, 20))
        self.assertEqual([a.parts for a in p.associes], [60, 40])
        self.assertEqual(p.associes[1].revenu_imposable, 20000)

    def test_nombre_de_lots_limite(self):
        self.assertEqual(len(lire_parametres({"nb_lots": "99"}).lots), inv.MAX_LOTS)
        self.assertEqual(len(lire_parametres({"nb_lots": "0"}).lots), 1)


CSV_DVF = """id_mutation,date_mutation,numero_disposition,nature_mutation,valeur_fonciere,adresse_numero,adresse_suffixe,adresse_nom_voie,adresse_code_voie,code_postal,code_commune,nom_commune,code_departement,ancien_code_commune,ancien_nom_commune,id_parcelle,ancien_id_parcelle,numero_volume,lot1_numero,lot1_surface_carrez,lot2_numero,lot2_surface_carrez,lot3_numero,lot3_surface_carrez,lot4_numero,lot4_surface_carrez,lot5_numero,lot5_surface_carrez,nombre_lots,code_type_local,type_local,surface_reelle_bati,nombre_pieces_principales,code_nature_culture,nature_culture,code_nature_culture_speciale,nature_culture_speciale,surface_terrain,longitude,latitude
2025-1,2025-03-01,1,Vente,140000,12,,GRANDE RUE,,69600,69149,Oullins,69,,,p1,,,1,,,,,,,,,,1,2,Appartement,40,2,,,,,,4.807,45.714
2025-1,2025-03-01,1,Vente,140000,12,,GRANDE RUE,,69600,69149,Oullins,69,,,p1,,,,,,,,,,,,,1,3,Dépendance,,0,,,,,,4.807,45.714
2025-2,2025-04-01,1,Vente,300000,3,,RUE A,,69600,69149,Oullins,69,,,p2,,,1,,2,,,,,,,,2,2,Appartement,40,2,,,,,,4.807,45.714
2025-2,2025-04-01,1,Vente,300000,3,,RUE A,,69600,69149,Oullins,69,,,p2,,,2,,,,,,,,,,2,2,Appartement,50,2,,,,,,4.807,45.714
2025-3,2025-05-01,1,Echange,90000,4,,RUE B,,69600,69149,Oullins,69,,,p3,,,1,,,,,,,,,,1,2,Appartement,30,1,,,,,,4.807,45.714
2025-4,2025-06-01,1,Vente,250000,5,,RUE C,,69600,69149,Oullins,69,,,p4,,,,,,,,,,,,,0,1,Maison,80,4,,,,,,4.808,45.715
"""


class DvfTests(SimpleTestCase):
    def test_analyse_garde_les_ventes_d_un_seul_logement(self):
        ventes = dvf.analyser_csv(CSV_DVF)
        # 2025-2 : deux appartements dans la même vente (prix global) ; 2025-3 : échange
        self.assertEqual(sorted((v["type"], v["prix"]) for v in ventes), [("Appartement", 140000), ("Maison", 250000)])

    def test_synthese_mediane_et_rayon(self):
        ventes = [{"date": "2025-0%d-01" % (k % 9 + 1), "type": "Appartement", "surface": 50, "prix": 50 * (3000 + 100 * k),
                   "pieces": "2", "lat": 45.714, "lon": 4.807 + k * 0.0001, "adresse": "Rue"} for k in range(12)]
        ventes.append({**ventes[0], "lat": 46.5})  # très loin
        s = dvf.synthese(ventes, 45.714, 4.807, "Appartement", aujourdhui=date(2026, 11, 18))
        self.assertEqual(s["nombre"], 12)
        self.assertEqual(s["rayon"], 500)
        self.assertEqual(s["mediane"], 3550)
        self.assertLessEqual(s["q1"], s["mediane"])
        self.assertEqual(sum(s["histogramme"]["effectifs"]), 12)

    def test_synthese_elargit_le_rayon(self):
        ventes = [{"date": "2025-06-01", "type": "Appartement", "surface": 40, "prix": 120000, "pieces": "", "lat": 45.714,
                   "lon": 4.807 + 0.01, "adresse": ""}]  # ≈ 780 m
        s = dvf.synthese(ventes, 45.714, 4.807, "Appartement", aujourdhui=date(2026, 11, 18))
        self.assertEqual(s["rayon"], 2000)
        self.assertEqual(s["nombre"], 1)

    def test_parcours_complet_sans_reseau(self):
        reponse_geo = b'{"features":[{"geometry":{"coordinates":[4.807,45.714]},"properties":{"label":"Grande Rue 69600 Oullins","citycode":"69149","city":"Oullins"}}]}'

        def lire(url):
            if "geocodage" in url:
                return reponse_geo
            if "/2025/" in url:
                return CSV_DVF.encode()
            raise dvf.urllib.error.HTTPError(url, 404, "absent", None, None)

        from django.core.cache import cache

        cache.clear()
        with mock.patch.object(dvf, "_lire", side_effect=lire):
            res = dvf.prix_du_marche("Grande Rue, 69600 Oullins", "Appartement", aujourdhui=date(2026, 11, 18))
        self.assertEqual(res["nombre"], 1)
        self.assertEqual(res["mediane"], 3500)
        self.assertEqual(res["lieu"]["insee"], "69149")

    def test_alsace_moselle_non_couverte(self):
        from django.core.cache import cache

        cache.clear()
        geo = b'{"features":[{"geometry":{"coordinates":[7.75,48.58]},"properties":{"label":"Strasbourg","citycode":"67482"}}]}'
        with mock.patch.object(dvf, "_lire", return_value=geo):
            with self.assertRaisesMessage(dvf.DvfIndisponible, "Alsace-Moselle"):
                dvf.prix_du_marche("Strasbourg")

    def test_service_injoignable(self):
        from django.core.cache import cache

        cache.clear()
        with mock.patch.object(dvf, "_lire", side_effect=OSError("réseau")):
            with self.assertRaises(dvf.DvfIndisponible):
                dvf.prix_du_marche("12 rue X, 69600 Oullins")


class InvestissementPagesTests(TestCase):
    DONNEES = {"nom": "Projet test", "adresse": "12 Grande Rue, 69600 Oullins", "nb_lots": "2",
               "lot-0-type": "t1", "lot-0-surface": "25", "lot-0-meuble": "1", "lot-0-loyer_cible": "600",
               "lot-1-type": "t3", "lot-1-surface": "60", "lot-1-loyer_cible": "850", "lot-1-etage": "1er",
               "prix_affiche": "160000", "prix": "150000", "taux": "3,5", "duree": "20"}

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@x.fr", "pw")

    def test_page_publique_et_vedette(self):
        r = self.client.get(reverse("simulateurs:accueil"))
        self.assertContains(r, "Investissement locatif")
        self.assertContains(r, "vedette")
        self.assertNotContains(r, "Bientôt</span></div>\n    {% ")
        r = self.client.get(reverse("simulateurs:rentabilite"))
        self.assertContains(r, "Nombre de lots")
        r = self.client.get(reverse("simulateurs:rentabilite") + "?exemple=1")
        self.assertContains(r, "Comparatif des régimes fiscaux")
        self.assertContains(r, "Se connecter pour enregistrer")

    def test_demarrage_cree_les_lots(self):
        r = self.client.post(reverse("simulateurs:rentabilite"), {"nom": "Immeuble", "adresse": "1 rue X, 06000 Nice",
                                                                  "nb_lots": "4", "lot-0-type": "t3", "lot-0-meuble": "0"})
        self.assertEqual(len(r.context["p"].lots), 4)
        self.assertEqual(r.context["p"].lots[0].type, "t3")
        self.assertEqual(r.context["p"].departement, "06")

    def test_recalcul_en_fragment(self):
        r = self.client.post(reverse("simulateurs:rentabilite_calcul"), self.DONNEES, HTTP_X_FRAGMENT="1")
        self.assertContains(r, 'id="bloc-resume"')
        self.assertContains(r, 'id="courbe-donnees"')
        self.assertNotContains(r, "<html")

    def test_anonyme_ne_peut_pas_enregistrer(self):
        r = self.client.post(reverse("simulateurs:simulation_enregistrer"), self.DONNEES)
        self.assertRedirects(r, f"{reverse('admin:login')}?next={reverse('simulateurs:simulation_enregistrer')}",
                             fetch_redirect_response=False)
        self.assertFalse(SimulationInvestissement.objects.exists())
        r = self.client.get(reverse("simulateurs:mes_simulations"))
        self.assertEqual(r.status_code, 302)

    def test_enregistrer_lister_dupliquer_supprimer(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("simulateurs:simulation_enregistrer"), self.DONNEES)
        s = SimulationInvestissement.objects.get()
        self.assertRedirects(r, s.get_absolute_url())
        self.assertEqual(s.parametres.prix, 150000)
        self.assertEqual(len(s.parametres.lots), 2)
        r = self.client.get(s.get_absolute_url())
        self.assertContains(r, "Enregistrer les modifications")
        self.client.post(reverse("simulateurs:simulation_enregistrer"), {**self.DONNEES, "simulation": s.pk, "prix": "140000"})
        s.refresh_from_db()
        self.assertEqual(s.parametres.prix, 140000)
        self.assertContains(self.client.get(reverse("simulateurs:mes_simulations")), "Projet test")
        self.client.post(reverse("simulateurs:simulation_dupliquer", args=[s.pk]))
        self.assertEqual(SimulationInvestissement.objects.count(), 2)
        self.client.post(reverse("simulateurs:simulation_supprimer", args=[s.pk]))
        self.assertEqual(SimulationInvestissement.objects.count(), 1)

    def test_creer_le_bien_et_ses_lots(self):
        from patrimoine.models import Bien, Proprietaire

        self.client.force_login(self.admin)
        pr = Proprietaire.objects.create(nom="SCI Test", type=Proprietaire.Type.SCI_IR)
        s = SimulationInvestissement.objects.create(nom="Projet", donnees=lire_parametres(self.DONNEES).en_dict())
        r = self.client.get(reverse("simulateurs:simulation_creer_bien", args=[s.pk]))
        self.assertContains(r, "69600")
        r = self.client.post(reverse("simulateurs:simulation_creer_bien", args=[s.pk]),
                             {"proprietaire": pr.pk, "nom": "Immeuble Oullins", "adresse": "12 Grande Rue",
                              "code_postal": "69600", "ville": "Oullins"})
        bien = Bien.objects.get()
        self.assertRedirects(r, reverse("admin:patrimoine_bien_change", args=[bien.pk]), fetch_redirect_response=False)
        self.assertEqual(bien.prix_acquisition, 150000)
        lots = list(bien.lots.order_by("designation"))
        self.assertEqual([(l.type, l.nb_pieces, l.meuble) for l in lots], [("appartement", 1, True), ("appartement", 3, False)])
        s.refresh_from_db()
        self.assertEqual(s.bien, bien)

    def test_droits_consultation_sans_enregistrement(self):
        from patrimoine import droits

        u = User.objects.create_user("lecteur", password="pw", is_staff=True)
        droits.appliquer(u, {"investissement": droits.CONSULTATION})
        s = SimulationInvestissement.objects.create(nom="Projet", donnees=lire_parametres(self.DONNEES).en_dict())
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("simulateurs:mes_simulations")).status_code, 200)
        r = self.client.get(s.get_absolute_url())
        self.assertNotContains(r, "Enregistrer les modifications")
        self.assertEqual(self.client.post(reverse("simulateurs:simulation_supprimer", args=[s.pk])).status_code, 403)
        u2 = User.objects.create_user("aucun", password="pw", is_staff=True)
        self.client.force_login(u2)
        self.assertEqual(self.client.get(reverse("simulateurs:mes_simulations")).status_code, 403)

    def test_prix_du_marche_json(self):
        with mock.patch.object(dvf, "prix_du_marche", side_effect=dvf.DvfIndisponible("Adresse introuvable")):
            r = self.client.get(reverse("simulateurs:rentabilite_dvf"), {"adresse": "nulle part"})
        self.assertEqual(r.json(), {"erreur": "Adresse introuvable"})


class ChiffrageTravauxPagesTests(TestCase):
    """Devis joints au chiffrage des travaux : envoi, droits, purge, copie."""

    def setUp(self):
        import tempfile

        self.dossier = tempfile.mkdtemp()
        self.reglage = self.settings(PRIVE_ROOT=self.dossier)
        self.reglage.enable()
        self.admin = User.objects.create_superuser("admin", "a@x.fr", "pw")
        self.s = SimulationInvestissement.objects.create(
            nom="Projet", donnees=lire_parametres(InvestissementPagesTests.DONNEES).en_dict())

    def tearDown(self):
        import shutil

        self.reglage.disable()
        shutil.rmtree(self.dossier, ignore_errors=True)

    def envoyer(self, nom="devis.pdf", contenu=b"%PDF-1.4 test"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post(reverse("simulateurs:simulation_devis", args=[self.s.pk]),
                                {"fichier": SimpleUploadedFile(nom, contenu)})

    def donnees_avec_piece(self, piece):
        import json

        ligne = {"poste": "Électricité", "mode": "devis", "devis": 7200, "entreprise": "Elec Pro",
                 "piece": piece.pk, "piece_nom": piece.nom, "piece_url": piece.url}
        return {**InvestissementPagesTests.DONNEES, "simulation": self.s.pk,
                "lot-0-travaux_detail": json.dumps({"imprevus": 10, "lignes": [ligne]})}

    def test_envoi_du_devis_et_validation(self):
        from simulateurs.models import DevisTravaux

        self.assertEqual(self.envoyer().status_code, 302)  # anonyme : connexion demandée
        self.client.force_login(self.admin)
        r = self.envoyer()
        self.assertEqual(r.status_code, 200)
        d = DevisTravaux.objects.get(pk=r.json()["id"])
        self.assertTrue(r.json()["url"].startswith("/prive/devis-travaux/"))
        self.assertTrue(d.fichier.storage.exists(d.fichier.name))
        self.assertEqual(self.envoyer("virus.exe").status_code, 400)
        self.assertEqual(self.client.post(reverse("simulateurs:simulation_devis", args=[self.s.pk])).status_code, 400)
        self.assertEqual(DevisTravaux.objects.count(), 1)

    def test_droit_consultation_ne_peut_pas_joindre(self):
        from patrimoine import droits

        u = User.objects.create_user("lecteur", password="pw", is_staff=True)
        droits.appliquer(u, {"investissement": droits.CONSULTATION})
        self.client.force_login(u)
        self.assertEqual(self.envoyer().status_code, 403)

    def test_resultats_et_purge_a_l_enregistrement(self):
        from simulateurs.models import DevisTravaux

        self.client.force_login(self.admin)
        gardee = DevisTravaux.objects.get(pk=self.envoyer().json()["id"])
        orpheline = DevisTravaux.objects.get(pk=self.envoyer("autre.pdf").json()["id"])
        self.client.post(reverse("simulateurs:simulation_enregistrer"), self.donnees_avec_piece(gardee))
        self.assertEqual(list(self.s.devis.values_list("pk", flat=True)), [gardee.pk])
        self.assertFalse(orpheline.fichier.storage.exists(orpheline.fichier.name))
        r = self.client.get(self.s.get_absolute_url())
        self.assertContains(r, "Travaux par corps de métier")
        self.assertContains(r, "Elec Pro")
        self.assertContains(r, gardee.url)
        # le fichier est servi aux personnes autorisées
        self.assertEqual(self.client.get(gardee.url).status_code, 200)

    def test_copie_garde_ses_devis(self):
        from simulateurs.models import DevisTravaux

        self.client.force_login(self.admin)
        d = DevisTravaux.objects.get(pk=self.envoyer().json()["id"])
        self.client.post(reverse("simulateurs:simulation_enregistrer"), self.donnees_avec_piece(d))
        self.client.post(reverse("simulateurs:simulation_dupliquer", args=[self.s.pk]))
        copie = SimulationInvestissement.objects.exclude(pk=self.s.pk).get()
        d2 = copie.devis.get()
        self.assertEqual(copie.donnees["lots"][0]["travaux_detail"]["lignes"][0]["piece"], d2.pk)
        # supprimer l'original ne retire pas le fichier partagé avec la copie
        self.client.post(reverse("simulateurs:simulation_supprimer", args=[self.s.pk]))
        self.assertTrue(d2.fichier.storage.exists(d2.fichier.name))
        self.client.post(reverse("simulateurs:simulation_supprimer", args=[copie.pk]))
        self.assertFalse(d2.fichier.storage.exists(d2.fichier.name))


class LectureChiffrageTests(SimpleTestCase):
    def test_json_nettoye(self):
        import json

        from simulateurs.forms import lire_chiffrage

        self.assertIsNone(lire_chiffrage(""))
        self.assertIsNone(lire_chiffrage("pas du json"))
        self.assertIsNone(lire_chiffrage(json.dumps({"lignes": "x"})))
        c = lire_chiffrage(json.dumps({"imprevus": 250, "lignes": [
            {"poste": "Sols", "mode": "pirate", "prix": -5, "piece": "3", "piece_url": "https://ailleurs.fr/x.pdf"},
        ] + [{"poste": "P", "mode": "forfait", "prix": 1}] * 50}))
        self.assertEqual(c["imprevus"], 100)
        self.assertEqual(len(c["lignes"]), 40)
        l = c["lignes"][0]
        self.assertEqual((l["mode"], l["prix"], l["piece"], l["piece_url"]), ("forfait", 0, None, ""))


class JeanbrunTests(SimpleTestCase):
    """Dispositif Jeanbrun : amortissement plafonné, conditions et alertes."""

    def projet_nu(self, **kw):
        from simulateurs.calculs import investissement as inv

        p = inv.exemple()
        for l in p.lots:
            l.meuble = False
        for k, v in kw.items():
            setattr(p, k, v)
        return p

    def regime(self, p):
        from simulateurs.calculs import investissement as inv

        return inv.simuler(p).jeanbrun

    def test_amortissement_ancien_et_plafond(self):
        # ancien intermédiaire : 3 % de (80 % de 190 000 + 83 000 de travaux) = 7 050 €, sous le plafond de 8 000 €
        j = self.regime(self.projet_nu())
        self.assertTrue(j.eligible)
        self.assertAlmostEqual(j.amortissements, 7050)
        # très social : 4 % de 235 000 = 9 400 €, plafond 12 000 € moins 5 000 € déjà pris ailleurs → 7 000 €
        j = self.regime(self.projet_nu(jeanbrun_niveau="tres_social", jeanbrun_autres=5000))
        self.assertAlmostEqual(j.amortissements, 7000)
        # neuf intermédiaire : 3,5 % de 80 % du prix, travaux exclus
        j = self.regime(self.projet_nu(jeanbrun_etat="neuf"))
        self.assertAlmostEqual(j.amortissements, 190000 * 0.8 * 0.035)

    def test_conditions_bloquantes(self):
        from simulateurs.calculs import investissement as inv

        j = inv.simuler(inv.exemple()).jeanbrun  # lots meublés
        self.assertFalse(j.eligible)
        self.assertIn("meublés", j.motif)
        p = self.projet_nu()
        for l in p.lots:
            l.travaux = 10000
        j = self.regime(p)
        self.assertFalse(j.eligible)
        self.assertIn("30 %", j.motif)
        p = self.projet_nu()
        p.lots[0].type = "maison"
        self.assertFalse(self.regime(p).eligible)

    def test_loyer_au_dessus_du_plafond(self):
        # zone A : 14,50 €/m² ; lot 001 de 24 m² → 348 € au plus pour 730 € visés
        j = self.regime(self.projet_nu(jeanbrun_zone="A"))
        self.assertTrue(j.eligible)
        self.assertEqual(len(j.conditions_ko), 1)
        self.assertIn("lot 001", j.conditions_ko[0])
        self.assertAlmostEqual(j.manque_loyer, (730 - 348) * 12 + (730 - 319) * 12 * 2)
        sans = self.regime(self.projet_nu())
        self.assertLess(j.cash_flow, sans.cash_flow)
        # plafond saisi pour la commune, assez haut : plus d'alerte
        j = self.regime(self.projet_nu(jeanbrun_zone="A", jeanbrun_plafond=34))
        self.assertEqual(j.conditions_ko, [])
        self.assertEqual(j.manque_loyer, 0)

    def test_page_affiche_conditions_et_alerte(self):
        from django.test import Client

        donnees = {**InvestissementPagesTests.DONNEES, "lot-0-meuble": "0", "jeanbrun_zone": "B1", "regime": "jeanbrun"}
        r = Client().post(reverse("simulateurs:rentabilite_calcul"), donnees, HTTP_X_FRAGMENT="1")
        self.assertContains(r, "Dispositif Jeanbrun : les conditions à remplir")
        self.assertContains(r, "Le projet ne semble pas remplir les conditions")
        self.assertContains(r, "Travaux d&#x27;au moins 30 %")
