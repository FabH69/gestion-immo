"""Liste « À faire » : rappels calculés (révisions, assurances, chaudières, check-lists) et rappels saisis."""
from datetime import time, timedelta

from django.urls import reverse

from .models.outils import heure_courte
from .models import Bail, Echeance, JournalFait, Rappel, RevisionLoyer, Tache, _ajouter_mois

PREAVIS_JOURS = 30
HISTORIQUE_JOURS = 90
DELAI_RETARD_JOURS = 5  # un loyer apparaît dans « À faire » après 5 jours de retard


def rappel_cfe(bien, aujourdhui=None):
    """Bien en LMNP ou en société : rappel annuel « Payer la CFE » au 15/12, important, 10 jours avant (créé s'il manque)."""
    from datetime import date

    from django.utils import timezone

    if bien.comptabilite not in ("lmnp", "societe") or bien.rappels.filter(titre__istartswith="Payer la CFE").exists():
        return None
    aujourdhui = aujourdhui or timezone.localdate()
    echeance = date(aujourdhui.year, 12, 15)
    if echeance < aujourdhui:
        echeance = date(aujourdhui.year + 1, 12, 15)
    return Rappel.objects.create(
        titre="Payer la CFE", date=echeance, categorie=Rappel.Categorie.FISCAL, bien=bien, periodicite=Rappel.Periodicite.ANNUEL,
        important=True, prevenir_jours=10,
        notes="Cotisation foncière des entreprises de la location meublée : avis dans l'espace professionnel d'impots.gouv.fr, "
              "date limite de paiement le 15 décembre. Une fois payée, ajoutez la facture en rubrique « Impôts et taxes (CFE) ».")


TITRE_IMPORT_LCD = "Importer les réservations du mois précédent (Airbnb, Booking)"
JOUR_IMPORT_LCD = 5  # J+5 : le 5 de chaque mois, pour le mois qui vient de se terminer


def _titre_import_lcd(lot):
    return f"{TITRE_IMPORT_LCD} – {lot}"


def rappel_import_lcd(lot, aujourdhui=None):
    """Lot en courte durée : rappel mensuel le 5 « Importer les réservations du mois précédent » (créé s'il manque).

    Le lot quitte la courte durée ou est vendu : le rappel existant est arrêté (décoché « actif »), puis repris s'il y revient.
    Renvoie le rappel créé, sinon None."""
    from datetime import date

    from django.utils import timezone

    titre = _titre_import_lcd(lot)
    existants = Rappel.objects.filter(bien=lot.bien, titre=titre)
    en_lcd = lot.exploitation == "courte" and not lot.vendu and not lot.bien.vendu
    if not en_lcd:
        existants.filter(actif=True).update(actif=False)
        return None
    if existants.exists():
        existants.filter(actif=False).update(actif=True)
        return None
    aujourdhui = aujourdhui or timezone.localdate()
    echeance = date(aujourdhui.year, aujourdhui.month, JOUR_IMPORT_LCD)
    if echeance < aujourdhui:
        echeance = _ajouter_mois(echeance, 1)
    return Rappel.objects.create(
        titre=titre, date=echeance, categorie=Rappel.Categorie.ADMINISTRATIF, bien=lot.bien,
        periodicite=Rappel.Periodicite.MENSUEL, prevenir_jours=0, dans_agenda=True,
        notes="Téléchargez les exports Airbnb et Booking du mois qui vient de se terminer, puis importez-les dans "
              "Courte durée › Suivi mensuel (choisir le logement, bouton d'import). Cochez « fait » : le rappel passe au 5 du mois suivant.")


def _anniversaire_recent(depart, aujourdhui):
    """Échéance annuelle à partir d'une date : la plus récente passée (pas une date d'il y a dix ans), ou la prochaine."""
    echeance = _ajouter_mois(depart, 12)
    while _ajouter_mois(echeance, 12) <= aujourdhui:
        echeance = _ajouter_mois(echeance, 12)
    return echeance


# filtres de la page « À faire » : (code, libellé)
GENRES = [("loyers", "Loyers"), ("revisions", "Révisions"), ("assurances", "Assurances"), ("chaudieres", "Chaudières"),
          ("dpe", "DPE"), ("entrees", "Entrées et sorties"), ("rappels", "Mes rappels")]


def a_faire(aujourdhui, horizon_jours=PREAVIS_JOURS, avec_taches=False):
    limite = aujourdhui + timedelta(days=horizon_jours)
    items = []

    def ajouter(date, titre, detail, categorie, url, action=None, stop=None, important=False, limite_propre=None, genre="rappels",
                heure=None, agenda=None):
        if date <= (limite_propre or limite):
            items.append({
                "date": date, "heure": heure, "heure_texte": heure_courte(heure), "agenda": agenda, "titre": titre, "detail": detail, "categorie": categorie, "genre": genre,
                "url": url, "action": action, "stop": stop, "en_retard": date < aujourdhui, "important": important,
            })

    baux = Bail.objects.filter(actif=True).select_related("lot__bien").prefetch_related("locataires")
    for bail in baux:
        noms = ", ".join(str(l) for l in bail.locataires.all())
        lieu = f"{bail.lot} – {noms}" if noms else str(bail.lot)

        revision = bail.prochaine_revision(aujourdhui)
        if revision and bail.rappel_revision:
            ajouter(revision, "Réviser le loyer (IRL)", lieu, "Révision", reverse("revision_loyer", args=[bail.pk]),
                    action=reverse("rappel_fait", args=["revision", bail.pk]),
                    stop=reverse("ne_plus_rappeler", args=["revision", bail.pk]), genre="revisions")

        echeance = _anniversaire_recent(bail.date_attestation_assurance or bail.date_debut, aujourdhui)
        if bail.rappel_assurance:
            ajouter(echeance, "Demander l'attestation d'assurance habitation", lieu, "Assurance",
                    reverse("admin:patrimoine_bail_change", args=[bail.pk]),
                    action=reverse("rappel_fait", args=["assurance", bail.pk]),
                    stop=reverse("ne_plus_rappeler", args=["assurance", bail.pk]), genre="assurances")

        if bail.lot.chaudiere_gaz:
            echeance = _anniversaire_recent(bail.lot.date_entretien_chaudiere or bail.date_debut, aujourdhui)
            ajouter(echeance, "Demander l'attestation d'entretien de la chaudière", lieu, "Chaudière",
                    reverse("admin:patrimoine_lot_change", args=[bail.lot.pk]),
                    action=reverse("rappel_fait", args=["chaudiere", bail.lot.pk]), genre="chaudieres")

    # DPE : fin de validité (prévenu 6 mois avant) et date d'interdiction de louer selon la classe (prévenu 1 an avant)
    from . import dpe
    from .models import Lot

    for lot in Lot.objects.filter(vendu=False, bien__vendu=False, type__in=("appartement", "maison", "studio")).select_related("bien"):
        url = reverse("admin:patrimoine_lot_change", args=[lot.pk])
        fin = lot.dpe_fin_validite
        if fin:
            ajouter(fin, "Faire refaire le DPE" if fin >= aujourdhui else "DPE expiré : le faire refaire",
                    f"{lot} · classe {lot.classe_dpe or '?'}, valable jusqu'au {fin:%d/%m/%Y}", "DPE", url,
                    limite_propre=aujourdhui + timedelta(days=dpe.PREVENIR_JOURS), genre="dpe", important=fin < aujourdhui)
        quand = dpe.interdiction(lot.classe_dpe, lot.bien.code_postal)
        if quand and lot.exploitation != "courte":
            sens = "depuis le" if quand <= aujourdhui else "à partir du"
            ajouter(quand, f"Classe {lot.classe_dpe} : location interdite {sens} {quand:%d/%m/%Y}", f"{lot} · selon la loi en vigueur, travaux nécessaires pour louer", "DPE", url,
                    limite_propre=aujourdhui + timedelta(days=365), genre="dpe", important=True)

    # loyers en retard de plus de 5 jours (12 derniers mois) : un rappel par loyer, réglé dès que le paiement est saisi
    impayes = (Echeance.objects.exclude(statut=Echeance.Statut.PAYE)
               .filter(date_exigibilite__lt=aujourdhui - timedelta(days=DELAI_RETARD_JOURS), date_exigibilite__gte=_ajouter_mois(aujourdhui, -12))
               .select_related("bail__lot__bien").prefetch_related("bail__locataires", "paiements"))
    for e in impayes:
        noms = ", ".join(str(l) for l in e.bail.locataires.all())
        ajouter(e.date_exigibilite, f"Loyer de {e.periode:%m/%Y} non réglé : {e.reste_du:.2f} €".replace(".", ","),
                f"{e.bail.lot} – {noms}" if noms else str(e.bail.lot), "Loyer",
                reverse("admin:patrimoine_echeance_change", args=[e.pk]), genre="loyers")

    for rappel in Rappel.objects.filter(fait=False, actif=True).select_related("bien"):
        # affiché dès qu'il entre dans la période (30 jours sur l'accueil) ; « me prévenir par mail N jours avant »
        # ne masque plus rien : il sert au mail, et l'avance seulement si N dépasse la période
        propre = (aujourdhui + timedelta(days=rappel.prevenir_jours)
                  if rappel.prevenir_jours is not None and rappel.prevenir_jours > horizon_jours else None)
        detail = str(rappel.bien or "")
        ajouter(rappel.date, rappel.titre, detail, rappel.get_categorie_display(),
                reverse("admin:patrimoine_rappel_change", args=[rappel.pk]),
                action=reverse("rappel_fait", args=["rappel", rappel.pk]), important=rappel.important, limite_propre=propre,
                heure=rappel.heure, agenda=reverse("rappel_agenda", args=[rappel.pk]))

    if avec_taches:
        taches = (Tache.objects.filter(faite=False, changement__termine=False, echeance__isnull=False)
                  .select_related("changement__lot__bien"))
        for t in taches:
            sens = "Sortie" if t.phase == Tache.Phase.DEPART else "Entrée"
            ajouter(t.echeance, t.libelle, f"{t.changement.lot} · {sens.lower()}", "Entrée et sortie",
                    reverse("admin:patrimoine_changementlocataire_change", args=[t.changement_id]),
                    action=reverse("tache_faite", args=[t.pk]), genre="entrees")

    # même jour : d'abord ce qui n'a pas d'heure (toute la journée), puis les rendez-vous dans l'ordre des heures
    return sorted(items, key=lambda i: (not i["important"], i["date"], i["heure"] is not None, i["heure"] or time.min))


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


# « Partir de zéro » : grandes dates de chaque année, créées en un clic (code, titre, mois, jour, catégorie, ★, mail N jours avant, notes)
RAPPELS_DE_BASE = [
    ("declaration", "Déclarer les revenus locatifs (2044, LMNP ou société)", 5, 20, "fiscal", True, 15,
     "Déclaration en ligne sur impots.gouv.fr : date limite entre fin mai et début juin selon votre département. "
     "Le récapitulatif 2044 et la compta LMNP de l'appli donnent les montants à reporter."),
    ("regularisation", "Faire la régularisation annuelle des charges", 6, 30, "administratif", False, 15,
     "Une fois les comptes de copropriété approuvés : comparer les provisions versées par chaque locataire aux charges réelles, "
     "puis envoyer le décompte (un mois avant, tenir les justificatifs à disposition)."),
    ("ag", "Assemblée générale de copropriété : lire la convocation", 4, 1, "ag", False, None,
     "Date indicative : remplacez-la par la date reçue du syndic et ajoutez l'heure pour l'avoir en rendez-vous dans l'agenda."),
    ("taxe_fonciere", "Payer la taxe foncière", 10, 15, "fiscal", True, 10,
     "Avis dans l'espace particulier d'impots.gouv.fr (fin août). Une fois payée, ajoutez-la dans la compta du bien. "
     "Pensez à refacturer la taxe d'enlèvement des ordures ménagères aux locataires."),
]


def _prochaine(mois, jour, aujourdhui):
    from datetime import date

    quand = date(aujourdhui.year, mois, jour)
    return quand if quand >= aujourdhui else date(aujourdhui.year + 1, mois, jour)


def rappels_de_base(aujourdhui):
    """Liste proposée sur la page d'abonnement : déjà créé (même titre) ou non, et la prochaine date."""
    titres = {t.lower() for t in Rappel.objects.values_list("titre", flat=True)}
    return [{"code": code, "titre": titre, "date": _prochaine(mois, jour, aujourdhui), "existe": titre.lower() in titres}
            for code, titre, mois, jour, *_ in RAPPELS_DE_BASE]


def creer_rappels_de_base(codes, aujourdhui):
    """Crée les rappels annuels choisis qui n'existent pas encore ; renvoie le nombre créé."""
    existants = {t.lower() for t in Rappel.objects.values_list("titre", flat=True)}
    crees = 0
    for code, titre, mois, jour, categorie, important, prevenir, notes in RAPPELS_DE_BASE:
        if code in codes and titre.lower() not in existants:
            Rappel.objects.create(titre=titre, date=_prochaine(mois, jour, aujourdhui), categorie=categorie,
                                  periodicite=Rappel.Periodicite.ANNUEL, important=important, prevenir_jours=prevenir, notes=notes,
                                  dans_agenda=True)
            crees += 1
    return crees
