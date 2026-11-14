"""Comparatif d'un logement exploité en courte durée avec une location nue (revenus fonciers), avant impôts.

Courte durée : chiffres réels de l'appli pour N−1 et pour N (janvier au mois en cours) : nuitées hors taxe de séjour,
commissions, ménage, consommables, charges (factures déposées, sinon charges mensuelles) et prêt.
Location nue : loyer saisi, vacance (1 mois par an par défaut), GLI (3 % par défaut) ; chaque charge de la courte durée
reste à la charge du propriétaire, est récupérée sur le locataire (sauf pendant la vacance) ou disparaît ;
plus les dépenses ajoutées à la main. Le prêt est le même dans les deux cas."""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from . import compta
from .models import ComparatifNue, EncaissementLCD, PieceComptable

ZERO = Decimal(0)
DOUZE = Decimal(12)
EXPLOITATION = [code for titre, rubriques in PieceComptable.RUBRIQUES if titre != "Prêt" for code, _ in rubriques]
LIBELLES = {**PieceComptable.LIBELLES_RUBRIQUES, "commission": "Commissions (plateformes, conciergerie)"}


def lots_courte_duree():
    from .models import Lot

    return Lot.objects.filter(exploitation="courte", vendu=False, bien__vendu=False).select_related("bien").order_by("bien__nom", "designation")


def comparatif_du_lot(lot):
    """Hypothèses de location nue du logement (créées avec les valeurs par défaut au premier affichage)."""
    c = ComparatifNue.objects.filter(lot=lot).first()
    if c is None:
        bail = lot.baux.order_by("-date_debut").first()
        c = ComparatifNue.objects.create(lot=lot, loyer_hc=lot.loyer_propose or (bail.loyer_hc if bail else 0),
                                         charges=bail.charges if bail else 0)
    return c


def _seul_logement(lot):
    """Les factures et le prêt sont rattachés au bien : on ne les reprend que si le bien n'a que ce logement."""
    return not lot.bien.lots.exclude(pk=lot.pk).filter(vendu=False).exists()


def courte_duree(lot, annee, jusqua):
    """Recettes et charges réelles de la courte durée de janvier à `jusqua` (mois inclus)."""
    from .lcd import suivi_annuel

    recettes, nuits, sejours = ZERO, 0, 0
    charges = defaultdict(lambda: ZERO)
    encaissements = list(EncaissementLCD.objects.filter(lot=lot, mois__year=annee, mois__month__lte=jusqua))
    if encaissements:
        recettes = sum((e.ca + e.aircover + e.regul for e in encaissements), ZERO)
    else:
        for m in suivi_annuel(lot, annee)[:jusqua]:
            recettes += m["brut"] - m["taxe"]  # la taxe de séjour est reversée à la commune
            nuits += m["nuits"]
            sejours += m["sejours"]
    seul = _seul_logement(lot)
    pret = ZERO
    if seul:
        detail = compta.detail_bien(lot.bien, annee, jusqua)
        for rubrique, mois in detail.items():
            total = sum((c["montant"] for c in mois.values()), ZERO)
            if rubrique in ("pret", "assurance_pret"):
                pret += total
            else:
                charges[rubrique] += total
    else:
        # bien à plusieurs logements : seulement ce qui est propre au logement (relevés, frais de gestion, charges mensuelles)
        if encaissements:
            for e in encaissements:
                charges["commission"] += e.commission + e.frais
                charges["menage"] += e.menage
        else:
            for m in suivi_annuel(lot, annee)[:jusqua]:
                charges["commission"] += m["commission"] + m["frais"] + m["conciergerie"]
                charges["menage"] += m["menage"]
                charges["consommables"] += m["consommables"]
        for c in lot.charges_lcd.all():
            rubrique = compta.rubrique_de_charge(c.libelle)
            for n in range(1, jusqua + 1):
                if c.active_en(date(annee, n, 1), date(annee, n, calendar.monthrange(annee, n)[1])):
                    charges[rubrique] += c.montant_mensuel
    return {"recettes": recettes, "charges": {k: v for k, v in charges.items() if v}, "pret": pret, "pret_connu": seul,
            "nuits": nuits, "sejours": sejours}


def location_nue(comparatif, courte, jusqua):
    """Même période en location nue, à partir des hypothèses et des charges réelles de la courte durée."""
    part = Decimal(jusqua) / DOUZE
    vacance = min(comparatif.vacance_mois, DOUZE) / DOUZE
    mois_loues = Decimal(jusqua) * (1 - vacance)
    loyers = comparatif.loyer_hc * mois_loues
    perte_vacance = comparatif.loyer_hc * Decimal(jusqua) * vacance
    gli = (comparatif.loyer_hc + comparatif.charges) * mois_loues * comparatif.gli / 100
    charges = {}
    for rubrique, montant in courte["charges"].items():
        traitement, pct = comparatif.traitement(rubrique)
        if traitement == "charge":
            charges[rubrique] = montant
        elif traitement == "recuperable":
            charges[rubrique] = montant * vacance  # payée par le propriétaire seulement quand le logement est vide
        elif traitement == "partielle":
            recupere = montant * pct / 100
            charges[rubrique] = montant - recupere + recupere * vacance
        else:
            charges[rubrique] = ZERO
    return {"loyers": loyers, "perte_vacance": perte_vacance, "gli": gli, "charges": charges, "mois_loues": mois_loues, "part": part}



def periode(lot, comparatif, annee, jusqua):
    """Une période (N−1 complète ou N à date) : lignes du tableau et totaux pour les deux modes."""
    c = courte_duree(lot, annee, jusqua)
    n = location_nue(comparatif, c, jusqua)
    part = n["part"]
    lignes = [{"libelle": "Loyers encaissés" if not c["nuits"] else f"Nuitées ({c['nuits']} nuits, {c['sejours']} séjours) / loyers",
               "courte": c["recettes"], "nue": n["loyers"], "genre": "recette",
               "note": f"{comparatif.loyer_hc:.0f} € × {n['mois_loues']:.1f} mois loués".replace(".", ",")}]
    for code in EXPLOITATION:
        if code in c["charges"]:
            traitement, pct = comparatif.traitement(code)
            note = dict(ComparatifNue.TRAITEMENTS)[traitement] + (f" ({pct:.0f} %)" if traitement == "partielle" else "")
            lignes.append({"libelle": LIBELLES.get(code, code), "courte": -c["charges"][code], "nue": -n["charges"][code],
                           "genre": "charge", "code": code, "note": note.lower()})
    if comparatif.gli:
        lignes.append({"libelle": "GLI (assurance loyers impayés)", "courte": ZERO, "nue": -n["gli"], "genre": "charge",
                       "note": f"{comparatif.gli:g} % des loyers et charges".replace(".", ",")})
    vacance = min(comparatif.vacance_mois, DOUZE) / DOUZE
    for l in comparatif.lignes.all():
        montant = l.montant_annuel * part
        courte = montant if l.cote in ("courte", "deux") else ZERO
        nue = (montant * vacance if l.recuperable else montant) if l.cote in ("nue", "deux") else ZERO
        cote = {"nue": "location nue", "courte": "courte durée", "deux": "les deux"}[l.cote]
        lignes.append({"libelle": l.libelle, "courte": -courte, "nue": -nue, "genre": "charge",
                       "note": f"ajoutée à la main, {cote}" + (", récupérable en location nue" if l.recuperable and l.cote != "courte" else "")})
    resultat = {k: sum((l[k] for l in lignes), ZERO) for k in ("courte", "nue")}
    for l in lignes:
        l["ecart"] = l["nue"] - l["courte"]
    pret = -c["pret"]
    cash = {k: resultat[k] + pret for k in resultat} if c["pret_connu"] and c["pret"] else None
    return {"annee": annee, "jusqua": jusqua, "lignes": lignes, "resultat": resultat, "ecart": resultat["nue"] - resultat["courte"],
            "pret": pret if c["pret_connu"] and c["pret"] else None, "pret_connu": c["pret_connu"],
            "cash": cash, "perte_vacance": n["perte_vacance"],
            "titre": f"{annee}" if jusqua == 12 else f"{annee}, janvier à {compta.MOIS[jusqua - 1]}"}


def comparer(lot, annee):
    """N−1 (année complète) et N (janvier au mois en cours) ; N seul s'il n'y a rien en N−1."""
    comparatif = comparatif_du_lot(lot)
    jusqua = compta.dernier_mois(annee)
    periodes = [periode(lot, comparatif, annee - 1, 12)]
    if jusqua:
        periodes.append(periode(lot, comparatif, annee, jusqua))
    return comparatif, periodes
