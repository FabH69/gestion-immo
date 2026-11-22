"""Abonnement automatique à l'agenda : lien secret, rappels cochés, échéances choisies, droits, rappels de base."""
from datetime import date, time
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from .models import AbonnementAgenda, Bail, Bien, Locataire, Lot, Proprietaire, Rappel


class AbonnementAgendaTests(TestCase):
    def setUp(self):
        proprio = Proprietaire.objects.create(nom="SCI", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=proprio, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")
        lot = Lot.objects.create(bien=self.bien, designation="Apt")
        bail = Bail.objects.create(lot=lot, type=Bail.Type.NU, date_debut=date(2024, 12, 15), loyer_hc=Decimal("600"),
                                   charges=Decimal("40"), irl_trimestre="T2", irl_valeur=Decimal("143.58"))
        bail.locataires.add(Locataire.objects.create(nom="Martin"))
        self.admin = User.objects.create_superuser("admin", "a@example.com", "x")
        self.client.force_login(self.admin)
        self.coche = Rappel.objects.create(titre="AG copro", date=date(2026, 12, 10), heure=time(18, 15), bien=self.bien,
                                           dans_agenda=True, prevenir_jours=3)
        self.pas_coche = Rappel.objects.create(titre="Note perso", date=date(2026, 12, 11))

    def _activer(self):
        self.client.post("/agenda/", {"action": "activer"})
        return AbonnementAgenda.objects.get(utilisateur=self.admin)

    def _flux(self, abonnement):
        with mock.patch("patrimoine.agenda.timezone.localdate", return_value=date(2026, 11, 22)):
            return self.client.get(f"/agenda/{abonnement.jeton}/gestion-immo.ics")

    def test_lien_secret_et_contenu(self):
        page = self.client.get("/agenda/").content.decode()
        self.assertIn("Créer mon lien d'agenda", page)
        abonnement = self._activer()
        page = self.client.get("/agenda/").content.decode()
        self.assertIn(f"/agenda/{abonnement.jeton}/gestion-immo.ics", page)
        self.assertIn("webcal://", page)
        self.assertIn("calendar.google.com/calendar/r?cid=webcal", page)
        self.assertIn("toutes les quelques heures", page)
        self.client.logout()  # le téléphone n'est pas connecté
        reponse = self._flux(abonnement)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "text/calendar; charset=utf-8")
        texte = reponse.content.decode()
        self.assertIn("X-WR-CALNAME:Gestion immo", texte)
        # seulement le rappel coché
        self.assertIn("SUMMARY:AG copro", texte)
        self.assertNotIn("Note perso", texte)
        self.assertIn(f"UID:rappel-{self.coche.pk}@gestion-immo", texte)
        self.assertIn("DTSTART:20261210T171500Z", texte)
        self.assertIn("TRIGGER:-PT1H", texte)
        self.assertIn("TRIGGER:-P3D", texte)  # « me prévenir 3 jours avant »
        self.assertNotIn("Réviser le loyer", texte)  # échéances calculées : pas cochées
        self.assertTrue(all(len(l.encode()) <= 75 for l in texte.split("\r\n")))
        self.assertIsNotNone(AbonnementAgenda.objects.get(pk=abonnement.pk).dernier_acces)

    def test_echeances_calculees_choisies(self):
        abonnement = self._activer()
        self.client.post("/agenda/", {"action": "types", "types": ["revisions", "inconnu"]})
        abonnement.refresh_from_db()
        self.assertEqual(abonnement.types, "revisions")
        texte = self._flux(abonnement).content.decode()
        self.assertIn("SUMMARY:Réviser le loyer (IRL)", texte)
        self.assertIn("DTSTART;VALUE=DATE:20251215", texte)  # révision de 2025 pas encore faite
        self.assertIn("TRIGGER:PT9H", texte)  # journée : alerte à 9 h
        self.assertNotIn("assurance", texte)

    def test_rappel_fait_ou_decoche_quitte_l_agenda(self):
        abonnement = self._activer()
        self.coche.fait = True
        self.coche.save()
        self.assertNotIn("AG copro", self._flux(abonnement).content.decode())
        self.client.post("/admin/patrimoine/rappel/", {"action": "mettre_dans_agenda", "_selected_action": [self.pas_coche.pk]})
        self.assertIn("Note perso", self._flux(abonnement).content.decode())

    def test_nouveau_lien_arret_et_compte_desactive(self):
        abonnement = self._activer()
        ancien = abonnement.jeton
        self.client.post("/agenda/", {"action": "regenerer"})
        nouveau = AbonnementAgenda.objects.get(utilisateur=self.admin)
        self.assertNotEqual(ancien, nouveau.jeton)
        self.assertEqual(self.client.get(f"/agenda/{ancien}/gestion-immo.ics").status_code, 404)
        self.assertEqual(self._flux(nouveau).status_code, 200)
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self._flux(nouveau).status_code, 404)
        self.admin.is_active = True
        self.admin.save()
        self.client.force_login(self.admin)
        self.client.post("/agenda/", {"action": "desactiver"})
        self.assertFalse(AbonnementAgenda.objects.exists())
        self.assertEqual(self.client.get("/agenda/inexistant-mais-assez-long-xx/gestion-immo.ics").status_code, 404)

    def test_droits(self):
        from . import droits

        invite = User.objects.create_user("invite", "i@example.com", "x", is_staff=True)
        self.client.force_login(invite)
        self.assertEqual(self.client.get("/agenda/").status_code, 403)
        droits.appliquer(invite, {"outils": droits.CONSULTATION})
        invite = User.objects.get(pk=invite.pk)
        self.client.force_login(invite)
        page = self.client.get("/agenda/").content.decode()
        self.assertNotIn("Partir de zéro", page)  # consultation : pas de création de rappels
        self.client.post("/agenda/", {"action": "activer"})
        abonnement = AbonnementAgenda.objects.get(utilisateur=invite)
        self.client.post("/agenda/", {"action": "types", "types": ["revisions", "loyers"]})
        abonnement.refresh_from_db()
        self.assertEqual(self.client.post("/agenda/", {"action": "base", "base": ["taxe_fonciere"]}).status_code, 403)
        from .agenda import evenements_utilisateur

        with mock.patch("patrimoine.agenda.timezone.localdate", return_value=date(2026, 11, 22)):
            genres = {e["uid"].split("-")[0] for e in evenements_utilisateur(invite, abonnement.liste_types)}
        self.assertIn("revisions", genres)
        self.assertNotIn("loyers", genres)  # pas de droit sur les loyers

    def test_partir_de_zero(self):
        Rappel.objects.create(titre="Payer la taxe foncière", date=date(2027, 10, 15))
        with mock.patch("patrimoine.views.outils.timezone.localdate", return_value=date(2026, 11, 22)):
            page = self.client.get("/agenda/").content.decode()
            self.assertIn("déjà créé", page)
            self.client.post("/agenda/", {"action": "base", "base": ["declaration", "taxe_fonciere", "ag"]})
        declaration = Rappel.objects.get(titre__startswith="Déclarer les revenus")
        self.assertEqual(declaration.date, date(2027, 5, 20))
        self.assertTrue(declaration.dans_agenda)
        self.assertEqual(declaration.periodicite, Rappel.Periodicite.ANNUEL)
        self.assertEqual(Rappel.objects.filter(titre="Payer la taxe foncière").count(), 1)  # pas de doublon
        self.assertFalse(Rappel.objects.filter(titre__startswith="Faire la régularisation").exists())  # pas coché

    def test_liens_vers_la_page(self):
        self.assertIn("/agenda/", self.client.get("/a-faire/").content.decode())
        fiche = self.client.get(f"/admin/patrimoine/rappel/{self.coche.pk}/change/").content.decode()
        self.assertIn("dans mon agenda", fiche)
        self.assertIn('href="/agenda/"', fiche)


class RetourApresModificationRappelTests(TestCase):
    def test_enregistrer_revient_au_tableau_de_bord(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        rappel = Rappel.objects.create(titre="AG", date=date(2026, 12, 10))
        donnees = {"titre": "AG modifiée", "date": "10/12/2026", "heure": "", "periodicite": "ponctuel", "categorie": "ag", "actif": "on"}
        reponse = self.client.post(f"/admin/patrimoine/rappel/{rappel.pk}/change/", donnees)
        self.assertRedirects(reponse, "/admin/", fetch_redirect_response=False)
        reponse = self.client.post(f"/admin/patrimoine/rappel/{rappel.pk}/change/", {**donnees, "_continue": "1"})
        self.assertEqual(reponse["Location"], f"/admin/patrimoine/rappel/{rappel.pk}/change/")
        reponse = self.client.post(f"/admin/patrimoine/rappel/{rappel.pk}/change/?next=/a-faire/", donnees)
        self.assertEqual(reponse["Location"], "/a-faire/")
