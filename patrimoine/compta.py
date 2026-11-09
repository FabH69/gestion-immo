"""Comptabilité par régime : revenus fonciers (lignes 2072 ou 2044), LMNP et société (rubriques).

Chaque bien a un régime (champ « Comptabilité » de sa fiche) qui le range dans une entrée du menu Comptabilité et choisit
la codification de ses factures. Le récapitulatif par rubrique reprend le tableau de suivi de Fabien : ce que l'appli
connaît déjà se remplit seul (réservations et frais de gestion, échéancier des prêts) ; le reste vient des factures codées.
Tant qu'aucune facture n'est codée pour une charge du logement, l'estimation des charges mensuelles tient sa place.
"""
import calendar
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Bien, ChargeLCD, ChargeLMNP, EncaissementLCD, PieceComptable, Proprietaire

ZERO = Decimal("0.00")
REGIMES = ["foncier", "lmnp", "societe"]
PAR_RUBRIQUE = ("lmnp", "societe")
SOURCES = {"R": "réservations et frais de gestion", "P": "échéancier du prêt", "F": "factures et charges saisies",
           "E": "estimation des charges mensuelles"}
CHARGES_LOGEMENT = {code for code, _ in PieceComptable.RUBRIQUES[1][1]}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# factures à codifier : la codification attendue dépend du régime du bien
_RUBRIQUE = Q(bien__comptabilite__in=PAR_RUBRIQUE)
MANQUE = (Q(preremplie=True) | Q(montant__isnull=True) | Q(date__isnull=True)
          | (_RUBRIQUE & Q(rubrique="")) | (~_RUBRIQUE & Q(ligne="")))


def libelle(regime):
    """Nom de l'entrée du menu : « Revenus fonciers », « LMNP », ou le nom de la société (H-INVEST)."""
    if regime == "foncier":
        return "Revenus fonciers"
    if regime == "lmnp":
        return "LMNP"
    noms = sorted(set(Bien.objects.filter(comptabilite="societe").values_list("proprietaire__nom", flat=True)))
    return noms[0] if len(noms) == 1 else "Sociétés"


def regimes_utilises():
    return [r for r in REGIMES if Bien.objects.filter(comptabilite=r).exists()]


def biens(regime, annee=None):
    qs = Bien.objects.filter(comptabilite=regime).select_related("proprietaire").order_by("nom")
    if annee:
        qs = qs.filter(Q(vendu=False) | Q(date_vente__year__gte=annee))
    return qs


def a_codifier(regime=None):
    qs = PieceComptable.objects.filter(MANQUE)
    return qs.filter(bien__comptabilite=regime) if regime else qs


def biens_2044():
    """Biens en nom propre déclarés en revenus fonciers (déclaration 2044, ex. Pollionnay)."""
    return Bien.objects.filter(comptabilite="foncier", proprietaire__type=Proprietaire.Type.NOM_PROPRE)


# --- correspondances vers les rubriques ------------------------------------------------------------------------------
def _norme(texte):
    return "".join(c for c in unicodedata.normalize("NFD", (texte or "").lower()) if unicodedata.category(c) != "Mn")


MOTS_CHARGES = [("pno", ["pno", "assurance"]), ("copro", ["copro", "syndic"]), ("taxe_fonciere", ["taxe fonciere", "fonciere"]),
                ("electricite", ["electricite", "edf", "engie"]), ("eau", ["eau"]), ("fibre", ["fibre", "internet", "box"]),
                ("comptable", ["comptab", "cga"])]


def rubrique_de_charge(libelle_charge):
    """Rubrique d'une charge mensuelle (Courte durée › Charges mensuelles) d'après son libellé."""
    t = _norme(libelle_charge)
    return next((code for code, mots in MOTS_CHARGES if any(m in t for m in mots)), "autre")


def rubrique_de_charge_lmnp(charge):
    """Rubrique d'une charge LMNP saisie à l'année (États › Charges LMNP)."""
    if charge.categorie == ChargeLMNP.Categorie.GESTION:
        return "comptable" if any(m in _norme(charge.libelle) for m in ("compta", "cga", "expert")) else "commission"
    return {"taxe_fonciere": "taxe_fonciere", "copropriete": "copro", "assurance": "pno", "energie": "electricite"}.get(charge.categorie, "autre")


# --- récapitulatif par rubrique --------------------------------------------------------------------------------------
def dernier_mois(annee):
    """Mois retenus : jusqu'au mois en cours pour l'année en cours, toute l'année pour une année passée."""
    aujourdhui = timezone.localdate()
    if annee < aujourdhui.year:
        return 12
    return aujourdhui.month if annee == aujourdhui.year else 0


def detail_bien(bien, annee, jusqua=12):
    """{rubrique: {mois: {"montant", "sources"}}} pour un bien ; le mois 0 reçoit les montants saisis à l'année."""
    from .lcd import suivi_annuel

    d = defaultdict(lambda: defaultdict(lambda: {"montant": ZERO, "sources": set()}))

    def ajouter(rubrique, mois, montant, source):
        if montant:
            case = d[rubrique][mois]
            case["montant"] += Decimal(montant)
            case["sources"].add(source)

    # 1. factures codées (le mois de la prestation s'il est indiqué, sinon le paiement, sinon la facture)
    factures = defaultdict(set)
    for p in PieceComptable.objects.filter(bien=bien, preremplie=False).exclude(rubrique="").exclude(montant=None):
        jour = p.mois_concerne or p.date_paiement or p.date
        if jour and jour.year == annee and jour.month <= jusqua:
            ajouter(p.rubrique, jour.month, p.montant, "F")
            factures[p.rubrique].add(jour.month)

    # 2. charges LMNP saisies à l'année (hors amortissements)
    for c in ChargeLMNP.objects.filter(lot__bien=bien, annee=annee).exclude(categorie=ChargeLMNP.Categorie.AMORTISSEMENT):
        ajouter(rubrique_de_charge_lmnp(c), 0, c.montant, "F")
        factures[rubrique_de_charge_lmnp(c)].add(0)

    # 3. courte durée : commissions des plateformes, conciergerie, ménage et consommables (factures prioritaires, mois par mois)
    for lot in bien.lots.all():
        saisies = {e.mois.month: e for e in EncaissementLCD.objects.filter(lot=lot, mois__year=annee)}
        if saisies:
            for m, e in saisies.items():
                if m <= jusqua:
                    ajouter("commission", m, e.commission + e.frais, "R")
                    if m not in factures["menage"]:
                        ajouter("menage", m, e.menage, "R")
        elif lot.exploitation == "courte" or lot.reservations_lcd.exists():
            for m in suivi_annuel(lot, annee)[:jusqua]:
                n = m["mois"]
                ajouter("commission", n, m["commission"] + m["frais"], "R")
                if n not in factures["commission"]:
                    ajouter("commission", n, m.get("conciergerie", ZERO), "R")
                if n not in factures["menage"]:
                    ajouter("menage", n, m.get("menage", ZERO), "R")
                if n not in factures["consommables"]:
                    ajouter("consommables", n, m.get("consommables", ZERO), "R")

    # 4. charges du logement sans facture de l'année : estimation des charges mensuelles
    for c in ChargeLCD.objects.filter(lot__bien=bien):
        rubrique = rubrique_de_charge(c.libelle)
        if factures[rubrique]:
            continue
        for n in range(1, jusqua + 1):
            if c.active_en(date(annee, n, 1), date(annee, n, calendar.monthrange(annee, n)[1])):
                ajouter(rubrique, n, c.montant_mensuel, "E")

    # 5. prêts du bien : mensualités (capital + intérêts) et assurance
    for pret in bien.prets.all():
        for e in pret.echeancier():
            if e["date"].year == annee and e["date"].month <= jusqua:
                ajouter("pret", e["date"].month, e["mensualite"], "P")
                ajouter("assurance_pret", e["date"].month, e["assurance"] or ZERO, "P")
    return d


def _lien(regime, bien, rubrique, annee, sources):
    if len(sources) != 1 or bien is None:
        return ""
    source = next(iter(sources))
    if source == "F":
        return f"{reverse('admin:patrimoine_piececomptable_changelist')}?compta={regime}&bien={bien.pk}&rubrique={rubrique}&annee={annee}"
    if source == "R":
        lot = next((l for l in bien.lots.all() if l.exploitation == "courte"), None)
        return f"{reverse('suivi_lcd')}?lot={lot.pk}&annee={annee}" if lot else ""
    if source == "P":
        return f"{reverse('admin:patrimoine_pret_changelist')}?bien__id__exact={bien.pk}"
    if source == "E":
        return f"{reverse('admin:patrimoine_chargelcd_changelist')}?lot__bien__id__exact={bien.pk}"
    return ""


def recapitulatif(regime, annee, bien_choisi=None, par_mois=False):
    """Tableau des rubriques : une colonne par bien (ou par mois pour la vue mensuelle), avec le total."""
    jusqua = dernier_mois(annee)
    liste = [b for b in biens(regime, annee) if bien_choisi is None or b.pk == bien_choisi.pk]
    details = {b.pk: detail_bien(b, annee, jusqua) for b in liste}
    sans_mois = any(0 in mois for d in details.values() for mois in d.values())
    if par_mois:
        colonnes = [{"titre": MOIS_COURTS[n - 1], "mois": n} for n in range(1, jusqua + 1)]
        if sans_mois:
            colonnes.append({"titre": "à l'année", "mois": 0})
    else:
        colonnes = [{"titre": b.nom, "bien": b} for b in liste]

    def case(rubrique, col):
        montant, sources = ZERO, set()
        for b in liste:
            if par_mois:
                mois = [col["mois"]]
            elif col["bien"].pk != b.pk:
                continue
            else:
                mois = list(details[b.pk][rubrique].keys())
            for n in mois:
                c = details[b.pk][rubrique].get(n)
                if c:
                    montant += c["montant"]
                    sources |= c["sources"]
        bien = col.get("bien") or (liste[0] if len(liste) == 1 else None)
        return {"montant": montant, "sources": sorted(sources), "lien": _lien(regime, bien, rubrique, annee, sources) if montant else ""}

    groupes, totaux = [], [ZERO] * len(colonnes)
    for titre, rubriques in PieceComptable.RUBRIQUES:
        lignes = []
        for code, nom in rubriques:
            cases = [case(code, col) for col in colonnes]
            total = sum((c["montant"] for c in cases), ZERO)
            if code == "autre" and not total:
                continue
            totaux = [t + c["montant"] for t, c in zip(totaux, cases)]
            lignes.append({"code": code, "libelle": nom, "cases": cases, "total": total, "note": NOTES.get(code, "")})
        if lignes:
            groupes.append({"titre": titre if titre != "Autre" else "", "lignes": lignes})
    periode = ("aucun mois écoulé" if not jusqua else f"janvier à {MOIS[jusqua - 1]} {annee}" if jusqua < 12 else f"année {annee}")
    return {"colonnes": colonnes, "groupes": groupes, "totaux": totaux, "total": sum(totaux, ZERO), "periode": periode,
            "biens": liste, "sources": SOURCES}


NOTES = {"pret": "mensualités (capital + intérêts) ; le bilan 2031 ne retient que les intérêts",
         "commission": "plateformes et conciergerie"}


# --- récapitulatif 2044 (nom propre, revenus fonciers) -----------------------------------------------------------------
LIGNES_2044 = [  # (numéro de la 2044, libellé, ligne correspondante de l'annexe 2072, genre)
    ("211", "Loyers bruts encaissés", "1", ""),
    ("212", "Dépenses à payer par le propriétaire et mises à la charge des locataires", "2", ""),
    ("213", "Subventions ANAH, indemnités d'assurance", "3", ""),
    ("215", "Total des recettes", "5", "total"),
    ("221", "Frais d'administration et de gestion", "6", ""),
    ("222", "Autres frais de gestion (forfait de 20 € par local)", "7", ""),
    ("223", "Primes d'assurance", "8", ""),
    ("224", "Dépenses de réparation, d'entretien et d'amélioration", "9", ""),
    ("225", "Charges récupérables non récupérées au départ du locataire", "10", ""),
    ("226", "Indemnités d'éviction, frais de relogement", "11", ""),
    ("227", "Taxes foncières, taxes annexes", "12", ""),
    ("228", "Déductions spécifiques", "15", ""),
    ("229", "Provisions pour charges de copropriété payées", "13", ""),
    ("230", "Régularisation des provisions déduites l'année précédente (à retrancher)", "14", ""),
    ("240", "Total des frais et charges", "16", "total"),
    ("250", "Intérêts et frais d'emprunt (dont assurance emprunteur)", "17", ""),
    ("", "Résultat foncier de l'immeuble (215 − 240 − 250)", "18", "total"),
]


def recap_2044(annee):
    from .annexe2072 import colonne

    colonnes = [colonne(b, annee) for b in biens_2044().filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).order_by("nom")]
    lignes = [{"numero": n, "libelle": l, "genre": g, "montants": [c["valeurs"].get(code, ZERO) for c in colonnes],
               "total": sum((c["valeurs"].get(code, ZERO) for c in colonnes), ZERO)} for n, l, code, g in LIGNES_2044]
    return {"colonnes": colonnes, "lignes": lignes}
