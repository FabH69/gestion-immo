"""Lecture des relevés de banque : OFX (ou QFX), CSV, CAMT.053 (XML) et PDF avec texte.

Chaque lecteur rend un Releve : les lignes (date, libellé, détail, montant signé) et, quand le fichier les donne,
les soldes de début et de fin. Le contrôle « solde de début + mouvements = solde de fin » dit si rien n'a été mal lu.
Un relevé PDF peut contenir plusieurs comptes : on garde celui dont le numéro figure dans l'IBAN du compte choisi."""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree


class ReleveIllisible(Exception):
    pass


@dataclass
class Ligne:
    date: date
    libelle: str
    montant: Decimal
    detail: str = ""


@dataclass
class Releve:
    format: str
    lignes: list = field(default_factory=list)
    solde_debut: Decimal | None = None
    solde_fin: Decimal | None = None
    numero: str = ""  # n° du compte lu dans le fichier (PDF)

    @property
    def controle(self):
        """(ok, texte) : None si le fichier ne donne pas les deux soldes."""
        if self.solde_debut is None or self.solde_fin is None:
            return None, "Le fichier ne donne pas les soldes : contrôle impossible."
        calcule = self.solde_debut + sum((l.montant for l in self.lignes), Decimal(0))
        if calcule == self.solde_fin:
            return True, f"Soldes vérifiés : {_fr(self.solde_debut)} + mouvements = {_fr(self.solde_fin)}."
        return False, (f"Écart : solde de début {_fr(self.solde_debut)} + mouvements = {_fr(calcule)}, "
                       f"mais le relevé indique {_fr(self.solde_fin)}. Une ligne a pu être mal lue.")


def _fr(m):
    return f"{m:,.2f} €".replace(",", " ").replace(".", ",")


def montant_fr(texte):
    """« 1 234,56 », « -1234.56 », « 1.234,56 » → Decimal."""
    t = (texte or "").strip().replace("\xa0", "").replace(" ", "").replace(" ", "").replace("€", "").replace("EUR", "")
    if not t:
        return None
    negatif = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("+-()")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        m = Decimal(t)
    except InvalidOperation:
        return None
    return -m if negatif else m


def date_fr(texte):
    texte = (texte or "").strip()
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(texte[:10] if f != "%Y%m%d" else texte[:8], f).date()
        except ValueError:
            continue
    return None


# --- OFX -------------------------------------------------------------------------------------------------------------
def lire_ofx(contenu):
    texte = contenu.decode("latin-1") if isinstance(contenu, bytes) else contenu

    def balise(bloc, nom):
        m = re.search(rf"<{nom}>([^<\r\n]*)", bloc, re.I)
        return m.group(1).strip() if m else ""

    releve = Releve("OFX")
    for bloc in re.findall(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)", texte, re.S | re.I):
        d, m = date_fr(balise(bloc, "DTPOSTED")[:8]), montant_fr(balise(bloc, "TRNAMT"))
        if d is None or m is None:
            continue
        nom, memo = balise(bloc, "NAME"), balise(bloc, "MEMO")
        releve.lignes.append(Ligne(d, nom or memo, m, memo if nom and memo != nom else ""))
    if not releve.lignes and "<OFX>" not in texte.upper():
        raise ReleveIllisible("Ce fichier n'est pas un relevé OFX.")
    fin = re.search(r"<LEDGERBAL>.*?<BALAMT>([^<\r\n]+)", texte, re.S | re.I)
    if fin:
        releve.solde_fin = montant_fr(fin.group(1))
    return releve


# --- CAMT.053 --------------------------------------------------------------------------------------------------------
def lire_camt(contenu):
    try:
        racine = ElementTree.fromstring(contenu)
    except ElementTree.ParseError as e:
        raise ReleveIllisible(f"Fichier XML illisible ({e}).")
    ns = {"c": racine.tag.split("}")[0].strip("{")} if racine.tag.startswith("{") else {}
    p = "c:" if ns else ""

    def texte(el, chemin):
        x = el.find(_ns(chemin, p), ns)
        return x.text.strip() if x is not None and x.text else ""

    releve = Releve("CAMT")
    stmt = racine.find(f".//{p}Stmt", ns)
    if stmt is None:
        raise ReleveIllisible("Ce fichier XML n'est pas un relevé CAMT.053.")
    releve.numero = texte(stmt, "Acct/Id/IBAN")
    for bal in stmt.findall(f"{p}Bal", ns):
        code = texte(bal, "Tp/CdOrPrtry/Cd")
        m = montant_fr(texte(bal, "Amt"))
        if m is not None and texte(bal, "CdtDbtInd") == "DBIT":
            m = -m
        if code in ("OPBD", "PRCD"):
            releve.solde_debut = m
        elif code in ("CLBD",):
            releve.solde_fin = m
    for n in stmt.findall(f"{p}Ntry", ns):
        m = montant_fr(texte(n, "Amt"))
        d = date_fr(texte(n, "BookgDt/Dt") or texte(n, "BookgDt/DtTm") or texte(n, "ValDt/Dt"))
        if m is None or d is None:
            continue
        if texte(n, "CdtDbtInd") == "DBIT":
            m = -m
        infos = [texte(n, "AddtlNtryInf")]
        for tx in n.findall(f".//{p}TxDtls", ns):
            infos += [texte(tx, "RltdPties/Dbtr/Nm"), texte(tx, "RltdPties/Cdtr/Nm"), texte(tx, "RmtInf/Ustrd")]
        infos = [i for i in infos if i]
        releve.lignes.append(Ligne(d, infos[0] if infos else "Opération", m, " ".join(infos[1:])))
    return releve


def _ns(chemin, p):
    return "/".join(p + morceau for morceau in chemin.split("/"))


# --- CSV -------------------------------------------------------------------------------------------------------------
COLONNES = {
    "date": ["date operation", "date d operation", "date de l operation", "date comptable", "date", "date valeur", "booking date"],
    "libelle": ["libelle", "libelle operation", "libelle simplifie", "description", "intitule", "operation", "label", "nature de l operation"],
    "detail": ["informations complementaires", "detail", "libelle complet", "reference", "commentaire"],
    "debit": ["debit", "debit euros", "montant debit", "sortie"],
    "credit": ["credit", "credit euros", "montant credit", "entree"],
    "montant": ["montant", "montant eur", "montant euros", "amount", "montant (eur)"],
}


def lire_csv(contenu):
    from .models.banque import normaliser

    if isinstance(contenu, bytes):
        for codage in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texte = contenu.decode(codage)
                break
            except UnicodeDecodeError:
                continue
    else:
        texte = contenu
    lignes = [l for l in texte.splitlines() if l.strip()]
    separateur = max([";", ",", "\t"], key=lambda s: sum(l.count(s) for l in lignes[:20]))
    rangees = list(csv.reader(lignes, delimiter=separateur))
    entete, colonnes = None, {}
    for i, r in enumerate(rangees[:30]):  # l'en-tête n'est pas toujours la 1re ligne (titre, n° de compte…)
        noms = [normaliser(c).lower() for c in r]
        trouve = {}
        for cle, possibles in COLONNES.items():
            for j, n in enumerate(noms):
                if n in possibles and j not in trouve.values():
                    trouve.setdefault(cle, j)
        if "date" in trouve and "libelle" in trouve and ("montant" in trouve or "debit" in trouve or "credit" in trouve):
            entete, colonnes = i, trouve
            break
    if entete is None:
        raise ReleveIllisible("Colonnes non reconnues : il faut au moins une date, un libellé et un montant (ou débit et crédit).")
    releve = Releve("CSV")
    for r in rangees[entete + 1:]:
        val = lambda cle: r[colonnes[cle]] if cle in colonnes and colonnes[cle] < len(r) else ""
        d = date_fr(val("date"))
        if d is None:
            continue
        if "montant" in colonnes and montant_fr(val("montant")) is not None:
            m = montant_fr(val("montant"))
        else:
            debit, credit = montant_fr(val("debit")), montant_fr(val("credit"))
            if debit is None and credit is None:
                continue
            m = (credit or 0) - abs(debit or 0)
        releve.lignes.append(Ligne(d, val("libelle").strip() or "Opération", Decimal(m), val("detail").strip()))
    return releve


# --- PDF -------------------------------------------------------------------------------------------------------------
MONTANT = r"([+-]) ?(\d{1,3}(?:[ . ]\d{3})*,\d{2})"
DATE = r"(\d\d/\d\d/\d{4})"


def _signe(signe, nombre):
    m = montant_fr(nombre)
    return -m if signe == "-" else m


def lire_pdf(contenu, chiffres_iban=""):
    """Relevés PDF « texte » (téléchargés sur le site de la banque). Lignes reconnues : « date date libellé … ± montant »
    ou « ± montant date date libellé » ; les lignes suivantes sans date complètent le libellé. Chaque compte du relevé
    commence à son « SOLDE … AU » de début et finit à celui de fin."""
    import logging

    import pypdf

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        lecteur = pypdf.PdfReader(io.BytesIO(contenu))
        texte = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in lecteur.pages)
    except Exception as e:  # PDF abîmé ou protégé
        raise ReleveIllisible(f"PDF illisible ({e}).")
    if len(texte.strip()) < 50:
        raise ReleveIllisible("Ce PDF ne contient pas de texte (relevé scanné ou photo) : importez plutôt le fichier OFX ou CSV.")
    comptes, courant, operation, numero = [], None, None, ""
    for brut in texte.splitlines():
        ligne = brut.strip()
        if m := re.search(r"COMPTE[^\n]*?N°\s*([\d ]{8,})", ligne, re.I):
            numero = re.sub(r"\D", "", m.group(1))
        if m := re.match(r"(?:ANCIEN |NOUVEAU )?SOLDE (?:CREDITEUR|DEBITEUR|AU|AU\s)[^\d]*" + r"(\d\d/\d\d/\d{4})\s+" + MONTANT, ligne, re.I):
            solde = _signe(m.group(2), m.group(3))
            if "DEBITEUR" in ligne.upper() and solde > 0:
                solde = -solde
            if courant is None or courant.solde_fin is not None:
                courant = Releve("PDF", solde_debut=solde, numero=numero)
                comptes.append(courant)
            else:
                courant.solde_fin = solde
            operation = None
            continue
        if courant is None or courant.solde_fin is not None:
            continue
        if m := re.match(MONTANT + DATE + r"\s+" + DATE + r"\s+(.*)$", ligne):
            operation = Ligne(date_fr(m.group(3)), m.group(5).strip(), _signe(m.group(1), m.group(2)))
            courant.lignes.append(operation)
            continue
        if m := re.match(DATE + r"\s+" + DATE + r"\s+(.*?)\s+" + MONTANT + r"$", ligne):
            operation = Ligne(date_fr(m.group(1)), m.group(3).strip(), _signe(m.group(4), m.group(5)))
            courant.lignes.append(operation)
            continue
        if not ligne or len(ligne) > 110 or re.search(r"Page \d|Relev[ée] N|ne constitue pas|SIEGE SOCIAL|CAPITAL SOCIAL", ligne, re.I):
            operation = None  # bas de page, mentions légales : le détail de l'opération est fini
            continue
        if operation and not re.match(r"(DONT TVA|MONTANT|EN EUR|DATE)", ligne, re.I) and operation.detail.count(" | ") < 2:
            operation.detail = (operation.detail + " | " + ligne) if operation.detail else ligne
    comptes = [c for c in comptes if c.lignes or c.solde_fin is not None]
    if not comptes:
        raise ReleveIllisible("Aucune opération reconnue dans ce PDF. Sa mise en page n'est pas encore connue : importez "
                              "plutôt le fichier OFX ou CSV de la banque.")
    if len(comptes) == 1:
        return comptes[0]
    if chiffres_iban:
        bons = [c for c in comptes if c.numero and c.numero in chiffres_iban]
        if len(bons) == 1:
            return bons[0]
    numeros = ", ".join(c.numero or "?" for c in comptes)
    raise ReleveIllisible(f"Ce PDF contient {len(comptes)} comptes (n° {numeros}). Indiquez l'IBAN du compte dans sa fiche "
                          f"pour que l'appli garde le bon.")


def lire(nom, contenu, chiffres_iban=""):
    """Choisit le lecteur d'après l'extension, puis d'après le contenu."""
    ext = nom.lower().rsplit(".", 1)[-1] if "." in nom else ""
    debut = contenu[:400].lstrip() if isinstance(contenu, bytes) else contenu[:400].encode()
    if ext == "pdf" or debut.startswith(b"%PDF"):
        return lire_pdf(contenu, chiffres_iban)
    if ext in ("ofx", "qfx") or b"OFXHEADER" in debut.upper() or b"<OFX>" in debut.upper():
        return lire_ofx(contenu)
    if ext == "xml" or debut.startswith(b"<?xml"):
        return lire_camt(contenu)
    if ext in ("csv", "txt", "tsv", ""):
        return lire_csv(contenu)
    raise ReleveIllisible("Format non reconnu : OFX, CSV, CAMT.053 (XML) ou PDF.")
