"""Lecture automatique des factures PDF « électroniques » (texte présent dans le PDF) : propose la date, le fournisseur,
le montant TTC et la ligne de l'annexe 2072. Les factures scannées ou photographiées n'ont pas de texte : rien n'est proposé.
Tout reste à vérifier par l'utilisateur avant d'être compté dans l'annexe."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

MOIS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "août": 8,
        "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}

MONTANT = r"(-?\d{1,3}(?:[   .]\d{3})*,\d{2}|-?\d+[.,]\d{2})"
MOTS_TOTAL = [  # du plus fiable au moins fiable
    r"net\s+[àa]\s+payer", r"total\s+ttc", r"montant\s+ttc", r"total\s+[àa]\s+payer", r"montant\s+[àa]\s+payer",
    r"montant\s+total", r"somme\s+[àa]\s+payer", r"montant\s+d[ûu]", r"total\s+(?:de\s+la\s+)?facture", r"[àa]\s+payer", r"total",
]

FOURNISSEURS = [
    ("finances publiques", "Impôts (DGFiP)"), ("impots.gouv", "Impôts (DGFiP)"), ("edf", "EDF"), ("engie", "Engie"),
    ("totalenergies", "TotalEnergies"), ("veolia", "Veolia"), ("suez", "Suez"), ("eau du grand lyon", "Eau du Grand Lyon"),
    ("orange", "Orange"), ("bouygues", "Bouygues Telecom"), ("free", "Free"), ("axa", "AXA"), ("maif", "MAIF"),
    ("macif", "MACIF"), ("matmut", "Matmut"), ("allianz", "Allianz"), ("groupama", "Groupama"), ("generali", "Generali"),
    ("mma", "MMA"), ("gmf", "GMF"), ("hiscox", "Hiscox"), ("foncia", "Foncia"), ("nexity", "Nexity"), ("citya", "Citya"),
    ("oralia", "Oralia"), ("régie", None), ("leroy merlin", "Leroy Merlin"), ("castorama", "Castorama"),
    ("brico dépôt", "Brico Dépôt"), ("point p", "Point P"), ("cedeo", "Cedeo"), ("crédit mutuel", "Crédit Mutuel"),
    ("caisse d'epargne", "Caisse d'Épargne"), ("caisse d'épargne", "Caisse d'Épargne"),
]

LIGNES = [  # (ligne 2072 proposée, mots-clés) : le premier qui correspond l'emporte
    ("12", ["taxe foncière", "taxe fonciere", "avis d'impôt", "avis d'impot", "finances publiques", "impots.gouv", "contribution sur les revenus locatifs"]),
    ("14", ["régularisation des charges", "regularisation des charges", "approbation des comptes", "décompte de charges", "decompte de charges"]),
    ("13", ["appel de fonds", "appel de charges", "provision sur charges", "provisions pour charges", "charges de copropriété", "charges de copropriete"]),
    ("8", ["propriétaire non occupant", "proprietaire non occupant", " pno", "avis d'échéance", "avis d'echeance", "prime d'assurance", "cotisation d'assurance", "assurance habitation", "garantie loyers impayés", "loyers impayés"]),
    ("6", ["honoraires de gestion", "gestion locative", "frais de gestion", "syndic", "expert-comptable", "expert comptable", "comptabilité", "honoraires"]),
    ("9b", ["pompe à chaleur", "pompe a chaleur", "isolation", "rénovation énergétique", "renovation energetique", "maprimerénov", "maprimerenov"]),
    ("9", ["travaux", "réparation", "reparation", "plomberie", "plombier", "électricité", "electricite", "électricien", "chauffage",
           "chaudière", "chaudiere", "peinture", "menuiserie", "serrurerie", "entretien", "dépannage", "depannage", "intervention",
           "remplacement", "fourniture et pose", "main d'oeuvre", "main d'œuvre"]),
]


def texte_pdf(fichier, pages=3):
    """Texte des premières pages d'un PDF ('' si scanné, protégé ou illisible)."""
    try:
        from pypdf import PdfReader

        fichier.seek(0)
        lecteur = PdfReader(fichier)
        texte = "\n".join((lecteur.pages[i].extract_text() or "") for i in range(min(pages, len(lecteur.pages))))
        fichier.seek(0)
        return texte
    except Exception:
        try:
            fichier.seek(0)
        except Exception:
            pass
        return ""


def _montant(chaine):
    chaine = chaine.replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in chaine:
        chaine = chaine.replace(".", "").replace(",", ".")
    try:
        return Decimal(chaine)
    except InvalidOperation:
        return None


def _date(texte):
    candidats = []
    for m in re.finditer(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", texte):
        j, mo, a = (int(x) for x in m.groups())
        a = a + 2000 if a < 100 else a
        candidats.append((m.start(), j, mo, a))
    for m in re.finditer(r"\b(\d{1,2})(?:er)?\s+(" + "|".join(MOIS) + r")\s+(\d{4})\b", texte, re.IGNORECASE):
        candidats.append((m.start(), int(m.group(1)), MOIS[m.group(2).lower()], int(m.group(3))))
    valides = []
    for pos, j, mo, a in sorted(candidats):
        try:
            d = date(a, mo, j)
        except ValueError:
            continue
        if 2000 <= a <= date.today().year + 1:
            valides.append((pos, d))
    if not valides:
        return None
    # une date précédée d'un mot comme « Date de facture », « Facture du »… est préférée
    for pos, d in valides:
        avant = texte[max(0, pos - 40):pos].lower()
        if re.search(r"(date\s+(de\s+(la\s+)?facture|d['’]émission|d['’]édition)|factur[ée]e?\s+le|facture\s+du|émise?\s+le|édité\s+le)\s*:?\s*$", avant):
            return d
    return valides[0][1]


# biens en LMNP ou en société : (rubrique proposée, mots entiers) ; le premier qui correspond l'emporte
RUBRIQUES = [
    ("impots_taxes", ["cotisation foncière des entreprises", "cotisation fonciere des entreprises", "cfe"]),
    ("taxe_fonciere", ["taxe foncière", "taxe fonciere", "avis d'impôt", "avis d'impot", "finances publiques"]),
    ("copro", ["appel de fonds", "appel de charges", "charges de copropriété", "charges de copropriete", "syndic"]),
    ("assurance_pret", ["assurance emprunteur", "assurance de prêt", "assurance de pret", "assurance du prêt", "assurance du pret"]),
    ("pno", ["propriétaire non occupant", "proprietaire non occupant", "pno", "prime d'assurance", "cotisation d'assurance"]),
    ("comptable", ["expert-comptable", "expert comptable", "comptabilité", "comptabilite", "cga", "centre de gestion"]),
    ("electricite", ["edf", "engie", "totalenergies", "électricité", "electricite", "kwh"]),
    ("eau", ["eau potable", "veolia", "suez", "saur", "assainissement", "m3"]),
    ("fibre", ["fibre", "internet", "livebox", "freebox", "bbox", "sfr box"]),
    ("menage", ["ménage", "menage", "nettoyage", "pressing", "blanchisserie"]),
    ("consommables", ["consommables", "papier toilette", "café", "cafe", "produits d'entretien"]),
    ("commission", ["conciergerie", "commission", "honoraires de gestion"]),
]


def analyser(texte, par_rubrique=False):
    """Propositions trouvées dans le texte : {date, montant, fournisseur, ligne} (clés absentes si rien trouvé).
    Pour un bien en LMNP ou en société, une rubrique est proposée à la place de la ligne 2072."""
    resultat = {}
    if not texte or len(texte.strip()) < 20:
        return resultat
    bas = texte.lower()

    d = _date(texte)
    if d:
        resultat["date"] = d

    for motif in MOTS_TOTAL:
        trouve = None
        for ligne in texte.splitlines():
            if re.search(motif, ligne, re.IGNORECASE) and not re.search(r"\bht\b|hors\s+taxe", ligne, re.IGNORECASE):
                montants = [m for m in (_montant(x) for x in re.findall(MONTANT, ligne)) if m is not None and m > 0]
                if montants:
                    trouve = montants[-1]
        if trouve is not None:
            resultat["montant"] = trouve
            break
    if "montant" not in resultat:  # à défaut : le plus grand montant suivi de € dans le document
        montants = [m for m in (_montant(x) for x in re.findall(MONTANT + r"\s*(?:€|eur\b)", texte, re.IGNORECASE)) if m and m > 0]
        if montants:
            resultat["montant"] = max(montants)

    for cle, nom in FOURNISSEURS:
        if nom and re.search(r"(?<![a-zà-ü])" + re.escape(cle) + r"(?![a-zà-ü])", bas):
            resultat["fournisseur"] = nom
            break
    if "fournisseur" not in resultat:
        for ligne in texte.splitlines():
            l = ligne.strip()
            if 3 <= len(l) <= 60 and re.search(r"[A-Za-zÀ-ü]{3}", l) and not re.search(r"factur|page|date|n°|client|devis|\d{2}/\d{2}", l, re.IGNORECASE):
                resultat["fournisseur"] = l[:60]
                break

    if par_rubrique:
        for rubrique, mots in RUBRIQUES:
            if any(re.search(r"(?<!\w)" + re.escape(m) + r"(?!\w)", bas) for m in mots):
                resultat["rubrique"] = rubrique
                break
        return resultat
    for ligne_2072, mots in LIGNES:
        if any(m in bas for m in mots):
            resultat["ligne"] = ligne_2072
            break
    return resultat
