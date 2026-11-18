"""Lecture des tableaux d'amortissement remis par les banques (PDF texte du Crédit Mutuel et de la Caisse d'Épargne).

Deux présentations sont reconnues, ligne par ligne :
- Crédit Mutuel : date, (type d'évènement), capital avant l'échéance, capital, intérêts, assurance et frais, échéance ;
- Caisse d'Épargne : rang, date, échéance, capital, intérêts, accessoires, capital restant dû, intérêts reportés, report.
Chaque ligne n'est retenue que si ses montants se recoupent (capital + intérêts + assurance = échéance)."""
import re
from datetime import date
from decimal import Decimal

MONTANT = r"\d+(?:[   ]\d{3})*,\d{2}"
LIGNE = re.compile(rf"^\s*(?:(\d{{1,3}})\s+)?(\d{{2}})[/-](\d{{2}})[/-](\d{{4}})\s+(?:[^\d\s][^\d]*?\s+)?((?:{MONTANT}\s*)+)$")
ZERO = Decimal(0)


def _montant(texte):
    return Decimal(re.sub(r"[   ]", "", texte).replace(",", "."))


def lire_ligne(texte):
    """Une ligne de tableau → dict (rang, date, capital, interets, assurance, restant) ou None."""
    m = LIGNE.match(texte.replace(" ", " "))
    if not m:
        return None
    rang, jour, mois, annee, montants = m.groups()
    try:
        d = date(int(annee), int(mois), int(jour))
    except ValueError:
        return None
    a = [_montant(x) for x in re.findall(MONTANT, montants)]
    if len(a) >= 7 and a[1] + a[2] + a[3] == a[0]:
        # Caisse d'Épargne : échéance, capital, intérêts, accessoires, capital restant dû…
        if a[0] == 0:
            return None  # ligne de mise en place sans montant
        return {"rang": int(rang) if rang else None, "date": d, "capital": a[1], "interets": a[2], "assurance": a[3], "restant": a[4]}
    if len(a) >= 5 and a[1] + a[2] + a[3] == a[4]:
        # Crédit Mutuel : capital avant l'échéance, capital, intérêts, assurance, échéance
        return {"rang": int(rang) if rang else None, "date": d, "capital": a[1], "interets": a[2], "assurance": a[3],
                "restant": a[0] - a[1]}
    return None


def texte_tableau(fichier):
    """Texte de toutes les pages d'un PDF de tableau, en gardant la mise en page des colonnes ('' si scanné)."""
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        fichier.seek(0)
        pages = PdfReader(fichier).pages
        essais = []
        for mode in ("layout", "plain"):
            texte = "\n".join((p.extract_text(extraction_mode=mode) or "") for p in pages)
            essais.append((len(lire(texte)), texte))
        return max(essais, key=lambda e: e[0])[1]
    except Exception:
        return ""
    finally:
        try:
            fichier.seek(0)
        except Exception:
            pass


def lire(texte):
    """Toutes les échéances reconnues dans le texte d'un tableau, dans l'ordre des dates, numérotées si besoin."""
    lignes, vues = [], set()
    for t in texte.splitlines():
        ligne = lire_ligne(t)
        if ligne and ligne["date"] not in vues:
            vues.add(ligne["date"])
            lignes.append(ligne)
    lignes.sort(key=lambda l: l["date"])
    for i, l in enumerate(lignes, 1):
        if l["rang"] is None:
            l["rang"] = i
    return lignes


def anomalies(lignes, montant_initial=None):
    """Contrôle d'enchaînement : le capital restant dû d'une ligne moins le capital remboursé de la suivante doit donner
    le capital restant dû de la suivante (sauf intérêts capitalisés pendant un différé, qui l'augmentent)."""
    erreurs = []
    precedent = montant_initial
    for l in lignes:
        if precedent is not None and l["capital"] and precedent - l["capital"] != l["restant"]:
            erreurs.append(f"échéance {l['rang']} du {l['date']:%d/%m/%Y} : {precedent} − {l['capital']} ≠ {l['restant']}")
        precedent = l["restant"]
    if lignes and lignes[-1]["restant"] != 0:
        erreurs.append(f"le tableau se termine avec {lignes[-1]['restant']} € restant dû (dernière page manquante ?)")
    return erreurs


def enregistrer(pret, lignes):
    """Remplace le tableau du prêt par ces lignes."""
    from .models import LigneEcheancier

    pret.lignes.all().delete()
    LigneEcheancier.objects.bulk_create([
        LigneEcheancier(pret=pret, rang=l["rang"], date=l["date"], capital=l["capital"], interets=l["interets"],
                        assurance=l["assurance"] or ZERO, restant=l["restant"]) for l in lignes])
    return len(lignes)
