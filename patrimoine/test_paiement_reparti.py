"""Lot Saint Pierre 2 : changement de locataire en avril (Lassus sort, Voldoire entre). L'ancien suivi n'a qu'un montant
par mois pour le logement (avril : 136 € de Lassus + 560 € de Voldoire = 696 €). Repris, il doit donner 136 € à Lassus
et 560 € à Voldoire, puis 700 € par mois, sans que chaque loyer de Voldoire soit coupé en deux (564 € + 136 €).
Et dans les déclarations, un paiement réparti sur deux loyers sort sur une seule ligne."""
from datetime import date
from decimal import Decimal as D

from django.test import TestCase

from .annexe2072 import colonne, loyers_detailles
from .models import Bail, Bien, EncaissementHistorique, Echeance, Locataire, Lot, Paiement, Proprietaire, Versement
from .reprise_historique import convertir, reconstruire


class ChangementDeLocataireTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI SP", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Saint Pierre", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=self.bien, designation="Saint Pierre 2")
        # Lassus : 680 €, sort le 6 avril (136 € au prorata) ; Voldoire : 700 €, entre le 7 avril (560 € au prorata)
        self.lassus = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2024, 1, 1), date_fin=date(2025, 4, 6),
                                          actif=False, loyer_hc=D("680"), charges=D("0"), jour_echeance=1)
        self.lassus.locataires.add(Locataire.objects.create(nom="LASSUS"))
        self.voldoire = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 4, 7), loyer_hc=D("700"),
                                            charges=D("0"), jour_echeance=1)
        self.voldoire.locataires.add(Locataire.objects.create(nom="VOLDOIRE", prenom="Marina"))
        for m, montant in [(1, "680"), (2, "680"), (3, "680"), (4, "696")] + [(m, "700") for m in range(5, 13)]:
            EncaissementHistorique.objects.create(lot=self.lot, mois=date(2025, m, 1), montant=D(montant))

    def recu(self, bail):
        return {e.periode.month: e.montant_paye for e in bail.echeances.filter(periode__year=2025)}

    def verifier(self):
        self.assertEqual(self.recu(self.lassus), {1: D("680"), 2: D("680"), 3: D("680"), 4: D("136")})
        self.assertEqual(self.recu(self.voldoire), {4: D("560"), **{m: D("700") for m in range(5, 13)}})
        for e in Echeance.objects.all():
            self.assertEqual(e.paiements.count(), 1, e)  # plus aucun loyer coupé en deux
        self.assertFalse(any(v.avance for v in Versement.objects.all()))
        lignes = loyers_detailles(self.bien, 2025)
        avril = [(l["libelle"], l["montant"]) for l in lignes if l["date"] == date(2025, 4, 1)]
        self.assertEqual(len(avril), 2)
        self.assertIn(("01/04/2025 · loyer de avril 2025 · LASSUS (Saint Pierre 2)", D("136")), avril)
        self.assertIn(("01/04/2025 · loyer de avril 2025 · Marina VOLDOIRE (Saint Pierre 2)", D("560")), avril)
        self.assertEqual(colonne(self.bien, 2025)["valeurs"]["1"], D("2040") + D("696") + 8 * D("700"))

    def test_reprise_mois_de_changement_partage(self):
        convertir(aujourdhui=date(2026, 1, 15))
        self.verifier()

    def test_reconstruire_corrige_une_reprise_deja_faite(self):
        # ancienne reprise : les 696 € d'avril entièrement chez Voldoire → 136 € d'avance qui décale tous ses mois
        for h in EncaissementHistorique.objects.order_by("mois"):
            bail = self.voldoire if h.mois.month >= 4 else self.lassus
            v = Versement.objects.create(bail=bail, date=h.mois, montant=h.montant, notes="Repris du suivi historique")
            reste = h.montant
            for e in [bail.echeances.get_or_create(periode=date(2025, m, 1), nature=Echeance.Nature.LOYER, defaults=dict(
                    zip(("loyer_hc", "charges"), bail.montants_du_mois(date(2025, m, 1))), date_exigibilite=date(2025, m, 1)))[0]
                      for m in range(h.mois.month, 13 if bail == self.voldoire else 5)]:
                part = min(e.reste_du, reste)
                if part > 0:
                    Paiement.objects.create(echeance=e, versement=v, date=v.date, montant=part)
                    reste -= part
            h.delete()
        self.assertEqual(Paiement.objects.filter(echeance__bail=self.voldoire, echeance__periode__month=5).count(), 2)
        reconstruire(aujourdhui=date(2026, 1, 15))
        self.verifier()
        reconstruire(aujourdhui=date(2026, 1, 15))  # refaire ne change rien
        self.verifier()


class PaiementRepartiTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI R", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.bail = Bail.objects.create(lot=Lot.objects.create(bien=self.bien, designation="T2"), type=Bail.Type.NU,
                                        date_debut=date(2025, 1, 1), loyer_hc=D("700"), charges=D("0"), jour_echeance=1)
        self.bail.locataires.add(Locataire.objects.create(nom="DURAND"))
        for m in (4, 5):
            Echeance.generer_pour_mois(date(2025, m, 1))
        self.avril, self.mai = Echeance.objects.order_by("periode")

    def test_paiement_sur_deux_loyers_une_seule_ligne(self):
        Paiement.recu(self.avril, date(2025, 4, 2), D("564"))
        v = Versement.objects.create(bail=self.bail, date=date(2025, 4, 20), montant=D("836"))
        v.repartir([(self.avril, D("136")), (self.mai, D("700"))])
        lignes = loyers_detailles(self.bien, 2025)
        self.assertEqual([(l["libelle"], l["montant"]) for l in lignes], [
            ("02/04/2025 · loyer de avril 2025 · DURAND (T2)", D("564")),
            ("20/04/2025 · loyer de avril 2025 (136,00 €) et mai 2025 (700,00 €) · DURAND (T2)", D("836")),
        ])


class RefaireRepartitionTests(TestCase):
    """Bouton « Refaire la répartition de l'historique » (Baux et Paiements reçus), après une correction des baux."""

    def setUp(self):
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI SP", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Saint Pierre", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=self.bien, designation="Saint Pierre 2")
        # loyer mal saisi (569 € au lieu de 680 €) : 111 € de trop chaque mois, rangés sur le mois suivant
        self.lassus = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2024, 11, 1), date_fin=date(2025, 4, 6),
                                          actif=False, loyer_hc=D("569"), charges=D("0"), jour_echeance=1)
        self.lassus.locataires.add(Locataire.objects.create(nom="LASSUS"))
        self.voldoire = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 4, 7), loyer_hc=D("700"),
                                            charges=D("0"), jour_echeance=1)
        self.voldoire.locataires.add(Locataire.objects.create(nom="VOLDOIRE", prenom="Marina"))
        lignes = [(2024, 12, "680"), (2025, 1, "680"), (2025, 2, "680"), (2025, 3, "680"), (2025, 4, "696")]
        lignes += [(2025, m, "700") for m in range(5, 13)]
        for a, m, montant in lignes:
            EncaissementHistorique.objects.create(lot=self.lot, mois=date(a, m, 1), montant=D(montant))
        convertir(aujourdhui=date(2026, 1, 15))

    def recu(self, bail):
        return {e.periode: e.montant_paye for e in bail.echeances.all() if e.montant_paye}

    def test_loyer_corrige_puis_bouton_sur_le_bail_en_cours_du_logement(self):
        self.assertEqual(self.recu(self.lassus)[date(2025, 4, 1)], D("113.80"))  # avant : avril au prorata de 569 €
        Bail.objects.filter(pk=self.lassus.pk).update(loyer_hc=D("680"), loyer_initial=D("680"))
        r = self.client.post("/admin/patrimoine/bail/", {"action": "refaire_historique", "_selected_action": [self.voldoire.pk]},
                             follow=True)
        self.assertContains(r, "Répartition de l&#x27;historique refaite pour Saint Pierre 2")
        self.assertEqual(self.recu(self.lassus), {date(2025, 1, 1): D("680"), date(2025, 2, 1): D("680"), date(2025, 3, 1): D("680"),
                                                  date(2025, 4, 1): D("136"), date(2024, 12, 1): D("680")})
        self.assertEqual(self.recu(self.voldoire), {date(2025, 4, 1): D("560"), **{date(2025, m, 1): D("700") for m in range(5, 13)}})
        for e in Echeance.objects.all():
            self.assertLessEqual(e.paiements.count(), 1, e)
        self.assertEqual(Versement.objects.filter(bail=self.lassus, date=date(2025, 4, 1)).get().montant, D("136"))
        self.assertEqual(sum(v.montant for v in Versement.objects.all()), D("680") + 3 * D("680") + D("696") + 8 * D("700"))

    def test_locataire_ajoute_entre_temps_bouton_sur_les_paiements(self):
        # un autre locataire a occupé le logement de janvier à mars : Lassus sort fin décembre
        Bail.objects.filter(pk=self.lassus.pk).update(date_fin=date(2024, 12, 31))
        Echeance.objects.filter(bail=self.lassus, periode__gte=date(2025, 1, 1)).delete()
        autre = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), date_fin=date(2025, 4, 6),
                                    actif=False, loyer_hc=D("680"), charges=D("0"), jour_echeance=1)
        autre.locataires.add(Locataire.objects.create(nom="MARTIN"))
        r = self.client.post("/admin/patrimoine/versement/", {"action": "refaire_historique",
                                                             "_selected_action": [Versement.objects.first().pk]}, follow=True)
        self.assertContains(r, "Répartition de l&#x27;historique refaite")
        self.assertEqual(self.recu(autre), {date(2025, 1, 1): D("680"), date(2025, 2, 1): D("680"), date(2025, 3, 1): D("680"),
                                            date(2025, 4, 1): D("136")})
        self.assertEqual(self.recu(self.voldoire)[date(2025, 4, 1)], D("560"))
        self.assertEqual(self.recu(self.lassus), {date(2024, 12, 1): D("569")})  # son loyer n'a pas été corrigé ici
        self.assertFalse(Versement.objects.filter(bail=self.lassus, date__year=2025).exists())
