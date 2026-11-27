"""Rapidité : le nombre de requêtes d'une page ne doit pas grossir avec le nombre de loyers et de paiements reçus
(sinon l'appli devient très lente sur PythonAnywhere quand l'historique s'allonge)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import controles
from .models import Bail, Bien, Echeance, Locataire, Lot, Paiement, Proprietaire, Versement

PAGES = ["/admin/", "/controles/", "/impayes/relancer/", "/finances/encaissements/", "/admin/patrimoine/lot/",
         "/admin/patrimoine/echeance/"]


@override_settings(SAUVEGARDE_AUTO=False, LOYERS_AUTO=False)
class RapiditeTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.proprio = Proprietaire.objects.create(nom="SCI R", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=self.proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.n = 0

    def creer_baux(self, nombre):
        for _ in range(nombre):
            self.n += 1
            bail = Bail.objects.create(lot=Lot.objects.create(bien=self.bien, designation=f"Apt {self.n}", surface=30), type=Bail.Type.NU,
                                       date_debut=date(2024, 1, 1), loyer_hc=Decimal("600"), charges=Decimal("50"))
            bail.locataires.add(Locataire.objects.create(nom=f"Locataire {self.n}"))

    def ajouter_mois(self, debut, fin):
        """Loyers des mois [debut, fin[ depuis janvier 2024 pour chaque bail, payés sauf un sur trois (impayés pour les relances),
        avec 50 € d'avance gardée sur chaque paiement."""
        for bail in Bail.objects.all():
            for m in range(debut, fin):
                periode = date(2024 + m // 12, m % 12 + 1, 1)
                e = Echeance.objects.create(bail=bail, periode=periode, loyer_hc=Decimal("600"), charges=Decimal("50"), date_exigibilite=periode)
                if m % 3:
                    v = Versement.objects.create(bail=bail, date=periode, montant=Decimal("700"))
                    v.repartir([(e, e.total)])

    def requetes(self, page):
        controles.oublier()
        with CaptureQueriesContext(connection) as q:
            r = self.client.get(page)
        self.assertEqual(r.status_code, 200, page)
        return len(q)

    def test_requetes_stables_quand_l_historique_grossit(self):
        self.creer_baux(3)
        self.ajouter_mois(0, 6)
        avant = {p: self.requetes(p) for p in PAGES}
        self.ajouter_mois(6, 30)  # 5 fois plus de loyers et de paiements reçus, mêmes baux
        for p in PAGES:
            apres = self.requetes(p)
            # jamais une requête par loyer ou par paiement reçu (quelques-unes d'écart tolérées : pagination, années en plus)
            self.assertLessEqual(apres - avant[p], 4, f"{p} : {avant[p]} requêtes avec 6 mois d'historique, {apres} avec 30 mois")

    def test_prefetch_reste_juste_apres_un_paiement(self):
        """Liste des paiements chargée d'avance puis nouveau paiement : le statut et le reste dû sont bien à jour."""
        self.creer_baux(1)
        self.ajouter_mois(0, 1)  # janvier 2024, impayé
        e = Echeance.objects.prefetch_related("paiements").get()
        self.assertEqual(e.reste_du, Decimal("650"))
        v = Versement.objects.create(bail=e.bail, date=date(2024, 1, 5), montant=Decimal("650"))
        v.repartir([(e, Decimal("650"))])
        e2 = Echeance.objects.prefetch_related("paiements").get()
        self.assertEqual((e2.statut, e2.reste_du), (Echeance.Statut.PAYE, Decimal("0")))
        # même objet que celui chargé avant le paiement : Paiement(echeance=e) l'a remis à jour
        p = Paiement(echeance=e, versement=Versement.objects.create(bail=e.bail, date=date(2024, 1, 6), montant=Decimal("10")),
                     date=date(2024, 1, 6), montant=Decimal("10"))
        p.save()
        self.assertEqual(e.montant_paye, Decimal("660"))
        self.assertEqual(e.statut, Echeance.Statut.PAYE)
