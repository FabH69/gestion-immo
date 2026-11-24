"""Amortissements des biens en LMNP et en société : plan linéaire de chaque immobilisation, dotations de l'année,
registre d'un dossier, report des amortissements non déduits (LMNP) et alertes sur les factures de plus de 500 €.

Convention : linéaire, prorata temporis sur une année de 360 jours (mois de 30 jours) la première année et l'année de sortie."""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from .models import ChargeLMNP, Immobilisation, PieceComptable, ReportAmortissements

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

# au-delà de ce montant, un achat (meuble, équipement, travaux d'amélioration) s'amortit au lieu de passer en charge
SEUIL = Decimal(500)

# durées d'usage proposées (en années) ; chaque fiche peut les changer
DUREES = {"terrain": 0, "gros_oeuvre": 50, "facade": 25, "installations": 20, "agencements": 10, "travaux": 15,
          "mobilier": 7, "electromenager": 5, "informatique": 3, "frais": 5, "autre": 5}

# décomposition habituelle du prix d'un logement : (catégorie, part en %, durée)
DECOMPOSITION = [("terrain", 15, 0), ("gros_oeuvre", 40, 50), ("facade", 10, 25), ("installations", 15, 20), ("agencements", 20, 12)]

# rubriques des biens en LMNP / société où un achat de plus de 500 € peut cacher une immobilisation
RUBRIQUES_A_VERIFIER = ("autre", "consommables")


def _jour_360(d):
    return (d.month - 1) * 30 + min(d.day, 30) - 1  # 0 le 1er janvier, 359 le 31 décembre


def plan(immo):
    """Plan d'amortissement : [{annee, dotation, cumul, vnc, reprise}] ; la ligne « reprise » résume les années
    amorties par le comptable avant la première année tenue dans l'appli."""
    valeur = immo.valeur or ZERO
    if not immo.duree or valeur <= 0:
        return []
    annuel = (valeur / immo.duree).quantize(CENT, ROUND_HALF_UP)
    debut = immo.mise_en_service
    lignes, cumul, annee = [], ZERO, debut.year
    if immo.annee_reprise and immo.annee_reprise > debut.year:
        cumul = min(immo.amortissements_anterieurs or ZERO, valeur)
        lignes.append({"annee": immo.annee_reprise - 1, "dotation": None, "cumul": cumul, "vnc": valeur - cumul, "reprise": True})
        annee = immo.annee_reprise
    fin = immo.date_sortie
    while cumul < valeur and (fin is None or annee <= fin.year) and annee < debut.year + immo.duree + 2:
        dotation = annuel
        if annee == debut.year:
            dotation = annuel * (360 - _jour_360(debut)) / 360
        if fin and annee == fin.year:
            jours = _jour_360(fin) + 1 - (_jour_360(debut) if annee == debut.year else 0)
            dotation = annuel * max(jours, 0) / 360
        dotation = min(dotation.quantize(CENT, ROUND_HALF_UP), valeur - cumul)
        cumul += dotation
        lignes.append({"annee": annee, "dotation": dotation, "cumul": cumul, "vnc": valeur - cumul, "reprise": False})
        annee += 1
    return lignes


def dotation(immo, annee):
    return next((l["dotation"] for l in plan(immo) if l["annee"] == annee and not l["reprise"]), ZERO)


def situation(immo, annee):
    """Valeur, amortissements avant l'année, dotation de l'année, valeur restante à la fin de l'année."""
    lignes = plan(immo)
    avant = max((l["cumul"] for l in lignes if l["annee"] < annee), default=ZERO)
    dot = next((l["dotation"] for l in lignes if l["annee"] == annee and not l["reprise"]), ZERO)
    sortie = immo.date_sortie is not None and immo.date_sortie.year < annee
    return {"immo": immo, "valeur": immo.valeur, "anterieurs": avant, "dotation": dot,
            "vnc": ZERO if sortie else immo.valeur - avant - dot, "sortie": sortie}


def duree_proposee(categorie):
    return DUREES.get(categorie, 5)


# --- dossiers ----------------------------------------------------------------------------------------------------------
def immobilisations_dossier(dossier):
    from . import dossiers

    return Immobilisation.objects.filter(bien__in=dossiers.biens(dossier)).select_related("bien", "lot", "piece")


def registre(dossier, annee):
    """Lignes du registre (immobilisations en service pendant l'année ou avant) et totaux."""
    lignes = [situation(i, annee) for i in immobilisations_dossier(dossier) if i.mise_en_service.year <= annee]
    lignes = [l for l in lignes if not (l["sortie"] and l["immo"].date_sortie.year < annee - 1)]
    total = {k: sum((l[k] for l in lignes), ZERO) for k in ("valeur", "anterieurs", "dotation", "vnc")}
    return lignes, total


def dotations_biens(biens, annee):
    return sum((dotation(i, annee) for i in Immobilisation.objects.filter(bien__in=biens)), ZERO)


def a_verifier(biens, annee=None):
    """Factures de plus de 500 € classées en charges dans une rubrique où peut se cacher un achat à amortir,
    et charges LMNP « entretien » ou « autre » de plus de 500 €, sauf celles confirmées comme charges."""
    pieces = PieceComptable.objects.filter(bien__in=biens, rubrique__in=RUBRIQUES_A_VERIFIER, montant__gt=SEUIL,
                                           charge_confirmee=False, preremplie=False)
    if annee:
        pieces = pieces.filter(Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee))
    return pieces.select_related("bien").order_by("-montant")


# --- LMNP : dotations par lot et report des amortissements non déduits --------------------------------------------------
def dotations_lot(lot, annee, lots_lmnp_du_bien):
    """Dotations des immobilisations du lot ; celles du bien entier vont au premier lot LMNP du bien."""
    qs = Immobilisation.objects.filter(bien=lot.bien)
    premier = lots_lmnp_du_bien[0] if lots_lmnp_du_bien else None
    total = ZERO
    for i in qs:
        cible = i.lot_id if i.lot_id in {l.pk for l in lots_lmnp_du_bien} else (premier.pk if premier else None)
        if cible == lot.pk:
            total += dotation(i, annee)
    return total


def depart_lmnp():
    """Année d'où part le calcul des reports : celle de la reprise si le comptable a donné son report, sinon la première
    mise en service d'une immobilisation LMNP. Renvoie (année ou None, report saisi ou None)."""
    from .lmnp import lots_lmnp

    report = ReportAmortissements.objects.filter(dossier="2031").first()
    if report:
        return report.annee, report
    premiere = Immobilisation.objects.filter(bien__lots__in=lots_lmnp()).order_by("mise_en_service").first()
    return (premiere.mise_en_service.year if premiere else None), None


def report_lmnp(annee, totaux_de):
    """Amortissements non déduits reportés au début de l'année (LMNP, tous les biens ensemble, comme la 2031).
    totaux_de(y) donne {"resultat", "amortissements"} d'une année, sans report."""
    debut, report = depart_lmnp()
    if debut is None or debut > annee:
        return ZERO
    reste = report.montant if report else ZERO
    for y in range(debut, annee):  # report sans limite de durée (CGI art. 39 C II) : toujours depuis l'année de départ
        t = totaux_de(y)
        deduit = min(t["amortissements"] + reste, max(t["resultat"], ZERO))
        reste = t["amortissements"] + reste - deduit
    return reste


def lignes_alerte_charges_lmnp(lots, annee):
    return ChargeLMNP.objects.filter(lot__in=lots, annee=annee, montant__gt=SEUIL,
                                     categorie__in=(ChargeLMNP.Categorie.ENTRETIEN, ChargeLMNP.Categorie.AUTRE))


# --- facture classée « Immobilisation (à amortir) » ---------------------------------------------------------------------
def depuis_facture(piece, categorie=None, duree=None, mise_en_service=None):
    """Crée ou met à jour la fiche immobilisation d'une facture classée « Immobilisation (à amortir) » ; supprime celle
    créée depuis la facture si la facture est reclassée en charge. Renvoie (immobilisation ou None, créée ?)."""
    liees = Immobilisation.objects.filter(piece=piece)
    if piece.rubrique != "immobilisation" or piece.montant is None or piece.date is None:
        liees.filter(origine=Immobilisation.Origine.FACTURE).delete()
        return None, False
    immo = liees.first()
    cree = immo is None
    if cree:
        immo = Immobilisation(piece=piece, origine=Immobilisation.Origine.FACTURE,
                              designation=(piece.libelle or piece.fournisseur or "Immobilisation")[:200])
    immo.bien = piece.bien
    immo.fournisseur = piece.fournisseur
    immo.date_achat = piece.date
    immo.valeur = piece.montant
    if categorie:
        immo.categorie = categorie
    if duree is not None:
        immo.duree = duree
    elif cree:
        immo.duree = duree_proposee(immo.categorie)
    if mise_en_service:
        immo.mise_en_service = mise_en_service
    elif cree:
        immo.mise_en_service = piece.date
    immo.save()
    return immo, cree
