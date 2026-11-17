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
