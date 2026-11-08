"""Liste « À faire » : rappels calculés (révisions, assurances, chaudières, check-lists) et rappels saisis."""
from datetime import timedelta

from django.urls import reverse

from .models import Bail, Echeance, JournalFait, Rappel, RevisionLoyer, Tache, _ajouter_mois

PREAVIS_JOURS = 30
HISTORIQUE_JOURS = 90
DELAI_RETARD_JOURS = 5  # un loyer apparaît dans « À faire » après 5 jours de retard


def _anniversaire_recent(depart, aujourdhui):
    """Échéance annuelle à partir d'une date : la plus récente passée (pas une date d'il y a dix ans), ou la prochaine."""
    echeance = _ajouter_mois(depart, 12)
    while _ajouter_mois(echeance, 12) <= aujourdhui:
        echeance = _ajouter_mois(echeance, 12)
    return echeance


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
            ajouter(revision, "Réviser le loyer (IRL)", lieu, "Révision", reverse("revision_loyer", args=[bail.pk]),
                    action=reverse("rappel_fait", args=["revision", bail.pk]))

        echeance = _anniversaire_recent(bail.date_attestation_assurance or bail.date_debut, aujourdhui)
        if bail.rappel_assurance:
            ajouter(echeance, "Demander l'attestation d'assurance habitation", lieu, "Assurance",
                    reverse("admin:patrimoine_bail_change", args=[bail.pk]),
                    action=reverse("rappel_fait", args=["assurance", bail.pk]))

        if bail.lot.chaudiere_gaz:
            echeance = _anniversaire_recent(bail.lot.date_entretien_chaudiere or bail.date_debut, aujourdhui)
            ajouter(echeance, "Demander l'attestation d'entretien de la chaudière", lieu, "Chaudière",
                    reverse("admin:patrimoine_lot_change", args=[bail.lot.pk]),
                    action=reverse("rappel_fait", args=["chaudiere", bail.lot.pk]))

    # loyers en retard de plus de 5 jours (12 derniers mois) : un rappel par loyer, réglé dès que le paiement est saisi
    impayes = (Echeance.objects.exclude(statut=Echeance.Statut.PAYE)
               .filter(date_exigibilite__lt=aujourdhui - timedelta(days=DELAI_RETARD_JOURS), date_exigibilite__gte=_ajouter_mois(aujourdhui, -12))
               .select_related("bail__lot__bien").prefetch_related("bail__locataires", "paiements"))
    for e in impayes:
        noms = ", ".join(str(l) for l in e.bail.locataires.all())
        ajouter(e.date_exigibilite, f"Loyer de {e.periode:%m/%Y} non réglé : {e.reste_du:.2f} €".replace(".", ","),
                f"{e.bail.lot} – {noms}" if noms else str(e.bail.lot), "Loyer",
                reverse("admin:patrimoine_echeance_change", args=[e.pk]))

    for rappel in Rappel.objects.filter(fait=False, actif=True).select_related("bien"):
        ajouter(rappel.date, rappel.titre, str(rappel.bien or ""), rappel.get_categorie_display(),
                reverse("admin:patrimoine_rappel_change", args=[rappel.pk]),
                action=reverse("rappel_fait", args=["rappel", rappel.pk]))

    # les tâches d'entrée et de sortie ont leur propre bloc sur l'accueil (bloc_taches_changement)

    return sorted(items, key=lambda i: i["date"])


def deja_fait(aujourdhui, jours=HISTORIQUE_JOURS):
    """Ce qui a été fait ces derniers jours : rappels cochés, tâches de check-list, révisions appliquées."""
    depuis = aujourdhui - timedelta(days=jours)
    items = [{"date": j.date, "titre": j.titre, "detail": j.detail, "categorie": j.categorie, "url": j.url}
             for j in JournalFait.objects.filter(date__gte=depuis)]
    for t in Tache.objects.filter(faite=True, date_faite__gte=depuis).select_related("changement__lot__bien"):
        items.append({"date": t.date_faite, "titre": t.libelle, "detail": str(t.changement.lot), "categorie": "Changement de locataire",
                      "url": reverse("admin:patrimoine_changementlocataire_change", args=[t.changement_id])})
    revisions = RevisionLoyer.objects.filter(appliquee=True, date_effet__gte=depuis, date_effet__lte=aujourdhui).select_related("bail__lot__bien")
    for r in revisions:
        items.append({"date": r.date_effet, "titre": f"Loyer révisé : {r.ancien_loyer} € → {r.nouveau_loyer} €", "detail": str(r.bail.lot),
                      "categorie": "Révision", "url": reverse("admin:patrimoine_revisionloyer_change", args=[r.pk])})
    return sorted(items, key=lambda i: i["date"], reverse=True)
