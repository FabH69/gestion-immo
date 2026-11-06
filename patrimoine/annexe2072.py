"""Annexe 2072-S-A1-SD (SCI à l'IR) : revenus de chaque immeuble selon la règle des revenus fonciers.

Sources : loyers HC encaissés dans l'année (paiements, ou ancien suivi), factures codées dans la comptabilité interne
(année du paiement), forfait de 20 € par local (ligne 7), intérêts et assurance des prêts rattachés au bien (ligne 17).
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q

ZERO = Decimal("0.00")
FORFAIT_PAR_LOCAL = Decimal("20")

LIGNES = [
    ("1", "Montant brut des fermages ou des loyers encaissés", "auto"),
    ("2", "Dépenses déductibles incombant normalement à la société immobilière et mises par convention à la charge des locataires", ""),
    ("3", "Recettes brutes diverses. Subventions ANAH, indemnités d'assurance", ""),
    ("4", "Recettes qu'aurait pu produire la location des propriétés qui ne sont pas affectées à l'habitation dont la société "
          "se réserve la jouissance ou qu'elle met gratuitement à la disposition des associés ou des tiers", ""),
    ("5", "TOTAL DES RECETTES (lignes 1 + 2 + 3 + 4)", "total"),
    ("titre", "DÉDUCTIONS, FRAIS ET CHARGES", "titre"),
    ("6", "Frais d'administration et de gestion", ""),
    ("7", "Autres frais de gestion non déductibles pour leur montant réel (montant forfaitaire fixé à 20 € par local)", "auto"),
    ("8", "Primes d'assurance", ""),
    ("9", "Dépenses de réparation, d'entretien et d'amélioration", ""),
    ("9b", "Dont dépenses de travaux de rénovation énergétique permettant à un bien de passer d'une classe énergétique "
           "E, F ou G à une classe A, B, C ou D", ""),
    ("10", "Charges récupérables non récupérées au départ du locataire", ""),
    ("11", "Indemnités d'éviction, frais de relogement", ""),
    ("12", "Impositions (y compris la CRL payée au titre de l'année considérée)", ""),
    ("13", "Provisions pour charges de copropriété payées au titre de l'année considérée", ""),
    ("14", "Régularisation des provisions pour charges de copropriété déduites au titre de l'année antérieure par les "
           "copropriétaires bailleurs", ""),
    ("15", "Montant de la déduction spécifique", ""),
    ("16", "TOTAL DES DÉDUCTIONS, FRAIS ET CHARGES (lignes 6+7+8+9+10+11+12+13−14+15)", "total"),
    ("17", "Intérêts des emprunts contractés pour l'acquisition, la reconstruction, l'agrandissement, la réparation, "
           "l'amélioration ou la conservation des immeubles", "auto"),
    ("18", "REVENU (+) OU DÉFICIT (−) DE L'IMMEUBLE (lignes 5 − 16 − 17)", "total"),
    ("19", "Réintégration du supplément de déduction", ""),
    ("20", "Rémunérations et avantages en nature attribués aux associés", ""),
    ("21", "REVENU NET (+) OU DÉFICIT (−) (lignes 18 + 19 + 20)", "total"),
]


def _annee_q(annee):
    return Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee)


def colonne(bien, annee):
    """Montants de l'annexe pour un immeuble et une année, avec le détail des sources."""
    from .etats import loyers_encaisses
    from .models import PieceComptable, Pret

    v = defaultdict(lambda: ZERO)
    detail = defaultdict(list)

    # ligne 1 : loyers hors charges encaissés dans l'année (date de paiement)
    for bloc in loyers_encaisses(annee, bien.proprietaire)["proprietaires"]:
        for b in bloc["biens"]:
            if b["bien"].pk == bien.pk:
                v["1"] += b["sous_total"]["loyers_hc"]
    if v["1"]:
        detail["1"].append(("Loyers hors charges encaissés (paiements de l'année)", v["1"], None))

    # factures codées
    for p in PieceComptable.objects.filter(_annee_q(annee), bien=bien).order_by("date"):
        if p.ligne == "non":
            continue
        cle = "9" if p.ligne == "9b" else p.ligne
        v[cle] += p.montant
        if p.ligne == "9b":
            v["9b"] += p.montant
        detail[p.ligne].append((f"{p.date:%d/%m/%Y} {p.fournisseur} – {p.libelle}".strip(), p.montant, p))

    # ligne 7 : forfait de 20 € par local (lots non vendus avant l'année)
    nb_locaux = bien.lots.filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).count()
    if nb_locaux:
        v["7"] = FORFAIT_PAR_LOCAL * nb_locaux
        detail["7"].append((f"Forfait 20 € × {nb_locaux} local{'aux' if nb_locaux > 1 else ''}", v["7"], None))

    # ligne 17 : intérêts et assurance des prêts rattachés au bien
    for pret in Pret.objects.filter(bien=bien):
        cumul = pret.cumul_annee(annee)
        montant = cumul["interets"] + cumul["assurance"]
        if montant:
            v["17"] += montant
            detail["17"].append((f"{pret.banque} {pret.reference} : intérêts {cumul['interets']} € + assurance {cumul['assurance']} €",
                                 montant, None))

    v["5"] = v["1"] + v["2"] + v["3"] + v["4"]
    v["16"] = v["6"] + v["7"] + v["8"] + v["9"] + v["10"] + v["11"] + v["12"] + v["13"] - v["14"] + v["15"]
    v["18"] = v["5"] - v["16"] - v["17"]
    v["21"] = v["18"] + v["19"] + v["20"]
    return {"bien": bien, "valeurs": dict(v), "detail": dict(detail)}


def annexe(biens, annee):
    colonnes = [colonne(b, annee) for b in biens]
    total_23 = sum((c["valeurs"]["21"] for c in colonnes), ZERO)
    pages = []
    for i in range(0, max(len(colonnes), 1), 6):  # 6 immeubles par page, comme le formulaire
        cols = colonnes[i:i + 6]
        vides = 6 - len(cols)
        lignes = [{"code": code, "numero": "9 bis" if code == "9b" else code, "libelle": libelle, "genre": genre,
                   "montants": [c["valeurs"].get(code, ZERO) for c in cols]} for code, libelle, genre in LIGNES]
        pages.append({"colonnes": cols, "lignes": lignes, "vides": range(vides)})
    somme = lambda *codes: sum((c["valeurs"].get(k, ZERO) * signe for c in colonnes for k, signe in codes), ZERO)
    resultat = [  # cadre I de la 2072-S-SD
        ("R1", "Revenus bruts", "total des lignes 5 + 19 − 20 + 22 des immeubles", somme(("5", 1), ("19", 1), ("20", -1))),
        ("R2", "Paiement sur travaux", "total des lignes 9", somme(("9", 1))),
        ("R3", "Frais et charges autres qu'intérêts d'emprunts", "total des lignes 16", somme(("16", 1))),
        ("R4", "Intérêts d'emprunts", "total des lignes 17", somme(("17", 1))),
        ("R5", "Revenu net (+) ou déficit net (−)", "total des lignes 23", total_23),
    ]
    return {"colonnes": colonnes, "pages": pages, "total_23": total_23, "annee": annee, "resultat": resultat}


def part_personnelle(resultat, pourcentage):
    """Cadre I × % de détention du foyer : seul usage de ce pourcentage dans l'application."""
    taux = Decimal(pourcentage or 0) / 100
    return [(code, libelle, aide, montant, (montant * taux).quantize(Decimal("0.01"))) for code, libelle, aide, montant in resultat]
