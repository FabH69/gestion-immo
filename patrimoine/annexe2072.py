"""Annexe 2072-S-A1-SD (SCI à l'IR) : revenus de chaque immeuble selon la règle des revenus fonciers.

Sources : loyers HC encaissés dans l'année (paiements, ou ancien suivi), factures codées dans la comptabilité interne
(année du paiement), forfait de 20 € par local (ligne 7), intérêts et assurance des prêts rattachés au bien (ligne 17).
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

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


MOIS_LONGS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
              "décembre"]


def _retenu(montant, charges, bail):
    """Part du loyer encaissé à déclarer en ligne 1. Charges au forfait : elles font partie du loyer, on déclare le
    montant encaissé charges comprises. Provisions pour charges : ce n'est pas un revenu, on déclare le loyer hors
    charges (les charges récupérables payées ne sont alors pas déductibles, sauf celles non récupérées au départ du
    locataire, ligne 10 de la 2072 / 225 de la 2044)."""
    if bail is not None and bail.mode_charges == "provision":
        return montant - charges
    return montant


def loyers_par_mois(bien, annee):
    """Loyers encaissés dans l'année à déclarer en ligne 1, un par paiement (ou par mois repris d'un ancien suivi), du
    1er janvier au 31 décembre : [(« 05/01/2025 · loyer de janvier 2025 · M. Jean DUPONT (Apt 1er) · forfait de
    charges compris », montant retenu)]. Forfait de charges : charges comprises ; provisions : hors charges."""
    return [(l["libelle"], l["montant"]) for l in loyers_detailles(bien, annee)]


def loyers_detailles(bien, annee):
    """Comme loyers_par_mois, avec le lot de chaque loyer : [{"date", "libelle", "montant", "lot"}], par date."""
    from .models import EncaissementHistorique, Paiement

    lignes = []
    paiements = Paiement.objects.filter(date__year=annee, echeance__bail__lot__bien=bien) \
        .select_related("echeance__bail__lot").prefetch_related("echeance__bail__locataires").order_by("date", "echeance__periode", "pk")
    # un paiement reçu réparti sur plusieurs loyers (il finit un mois et en avance un autre) = une seule ligne
    groupes = {}
    for p in paiements:
        groupes.setdefault((p.versement_id, p.date, p.echeance.bail_id), []).append(p)
    deja = set()
    for parts in groupes.values():
        p, e = parts[0], parts[0].echeance
        montant, charges, mois = ZERO, ZERO, []
        for q in parts:
            eq = q.echeance
            c = (q.montant * eq.charges / eq.total).quantize(Decimal("0.01"), ROUND_HALF_UP) if eq.total else ZERO
            montant += _retenu(q.montant, c, eq.bail)
            charges += c
            mois.append((eq.periode, _retenu(q.montant, c, eq.bail)))
        noms = ", ".join(str(l) for l in e.bail.locataires.all()) or "locataire"
        lignes.append({"date": p.date, "lot": e.bail.lot, "montant": montant,
                       "libelle": f"{p.date:%d/%m/%Y} · {_mois_couverts(mois)} · {noms} "
                                  f"({e.bail.lot.designation}){_precision(e.bail, charges)}"})
        deja.add((e.bail.lot_id, p.date.month))
    for h in EncaissementHistorique.objects.filter(mois__year=annee, lot__bien=bien).select_related("lot"):
        if (h.lot_id, h.mois.month) in deja:
            continue
        bail = h.lot.baux.filter(date_debut__lte=h.mois).order_by("-date_debut").first()
        noms = ", ".join(str(l) for l in bail.locataires.all()) if bail else ""
        lignes.append({"date": h.mois, "lot": h.lot, "montant": _retenu(h.montant, h.charges, bail),
                       "libelle": f"{MOIS_LONGS[h.mois.month - 1].capitalize()} {h.mois.year} · {noms or 'suivi repris'} "
                                  f"({h.lot.designation}, suivi repris){_precision(bail, h.charges)}"})
    lignes.sort(key=lambda x: x["date"])
    return lignes


def grouper_par_lot(elements, montant=lambda e: e["montant"]):
    """Quand les éléments d'un immeuble viennent de plusieurs lots, les range lot par lot (ordre des désignations,
    puis par date dans chaque lot ; ceux sans lot à la fin) et marque le premier de chaque lot d'un « groupe » :
    {"lot", "nb", "total"}, pour un sous-titre avec son sous-total. Un seul lot : la liste reste telle quelle."""
    lots = {e["lot"].pk: e["lot"] for e in elements if e.get("lot") is not None}
    if len(lots) < 2:
        return elements
    cle = lambda e: (1, "", 0) if e.get("lot") is None else (0, e["lot"].designation.lower(), e["lot"].pk)
    ranges = sorted(elements, key=cle)  # tri stable : l'ordre des dates est gardé dans chaque lot
    debut = None
    for e in ranges:
        if debut is None or cle(e) != cle(debut):
            debut = e
            e["groupe"] = {"lot": e.get("lot"), "nb": 0, "total": ZERO,
                           "libelle": e["lot"].designation if e.get("lot") is not None else "Autres"}
        debut["groupe"]["nb"] += 1
        debut["groupe"]["total"] += montant(e)
    return ranges


def _euros(m):
    return f"{m:,.2f} €".replace(",", " ").replace(".", ",")


def _mois_couverts(mois):
    """« loyer de mai 2025 », ou pour un paiement réparti : « loyer de mai 2025 (564,00 €) et juin 2025 (136,00 €) »."""
    noms = [f"{MOIS_LONGS[m.month - 1]} {m.year}" for m, _ in mois]
    if len(mois) == 1:
        return f"loyer de {noms[0]}"
    detail = [f"{n} ({_euros(montant)})" for n, (_, montant) in zip(noms, mois)]
    return "loyer de " + ", ".join(detail[:-1]) + " et " + detail[-1]


def _precision(bail, charges):
    if not charges or bail is None:
        return ""
    return " · hors provisions de charges" if bail.mode_charges == "provision" else " · forfait de charges compris"


def baux_au_forfait(biens, annee):
    """Rappel : baux au forfait de charges (avec des charges) des immeubles, actifs pendant l'année."""
    from .models import Bail

    return list(Bail.objects.filter(lot__bien__in=biens, mode_charges="forfait", charges__gt=0, date_debut__year__lte=annee)
                .exclude(date_fin__year__lt=annee).select_related("lot__bien").prefetch_related("locataires")
                .order_by("lot__bien__nom", "lot__designation"))


def colonne(bien, annee):
    """Montants de l'annexe pour un immeuble et une année, avec le détail des sources."""
    from .models import PieceComptable, Pret

    v = defaultdict(lambda: ZERO)
    detail = defaultdict(list)

    # ligne 1 : loyers encaissés dans l'année (date de paiement), forfait de charges compris, provisions exclues
    lignes = loyers_detailles(bien, annee)
    v["1"] = sum((l["montant"] for l in lignes), ZERO)
    if v["1"]:
        detail["1"] += [(l["libelle"], l["montant"], None, l["lot"]) for l in lignes]

    # factures codées
    for p in PieceComptable.objects.filter(_annee_q(annee), bien=bien).order_by("date"):
        if p.ligne == "non" or p.a_codifier:
            continue
        cle = "9" if p.ligne == "9b" else p.ligne
        # ligne 14 (2044 : ligne 230) : montant à retrancher, positif même si l'avoir du syndic est saisi en négatif
        montant = abs(p.montant) if p.ligne == "14" else p.montant
        v[cle] += montant
        if p.ligne == "9b":
            v["9b"] += montant
        detail[p.ligne].append((f"{p.date:%d/%m/%Y} {p.fournisseur} – {p.libelle}".strip(), montant, p))

    # ligne 7 : forfait de 20 € par local (lots non vendus avant l'année)
    nb_locaux = bien.lots.filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).count()
    if nb_locaux:
        v["7"] = FORFAIT_PAR_LOCAL * nb_locaux
        detail["7"].append((f"Forfait 20 € × {nb_locaux} {'locaux' if nb_locaux > 1 else 'local'}", v["7"], None))

    # ligne 17 : intérêts et assurance des prêts rattachés au bien
    for pret in Pret.objects.filter(bien=bien):
        m = pret.montants_declares(annee)
        montant = m["interets"] + m["assurance"]
        if montant:
            v["17"] += montant
            libelle = f"{pret.banque} {pret.reference} : intérêts {m['interets']} € + assurance {m['assurance']} €"
            if m["source"] == "attestation":
                libelle += f" (attestation {annee} de la banque)"
            elif m["source"] == "estimation":
                libelle += " (estimation : tableau d'amortissement de la banque non importé)"
            detail["17"].append((libelle, montant, None))

    v["5"] = v["1"] + v["2"] + v["3"] + v["4"]
    v["16"] = v["6"] + v["7"] + v["8"] + v["9"] + v["10"] + v["11"] + v["12"] + v["13"] - v["14"] + v["15"]
    v["18"] = v["5"] - v["16"] - v["17"]
    v["21"] = v["18"] + v["19"] + v["20"]
    return {"bien": bien, "valeurs": dict(v), "detail": dict(detail)}


def sous_lignes(colonnes, code, annee):
    """Éléments qui composent une ligne (factures, loyers, forfait, prêts), un par sous-ligne dépliable, avec le montant
    dans la colonne de son immeuble. La ligne 9 reprend aussi les travaux 9 bis, qu'elle inclut."""
    from .models import Pret

    sous = []
    for i, c in enumerate(colonnes):
        du_bien = []
        prets = {f"{p.banque} {p.reference}": p for p in Pret.objects.filter(bien=c["bien"])} if code == "17" else {}
        for cle in (["9", "9b"] if code == "9" else [code]):
            for libelle, montant, piece, *lot in c["detail"].get(cle, []):
                pret = next((p for nom, p in prets.items() if libelle.startswith(nom)), None)
                declare = pret.montants_declares(annee) if pret else None
                # avoir saisi en négatif sur la ligne 14 : la ligne le retranche en positif (cerfa) ; le détail montre le même
                # montant que la ligne, pour que la somme du détail donne le total, et rappelle le signe saisi dans un badge
                saisi = piece.montant if piece and piece.montant is not None and piece.montant != montant else None
                du_bien.append({"libelle": f"Prêt {pret.banque} {pret.reference}" if pret else libelle, "piece": piece, "saisi": saisi, "retenu": montant,
                             "bien": c["bien"], "pret": pret, "declare": declare, "renovation": cle == "9b" and code == "9",
                             "estimation": bool(declare and declare["source"] == "estimation"),
                             "lot": lot[0] if lot else None,
                             "montants": [montant if j == i else None for j in range(len(colonnes))], "total": montant})
        # plusieurs lots dans l'immeuble : sous-titre par lot avec son sous-total, puis ses lignes par date
        du_bien = grouper_par_lot(du_bien, montant=lambda d: d["total"])
        for d in du_bien:
            if "groupe" in d:
                d["groupe"]["montants"] = [d["groupe"]["total"] if j == i else None for j in range(len(colonnes))]
        sous += du_bien
    return sous


def arrondis(detail, nb_colonnes):
    """Sous-ligne « somme du détail » quand, dans une colonne, les centimes du détail sont arrondis à l'euro sur la
    ligne du formulaire (ex. 81,81 € → 82 €) : liste (somme exacte, arrondi) par colonne, None si rien à expliquer."""
    sommes = [sum((d["montants"][i] for d in detail if d["montants"][i] is not None), ZERO) for i in range(nb_colonnes)]
    notes = [(s, euro_le_plus_proche(s)) if s != euro_le_plus_proche(s) else None for s in sommes]
    return notes if any(notes) else None


LIGNES_SAISIES = ["1", "2", "3", "4", "6", "7", "8", "9", "9b", "10", "11", "12", "13", "14", "15", "17", "19", "20"]


def euro_le_plus_proche(montant):
    """Arrondi fiscal : à l'euro le plus proche (0,50 € et plus → euro supérieur)."""
    return Decimal(montant).quantize(Decimal(1), ROUND_HALF_UP)


def arrondir(valeurs):
    """Montants du formulaire arrondis à l'euro le plus proche, ligne par ligne ; les totaux sont refaits
    à partir des lignes arrondies, comme sur le Cerfa."""
    v = defaultdict(lambda: ZERO, {k: euro_le_plus_proche(valeurs.get(k, ZERO)) for k in LIGNES_SAISIES})
    v["5"] = v["1"] + v["2"] + v["3"] + v["4"]
    v["16"] = v["6"] + v["7"] + v["8"] + v["9"] + v["10"] + v["11"] + v["12"] + v["13"] - v["14"] + v["15"]
    v["18"] = v["5"] - v["16"] - v["17"]
    v["21"] = v["18"] + v["19"] + v["20"]
    return dict(v)


def annexe(biens, annee):
    colonnes = []
    for b in biens:
        c = colonne(b, annee)
        c["valeurs_exactes"], c["valeurs"] = c["valeurs"], arrondir(c["valeurs"])  # le détail garde les centimes
        colonnes.append(c)
    total_23 = sum((c["valeurs"]["21"] for c in colonnes), ZERO)
    pages = []
    for i in range(0, max(len(colonnes), 1), 6):  # 6 immeubles par page, comme le formulaire
        cols = colonnes[i:i + 6]
        vides = 6 - len(cols)
        lignes = [{"code": code, "numero": "9 bis" if code == "9b" else code, "libelle": libelle, "genre": genre,
                   "detail": sous_lignes(cols, code, annee) if genre not in ("total", "titre") else [],
                   "montants": [c["valeurs"].get(code, ZERO) for c in cols]} for code, libelle, genre in LIGNES]
        for l in lignes:
            l["arrondis"] = arrondis(l["detail"], len(cols)) if l["detail"] else None
        pages.append({"colonnes": cols, "lignes": lignes, "vides": range(vides)})
    somme = lambda *codes: sum((c["valeurs"].get(k, ZERO) * signe for c in colonnes for k, signe in codes), ZERO)
    resultat = [  # cadre I de la 2072-S-SD
        ("R1", "Revenus bruts", "total des lignes 5 + 19 − 20 + 22 des immeubles", somme(("5", 1), ("19", 1), ("20", -1))),
        ("R2", "Paiement sur travaux", "total des lignes 9", somme(("9", 1))),
        ("R3", "Frais et charges autres qu'intérêts d'emprunts", "total des lignes 16", somme(("16", 1))),
        ("R4", "Intérêts d'emprunts", "total des lignes 17", somme(("17", 1))),
        ("R5", "Revenu net (+) ou déficit net (−)", "total des lignes 23", total_23),
    ]
    return {"colonnes": colonnes, "pages": pages, "total_23": total_23, "annee": annee, "resultat": resultat,
            "baux_forfait": baux_au_forfait(biens, annee)}


def part_personnelle(resultat, pourcentage):
    """Cadre I × % de détention du foyer : seul usage de ce pourcentage dans l'application."""
    taux = Decimal(pourcentage or 0) / 100
    return [(code, libelle, aide, montant, euro_le_plus_proche(montant * taux)) for code, libelle, aide, montant in resultat]
