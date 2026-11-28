"""Configuration du logement → modèle d'état des lieux ; menu « État des lieux » du bail ; boutons photo (téléphone / PC)."""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from . import edl as logique
from . import modele_edl
from .models import Bail, Bien, EtatDesLieux, Locataire, Lot, Proprietaire


class ModeleEtatDesLieuxTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        proprio = Proprietaire.objects.create(nom="SCI Config", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(nom="Maison M", proprietaire=proprio, adresse="2 rue", code_postal="69000", ville="Lyon")
        self.lot = Lot.objects.create(bien=self.bien, designation="Maison", type=Lot.Type.MAISON, nb_pieces=4)
        self.bail = Bail.objects.create(lot=self.lot, type=Bail.Type.NU, date_debut=date(2025, 1, 1), loyer_hc=900)
        self.bail.locataires.add(Locataire.objects.create(nom="MARTIN", prenom="Paul"))

    def test_configuration_genere_les_pieces_dans_l_ordre(self):
        self.lot.configuration = {"entree": 1, "sejour": 1, "cuisine": 1, "chambre": 3, "salle_bain": 1, "salle_eau": 1, "wc": 2,
                                  "garage": 1, "cave": 1, "inconnu": 4, "jardin": "x"}
        self.lot.pieces = "Véranda\nCave"
        self.assertEqual(self.lot.liste_pieces(), [
            "Entrée", "Séjour", "Cuisine", "Chambre 1", "Chambre 2", "Chambre 3", "Salle de bains", "Salle d'eau",
            "WC 1", "WC 2", "Garage", "Cave", "Véranda"])  # « Cave » saisie à la main n'est pas doublée

    def test_sans_configuration_la_liste_manuelle_puis_standard_restent(self):
        self.lot.pieces = "Pièce de vie\nMezzanine"
        self.assertEqual(self.lot.liste_pieces(), ["Pièce de vie", "Mezzanine"])
        self.lot.pieces = ""
        self.assertEqual(self.lot.liste_pieces()[:3], ["Entrée", "Séjour", "Cuisine"])

    def test_elements_propres_a_chaque_piece(self):
        self.assertIn("Hotte", modele_edl.elements_pour("Cuisine"))
        self.assertIn("Chasse d'eau", modele_edl.elements_pour("WC 2"))
        self.assertIn("Baignoire", modele_edl.elements_pour("Salle de bains"))
        self.assertIn("Porte de garage", modele_edl.elements_pour("Garage"))
        self.assertIn("Porte et serrure", modele_edl.elements_pour("Cave n° 12"))
        self.assertIn("Placard / penderie", modele_edl.elements_pour("Chambre 1"))
        self.assertIn("Chauffage / radiateur", modele_edl.elements_pour("Véranda"))  # pièce inconnue : liste habituelle

    def test_etat_des_lieux_reprend_le_modele(self):
        self.lot.configuration = {"cuisine": 1, "wc": 1, "cave": 1}
        self.lot.save()
        self.client.post(f"/baux/{self.bail.pk}/etat-des-lieux/entree/", {"signataire_bailleur": "SCI Config"})
        edl = EtatDesLieux.objects.get()
        pieces = dict(logique.pieces(edl))
        self.assertEqual(list(pieces), ["Cuisine", "WC", "Cave"])
        self.assertIn("Hotte", [e.nom for e in pieces["Cuisine"]])
        self.assertIn("Cuvette et abattant", [e.nom for e in pieces["WC"]])
        # une pièce ajoutée pendant la saisie reçoit aussi ses éléments propres
        self.client.post(f"/etats-des-lieux/{edl.pk}/saisie/?etape=0", {"action": "ajouter_piece", "nouvelle_piece": "Garage"})
        self.assertIn("Porte de garage", [e.nom for e in dict(logique.pieces(edl))["Garage"]])

    def test_fiche_vierge_pdf_par_piece(self):
        self.lot.configuration = {"cuisine": 1, "garage": 1}
        self.lot.save()
        r = self.client.get(f"/baux/{self.bail.pk}/etat-des-lieux-entree.pdf")
        self.assertEqual(r.status_code, 200)

    def test_fiche_du_logement_enregistre_la_configuration(self):
        url = f"/admin/patrimoine/lot/{self.lot.pk}/change/"
        page = self.client.get(url).content.decode()
        self.assertIn("Pièces et état des lieux", page)
        self.assertIn('name="config_chambre"', page)
        form = self.client.get(url).context["adminform"].form
        donnees = {k: v for k, v in form.initial.items() if v is not None and not isinstance(v, (list, dict))}
        donnees.update({f"config_{c}": "" for c in modele_edl.CODES})
        donnees.update({"bien": self.bien.pk, "designation": "Maison", "type": "maison", "exploitation": "longue",
                        "config_chambre": "2", "config_wc": "1", "config_garage": "1",
                        "baux-TOTAL_FORMS": "0", "baux-INITIAL_FORMS": "0", "photos-TOTAL_FORMS": "0", "photos-INITIAL_FORMS": "0"})
        for cle in list(donnees):
            if donnees[cle] is True:
                donnees[cle] = "on"
            elif donnees[cle] is False:
                del donnees[cle]
        r = self.client.post(url, donnees)
        self.assertEqual(r.status_code, 302, r.context and r.context["adminform"].form.errors)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.configuration, {"chambre": 2, "wc": 1, "garage": 1})
        page = self.client.get(url).content.decode()
        self.assertIn('name="config_chambre" value="2"', page)
        self.assertIn("Porte de garage", page)  # aperçu du modèle

    def test_menu_etat_des_lieux_mene_a_la_saisie_avec_photos(self):
        from .fiches import _documents_bail
        edl, _ = _documents_bail(self.bail)
        self.assertEqual(edl[0], ("Faire l'état des lieux d'entrée (pièce par pièce, photos)", f"/baux/{self.bail.pk}/etat-des-lieux/entree/", False))
        self.assertEqual(edl[1][1], f"/baux/{self.bail.pk}/etat-des-lieux/sortie/")
        self.assertNotIn("/admin/patrimoine/etatdeslieux/add/", " ".join(u for _, u, _ in edl))
        EtatDesLieux.objects.create(bail=self.bail, type="entree", date=date(2026, 11, 28))
        edl, _ = _documents_bail(self.bail)
        self.assertEqual(edl[0][0], "Reprendre l'état des lieux d'entrée du 28/11/2026")

    def test_boutons_prendre_une_photo_et_importer(self):
        self.client.post(f"/baux/{self.bail.pk}/etat-des-lieux/entree/", {"signataire_bailleur": "SCI Config"})
        edl = EtatDesLieux.objects.get()
        page = self.client.get(f"/etats-des-lieux/{edl.pk}/saisie/?etape=1").content.decode()
        self.assertIn("Prendre une photo", page)
        self.assertIn("Importer des photos", page)
        element = edl.elements.order_by("ordre").first()
        champs = page.count(f'name="photos-{element.pk}"')
        self.assertEqual(champs, 2)  # appareil photo (capture) + import (galerie ou fichiers, plusieurs à la fois)
        self.assertIn(f'accept="image/*" multiple data-element="{element.pk}"', page)
