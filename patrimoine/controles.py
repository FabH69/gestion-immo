"""Page « Contrôles » : informations manquantes ou à vérifier (pas d'échéance datée : celles-ci restent dans « À faire »).

Chaque contrôle renvoie des lignes (clé, libellé, lien pour compléter). Une ligne peut être écartée avec « Ne pas
contrôler » (cas voulu, ex. un dépôt de garantie réellement à 0 €) : elle est mémorisée dans ControleIgnore."""
import calendar
import functools
from datetime import date

from django.urls import reverse

from .models import _ajouter_mois

BLOQUANT, A_COMPLETER = "bloquant", "completer"
HABITATION = ("appartement", "maison", "studio")
TYPES_BAIL_HABITATION = ("nu", "meuble", "etudiant", "mobilite")
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


_GABARIT_PK = 987654321


@functools.lru_cache(maxsize=None)
def _gabarit_url(nom):
    return reverse(f"admin:patrimoine_{nom}_change", args=[_GABARIT_PK])


def _a(nom, pk):
    # adresse calculée une fois par table puis complétée : reverse() est lent quand il y a des centaines de lignes
    return _gabarit_url(nom).replace(str(_GABARIT_PK), str(pk))


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


def lots_dpe_expire():
    from django.utils import timezone

    aujourdhui = timezone.localdate()
    return [(f"lot:{l.pk}", f"{_nom(l)} · expiré le {l.dpe_fin_validite:%d/%m/%Y}", _a("lot", l.pk)) for l in _lots_habitation()
            if l.classe_dpe and l.dpe_fin_validite and l.dpe_fin_validite < aujourdhui]


def lots_dpe_sans_date():
    return [(f"lot:{l.pk}", _nom(l), _a("lot", l.pk)) for l in _lots_habitation() if l.classe_dpe and not l.dpe_fin_validite]


def lots_location_interdite():
    """Logements loués ou à louer dont la classe est déjà interdite à la location (loi Climat)."""
    from django.utils import timezone

    from . import dpe

    aujourdhui = timezone.localdate()
    lignes = []
    for l in _lots_habitation():
        quand = dpe.interdiction(l.classe_dpe, l.bien.code_postal)
        if quand and quand <= aujourdhui and l.exploitation != "courte":
            lignes.append((f"lot:{l.pk}:{l.classe_dpe}", f"{_nom(l)} · classe {l.classe_dpe}", _a("lot", l.pk)))
    return lignes


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


def historique_non_repris():
    """Loyers du suivi historique pas encore devenus des paiements reçus (pas de bail ce mois-là, paiement déjà saisi)."""
    from .models import EncaissementHistorique
    from .reprise_historique import raisons

    lignes = list(EncaissementHistorique.objects.select_related("lot__bien").order_by("lot_id", "mois"))
    return [(f"historique:{h.pk}", f"{_nom(h.lot)} – {MOIS_FR[h.mois.month - 1]} {h.mois.year} : {r or 'à reprendre'}",
             _a("encaissementhistorique", h.pk))
            for h, r in zip(lignes, raisons(lignes))]


def loyers_payes_sans_reglement():
    """Règle de base : un loyer n'est payé que par un règlement reçu (ou une remise). Ne doit jamais rien trouver."""
    from django.db.models import DecimalField, F, Sum, Value
    from django.db.models.functions import Coalesce

    from .models import Echeance

    zero = Value(0, output_field=DecimalField())
    qs = (Echeance.objects.filter(statut=Echeance.Statut.PAYE)
          .annotate(paye=Coalesce(Sum("paiements__montant"), zero))
          .filter(paye__lt=F("loyer_hc") + F("charges") - F("remise")).select_related("bail__lot__bien"))
    return [(f"echeance:{e.pk}", f"{_nom(e.bail.lot)} – {MOIS_FR[e.periode.month - 1]} {e.periode.year}", _a("echeance", e.pk)) for e in qs]


def avances_non_utilisees():
    """Paiements reçus dont une partie n'est affectée à aucun loyer : à garder pour le mois suivant ou à rembourser.
    Calcul fait par la base (une requête), même avec des années de paiements reçus."""
    from django.db.models import DecimalField, F, Sum, Value
    from django.db.models.functions import Coalesce

    from .models import Versement

    zero = Value(0, output_field=DecimalField())
    qs = (Versement.objects.filter(annule=False)
          .annotate(reparti=Coalesce(Sum("paiements__montant"), zero))
          .annotate(reste=F("montant") - F("reparti") - Coalesce(F("part_depot"), zero) - Coalesce(F("rembourse"), zero))
          .filter(reste__gt=0).select_related("bail__lot__bien").order_by("-date", "-pk"))
    return [(f"avance:{v.pk}", f"{_nom(v.bail.lot)} – {v.reste:.2f} € reçus le {v.date:%d/%m/%Y}".replace(".", ","), _a("versement", v.pk))
            for v in qs]


# (code, niveau, titre, explication, icône, fonction, module de droits nécessaire pour la voir)
CONTROLES = [
    ("bail_irl", BLOQUANT, "Baux sans IRL de référence", "La révision du loyer ne peut pas être calculée.", "irl", baux_sans_irl, "baux"),
    ("bail_locataire", BLOQUANT, "Baux sans locataire", "Le bail, les quittances et les relances ne peuvent pas être édités.", "personne",
     baux_sans_locataire, "baux"),
    ("lot_surface", BLOQUANT, "Logements sans surface", "Mention obligatoire dans le bail et l'annonce.", "surface", lots_sans_surface, "biens"),
    ("lot_dpe", BLOQUANT, "Logements sans DPE", "Classe énergie obligatoire dans le bail et l'annonce.", "dpe", lots_sans_dpe, "biens"),
    ("lot_dpe_expire", BLOQUANT, "Logements au DPE expiré", "Un DPE valable est obligatoire pour l'annonce et le bail : à refaire.",
     "dpe", lots_dpe_expire, "biens"),
    ("lot_interdit", BLOQUANT, "Logements interdits à la location (classe DPE)",
     "Selon la loi en vigueur, ni nouveau bail ni renouvellement : travaux ou nouveau DPE nécessaires.", "dpe", lots_location_interdite, "biens"),
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
    ("lot_dpe_date", A_COMPLETER, "DPE sans date", "Saisissez la date ou le n° du DPE pour être prévenu avant la fin de validité.",
     "dpe", lots_dpe_sans_date, "biens"),
    ("pret_tableau", A_COMPLETER, "Prêts sans tableau de la banque", "L'échéancier est calculé : importez le tableau pour avoir les "
     "montants exacts.", "banque", prets_sans_tableau, "finances"),
    ("loyer_sans_reglement", BLOQUANT, "Loyers payés sans règlement reçu",
     "Un loyer n'est payé que par un paiement reçu (ou une remise) : à corriger.", "facture", loyers_payes_sans_reglement, "loyers"),
    ("historique", A_COMPLETER, "Loyers de l'historique pas encore repris",
     "Ces encaissements ne sont pas encore des paiements reçus : rattachez-les au bon bail.", "facture", historique_non_repris, "loyers"),
    ("avance", A_COMPLETER, "Avances non utilisées", "Trop-perçus gardés pour les loyers suivants : à imputer ou à rembourser.",
     "banque", avances_non_utilisees, "loyers"),
]
NIVEAUX = [(BLOQUANT, "Bloquent un document"), (A_COMPLETER, "À compléter")]
# ce qui est vérifié, dit court pour « Tout est bon »
SUJETS = {"bail_irl": "IRL des baux", "bail_locataire": "locataires des baux", "lot_surface": "surfaces", "lot_dpe": "DPE", "lot_dpe_expire": "validité des DPE",
          "lot_interdit": "classes DPE louables", "lot_dpe_date": "dates des DPE",
          "locataire_email": "e-mails des locataires", "bail_depot": "dépôts de garantie", "bail_pdl": "compteurs électriques",
          "chaudiere": "entretien des chaudières", "conciergerie": "factures de conciergerie", "booking": "imports Booking",
          "pret_tableau": "tableaux des prêts", "loyer_sans_reglement": "règlements des loyers",
          "historique": "reprise de l'historique", "avance": "avances des locataires"}


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


DUREE_PASTILLE = 15 * 60  # recalcul au plus tous les quarts d'heure, et tout de suite après une modification


def _cle_cache(user):
    from django.core.cache import cache

    return f"controles:{cache.get('controles:generation', 0)}:{getattr(user, 'pk', None) or 'tous'}"


def memoriser(user, resultats):
    """Garde le nombre de contrôles à traiter : la pastille du menu ne refait pas tous les contrôles à chaque page."""
    from django.core.cache import cache

    n = sum(1 for r in resultats if r["nombre"])
    cache.set(_cle_cache(user), n, DUREE_PASTILLE)
    return n


def oublier(user=None):
    """Sans utilisateur : la pastille de tout le monde sera recalculée à la prochaine page."""
    from django.core.cache import cache

    if user is None:
        try:
            cache.incr("controles:generation")
        except ValueError:
            cache.set("controles:generation", 1, None)
    else:
        cache.delete(_cle_cache(user))


class OublierApresModificationMiddleware:
    """Après un enregistrement (formulaire envoyé), la pastille des contrôles est recalculée à la page suivante."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method == "POST" and response.status_code < 400:
            oublier()
        return response


def nombre_a_traiter(user=None):
    """Pastille du menu : nombre de contrôles qui ont au moins une ligne (recalculé au plus toutes les 2 minutes)."""
    from django.core.cache import cache

    n = cache.get(_cle_cache(user))
    return memoriser(user, executer(user)) if n is None else n
