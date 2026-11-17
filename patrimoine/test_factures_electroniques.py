"""Import des factures électroniques : Factur-X (PDF + XML CII), CII et UBL."""
import io
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import factures_electroniques as fe
from .models import Bien, PieceComptable, Proprietaire

URL = "/admin/patrimoine/piececomptable/import/"

CII = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>urn:cen.eu:en16931:2017</ram:ID>
  </ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>{numero}</ram:ID><ram:TypeCode>{type}</ram:TypeCode>
    <ram:IssueDateTime><udt:DateTimeString format="102">20260314</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem><ram:SpecifiedTradeProduct><ram:Name>Remplacement chauffe-eau</ram:Name>
    </ram:SpecifiedTradeProduct></ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Dupont Plomberie SARL</ram:Name>
        <ram:SpecifiedLegalOrganization><ram:ID schemeID="0002">123456789</ram:ID></ram:SpecifiedLegalOrganization>
        <ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">FR32123456789</ram:ID></ram:SpecifiedTaxRegistration>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty><ram:Name>SCI X</ram:Name></ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><udt:DateTimeString format="102">20260413</udt:DateTimeString>
      </ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>450.00</ram:LineTotalAmount>
        <ram:TaxBasisTotalAmount>450.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="EUR">45.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>495.00</ram:GrandTotalAmount>
        <ram:DuePayableAmount>495.00</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""

UBL = """<?xml version="1.0" encoding="UTF-8"?>
<{racine} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{racine}-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>
  <cbc:ID>{numero}</cbc:ID>
  <cbc:IssueDate>2026-09-30</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyName><cbc:Name>EDF</cbc:Name></cac:PartyName>
    <cac:PartyTaxScheme><cbc:CompanyID>FR03552081317</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>
    <cac:PartyLegalEntity><cbc:RegistrationName>Électricité de France</cbc:RegistrationName>
      <cbc:CompanyID schemeID="0009">55208131766522</cbc:CompanyID></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">12.00</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">60.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">60.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">72.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">72.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:{ligne}><cbc:ID>1</cbc:ID><cac:Item><cbc:Name>Électricité septembre 2026</cbc:Name></cac:Item></cac:{ligne}>
</{racine}>"""


def cii(numero="F-2026-041", type_doc="380"):
    return CII.format(numero=numero, type=type_doc).encode()


def ubl(numero="EDF-0930", avoir=False):
    racine, ligne = ("CreditNote", "CreditNoteLine") if avoir else ("Invoice", "InvoiceLine")
    return UBL.format(racine=racine, ligne=ligne, numero=numero).encode()


def facturx(xml):
    """PDF avec le XML CII en pièce jointe « factur-x.xml », comme un Factur-X."""
    from pypdf import PdfWriter

    ecrivain = PdfWriter()
    ecrivain.add_blank_page(595, 842)
    ecrivain.add_attachment("factur-x.xml", xml)
    sortie = io.BytesIO()
    ecrivain.write(sortie)
    return sortie.getvalue()


class LectureFacturesElectroniquesTests(TestCase):
    def test_cii(self):
        fmt, d = fe.lire_xml(cii())
        self.assertEqual(fmt, "cii")
        self.assertEqual((d["numero"], d["date"], d["fournisseur"], d["siren"]),
                         ("F-2026-041", date(2026, 3, 14), "Dupont Plomberie SARL", "123456789"))
        self.assertEqual((d["montant_ht"], d["montant_tva"], d["montant_ttc"]), (Decimal("450.00"), Decimal("45.00"), Decimal("495.00")))
        self.assertEqual((d["objet"], d["echeance"], d["avoir"]), ("Remplacement chauffe-eau", date(2026, 4, 13), False))

    def test_avoir_cii_en_negatif(self):
        _, d = fe.lire_xml(cii(type_doc="381"))
        self.assertTrue(d["avoir"])
        self.assertEqual(d["montant_ttc"], Decimal("-495.00"))

    def test_ubl_facture_et_avoir(self):
        fmt, d = fe.lire_xml(ubl())
        self.assertEqual(fmt, "ubl")
        self.assertEqual((d["numero"], d["date"], d["fournisseur"], d["siren"]),
                         ("EDF-0930", date(2026, 9, 30), "Électricité de France", "552081317"))  # SIRET → SIREN
        self.assertEqual((d["montant_ht"], d["montant_tva"], d["montant_ttc"]), (Decimal("60.00"), Decimal("12.00"), Decimal("72.00")))
        _, d = fe.lire_xml(ubl(avoir=True))
        self.assertEqual((d["avoir"], d["montant_ttc"], d["objet"]), (True, Decimal("-72.00"), "Électricité septembre 2026"))

    def test_facturx(self):
        fichier = io.BytesIO(facturx(cii()))
        fmt, d = fe.lire(fichier, "facture.pdf")
        self.assertEqual((fmt, d["montant_ttc"]), ("facturx", Decimal("495.00")))

    def test_siren(self):
        self.assertEqual(fe.siren_depuis("FR32 123 456 789"), "123456789")
        self.assertEqual(fe.siren_depuis("123 456 789 00012"), "123456789")
        self.assertEqual(fe.siren_depuis("DE123456789"), "")

    def test_fichiers_refuses_ou_ignores(self):
        with self.assertRaises(fe.FactureIllisible):
            fe.lire_xml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><Invoice>&a;</Invoice>')
        with self.assertRaises(fe.FactureIllisible):
            fe.lire_xml(b"<commande/>")
        with self.assertRaises(fe.FactureIllisible):
            fe.lire_xml(b"pas du xml")
        self.assertIsNone(fe.lire(io.BytesIO(b"%PDF-1.4 rien"), "scan.pdf"))  # PDF ordinaire : lu comme avant
        self.assertIsNone(fe.lire(io.BytesIO(b"\xff\xd8"), "photo.jpg"))


class ImportFacturesElectroniquesTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "x"))
        sci = Proprietaire.objects.create(nom="SCI X", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=sci, nom="Immeuble", adresse="1 rue", code_postal="69000", ville="Lyon")

    def importer(self, *fichiers, bien=None):
        return self.client.post(URL, {"bien": (bien or self.bien).pk, "fichiers": list(fichiers)}, follow=True)

    def test_trois_formats_arrivent_a_codifier(self):
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            self.assertContains(self.client.get(URL), ".xml")
            r = self.importer(SimpleUploadedFile("plombier.pdf", facturx(cii())),
                              SimpleUploadedFile("edf.xml", ubl()),
                              SimpleUploadedFile("autre.xml", cii(numero="F-2026-042")))
            self.assertContains(r, "3 factures électroniques")
            pieces = {p.numero_facture: p for p in PieceComptable.objects.all()}
            self.assertEqual(set(pieces), {"F-2026-041", "EDF-0930", "F-2026-042"})
            plombier = pieces["F-2026-041"]
            self.assertEqual((plombier.format_electronique, plombier.siren_fournisseur, plombier.date, plombier.montant,
                              plombier.montant_ht, plombier.montant_tva, plombier.ligne),
                             ("facturx", "123456789", date(2026, 3, 14), Decimal("495.00"), Decimal("450.00"), Decimal("45.00"), "9"))
            self.assertIn("13/04/2026", plombier.notes)
            self.assertEqual(pieces["EDF-0930"].format_electronique, "ubl")
            self.assertEqual(pieces["F-2026-042"].format_electronique, "cii")
            self.assertTrue(all(p.a_codifier for p in pieces.values()))  # à vérifier dans « À codifier »
            liste = self.client.get("/admin/patrimoine/piececomptable/?a_codifier=oui")
            self.assertContains(liste, "Factur-X")
            self.assertContains(liste, ">XML<")

    def test_meme_facture_en_pdf_puis_en_xml_est_un_doublon(self):
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            self.importer(SimpleUploadedFile("plombier.pdf", facturx(cii())))
            r = self.importer(SimpleUploadedFile("plombier.xml", cii()))
            self.assertEqual(PieceComptable.objects.count(), 1)
            self.assertContains(r, "F-2026-041 déjà importée")

    def test_xml_qui_nest_pas_une_facture(self):
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            r = self.importer(SimpleUploadedFile("commande.xml", b"<commande/>"))
            self.assertEqual(PieceComptable.objects.count(), 0)
            self.assertContains(r, "non reconnu comme facture électronique")

    def test_bien_lmnp_rubrique_proposee(self):
        sci = Proprietaire.objects.create(nom="Fabien", type=Proprietaire.Type.NOM_PROPRE)
        lmnp = Bien.objects.create(proprietaire=sci, nom="Studio", adresse="2 rue", code_postal="69000", ville="Lyon", comptabilite="lmnp")
        with tempfile.TemporaryDirectory() as prive, override_settings(PRIVE_ROOT=prive):
            self.importer(SimpleUploadedFile("edf.xml", ubl()), bien=lmnp)
            self.assertEqual(PieceComptable.objects.get().rubrique, "electricite")
