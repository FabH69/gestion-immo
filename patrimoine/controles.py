"""Page « Contrôles » : informations manquantes ou à vérifier (pas d'échéance datée : celles-ci restent dans « À faire »).

Chaque contrôle renvoie des lignes (clé, libellé, lien pour compléter). Une ligne peut être écartée avec « Ne pas
contrôler » (cas voulu, ex. un dépôt de garantie réellement à 0 €) : elle est mémorisée dans ControleIgnore."""
import calendar
from datetime import date

from django.urls import reverse

from .models import _ajouter_mois

BLOQUANT, A_COMPLETER = "bloquant", "completer"
HABITATION = ("appartement", "maison", "studio")
TYPES_BAIL_HABITATION = ("nu", "meuble", "etudiant", "mobilite")
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _a(nom, pk):
    return reverse(f"admin:patrimoine_{nom}_change", args=[pk])


def _nom(lot):
    """« Pontch 1 » plutôt que « Pontch – Pontch 1 » quand la désignation reprend déjà le nom du bien."""
    return lot.designation if lot.bien.nom.strip().lower() in lot.designation.lower() else str(lot)


def _baux_actifs():
    from .models import Bail

    return Bail.objects.filter(actif=True, lot__vendu=False).select_related("lot__bien").prefetch_related("locataires")


def _lots_habitation():
    from .models import Lot

    return Lot.objects.filter(vendu=False, type__in=HABITATION).select_related("bien")


def baux_sans_irl():
    return [(f"bail:{b.pk}", _nom(b.lot), _a("bail", b.pk)) for b in _baux_actifs()
            if b.revision_annuelle and b.type in TYPES_BAIL_HABITATION and not (b.irl_trimestre and b.irl_valeur)]


def baux_sans_locataire():
    return [(f"bail:{b.pk}", _nom(b.lot), _a("bail", b.pk)) for b in _baux_actifs() if not b.locataires.all()]


def lots_sans_surface():
    return [(f"lot:{l.pk}", _nom(l), _a("lot", l.pk)) for l in _lots_habitation() if not l.surface]


def lots_sans_dpe():
    return [(f"lot:{l.pk}", _nom(l), _a("lot", l.pk)) for l in _lots_habitation() if not l.classe_dpe]


def locataires_sans_email():
    vus, lignes = set(), []
    for b in _baux_actifs():
        for loc in b.locataires.all():
            if not loc.email and loc.pk not in vus:
                vus.add(loc.pk)
                lignes.append((f"locataire:{loc.pk}", str(loc), _a("locataire", loc.pk)))
    return lignes


def baux_depot_zero():
    # le bail mobilité interdit le dépôt de garantie
    return [(f"bail:{b.pk}", _nom(b.lot), _a("bail", b.pk)) for b in _baux_actifs()
            if not b.depot_garantie and b.type != "mobilite"]


def baux_sans_pdl():
    return [(f"bail:{b.pk}", _nom(b.lot), _a("bail", b.pk)) for b in _baux_actifs()
            if b.type in TYPES_BAIL_HABITATION and not (b.pdl_electricite or b.lot.pdl)]


def chaudieres_sans_entretien():
    from .models import Lot

    return [(f"lot:{l.pk}", _nom(l), _a("lot", l.pk))
            for l in Lot.objects.filter(vendu=False, chaudiere_gaz=True, date_entretien_chaudiere__isnull=True).select_related("bien")]


def prets_sans_tableau():
    from django.db.models import Max

    from .models import Pret

    lignes = []
    for p in Pret.objects.select_related("bien").annotate(n=Max("lignes__rang")):
        if not p.n and p.date_derniere_echeance >= date.today():
            nom = f"{p.banque} {p.reference}".strip() + (f" ({p.bien.nom})" if p.bien else "")
            lignes.append((f"pret:{p.pk}", nom, reverse("admin:patrimoine_pret_tableau", args=[p.pk])))
    return lignes


def _mois_passes(depuis, aujourdhui, delai_jours=0):
    """Mois complets depuis `depuis` (inclus) ; un mois n'est retenu que `delai_jours` après sa fin."""
    mois = depuis.replace(day=1)
    while True:
        fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
        if (aujourdhui - fin).days <= delai_jours:
            break
        yield mois
        mois = _ajouter_mois(mois, 1)


def _mois_avec_sejours(lot, plateforme=None):
    qs = lot.reservations_lcd.filter(annulee=False)
    if plateforme:
        qs = qs.filter(plateforme=plateforme)
    return {d.replace(day=1) for d in qs.values_list("arrivee", flat=True)}


def conciergerie_manquante(aujourdhui=None):
    """Mois avec des séjours mais sans facture de conciergerie (la facture arrive vers le 5 du mois suivant)."""
    from .models import PieceComptable
    from .views.compta import lots_conciergerie

    aujourdhui = aujourdhui or date.today()
    lignes = []
    for lot in lots_conciergerie():
        parametres = getattr(lot, "parametres_lcd", None)
        if not parametres or not parametres.commission_gestion:
            continue
        factures = PieceComptable.objects.filter(bien=lot.bien, rubrique="commission", fournisseur__istartswith="concierg")
        premiers = [m for m in factures.values_list("mois_concerne", flat=True) if m]
        if not premiers:
            continue  # contrôle actif à partir de la première facture enregistrée
        couverts = {m.replace(day=1) for m in premiers}
        sejours = _mois_avec_sejours(lot)
        depuis = max(min(couverts), _ajouter_mois(aujourdhui.replace(day=1), -12))
        for mois in _mois_passes(depuis, aujourdhui, delai_jours=12):
            if mois in sejours and mois not in couverts:
                lignes.append((f"conciergerie:{lot.pk}:{mois:%Y-%m}", f"{_nom(lot)} – {MOIS_FR[mois.month - 1]} {mois.year}",
                               reverse("conciergerie_ajouter") + f"?compta={lot.bien.comptabilite}"))
    return lignes


def booking_manquant(aujourdhui=None):
    """Logement loué sur Booking : mois passé sans aucun séjour Booking (relevé des versements pas encore importé ?)."""
    from .models import Lot

    aujourdhui = aujourdhui or date.today()
    lignes = []
    for lot in Lot.objects.filter(vendu=False, exploitation="courte").select_related("bien"):
        booking = _mois_avec_sejours(lot, "booking")
        if not booking:
            continue
        depuis = max(min(booking), _ajouter_mois(aujourdhui.replace(day=1), -12))
        for mois in _mois_passes(depuis, aujourdhui, delai_jours=5):
            if mois not in booking:
                lignes.append((f"booking:{lot.pk}:{mois:%Y-%m}", f"{_nom(lot)} – {MOIS_FR[mois.month - 1]} {mois.year}",
                               reverse("suivi_lcd") + f"?lot={lot.pk}&annee={mois.year}"))
    return lignes


# (code, niveau, titre, explication, icône, fonction, module de droits nécessaire pour la voir)
CONTROLES = [
    ("bail_irl", BLOQUANT, "Baux sans IRL de référence", "La révision du loyer ne peut pas être calculée.", "irl", baux_sans_irl, "baux"),
    ("bail_locataire", BLOQUANT, "Baux sans locataire", "Le bail, les quittances et les relances ne peuvent pas être édités.", "personne",
     baux_sans_locataire, "baux"),
    ("lot_surface", BLOQUANT, "Logements sans surface", "Mention obligatoire dans le bail et l'annonce.", "surface", lots_sans_surface, "biens"),
    ("lot_dpe", BLOQUANT, "Logements sans DPE", "Classe énergie obligatoire dans le bail et l'annonce.", "dpe", lots_sans_dpe, "biens"),
    ("locataire_email", A_COMPLETER, "Locataires sans adresse e-mail", "Quittances et relances par mail impossibles.", "mail",
     locataires_sans_email, "locataires"),
    ("bail_depot", A_COMPLETER, "Baux avec un dépôt de garantie à 0 €", "À vérifier : dépôt non versé, ou montant non saisi ?", "depot",
     baux_depot_zero, "baux"),
    ("bail_pdl", A_COMPLETER, "Baux sans n° de compteur électrique (PDL)", "Repris dans l'état des lieux et utile au changement de locataire.",
     "eclair", baux_sans_pdl, "baux"),
    ("chaudiere", A_COMPLETER, "Chaudières sans date d'entretien", "Le rappel annuel de l'attestation part de cette date.", "chaudiere",
     chaudieres_sans_entretien, "biens"),
    ("conciergerie", A_COMPLETER, "Mois avec séjours sans facture de conciergerie",
     "La facture arrive vers le 5 du mois suivant : déposez-la pour la contrôler.", "facture", conciergerie_manquante, "compta"),
    ("booking", A_COMPLETER, "Mois sans séjour Booking", "Relevé des versements Booking pas encore importé ?", "booking",
     booking_manquant, "lcd"),
    ("pret_tableau", A_COMPLETER, "Prêts sans tableau de la banque", "L'échéancier est calculé : importez le tableau pour avoir les "
     "montants exacts.", "banque", prets_sans_tableau, "finances"),
]
NIVEAUX = [(BLOQUANT, "Bloquent un document"), (A_COMPLETER, "À compléter")]
# ce qui est vérifié, dit court pour « Tout est bon »
SUJETS = {"bail_irl": "IRL des baux", "bail_locataire": "locataires des baux", "lot_surface": "surfaces", "lot_dpe": "DPE",
          "locataire_email": "e-mails des locataires", "bail_depot": "dépôts de garantie", "bail_pdl": "compteurs électriques",
          "chaudiere": "entretien des chaudières", "conciergerie": "factures de conciergerie", "booking": "imports Booking",
          "pret_tableau": "tableaux des prêts"}


def executer(user=None):
    """Résultat de tous les contrôles visibles par l'utilisateur, lignes écartées retirées."""
    from . import droits
    from .models import ControleIgnore

    acces = droits.niveaux(user) if user is not None else None
    ignores = {}
    for code, cle in ControleIgnore.objects.values_list("code", "cle"):
        ignores.setdefault(code, set()).add(cle)
    resultats = []
    for code, niveau, titre, explication, icone, fonction, module in CONTROLES:
        if acces is not None and not acces.get(module):
            continue
        lignes = [{"cle": c, "libelle": l, "url": u} for c, l, u in fonction() if c not in ignores.get(code, set())]
        resultats.append({"code": code, "niveau": niveau, "titre": titre, "sujet": SUJETS.get(code, titre), "explication": explication, "icone": icone,
                          "lignes": lignes, "nombre": len(lignes), "ignores": len(ignores.get(code, ()))})
    return resultats


def _cle_cache(user):
    return f"controles:{getattr(user, 'pk', None) or 'tous'}"


def memoriser(user, resultats):
    """Garde 2 minutes le nombre de contrôles à traiter : la pastille du menu ne refait pas tous les contrôles à chaque page."""
    from django.core.cache import cache

    n = sum(1 for r in resultats if r["nombre"])
    cache.set(_cle_cache(user), n, 120)
    return n


def oublier(user=None):
    from django.core.cache import cache

    cache.delete(_cle_cache(user))


def nombre_a_traiter(user=None):
    """Pastille du menu : nombre de contrôles qui ont au moins une ligne (recalculé au plus toutes les 2 minutes)."""
    from django.core.cache import cache

    n = cache.get(_cle_cache(user))
    return memoriser(user, executer(user)) if n is None else n
