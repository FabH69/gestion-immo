"""Dossier de financement (« dossier banque ») d'une simulation d'investissement enregistrée.

Les chiffres viennent de la simulation (montage, prévisionnel, travaux) ; le dossier ajoute ce que l'appli ne sait pas
(revenus du foyer, photos, DPE, atouts du quartier), rangé dans SimulationInvestissement.dossier (JSON).
"""
from datetime import date
from decimal import Decimal

from .calculs import investissement as inv

SECTIONS = [
    ("porteur", "Le porteur du projet"),
    ("bien", "Le bien et son emplacement"),
    ("marche", "Marché et loyers"),
    ("montage", "Montage financier"),
    ("prev", "Prévisionnel sur 10 ans"),
    ("travaux", "Travaux et devis"),
    ("dpe", "Performance énergétique (DPE)"),
    ("pieces", "Pièces jointes"),
]
PIECES = [
    ("compromis", "Compromis de vente"),
    ("bulletins", "3 derniers bulletins de salaire"),
    ("avis", "2 derniers avis d'imposition"),
    ("releves", "Relevés de comptes, 3 mois"),
    ("baux", "Baux et quittances des biens loués"),
    ("prets", "Tableaux d'amortissement des prêts en cours"),
    ("dpe", "DPE actuels"),
    ("audit", "Audit énergétique ou DPE projeté après travaux"),
]
CLASSES_DPE = "ABCDEFG"
# seuils de l'étiquette énergie (kWh d'énergie primaire / m² / an), DPE depuis juillet 2021
SEUILS_DPE = [("A", "≤ 70"), ("B", "71 à 110"), ("C", "111 à 180"), ("D", "181 à 250"), ("E", "251 à 330"),
              ("F", "331 à 420"), ("G", "> 420")]
COULEURS_DPE = {"A": "#00a06d", "B": "#50b05a", "C": "#a5cc3f", "D": "#f2e600", "E": "#f0b40f", "F": "#eb8235",
                "G": "#d7221f"}
# loi Climat et résilience : interdiction de louer selon la classe du logement
LOI_CLIMAT = {"G": "Les logements classés G ne peuvent plus être loués depuis le 1er janvier 2025, et leur loyer est gelé.",
              "F": "Les logements classés F ne pourront plus être loués à partir du 1er janvier 2028, et leur loyer est déjà gelé.",
              "E": "Les logements classés E ne pourront plus être loués à partir du 1er janvier 2034."}
TAUX_HCSF = 35          # taux d'endettement maximal recommandé par le Haut Conseil de stabilité financière
PART_LOYERS_HCSF = 70   # part des loyers retenue par les banques dans les revenus
INDEXATION_LOYERS = 1.5
INDEXATION_CHARGES = 2.0
ANNEES_PREVISIONNEL = 10


def reglages(dossier):
    """Réglages du dossier, complétés par les valeurs par défaut."""
    d = dict(dossier or {})
    sections = d.get("sections") or {}
    return {
        "sections": {code: sections.get(code, True) for code, _ in SECTIONS},
        "presente_par": d.get("presente_par", ""),
        "contact": d.get("contact", ""),
        "mot": d.get("mot", ""),
        "revenus_mois": d.get("revenus_mois"),
        "credits_mois": d.get("credits_mois"),            # None : repris des prêts de l'appli
        "loyers_existants_mois": d.get("loyers_existants_mois"),  # None : repris des baux de l'appli
        "epargne": d.get("epargne"),
        "inclure_patrimoine": d.get("inclure_patrimoine", True),
        "description": d.get("description", ""),
        "atouts": d.get("atouts", ""),
        "indexation_loyers": d.get("indexation_loyers", INDEXATION_LOYERS),
        "indexation_charges": d.get("indexation_charges", INDEXATION_CHARGES),
        "dpe": {"actuel": "", "actuel_kwh": None, "actuel_ges": None, "vise": "", "vise_kwh": None, "vise_ges": None,
                "lots": [], **(d.get("dpe") or {})},
        "pieces": [p for p in d.get("pieces") or [] if p in dict(PIECES)],
        "couverture": d.get("couverture"),
    }


def lire_reglages(data, nb_lots):
    """Réglages envoyés par le formulaire de préparation → dict à ranger dans SimulationInvestissement.dossier."""
    from .forms import nombre

    def montant(nom):
        brut = (data.get(nom) or "").strip()
        return None if brut == "" else max(0.0, nombre(brut))

    def texte(nom, n):
        return (data.get(nom) or "").strip()[:n]

    def classe(nom):
        v = (data.get(nom) or "").strip().upper()
        return v if v in CLASSES_DPE and len(v) == 1 else ""

    def entier(nom):
        v = montant(nom)
        return None if v is None else int(round(v))

    couverture = data.get("couverture")
    return {
        "sections": {code: data.get(f"section-{code}") == "on" for code, _ in SECTIONS},
        "presente_par": texte("presente_par", 120), "contact": texte("contact", 200), "mot": texte("mot", 1500),
        "revenus_mois": montant("revenus_mois"), "credits_mois": montant("credits_mois"),
        "loyers_existants_mois": montant("loyers_existants_mois"), "epargne": montant("epargne"),
        "inclure_patrimoine": data.get("inclure_patrimoine") == "on",
        "description": texte("description", 1500), "atouts": texte("atouts", 1500),
        "indexation_loyers": min(10.0, montant("indexation_loyers") if montant("indexation_loyers") is not None else INDEXATION_LOYERS),
        "indexation_charges": min(10.0, montant("indexation_charges") if montant("indexation_charges") is not None else INDEXATION_CHARGES),
        "dpe": {"actuel": classe("dpe_actuel"), "actuel_kwh": entier("dpe_actuel_kwh"), "actuel_ges": entier("dpe_actuel_ges"),
                "vise": classe("dpe_vise"), "vise_kwh": entier("dpe_vise_kwh"), "vise_ges": entier("dpe_vise_ges"),
                "lots": [{"actuel": classe(f"dpe-lot-{i}-actuel"), "vise": classe(f"dpe-lot-{i}-vise")} for i in range(nb_lots)]},
        "pieces": [code for code, _ in PIECES if data.get(f"piece-{code}") == "on"],
        "couverture": int(couverture) if (couverture or "").isdigit() else None,
    }


# ---------------------------------------------------------------------------------------------------------------------
# Patrimoine déjà géré dans l'appli
# ---------------------------------------------------------------------------------------------------------------------

def patrimoine(exclure_bien=None, aujourdhui=None):
    """Biens en portefeuille : lots, loyers des baux actifs, capital restant dû et mensualités des prêts en cours."""
    from patrimoine.models import Bail, Bien, Pret

    aujourdhui = aujourdhui or date.today()
    lignes, total_loyers, total_mens, total_crd = [], Decimal(0), Decimal(0), Decimal(0)
    prets = list(Pret.objects.all())

    def en_cours(pret):
        crd = pret.capital_restant_du(aujourdhui)
        return crd, (pret.mensualite_totale if crd > 0 else Decimal(0))

    for bien in Bien.objects.filter(vendu=False).exclude(pk=exclure_bien).order_by("nom"):
        lots = bien.lots.filter(vendu=False).count()
        loyers = sum((b.loyer_hc for b in Bail.objects.filter(lot__bien=bien, actif=True)), Decimal(0))
        crd = mens = Decimal(0)
        for pret in [p for p in prets if p.bien_id == bien.pk]:
            c, m = en_cours(pret)
            crd, mens = crd + c, mens + m
        lignes.append({"nom": bien.nom, "ville": bien.ville, "lots": lots, "loyers": float(loyers), "crd": float(crd),
                       "mensualites": float(mens)})
        total_loyers, total_mens, total_crd = total_loyers + loyers, total_mens + mens, total_crd + crd
    for pret in [p for p in prets if p.bien_id is None]:
        c, m = en_cours(pret)
        if c > 0:
            lignes.append({"nom": f"Prêt {pret.banque}", "ville": "", "lots": 0, "loyers": 0.0, "crd": float(c),
                           "mensualites": float(m)})
            total_mens, total_crd = total_mens + m, total_crd + c
    return {"lignes": lignes, "loyers": float(total_loyers), "mensualites": float(total_mens), "crd": float(total_crd)}


# ---------------------------------------------------------------------------------------------------------------------
# Calculs du dossier
# ---------------------------------------------------------------------------------------------------------------------

def endettement(revenus, credits, loyers_existants, mensualite_projet, loyers_projet):
    """Taux d'endettement après projet, au sens du HCSF : charges de crédit ÷ (revenus + 70 % des loyers)."""
    base = (revenus or 0) + PART_LOYERS_HCSF / 100 * ((loyers_existants or 0) + loyers_projet)
    charges = (credits or 0) + mensualite_projet
    taux = charges / base * 100 if base else None
    return {"taux": taux, "part": taux / 100 if taux is not None else None, "charges": charges, "base": base, "reste_a_vivre": base - charges if base else None,
            "ok": taux is not None and taux <= TAUX_HCSF, "seuil": TAUX_HCSF}


def previsionnel(r, idx_loyers=INDEXATION_LOYERS, idx_charges=INDEXATION_CHARGES, annees=ANNEES_PREVISIONNEL):
    """Loyers, échéances du prêt + charges, et cash-flow avant impôt sur 10 ans."""
    assurance = r.charges.get("Assurance du prêt", 0)
    charges = r.charges_total - assurance
    lignes = []
    for k in range(annees):
        loyers = r.loyers_an * (1 + idx_loyers / 100) ** k
        ch = charges * (1 + idx_charges / 100) ** k
        a = r.annees[k] if k < len(r.annees) else None
        pret = (a.mensualites + a.assurance) if a else 0.0
        lignes.append({"annee": k + 1, "loyers": loyers, "charges": ch, "pret": pret, "sorties": ch + pret,
                       "cash_flow": loyers - ch - pret, "restant": a.restant if a else 0.0})
    return lignes


def graphique(lignes, largeur=640, hauteur=250):
    """Coordonnées SVG des barres (loyers, sorties) et de la courbe de cash-flow, sur une seule échelle."""
    if not lignes:
        return None
    haut = max(max(l["loyers"], l["sorties"]) for l in lignes)
    bas = min(0.0, min(l["cash_flow"] for l in lignes))
    pas_axe = _pas(haut - bas)
    haut = pas_axe * -(-haut // pas_axe)
    bas = -pas_axe * -(-(-bas) // pas_axe) if bas < 0 else 0.0
    g, d, h, b = 52, 12, 22, 34
    y = lambda v: h + (haut - v) / (haut - bas) * (hauteur - h - b)
    pas = (largeur - g - d) / len(lignes)
    barre = pas * 0.32
    graduations = []
    v = bas
    while v <= haut + 0.01:
        graduations.append({"y": round(y(v), 1), "texte": f"{v / 1000:g} k€".replace(".", ","), "zero": abs(v) < 0.01})
        v += pas_axe
    barres, points = [], []
    for i, l in enumerate(lignes):
        x = g + i * pas + pas / 2
        barres.append({"x1": round(x - barre - 1, 1), "y1": round(y(l["loyers"]), 1), "h1": round(y(0) - y(l["loyers"]), 1),
                       "x2": round(x + 1, 1), "y2": round(y(l["sorties"]), 1), "h2": round(y(0) - y(l["sorties"]), 1),
                       "largeur": round(barre, 1), "cx": round(x, 1), "cy": round(y(l["cash_flow"]), 1), "annee": l["annee"]})
        points.append(f"{x:.1f},{y(l['cash_flow']):.1f}")
    return {"largeur": largeur, "hauteur": hauteur, "gauche": g, "droite": largeur - d, "bas_texte": hauteur - 14,
            "graduations": graduations, "barres": barres, "points": " ".join(points), "dernier": barres[-1],
            "dernier_cf": lignes[-1]["cash_flow"]}


def _pas(etendue):
    for p in (1000, 2000, 4000, 5000, 10000, 20000, 25000, 50000, 100000, 200000, 500000):
        if etendue / p <= 6:
            return float(p)
    return 1000000.0


def lignes_energie(p):
    """Postes du chiffrage cochés « rénovation énergétique » : lieu, poste, montant, justificatif."""
    sources = [(f"Lot {i + 1:03d}", l.travaux_detail, l.surface or 0) for i, l in enumerate(p.lots)]
    sources.append(("Parties communes", p.travaux_communs, 0))
    lignes = []
    for lieu, detail, surface in sources:
        for ligne in (detail or {}).get("lignes") or []:
            if not ligne.get("energie"):
                continue
            montant = inv.montant_ligne(ligne, surface)
            if not montant:
                continue
            devis = ligne.get("mode") == "devis"
            lignes.append({"lieu": lieu, "poste": ligne.get("poste") or "Autre", "montant": montant, "devis": devis,
                           "entreprise": ligne.get("entreprise") or "", "piece_url": ligne.get("piece_url") or ""})
    return lignes


def position_marche(marche, prix_m2):
    """Repères de la règle « notre achat / médiane / fourchette », en % de la largeur."""
    if not marche or not marche.get("nombre") or not prix_m2:
        return None
    bas = min(marche["q1"], prix_m2) * 0.9
    haut = max(marche["q3"], prix_m2) * 1.1
    pos = lambda v: round((v - bas) / (haut - bas) * 100, 1)
    ecart = (prix_m2 - marche["mediane"]) / marche["mediane"] if marche["mediane"] else 0
    return {"q1": pos(marche["q1"]), "q3": pos(marche["q3"]), "mediane": pos(marche["mediane"]), "nous": pos(prix_m2),
            "ecart": ecart}


def contexte(simulation, marche=None, lieu=None, photos=(), aujourdhui=None):
    """Tout ce que les pages du dossier affichent."""
    p = simulation.parametres
    r = inv.simuler(p)
    reg = reglages(simulation.dossier)
    patri = patrimoine(simulation.bien_id, aujourdhui) if reg["inclure_patrimoine"] else None
    credits = reg["credits_mois"] if reg["credits_mois"] is not None else (patri["mensualites"] if patri else 0)
    loyers_ex = reg["loyers_existants_mois"] if reg["loyers_existants_mois"] is not None else (patri["loyers"] if patri else 0)
    mens_totale = r.mensualite + r.assurance_mois
    dette = endettement(reg["revenus_mois"], credits, loyers_ex, mens_totale, r.loyers_mois) if reg["revenus_mois"] else None
    prev = previsionnel(r, reg["indexation_loyers"], reg["indexation_charges"])
    energie = lignes_energie(p)
    dpe = reg["dpe"]
    lots_dpe = []
    for i, l in enumerate(p.lots):
        x = dpe["lots"][i] if i < len(dpe["lots"]) else {}
        lots_dpe.append({"numero": i + 1, "lot": l, "actuel": x.get("actuel", ""), "vise": x.get("vise", "")})
    a_un_dpe = bool(dpe["actuel"] or dpe["vise"] or any(x["actuel"] or x["vise"] for x in lots_dpe))
    prix_m2 = p.prix / r.surface if r.surface else 0
    retenu = r.retenu
    photos = list(photos)
    couverture = next((ph for ph in photos if ph.pk == reg["couverture"]), photos[0] if photos else None)
    pieces_auto = [f"Devis {d['poste'].lower()}{', ' + d['entreprise'] if d['entreprise'] else ''}"
                   for d in r.lignes_devis if d["piece_url"]]
    montre_dpe = reg["sections"]["dpe"] and (a_un_dpe or bool(energie))
    ordre = [c for c, _ in SECTIONS if reg["sections"][c] and (c != "dpe" or montre_dpe)]
    return {
        "s": simulation, "p": p, "r": r, "reg": reg, "sections": reg["sections"],
        "num": {c: i + 1 for i, c in enumerate(ordre)}, "nb_pages": len(ordre),
        "aujourdhui": aujourdhui or date.today(),
        "patrimoine": patri, "credits": credits, "loyers_existants": loyers_ex, "mensualite_totale": mens_totale,
        "dette": dette, "jauge": min(100.0, dette["taux"] / 50 * 100) if dette and dette["taux"] is not None else 0,
        "seuil_jauge": TAUX_HCSF / 50 * 100,
        "frais_banque_garantie": r.frais_bancaires + r.garantie,
        "previsionnel": prev, "graphique": graphique(prev), "regime": retenu,
        "marche": marche, "regle": position_marche(marche, prix_m2), "prix_m2": prix_m2, "lieu": lieu,
        "loyer_m2": (sum(inv.loyer_lot(l, p.scenario) - (l.charges or 0) for l in p.lots) / r.surface) if r.surface else 0,
        "atouts": [a.strip() for a in reg["atouts"].splitlines() if a.strip()],
        "photos": photos, "couverture": couverture, "autres_photos": [ph for ph in photos if ph != couverture][:6],
        "energie": energie, "total_energie": sum(l["montant"] for l in energie),
        "dpe": dpe, "lots_dpe": lots_dpe, "a_un_dpe": a_un_dpe,
        "montre_dpe": montre_dpe,
        "echelle_dpe": [{"classe": c, "seuil": t, "couleur": COULEURS_DPE[c], "largeur": 20 + 6 * i}
                        for i, (c, t) in enumerate(SEUILS_DPE)],
        "couleurs_dpe": COULEURS_DPE, "loi_climat": LOI_CLIMAT.get(dpe["actuel"], ""),
        "gain_dpe": ((dpe["actuel_kwh"] - dpe["vise_kwh"]) / dpe["actuel_kwh"]
                     if dpe["actuel_kwh"] and dpe["vise_kwh"] is not None else None),
        "pieces_auto": pieces_auto, "pieces": [(code, nom, code in reg["pieces"]) for code, nom in PIECES],
        "liste_sections": SECTIONS, "classes_dpe": CLASSES_DPE,
    }
