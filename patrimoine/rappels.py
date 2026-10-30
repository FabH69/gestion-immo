"""Liste « À faire » : rappels calculés (révisions, assurances, chaudières, check-lists) et rappels saisis."""
from datetime import timedelta

from django.urls import reverse

from .models import Bail, Rappel, Tache, _ajouter_mois

PREAVIS_JOURS = 30


def a_faire(aujourdhui, horizon_jours=PREAVIS_JOURS):
    limite = aujourdhui + timedelta(days=horizon_jours)
    items = []

    def ajouter(date, titre, detail, categorie, url, action=None):
        if date <= limite:
            items.append({
                "date": date, "titre": titre, "detail": detail, "categorie": categorie,
                "url": url, "action": action, "en_retard": date < aujourdhui,
            })

    baux = Bail.objects.filter(actif=True).select_related("lot__bien").prefetch_related("locataires")
    for bail in baux:
        noms = ", ".join(str(l) for l in bail.locataires.all())
        lieu = f"{bail.lot} – {noms}" if noms else str(bail.lot)

        revision = bail.prochaine_revision(aujourdhui)
        if revision:
            ajouter(revision, "Réviser le loyer (IRL)", lieu, "Révision", reverse("revision_loyer", args=[bail.pk]))

        echeance = _ajouter_mois(bail.date_attestation_assurance or bail.date_debut, 12)
        ajouter(echeance, "Demander l'attestation d'assurance habitation", lieu, "Assurance",
                reverse("admin:patrimoine_bail_change", args=[bail.pk]),
                action=reverse("rappel_fait", args=["assurance", bail.pk]))

        if bail.lot.chaudiere_gaz:
            echeance = _ajouter_mois(bail.lot.date_entretien_chaudiere or bail.date_debut, 12)
            ajouter(echeance, "Demander l'attestation d'entretien de la chaudière", lieu, "Chaudière",
                    reverse("admin:patrimoine_lot_change", args=[bail.lot.pk]),
                    action=reverse("rappel_fait", args=["chaudiere", bail.lot.pk]))

    for rappel in Rappel.objects.filter(fait=False).select_related("bien"):
        ajouter(rappel.date, rappel.titre, str(rappel.bien or ""), rappel.get_categorie_display(),
                reverse("admin:patrimoine_rappel_change", args=[rappel.pk]),
                action=reverse("rappel_fait", args=["rappel", rappel.pk]))

    taches = Tache.objects.filter(faite=False, changement__termine=False, echeance__isnull=False).select_related("changement__lot__bien")
    for t in taches:
        ajouter(t.echeance, t.libelle, str(t.changement.lot), "Changement de locataire",
                reverse("admin:patrimoine_changementlocataire_change", args=[t.changement_id]),
                action=reverse("rappel_fait", args=["tache", t.pk]))

    return sorted(items, key=lambda i: i["date"])
