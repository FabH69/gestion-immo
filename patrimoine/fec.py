"""Reprise des données comptables d'un ancien logiciel ou d'un expert-comptable par le FEC (fichier des écritures comptables,
article A47 A-1 du livre des procédures fiscales : 18 colonnes, séparées par une tabulation ou une barre verticale).

Ce que devient chaque classe de comptes :
- classe 6 (charges) : une facture par ligne d'écriture, sur la rubrique (LMNP, société) ou la ligne 2072 proposée ;
- classe 7 (loyers, charges refacturées) : loyers encaissés du logement, mois par mois (suivi repris) ;
- classe 2 et 28 (immobilisations et amortissements) : une fiche immobilisation par compte, reprise avec le cumul
  des amortissements à la clôture ; l'appli calcule ensuite les dotations à partir de l'année suivante ;
- classes 1, 4 et 5 (capital, emprunts, associés, tiers, banque) : soldes gardés pour mémoire, emprunts comparés aux prêts.

Tout passe par une simulation : rien n'est enregistré avant « Enregistrer la reprise », et un import enregistré s'annule
(tout ce qu'il a créé porte un lien vers lui)."""
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from . import amortissements, dossiers
from .models import (AnneeCloturee, DeclarationFaite, EncaissementHistorique, EncaissementLCD, Immobilisation, ImportFEC,
                     PieceComptable, Pret, SoldeFEC)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

COLONNES = ["JournalCode", "JournalLib", "EcritureNum", "EcritureDate", "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
            "PieceRef", "PieceDate", "EcritureLib", "Debit", "Credit", "EcritureLet", "DateLet", "ValidDate", "Montantdevise",
            "Idevise"]
OBLIGATOIRES = ["journalcode", "ecriturenum", "ecrituredate", "comptenum", "comptelib", "ecriturelib"]
MAX_ERREURS = 30

IGNORER = "ignorer"
RECETTE = "recette"


class ErreurFEC(Exception):
    pass


# --- lecture du fichier -------------------------------------------------------------------------------------------------
def sans_accents(texte):
    t = unicodedata.normalize("NFKD", str(texte or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _decoder(octets):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return octets.decode(enc)
        except UnicodeDecodeError:
            pass
    return octets.decode("latin-1")


def _montant(texte):
    t = re.sub(r"[\s  ]", "", texte or "").replace("+", "")
    if not t:
        return ZERO
    if "," in t and "." in t:
        t = t.replace(".", "")
    t = t.replace(",", ".")
    try:
        return Decimal(t).quantize(CENT)
    except InvalidOperation:
        raise ValueError(f"montant illisible « {texte} »")


def _date(texte):
    t = (texte or "").strip()
    if not t:
        return None
    for f in ("%Y%m%d", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(t[:10] if "-" in t else t, f).date()
        except ValueError:
            pass
    raise ValueError(f"date illisible « {texte} »")


def lire(octets, nom=""):
    """Lit un FEC : {"ecritures": [...], "erreurs": [...], "separateur", "siren", "date_cloture"}.
    Lève ErreurFEC si ce n'est pas un FEC (en-tête absent ou colonnes obligatoires manquantes)."""
    texte = _decoder(octets)
    lignes = texte.splitlines()
    n0 = next((i for i, l in enumerate(lignes) if l.strip()), None)
    if n0 is None:
        raise ErreurFEC("Le fichier est vide.")
    entete = lignes[n0]
    sep = "\t" if "\t" in entete else "|" if "|" in entete else None
    if sep is None:
        raise ErreurFEC("Séparateur introuvable : un FEC sépare ses colonnes par une tabulation ou une barre verticale « | ».")
    noms = [sans_accents(c).strip().strip('"') for c in entete.split(sep)]
    idx = {n: i for i, n in enumerate(noms)}
    manquantes = [c for c in OBLIGATOIRES if c not in idx]
    montant_sens = "debit" not in idx and "montant" in idx and "sens" in idx
    if not montant_sens and not ("debit" in idx and "credit" in idx):
        manquantes += ["debit", "credit"]
    if manquantes:
        attendues = {sans_accents(c): c for c in COLONNES + ["Montant", "Sens"]}
        raise ErreurFEC("Ce fichier n'a pas l'en-tête d'un FEC. Colonnes manquantes : "
                        + ", ".join(attendues.get(c, c) for c in manquantes) + ".")

    def champ(cols, nom_col):
        i = idx.get(nom_col)
        return cols[i].strip().strip('"') if i is not None and i < len(cols) else ""

    ecritures, erreurs = [], []
    for n, brute in enumerate(lignes[n0 + 1:], n0 + 2):
        if not brute.strip():
            continue
        cols = brute.split(sep)
        try:
            if montant_sens:
                m = _montant(champ(cols, "montant"))
                sens = champ(cols, "sens").upper()
                credit = sens in ("C", "-1", "CREDIT", "CRÉDIT")
                debit, credit_m = (ZERO, m) if credit else (m, ZERO)
            else:
                debit, credit_m = _montant(champ(cols, "debit")), _montant(champ(cols, "credit"))
            jour = _date(champ(cols, "ecrituredate"))
            if jour is None:
                raise ValueError("date d'écriture vide")
            compte = champ(cols, "comptenum")
            if not compte:
                raise ValueError("numéro de compte vide")
            try:
                piece_date = _date(champ(cols, "piecedate"))
            except ValueError:
                piece_date = None
            ecritures.append({
                "ligne": n, "journal": champ(cols, "journalcode"), "journal_lib": champ(cols, "journallib"),
                "num": champ(cols, "ecriturenum"), "date": jour, "compte": compte, "compte_lib": champ(cols, "comptelib"),
                "aux": champ(cols, "compauxnum"), "aux_lib": champ(cols, "compauxlib"), "piece_ref": champ(cols, "pieceref"),
                "piece_date": piece_date, "libelle": champ(cols, "ecriturelib"), "debit": debit, "credit": credit_m})
        except ValueError as e:
            if len(erreurs) < MAX_ERREURS:
                erreurs.append(f"Ligne {n} ignorée : {e}.")
            else:
                erreurs[-1] = "… et d'autres lignes ignorées."
    if not ecritures:
        raise ErreurFEC("Aucune écriture lisible dans le fichier.")
    m = re.search(r"(\d{9})FEC(\d{8})", nom or "", re.I)
    cloture = None
    if m:
        try:
            cloture = datetime.strptime(m.group(2), "%Y%m%d").date()
        except ValueError:
            pass
    return {"ecritures": ecritures, "erreurs": erreurs, "separateur": "tabulation" if sep == "\t" else "barre verticale",
            "siren": m.group(1) if m else "", "date_cloture": cloture}


def est_a_nouveau(e):
    """Écriture de reprise des soldes de l'exercice précédent (journal des à-nouveaux)."""
    return e["journal"].upper() in ("AN", "RAN", "ANO", "OUV", "AN0") or "nouveau" in sans_accents(e["journal_lib"]) \
        or "ouverture" in sans_accents(e["journal_lib"])


def analyse(ecritures):
    """Totaux, exercice, écritures déséquilibrées et comptes (classe, libellé, débits et crédits)."""
    par_ecriture = defaultdict(lambda: ZERO)
    comptes = {}
    for e in ecritures:
        par_ecriture[(e["journal"], e["num"])] += e["debit"] - e["credit"]
        c = comptes.setdefault(e["compte"], {"compte": e["compte"], "libelle": e["compte_lib"], "classe": e["compte"][:1],
                                             "nb": 0, "debit": ZERO, "credit": ZERO, "debit_an": ZERO, "credit_an": ZERO,
                                             "credit_exercice": ZERO, "premiere_date": None})
        c["nb"] += 1
        c["debit"] += e["debit"]
        c["credit"] += e["credit"]
        if est_a_nouveau(e):
            c["debit_an"] += e["debit"]
            c["credit_an"] += e["credit"]
        else:
            c["credit_exercice"] += e["credit"]
            if e["debit"] > 0 and (c["premiere_date"] is None or e["date"] < c["premiere_date"]):
                c["premiere_date"] = e["date"]
        if not c["libelle"] and e["compte_lib"]:
            c["libelle"] = e["compte_lib"]
    desequilibres = [{"journal": j, "num": n, "ecart": v} for (j, n), v in par_ecriture.items() if v]
    total_debit = sum((e["debit"] for e in ecritures), ZERO)
    total_credit = sum((e["credit"] for e in ecritures), ZERO)
    dates = [e["date"] for e in ecritures]
    produits = sum((c["credit"] - c["debit"] for c in comptes.values() if c["classe"] == "7"), ZERO)
    charges = sum((c["debit"] - c["credit"] for c in comptes.values() if c["classe"] == "6"), ZERO)
    return {"comptes": dict(sorted(comptes.items())), "desequilibres": desequilibres, "total_debit": total_debit,
            "total_credit": total_credit, "debut": min(dates), "fin": max(dates), "nb": len(ecritures),
            "produits": produits, "charges": charges, "resultat": produits - charges}


# --- correspondance des comptes ------------------------------------------------------------------------------------------
def par_rubrique(dossier):
    """LMNP et sociétés : rubriques du récapitulatif ; SCI à l'IR et nom propre : lignes de l'annexe 2072."""
    return dossiers.amortissable(dossier)


def cibles(dossier):
    """[(valeur, libellé)] de la liste « Devient » d'un compte de charges ou de produits."""
    if par_rubrique(dossier):
        choix = [(f"rubrique:{code}", f"{titre + ' · ' if titre and titre != 'Autre' else ''}{libelle}")
                 for titre, rubriques in PieceComptable.RUBRIQUES for code, libelle in rubriques
                 if code not in ("immobilisation", "pret")]
    else:
        choix = [(f"ligne:{code}", libelle) for code, libelle in PieceComptable.Ligne.choices]
    return [(RECETTE, "Loyers encaissés (recettes du logement)")] + choix + [(IGNORER, "Ne pas reprendre")]


MOTS_RUBRIQUES = [  # (mots du libellé du compte, rubrique) : le premier trouvé l'emporte
    (("emprunteur", "assurance pret", "assurance credit", "assurance emprunt"), "assurance_pret"),
    (("interet",), "interets_pret"),
    (("fonciere des entreprises", "cfe"), "impots_taxes"),
    (("taxe fonciere", "taxes foncieres", "fonciere"), "taxe_fonciere"),
    (("copro", "syndic"), "copro"),
    (("comptab", "expert"), "comptable"),
    (("conciergerie", "commission", "airbnb", "booking"), "commission"),
    (("menage", "nettoyage", "blanchisserie", "linge"), "menage"),
    (("internet", "fibre", "telecom", "telephon", "box"), "fibre"),
    (("electric", "edf", "engie", "gaz", "energie"), "electricite"),
    (("eau ", "eau,", "eaux"), "eau"),
    (("assurance", "pno"), "pno"),
    (("fourniture", "petit equipement", "consommable", "produits d'accueil"), "consommables"),
]
COMPTES_RUBRIQUES = [  # (début du numéro de compte, rubrique)
    ("6061", "electricite"), ("601", "consommables"), ("602", "consommables"), ("606", "consommables"), ("614", "copro"),
    ("616", "pno"), ("6226", "comptable"), ("6222", "commission"), ("626", "fibre"), ("63512", "taxe_fonciere"),
    ("6351", "impots_taxes"), ("635", "impots_taxes"), ("637", "impots_taxes"), ("661", "interets_pret"),
]
COMPTES_LIGNES = [  # SCI à l'IR : (début du numéro de compte, ligne de l'annexe 2072)
    ("6226", "6"), ("622", "6"), ("6227", "6"), ("614", "13"), ("616", "8"), ("615", "9"), ("635", "12"), ("637", "12"),
    ("661", "17"), ("627", "17"), ("62", "6"), ("61", "9"),
]


def proposer(compte, libelle, dossier, avec_prets=False):
    """Destination proposée pour un compte de charges (6) ou de produits (7)."""
    lib = sans_accents(libelle) + " "
    if compte[:1] == "7":
        if compte.startswith(("706", "708", "752")):
            return RECETTE
        if not par_rubrique(dossier) and compte.startswith(("74", "791", "758")):
            return "ligne:3"
        return IGNORER
    if compte[:1] != "6" or compte.startswith(("68", "69", "603", "6087")):
        return IGNORER  # dotations (recalculées par le registre), impôt sur les sociétés, variations de stock
    if par_rubrique(dossier):
        rubrique = next((r for mots, r in MOTS_RUBRIQUES if any(m in lib for m in mots)), None) \
            or next((r for debut, r in COMPTES_RUBRIQUES if compte.startswith(debut)), "autre")
        if avec_prets and rubrique in ("interets_pret", "assurance_pret"):
            return IGNORER  # déjà comptés par l'échéancier des prêts de l'appli
        return f"rubrique:{rubrique}"
    if "interet" in lib or "emprunteur" in lib:
        return IGNORER if avec_prets else "ligne:17"
    ligne = next((l for debut, l in COMPTES_LIGNES if compte.startswith(debut)), "non")
    if avec_prets and ligne == "17":
        return IGNORER
    return f"ligne:{ligne}"


def choix_biens(dossier):
    """[(valeur, libellé)] de la liste « Bien » : chaque bien du dossier, et ses logements quand il en a plusieurs."""
    choix = []
    biens = list(dossiers.biens(dossier).prefetch_related("lots"))
    if len(biens) > 1:
        choix.append(("auto", "D'après le libellé de chaque écriture"))
    for b in biens:
        choix.append((f"b{b.pk}", b.nom))
        lots = list(b.lots.all())
        if len(lots) > 1:
            choix += [(f"l{l.pk}", f"{b.nom} · {l.designation}") for l in lots]
    return choix


def bien_du_libelle(texte, biens):
    t = sans_accents(texte)
    return next((b for b in sorted(biens, key=lambda b: -len(b.nom)) if sans_accents(b.nom).strip() and sans_accents(b.nom) in t),
                None)


def _bien_propose(libelle, biens):
    if len(biens) == 1:
        return f"b{biens[0].pk}"
    b = bien_du_libelle(libelle, biens)
    return f"b{b.pk}" if b else "auto"


# --- immobilisations -----------------------------------------------------------------------------------------------------
COMPTES_IMMOS = [("211", "terrain"), ("212", "terrain"), ("2135", "agencements"), ("2131", "gros_oeuvre"), ("213", "gros_oeuvre"),
                 ("214", "gros_oeuvre"), ("215", "installations"), ("2181", "agencements"), ("2183", "informatique"),
                 ("2184", "mobilier"), ("218", "mobilier"), ("205", "informatique"), ("201", "frais"), ("203", "frais")]


def _racine(compte):
    return compte.rstrip("0") or compte


def est_compte_immo(compte):
    return compte[:2] in ("20", "21") and not compte.startswith(("206", "207"))  # droit au bail, fonds : non amortis


def _categorie_immo(compte, libelle):
    from .views.immobilisations import _categorie

    cat = _categorie(libelle)
    if cat != "autre":
        return cat
    return next((c for debut, c in COMPTES_IMMOS if compte.startswith(debut)), "autre")


def proposer_immobilisations(a, biens, fin):
    """Une fiche par compte d'immobilisation encore à l'actif : valeur à la clôture, cumul des comptes 28 correspondants
    (partagé au prorata quand un compte 28 couvre plusieurs comptes), durée déduite de la dotation de l'exercice."""
    comptes = a["comptes"]
    immos = {c: x for c, x in comptes.items() if est_compte_immo(c) and x["debit"] - x["credit"] > 0}
    amorts = {c: x for c, x in comptes.items() if c.startswith("28")}
    rattache = defaultdict(list)  # compte 28 -> comptes d'immobilisation
    for c28 in amorts:
        cle = _racine(c28)[2:]
        candidats = [(len(_racine(ci)[1:]), ci) for ci in immos
                     if _racine(ci)[1:].startswith(cle) or cle.startswith(_racine(ci)[1:])]
        if candidats:
            meilleur = max(l for l, _ in candidats) if any(cle.startswith(_racine(ci)[1:]) for _, ci in candidats) else None
            choisis = [ci for l, ci in candidats if meilleur is None or l == meilleur]
            for ci in choisis:
                rattache[c28].append(ci)
    cumul, dot = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for c28, liste in rattache.items():
        x = amorts[c28]
        valeurs = {ci: immos[ci]["debit"] - immos[ci]["credit"] for ci in liste}
        total = sum(valeurs.values(), ZERO)
        for ci in liste:
            part = valeurs[ci] / total if total else Decimal(1) / len(liste)
            cumul[ci] += ((x["credit"] - x["debit"]) * part).quantize(CENT)
            dot[ci] += (x["credit_exercice"] * part).quantize(CENT)
    lignes = {}
    for ci, x in immos.items():
        valeur = x["debit"] - x["credit"]
        categorie = _categorie_immo(ci, x["libelle"])
        c, d = max(min(cumul[ci], valeur), ZERO), dot[ci]
        entiere = x["debit_an"] > 0  # déjà là au début de l'exercice : la dotation couvre une année pleine
        if categorie == "terrain":
            duree = 0
        elif d > 0 and entiere:
            duree = max(1, min(100, int((valeur / d).to_integral_value(ROUND_HALF_UP))))
        else:
            duree = amortissements.duree_proposee(categorie)
        estimee = False
        if x["debit_an"] == 0 and x["premiere_date"]:
            mes = x["premiere_date"]
        elif duree and c > 0:
            annees = float(c / (valeur / duree))
            jour = fin + timedelta(days=1) - timedelta(days=round(annees * 365.25))
            mes, estimee = date(jour.year, jour.month, 1), True
        else:
            mes, estimee = a["debut"], True
        lignes[ci] = {"compte": ci, "inclure": True, "designation": (x["libelle"] or f"Compte {ci}")[:200], "categorie": categorie,
                      "bien": _bien_propose(x["libelle"], biens), "mise_en_service": mes.isoformat(), "mes_estimee": estimee,
                      "valeur": str(valeur), "duree": duree, "cumul": str(c), "dotation_fec": str(d)}
    return lignes


# --- préparation d'un import ---------------------------------------------------------------------------------------------
def preparer(dossier, fichier):
    """Crée l'import en préparation, avec la correspondance proposée (reprise de celle du dernier import du dossier)."""
    octets = fichier.read()
    lu = lire(octets, fichier.name)
    a = analyse(lu["ecritures"])
    import hashlib

    empreinte = hashlib.sha256(octets).hexdigest()
    deja = ImportFEC.objects.filter(dossier=dossier["code"], empreinte=empreinte, etat=ImportFEC.Etat.IMPORTE).first()
    if deja:
        raise ErreurFEC(f"Ce fichier a déjà été importé le {timezone.localtime(deja.importe_le):%d/%m/%Y}. "
                        "Annulez d'abord cet import pour le refaire.")
    biens = list(dossiers.biens(dossier))
    if not biens:
        raise ErreurFEC("Ce dossier n'a aucun bien : créez d'abord le bien (ou réglez son régime fiscal) avant la reprise.")
    avec_prets = Pret.objects.filter(bien__in=biens).exists()
    precedent = ImportFEC.objects.filter(dossier=dossier["code"]).exclude(correspondances={}).first()
    anciennes = precedent.correspondances if precedent else {}
    corr = {}
    for c, x in a["comptes"].items():
        if x["classe"] in ("6", "7"):
            corr[c] = anciennes.get(c) or {"cible": proposer(c, x["libelle"], dossier, avec_prets),
                                           "bien": _bien_propose(x["libelle"], biens)}
    fin = lu["date_cloture"] or a["fin"]
    fichier.seek(0)
    imp = ImportFEC(dossier=dossier["code"], nom_fichier=fichier.name[:200], empreinte=empreinte, siren=lu["siren"],
                    debut=a["debut"], fin=a["fin"], date_cloture=lu["date_cloture"], nb_lignes=a["nb"], correspondances=corr,
                    reglages_immos=proposer_immobilisations(a, biens, fin) if dossiers.amortissable(dossier) else {},
                    options={"annee_reprise": fin.year + 1, "charges": True, "recettes": True})
    imp.fichier.save(fichier.name, fichier, save=False)
    imp.save()
    return imp


def relire(imp):
    with imp.fichier.open("rb") as f:
        lu = lire(f.read(), imp.nom_fichier)
    return lu, analyse(lu["ecritures"])


# --- simulation ----------------------------------------------------------------------------------------------------------
def _cible_bien(valeur, texte, biens, lots, defaut):
    """(bien, lot) d'après la valeur de la liste « Bien » (b<id>, l<id>, auto)."""
    if valeur and valeur.startswith("l") and valeur[1:].isdigit() and int(valeur[1:]) in lots:
        lot = lots[int(valeur[1:])]
        return lot.bien, lot
    if valeur and valeur.startswith("b") and valeur[1:].isdigit():
        b = next((x for x in biens if x.pk == int(valeur[1:])), None)
        if b:
            return b, None
    if valeur == "auto":
        return bien_du_libelle(texte, biens) or defaut, None
    return None, None


def _premier_mois(d):
    return date(d.year, d.month, 1)


def _fournisseurs(ecritures):
    """{(journal, n° d'écriture): nom du tiers} d'après le compte auxiliaire (fournisseur, client) de l'écriture."""
    res = {}
    for e in ecritures:
        if e["aux_lib"] and (e["journal"], e["num"]) not in res:
            res[(e["journal"], e["num"])] = e["aux_lib"]
    return res


def simuler(imp, dossier, lu=None, a=None):
    """Ce que l'enregistrement créerait, sans rien écrire : factures, loyers, immobilisations, soldes, contrôles."""
    if lu is None:
        lu, a = relire(imp)
    ecritures = lu["ecritures"]
    biens = list(dossiers.biens(dossier).prefetch_related("lots"))
    lots = {l.pk: l for b in biens for l in b.lots.all()}
    defaut = next((b for b in biens if f"b{b.pk}" == imp.options.get("bien_defaut")), biens[0] if biens else None)
    tiers = _fournisseurs(ecritures)
    fermees, declares = AnneeCloturee.annees(), DeclarationFaite.verrous()
    libelles_cibles = dict(cibles(dossier))
    avec_pret = set(Pret.objects.filter(bien__in=biens).values_list("bien_id", flat=True))

    existantes = set(PieceComptable.objects.filter(bien__in=biens).filter(montant__isnull=False)
                     .values_list("bien_id", "date", "montant"))
    pieces, groupes, alertes = [], {}, []
    recettes = defaultdict(lambda: ZERO)
    sans_bien = set()
    for e in ecritures:
        c = imp.correspondances.get(e["compte"])
        if not c or c.get("cible") in (None, "", IGNORER) or est_a_nouveau(e):
            continue
        cible = c["cible"]
        bien, lot = _cible_bien(c.get("bien"), e["libelle"], biens, lots, defaut)
        if bien is None:
            sans_bien.add(e["compte"])
            continue
        if cible == RECETTE:
            if not imp.options.get("recettes", True):
                continue
            if lot is None:
                lot = next(iter(bien.lots.all()), None)
            if lot is None:
                sans_bien.add(e["compte"])
                continue
            recettes[(lot.pk, _premier_mois(e["date"]))] += e["credit"] - e["debit"]
            continue
        if not imp.options.get("charges", True):
            continue
        montant = e["debit"] - e["credit"]
        if not montant:
            continue
        jour = e["piece_date"] or e["date"]
        statut = "ok"
        annee = e["date"].year
        if annee in fermees or declares.get(annee, {}).get(bien.pk):
            statut = "verrou"
        elif (bien.pk, jour, montant) in existantes:
            statut = "doublon"
        p = {"bien": bien, "date": jour, "date_paiement": e["date"], "montant": montant, "cible": cible, "compte": e["compte"],
             "fournisseur": tiers.get((e["journal"], e["num"]), "")[:150], "libelle": (e["libelle"] or e["compte_lib"])[:200],
             "numero": e["piece_ref"][:50], "ecriture": f"{e['journal']} {e['num']}", "statut": statut}
        pieces.append(p)
        if statut == "ok":
            g = groupes.setdefault((bien.pk, cible), {"bien": bien, "cible": cible, "libelle": libelles_cibles.get(cible, cible),
                                                     "nb": 0, "total": ZERO, "annees": set()})
            g["nb"] += 1
            g["total"] += montant
            g["annees"].add(annee)
            if cible in ("rubrique:interets_pret", "rubrique:assurance_pret", "ligne:17") and bien.pk in avec_pret:
                g["alerte_pret"] = True

    existants_hist = set(EncaissementHistorique.objects.filter(lot__in=lots.values()).values_list("lot_id", "mois"))
    existants_lcd = set(EncaissementLCD.objects.filter(lot__in=lots.values()).values_list("lot_id", "mois"))
    loyers = []
    for (lot_pk, mois), montant in sorted(recettes.items(), key=lambda x: (str(lots[x[0][0]]), x[0][1])):
        lot = lots[lot_pk]
        courte = lot.exploitation == "courte"
        deja = (lot_pk, mois) in (existants_lcd if courte else existants_hist)
        loyers.append({"lot": lot, "mois": mois, "montant": montant, "courte": courte,
                       "statut": "existe" if deja else ("zero" if not montant else "ok")})

    immos = []
    annee_reprise = int(imp.options.get("annee_reprise") or (a["fin"].year + 1))
    existantes_immo = set(Immobilisation.objects.filter(bien__in=biens).values_list("bien_id", "designation", "valeur"))
    for compte, r in sorted(imp.reglages_immos.items()):
        bien, lot = _cible_bien(r.get("bien"), r.get("designation", ""), biens, lots, defaut)
        if r.get("bien") == "auto" and bien is None:
            bien = defaut
        valeur, cumul = Decimal(r["valeur"]), Decimal(r.get("cumul") or 0)
        mes = date.fromisoformat(r["mise_en_service"])
        essai = Immobilisation(valeur=valeur, duree=int(r.get("duree") or 0), mise_en_service=mes, amortissements_anterieurs=cumul,
                               annee_reprise=annee_reprise if mes.year < annee_reprise else None)
        statut = "ok" if r.get("inclure") else "exclue"
        if statut == "ok" and bien and (bien.pk, r["designation"], valeur) in existantes_immo:
            statut = "doublon"
        if statut == "ok" and bien is None:
            statut = "sans_bien"
        immos.append({**r, "bien_objet": bien, "lot_objet": lot, "valeur": valeur, "cumul": cumul, "mes": mes,
                      "dotation_fec": Decimal(r.get("dotation_fec") or 0), "statut": statut,
                      "categorie_libelle": Immobilisation.Categorie(r["categorie"]).label,
                      "dotation_reprise": amortissements.dotation(essai, annee_reprise) if essai.duree else ZERO})

    soldes = [{"compte": c, "libelle": x["libelle"], "debit": x["debit"], "credit": x["credit"], "solde": x["debit"] - x["credit"]}
              for c, x in a["comptes"].items() if x["classe"] in "12345" and not c.startswith(("28", "29"))
              and not (est_compte_immo(c))]
    soldes = [{**s, "montant": abs(s["solde"]), "sens": "D" if s["solde"] > 0 else "C"} for s in soldes if s["solde"]]
    emprunts_fec = -sum((s["solde"] for s in soldes if s["compte"].startswith("164")), ZERO)
    fin = imp.date_cloture or a["fin"]
    prets = list(_prets(dossier, biens))
    emprunts_appli = sum((p.capital_restant_du(fin) for p in prets), ZERO)

    if a["desequilibres"]:
        alertes.append(f"{len(a['desequilibres'])} écriture{'s' if len(a['desequilibres']) > 1 else ''} déséquilibrée"
                       f"{'s' if len(a['desequilibres']) > 1 else ''} (débit ≠ crédit) : le fichier est peut-être incomplet.")
    if a["total_debit"] != a["total_credit"]:
        alertes.append(f"Total des débits ({a['total_debit']} €) différent du total des crédits ({a['total_credit']} €).")
    if sans_bien:
        alertes.append("Choisissez un bien pour le{} compte{} {} (sinon ses écritures ne sont pas reprises).".format(
            "s" if len(sans_bien) > 1 else "", "s" if len(sans_bien) > 1 else "", ", ".join(sorted(sans_bien))))
    if any(g.get("alerte_pret") for g in groupes.values()):
        alertes.append("Des intérêts ou une assurance d'emprunt sont repris sur un bien qui a déjà un prêt dans l'appli : "
                       "ils seraient comptés deux fois. Choisissez « Ne pas reprendre » pour ces comptes.")
    if a["debut"].month != 1 or (fin.month, fin.day) != (12, 31):
        alertes.append(f"Exercice du {a['debut']:%d/%m/%Y} au {fin:%d/%m/%Y} : l'appli compte par année civile. "
                       "Les factures gardent leur date ; vérifiez l'année de reprise des immobilisations.")

    ok = [p for p in pieces if p["statut"] == "ok"]
    return {"pieces": pieces, "groupes": sorted(groupes.values(), key=lambda g: (g["bien"].nom, g["libelle"])),
            "nb_factures": len(ok), "total_factures": sum((p["montant"] for p in ok), ZERO),
            "nb_verrou": sum(1 for p in pieces if p["statut"] == "verrou"),
            "nb_doublons": sum(1 for p in pieces if p["statut"] == "doublon"),
            "loyers": loyers, "nb_loyers": sum(1 for l in loyers if l["statut"] == "ok"),
            "total_loyers": sum((l["montant"] for l in loyers if l["statut"] == "ok"), ZERO),
            "immos": immos, "nb_immos": sum(1 for i in immos if i["statut"] == "ok"), "annee_reprise": annee_reprise,
            "total_immos": sum((i["valeur"] for i in immos if i["statut"] == "ok"), ZERO),
            "cumul_immos": sum((i["cumul"] for i in immos if i["statut"] == "ok"), ZERO),
            "dotation_reprise": sum((i["dotation_reprise"] for i in immos if i["statut"] == "ok"), ZERO),
            "soldes": soldes, "emprunts_fec": emprunts_fec, "emprunts_appli": emprunts_appli, "prets": prets,
            "ecart_emprunts": emprunts_fec - emprunts_appli, "analyse": a, "erreurs_lecture": lu["erreurs"], "alertes": alertes,
            "bloquant": bool(sans_bien), "fin": fin, "separateur": lu["separateur"]}


def _prets(dossier, biens):
    if dossier["proprietaire"] is not None:
        return Pret.objects.filter(proprietaire=dossier["proprietaire"])
    return Pret.objects.filter(bien__in=biens)


# --- enregistrement et annulation ----------------------------------------------------------------------------------------
@transaction.atomic
def enregistrer(imp, dossier):
    """Crée les factures, les loyers, les immobilisations et les soldes de la simulation. Renvoie le résumé."""
    if imp.etat != ImportFEC.Etat.BROUILLON:
        raise ErreurFEC("Cet import est déjà enregistré ou annulé.")
    s = simuler(imp, dossier)
    if s["bloquant"]:
        raise ErreurFEC(s["alertes"][0] if s["alertes"] else "Correspondance incomplète.")
    note = f"Repris du FEC {imp.nom_fichier}"
    nouvelles = []
    for p in s["pieces"]:
        if p["statut"] != "ok":
            continue
        genre, _, code = p["cible"].partition(":")
        nouvelles.append(PieceComptable(
            bien=p["bien"], date=p["date"], date_paiement=p["date_paiement"], montant=p["montant"], fournisseur=p["fournisseur"],
            libelle=p["libelle"], numero_facture=p["numero"], rubrique=code if genre == "rubrique" else "",
            ligne=code if genre == "ligne" else "", charge_confirmee=True, import_fec=imp,
            notes=f"{note} · compte {p['compte']} · écriture {p['ecriture']}"))
    PieceComptable.objects.bulk_create(nouvelles)
    nb_loyers = 0
    for l in s["loyers"]:
        if l["statut"] != "ok":
            continue
        if l["courte"]:
            EncaissementLCD.objects.create(lot=l["lot"], mois=l["mois"], ca=l["montant"], notes=note[:255], import_fec=imp)
        else:
            EncaissementHistorique.objects.create(lot=l["lot"], mois=l["mois"], montant=l["montant"], note=note[:255], import_fec=imp)
        nb_loyers += 1
    nb_immos = 0
    for i in s["immos"]:
        if i["statut"] != "ok":
            continue
        Immobilisation.objects.create(
            bien=i["bien_objet"], lot=i["lot_objet"], designation=i["designation"], categorie=i["categorie"], mise_en_service=i["mes"],
            valeur=i["valeur"], duree=int(i.get("duree") or 0), amortissements_anterieurs=i["cumul"],
            annee_reprise=s["annee_reprise"] if i["mes"].year < s["annee_reprise"] else None, origine=Immobilisation.Origine.FEC,
            import_fec=imp, notes=f"{note} · compte {i['compte']}" + (" · mise en service estimée d'après le cumul des amortissements"
                                                                      if i.get("mes_estimee") else ""))
        nb_immos += 1
    SoldeFEC.objects.bulk_create([SoldeFEC(import_fec=imp, compte=x["compte"], libelle=x["libelle"][:200], debit=x["debit"],
                                           credit=x["credit"]) for x in s["soldes"]])
    imp.etat, imp.importe_le = ImportFEC.Etat.IMPORTE, timezone.now()
    imp.resume = {"factures": len(nouvelles), "total_factures": str(s["total_factures"]), "loyers": nb_loyers,
                  "total_loyers": str(s["total_loyers"]), "immobilisations": nb_immos, "soldes": len(s["soldes"]),
                  "verrouillees": s["nb_verrou"], "doublons": s["nb_doublons"], "annee_reprise": s["annee_reprise"]}
    imp.save()
    return imp.resume


def verrous_annulation(imp):
    fermees, declares = AnneeCloturee.annees(), DeclarationFaite.verrous()
    return [p for p in imp.pieces.select_related("bien") if p.motif_verrou(fermees, declares)]


@transaction.atomic
def annuler(imp):
    """Supprime tout ce que l'import a créé (sauf si une facture est dans une année clôturée ou déclarée)."""
    if imp.etat != ImportFEC.Etat.IMPORTE:
        raise ErreurFEC("Seul un import enregistré peut être annulé.")
    bloquees = verrous_annulation(imp)
    if bloquees:
        raise ErreurFEC(f"{len(bloquees)} facture{'s' if len(bloquees) > 1 else ''} de cet import "
                        f"{'sont' if len(bloquees) > 1 else 'est'} dans une année clôturée ou déclarée "
                        f"({bloquees[0].motif_verrou()}) : déverrouillez-la d'abord.")
    imp.pieces.all().delete()
    imp.encaissements.all().delete()
    imp.encaissements_lcd.all().delete()
    imp.immobilisations.all().delete()
    imp.soldes.all().delete()
    imp.etat, imp.annule_le = ImportFEC.Etat.ANNULE, timezone.now()
    imp.save()

