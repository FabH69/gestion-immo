"""Lecture des factures électroniques (réforme de la facturation électronique) dans leurs trois formats :
- Factur-X : PDF qui contient un fichier XML (format CII) en pièce jointe ;
- CII (UN/CEFACT Cross Industry Invoice) : fichier XML seul ;
- UBL (OASIS Universal Business Language) : fichier XML seul, facture ou avoir.

Les données sont lues dans le XML, pas devinées dans le texte : numéro, date, fournisseur et son SIREN, montants HT, TVA
et TTC, objet. Un avoir donne des montants négatifs. Rien n'est chargé depuis l'extérieur : un XML qui déclare un
DOCTYPE (entités) est refusé."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

TAILLE_MAX = 5 * 1024 * 1024  # un XML de facture fait quelques dizaines de Ko

FORMATS = [("facturx", "Factur-X"), ("cii", "CII"), ("ubl", "UBL")]
LIBELLES_FORMATS = dict(FORMATS)

# noms habituels du XML joint à un PDF Factur-X (ou ZUGFeRD / XRechnung, même format CII)
NOMS_XML_FACTURX = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd_invoice.xml")

AVOIRS_CII = {"381", "261", "262", "296", "308", "396", "420", "458", "532"}


class FactureIllisible(Exception):
    """Fichier XML qui n'est pas une facture électronique lisible."""


def _sans_espaces_de_noms(racine):
    for el in racine.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for cle in [c for c in el.attrib if "}" in c]:
            el.attrib[cle.split("}", 1)[1]] = el.attrib.pop(cle)
    return racine


def _analyser_xml(donnees):
    if len(donnees) > TAILLE_MAX:
        raise FactureIllisible("fichier trop volumineux pour une facture")
    if re.search(rb"<!DOCTYPE|<!ENTITY", donnees[:4096], re.IGNORECASE):
        raise FactureIllisible("XML refusé (DOCTYPE)")
    try:
        return _sans_espaces_de_noms(ElementTree.fromstring(donnees))
    except ElementTree.ParseError as e:
        raise FactureIllisible(f"XML illisible ({e})")


def _texte(el, chemin):
    trouve = el.find(chemin) if el is not None else None
    return (trouve.text or "").strip() if trouve is not None and trouve.text else ""


def _decimal(valeur):
    if not valeur:
        return None
    try:
        return Decimal(valeur.strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _date(valeur):
    chiffres = re.sub(r"\D", "", valeur or "")[:8]
    if len(chiffres) != 8:
        return None
    try:
        return date(int(chiffres[:4]), int(chiffres[4:6]), int(chiffres[6:8]))
    except ValueError:
        return None


def siren_depuis(identifiant):
    """SIREN (9 chiffres) tiré d'un SIREN, d'un SIRET ou d'un numéro de TVA français (FR + clé + SIREN) ; '' sinon."""
    brut = re.sub(r"[\s.\-]", "", identifiant or "").upper()
    if re.fullmatch(r"FR[0-9A-Z]{2}\d{9}", brut):
        return brut[-9:]
    if re.fullmatch(r"\d{14}", brut):
        return brut[:9]
    if re.fullmatch(r"\d{9}", brut):
        return brut
    return ""


def _premier_siren(*identifiants):
    for i in identifiants:
        s = siren_depuis(i)
        if s:
            return s
    return ""


def _objet(designations, note):
    lignes = [d for d in designations if d]
    if lignes:
        objet = lignes[0] + (f" (+ {len(lignes) - 1} ligne{'s' if len(lignes) > 2 else ''})" if len(lignes) > 1 else "")
    else:
        objet = note.splitlines()[0] if note else ""
    return objet[:200]


def lire_cii(racine):
    """Facture CII (CrossIndustryInvoice), seule ou extraite d'un Factur-X."""
    doc = racine.find("ExchangedDocument")
    transaction = racine.find("SupplyChainTradeTransaction")
    if doc is None or transaction is None:
        raise FactureIllisible("facture CII incomplète")
    accord = transaction.find("ApplicableHeaderTradeAgreement")
    vendeur = accord.find("SellerTradeParty") if accord is not None else None
    reglement = transaction.find("ApplicableHeaderTradeSettlement")
    totaux = reglement.find("SpecifiedTradeSettlementHeaderMonetarySummation") if reglement is not None else None
    devise = _texte(reglement, "InvoiceCurrencyCode")
    tva = None
    for t in (totaux.findall("TaxTotalAmount") if totaux is not None else []):
        if tva is None or (devise and t.get("currencyID") == devise):
            tva = _decimal(t.text)
    ttc = _decimal(_texte(totaux, "GrandTotalAmount"))
    ht = _decimal(_texte(totaux, "TaxBasisTotalAmount")) or _decimal(_texte(totaux, "LineTotalAmount"))
    if ttc is None and ht is not None:
        ttc = ht + (tva or 0)
    avoir = _texte(doc, "TypeCode") in AVOIRS_CII
    designations = [_texte(l, "SpecifiedTradeProduct/Name") for l in transaction.findall("IncludedSupplyChainTradeLineItem")]
    note = _texte(doc, "IncludedNote/Content")
    echeance = _date(_texte(reglement, "SpecifiedTradePaymentTerms/DueDateDateTime/DateTimeString"))
    return _resultat(
        numero=_texte(doc, "ID"), date_facture=_date(_texte(doc, "IssueDateTime/DateTimeString")),
        fournisseur=_texte(vendeur, "Name") or _texte(vendeur, "SpecifiedLegalOrganization/TradingBusinessName"),
        siren=_premier_siren(_texte(vendeur, "SpecifiedLegalOrganization/ID"), _texte(vendeur, "ID"),
                             *[t.text for t in (vendeur.findall("SpecifiedTaxRegistration/ID") if vendeur is not None else [])]),
        ht=ht, tva=tva, ttc=ttc, avoir=avoir, objet=_objet(designations, note), echeance=echeance,
        devise=devise, designations=designations)


def lire_ubl(racine):
    """Facture ou avoir UBL (Invoice ou CreditNote)."""
    avoir = racine.tag == "CreditNote"
    vendeur = racine.find("AccountingSupplierParty/Party")
    totaux = racine.find("LegalMonetaryTotal")
    tva = None
    devise = _texte(racine, "DocumentCurrencyCode")
    for t in racine.findall("TaxTotal/TaxAmount"):
        if tva is None or (devise and t.get("currencyID") == devise):
            tva = _decimal(t.text)
    ht = _decimal(_texte(totaux, "TaxExclusiveAmount")) or _decimal(_texte(totaux, "LineExtensionAmount"))
    ttc = _decimal(_texte(totaux, "TaxInclusiveAmount")) or _decimal(_texte(totaux, "PayableAmount"))
    if ttc is None and ht is not None:
        ttc = ht + (tva or 0)
    lignes = racine.findall("CreditNoteLine" if avoir else "InvoiceLine")
    designations = [_texte(l, "Item/Name") or _texte(l, "Item/Description") for l in lignes]
    identifiants = []
    if vendeur is not None:
        identifiants = ([_texte(vendeur, "PartyLegalEntity/CompanyID")]
                        + [i.text for i in vendeur.findall("PartyIdentification/ID")]
                        + [_texte(vendeur, "EndpointID")]
                        + [i.text for i in vendeur.findall("PartyTaxScheme/CompanyID")])
    echeance = _date(_texte(racine, "DueDate") or _texte(racine, "PaymentMeans/PaymentDueDate"))
    return _resultat(
        numero=_texte(racine, "ID"), date_facture=_date(_texte(racine, "IssueDate")),
        fournisseur=_texte(vendeur, "PartyLegalEntity/RegistrationName") or _texte(vendeur, "PartyName/Name"),
        siren=_premier_siren(*identifiants), ht=ht, tva=tva, ttc=ttc, avoir=avoir,
        objet=_objet(designations, _texte(racine, "Note")), echeance=echeance, devise=devise, designations=designations)


def _resultat(*, numero, date_facture, fournisseur, siren, ht, tva, ttc, avoir, objet, echeance, devise, designations):
    if avoir:  # un avoir diminue la dépense : montants négatifs
        ht, tva, ttc = (-abs(m) if m is not None else None for m in (ht, tva, ttc))
    return {"numero": numero[:50], "date": date_facture, "fournisseur": fournisseur[:150], "siren": siren,
            "montant_ht": ht, "montant_tva": tva, "montant_ttc": ttc, "avoir": avoir, "objet": objet,
            "echeance": echeance, "devise": devise, "designations": designations}


def lire_xml(donnees):
    """(format, données) d'un XML UBL ou CII ; FactureIllisible si ce n'est ni l'un ni l'autre."""
    racine = _analyser_xml(donnees)
    if racine.tag == "CrossIndustryInvoice":
        return "cii", lire_cii(racine)
    if racine.tag in ("Invoice", "CreditNote"):
        return "ubl", lire_ubl(racine)
    raise FactureIllisible("ce XML n'est ni une facture UBL ni une facture CII")


def xml_joint(fichier):
    """Contenu du XML joint à un PDF Factur-X (None si le PDF n'en contient pas)."""
    try:
        from pypdf import PdfReader

        fichier.seek(0)
        pieces = PdfReader(fichier).attachments
    except Exception:
        return None
    finally:
        try:
            fichier.seek(0)
        except Exception:
            pass
    noms = sorted((n for n in pieces if n.lower().endswith(".xml")),
                  key=lambda n: (n.lower() not in NOMS_XML_FACTURX, n.lower()))
    for nom in noms:
        contenu = pieces[nom]
        contenu = contenu[0] if isinstance(contenu, list) else contenu
        if contenu:
            return contenu
    return None


def lire(fichier, nom):
    """Facture électronique lue dans un fichier déposé : (format, données), ou None si ce n'en est pas une
    (PDF sans XML joint, photo). Lève FactureIllisible pour un XML qui n'est pas une facture."""
    nom = (nom or "").lower()
    if nom.endswith(".xml"):
        fichier.seek(0)
        donnees = fichier.read(TAILLE_MAX + 1)
        fichier.seek(0)
        return lire_xml(donnees)
    if nom.endswith(".pdf"):
        contenu = xml_joint(fichier)
        if not contenu:
            return None
        try:
            fmt, donnees = lire_xml(contenu)
        except FactureIllisible:
            return None  # PDF ordinaire avec une pièce jointe XML quelconque : lu comme un PDF classique
        return ("facturx" if fmt == "cii" else fmt), donnees
    return None


def texte_pour_codifier(donnees):
    """Texte (fournisseur, objet, lignes) passé à la lecture des mots-clés pour proposer la ligne ou la rubrique."""
    return "\n".join(filter(None, [donnees["fournisseur"], donnees["objet"], *donnees["designations"]])).ljust(20)
