"""Deux façons d'encaisser : « Encaisser le mois » (saisie) et « Relevé de banque » (import, rapprochement).
Toutes les données ci-dessous sont fictives."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from . import rapprochement, releves
from .models import (Bail, Bien, CompteBancaire, Echeance, LigneReleve, Locataire, Lot, PieceComptable, Pret,
                     Proprietaire, RegleRapprochement, Versement)

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.fr", "x"))
        self.proprio = Proprietaire.objects.create(nom="SCI Lilas", type=Proprietaire.Type.SCI_IR)
        self.bien = Bien.objects.create(proprietaire=self.proprio, nom="Les Lilas", adresse="1 rue", code_postal="69000", ville="Lyon")
        self.bail = self._bail("Apt 1er", "Durand", D("800"), D("40"), 5)
        self.garage = self._bail("Garage", "Johannel", D("65"), D("0"), 1)
        for m in (10, 11):
            Echeance.generer_pour_mois(date(2026, m, 1))
        self.compte = CompteBancaire.objects.create(proprietaire=self.proprio, nom="Compte SCI", banque="Banque fictive",
                                                    mode=CompteBancaire.Mode.RELEVE, associes="Martin Paul")

    def _bail(self, lot, nom, loyer, charges, jour):
        b = Bail.objects.create(lot=Lot.objects.create(bien=self.bien, designation=lot), type=Bail.Type.NU, date_debut=date(2026, 1, 1),
                                loyer_hc=loyer, charges=charges, jour_echeance=jour)
        b.locataires.add(Locataire.objects.create(nom=nom, prenom="Jean"))
        return b

    def ligne(self, libelle, montant, jour=date(2026, 11, 8), compte=None):
        compte = compte or self.compte
        return LigneReleve.objects.create(compte=compte, date=jour, libelle=libelle, montant=D(montant),
                                          empreinte=LigneReleve.calculer_empreinte(compte.pk, jour, D(montant), libelle, LigneReleve.objects.count()))

    def proposer(self, ligne):
        return rapprochement.proposer(ligne, rapprochement.Contexte(self.compte))

    def echeance(self, bail, mois):
        return Echeance.objects.get(bail=bail, periode=date(2026, mois, 1))


class EncaisserLeMoisTests(Base):
    URL = "/loyers/encaisser-le-mois/"

    def test_recu_un_clic_date_du_jour_d_echeance(self):
        oct_ = self.echeance(self.bail, 10)
        r = self.client.get(self.URL + "?mois=2026-10")
        self.assertContains(r, "Encaisser octobre 2026")
        self.assertContains(r, "✓ Reçu")
        self.client.post(self.URL + "?mois=2026-10", {"un": oct_.pk})
        v = Versement.objects.get()
        self.assertEqual(v.date, date(2026, 10, 5))  # pas la date de la saisie
        self.assertEqual(v.montant, D("840"))
        oct_.refresh_from_db()
        self.assertEqual(oct_.statut, Echeance.Statut.PAYE)
        r = self.client.get(self.URL + "?mois=2026-10")
        self.assertContains(r, "Reçu le 05/10")

    def test_cocher_plusieurs_loyers(self):
        ids = [self.echeance(self.bail, 11).pk, self.echeance(self.garage, 11).pk]
        self.client.post(self.URL + "?mois=2026-11", {"tous": "1", "coches": ids})
        self.assertEqual(Versement.objects.count(), 2)
        self.assertEqual(set(Versement.objects.values_list("date", flat=True)), {date(2026, 11, 5), date(2026, 11, 1)})

    def test_autre_montant_formulaire_date_d_echeance(self):
        from .admin.loyers import url_encaisser

        self.assertIn("date=2026-10-05", url_encaisser(self.echeance(self.bail, 10)))

    def test_menu_avec_aides(self):
        r = self.client.get(self.URL)
        self.assertContains(r, 'class="menu-aide"', count=5)
        self.assertContains(r, "Mode saisie : un mois à l&#x27;écran")

    def test_releve_accessible_depuis_l_accueil_compta(self):
        self.ligne("CB BRICO DEPOT", "-37.50")
        r = self.client.get("/comptabilite/")
        self.assertContains(r, "/loyers/releve-de-banque/")
        self.assertContains(r, "Relevé de banque (1 à traiter)")

    def test_banque_rangee_avec_la_comptabilite(self):
        from .templatetags.tableau_bord import _menu

        groupes = {titre: [l[1] for l in liens] for titre, liens in _menu()}
        self.assertIn("Banque", groupes["Comptabilité"])
        loyers = next(l for titre, liens in _menu() for l in liens if l[1] == "Loyers")
        self.assertEqual([s[0] for s in loyers[4]][:2], ["Encaisser le mois", "Paiements reçus"])
        r = self.client.get("/loyers/releve-de-banque/")
        self.assertContains(r, "Comptabilité</a> › Banque › Relevé de banque")


class LectureRelevesTests(TestCase):
    def test_ofx(self):
        ofx = b"""OFXHEADER:100\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261103<TRNAMT>130.00<FITID>1<NAME>VIR SEPA JOHANNEL<MEMO>LOYER OCT NOV</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261112<TRNAMT>-86.40<FITID>2<NAME>PRLV EDF</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>1043.60<DTASOF>20261130</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
        r = releves.lire("releve.ofx", ofx)
        self.assertEqual([(l.date, l.montant) for l in r.lignes], [(date(2026, 11, 3), D("130.00")), (date(2026, 11, 12), D("-86.40"))])
        self.assertEqual(r.lignes[0].detail, "LOYER OCT NOV")
        self.assertEqual(r.solde_fin, D("1043.60"))

    def test_csv_debit_credit_avec_titre(self):
        csv = "Compte courant;fictif\n\nDate;Libellé;Débit euros;Crédit euros\n03/11/2026;VIR JOHANNEL;;130,00\n12/11/2026;PRLV EDF;86,40;\n"
        r = releves.lire("export.csv", csv.encode("cp1252"))
        self.assertEqual([(l.libelle, l.montant) for l in r.lignes], [("VIR JOHANNEL", D("130.00")), ("PRLV EDF", D("-86.40"))])

    def test_csv_montant_signe(self):
        r = releves.lire("x.csv", b"date,label,amount\n2026-11-03,VIR DURAND,\"1 234,56\"\n2026-11-04,CB,-12.5\n")
        self.assertEqual([l.montant for l in r.lignes], [D("1234.56"), D("-12.5")])

    def test_camt053_et_controle_des_soldes(self):
        xml = b"""<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>
<Acct><Id><IBAN>FR7600000000000</IBAN></Id></Acct>
<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1043.60</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
<Ntry><Amt Ccy="EUR">130.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>2026-11-03</Dt></BookgDt><AddtlNtryInf>VIR JOHANNEL</AddtlNtryInf></Ntry>
<Ntry><Amt Ccy="EUR">86.40</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>2026-11-12</Dt></BookgDt><AddtlNtryInf>PRLV EDF</AddtlNtryInf></Ntry>
</Stmt></BkToCstmrStmt></Document>"""
        r = releves.lire("camt.xml", xml)
        self.assertEqual([l.montant for l in r.lignes], [D("130.00"), D("-86.40")])
        self.assertEqual(r.controle[0], True)

    def test_pdf_texte_deux_comptes_garde_celui_de_l_iban(self):
        from weasyprint import HTML

        html = """<pre style="font-size:9px">
COMPTE COURANT N° 11112222333 44
SOLDE CREDITEUR AU 31/12/2025  + 500,00
- 10,0002/01/2026 01/01/2026 COMMISSIONS
06/01/2026 06/01/2026 PRLV FOURNISSEUR ENERGIE - 50,00
Prelevement fournisseur energie
10/01/2026 10/01/2026 VIR DURAND LOYER + 840,00
SOLDE CREDITEUR AU 31/01/2026   + 1 280,00
COMPTE COURANT N° 55556666777 88
SOLDE CREDITEUR AU 31/12/2025  + 2 000,00
+ 1,0002/01/2026 01/01/2026 INTERETS CREDITEURS
SOLDE CREDITEUR AU 31/01/2026   + 2 001,00
</pre>"""
        pdf = HTML(string=html).write_pdf()
        with self.assertRaisesMessage(releves.ReleveIllisible, "2 comptes"):
            releves.lire("r.pdf", pdf)
        r = releves.lire("r.pdf", pdf, "99999000011112222333449")
        self.assertEqual([(l.date, l.montant) for l in r.lignes],
                         [(date(2026, 1, 2), D("-10.00")), (date(2026, 1, 6), D("-50.00")), (date(2026, 1, 10), D("840.00"))])
        self.assertIn("Prelevement fournisseur energie", r.lignes[1].detail)
        self.assertEqual(r.controle[0], True)

    def test_pdf_sans_texte(self):
        from weasyprint import HTML

        with self.assertRaisesMessage(releves.ReleveIllisible, "scanné"):
            releves.lire("scan.pdf", HTML(string="<p></p>").write_pdf())


class ImportTests(Base):
    CSV = b"Date;Libelle;Montant\n03/11/2026;VIR JOHANNEL;130,00\n03/11/2026;VIR JOHANNEL;130,00\n12/11/2026;PRLV EDF;-86,40\n"

    def test_reimport_sans_doublon_mais_lignes_identiques_gardees(self):
        imp, _ = rapprochement.importer(self.compte, "a.csv", self.CSV)
        self.assertEqual((imp.nb_lignes, imp.nb_nouvelles), (3, 3))  # deux virements identiques le même jour : deux lignes
        imp, _ = rapprochement.importer(self.compte, "a.csv", self.CSV)
        self.assertEqual(imp.nb_nouvelles, 0)
        self.assertEqual(LigneReleve.objects.count(), 3)

    def test_page_import(self):
        r = self.client.post(f"/loyers/releve-de-banque/?compte={self.compte.pk}",
                             {"action": "importer", "fichier": SimpleUploadedFile("a.csv", self.CSV)}, follow=True)
        self.assertContains(r, "3 ligne(s) lue(s), 3 nouvelle(s)")
        r = self.client.post(f"/loyers/releve-de-banque/?compte={self.compte.pk}",
                             {"action": "importer", "fichier": SimpleUploadedFile("a.pdf", b"%PDF-1.4 rien")}, follow=True)
        self.assertContains(r, "Relevé non importé")


class RapprochementLoyersTests(Base):
    def test_nom_et_montant_exact_sur(self):
        p = self.proposer(self.ligne("VIR SEPA M DURAND JEAN", "840"))
        self.assertEqual((p.nature, p.certitude), ("loyer", rapprochement.SURE))
        self.assertEqual(p.parts[0][0], self.echeance(self.bail, 11))

    def test_130_sur_deux_mois(self):
        l = self.ligne("VIR JOHANNEL K LOYER GARAGE", "130")
        p = self.proposer(l)
        self.assertEqual(p.certitude, rapprochement.VERIFIER)
        self.assertEqual([e.periode.month for e, _ in p.parts], [10, 11])
        rapprochement.appliquer(l, p)
        v = Versement.objects.get()
        self.assertEqual((v.date, v.montant), (date(2026, 11, 8), D("130")))  # date du relevé
        self.assertTrue(all(e.statut == Echeance.Statut.PAYE for e in Echeance.objects.filter(bail=self.garage)))

    def test_partiel_et_caf(self):
        p = self.proposer(self.ligne("VIR CAF DU RHONE APL DURAND", "300"))
        self.assertEqual(p.mode, "caf")
        self.assertIn("partiel", p.titre)
        self.assertEqual(p.certitude, rapprochement.VERIFIER)

    def test_montant_seul_sans_nom_a_verifier(self):
        p = self.proposer(self.ligne("VIR INST 4587", "65"))
        self.assertEqual((p.bail, p.certitude), (self.garage, rapprochement.VERIFIER))

    def test_paiement_deja_saisi_relie_sans_doublon(self):
        v = Versement.objects.create(bail=self.bail, date=date(2026, 11, 5), montant=D("840"))
        v.repartir([(self.echeance(self.bail, 11), D("840"))])
        l = self.ligne("VIR DURAND", "840")
        p = self.proposer(l)
        self.assertEqual(p.versement, v)
        rapprochement.appliquer(l, p)
        self.assertEqual(Versement.objects.count(), 1)
        rapprochement.derapprocher(LigneReleve.objects.get())
        self.assertTrue(Versement.objects.filter(pk=v.pk).exists())  # dérapprocher ne supprime pas un paiement saisi à la main

    def test_derapprocher_supprime_le_paiement_cree(self):
        l = self.ligne("VIR DURAND", "840")
        rapprochement.appliquer(l, self.proposer(l))
        rapprochement.derapprocher(LigneReleve.objects.get())
        self.assertFalse(Versement.objects.exists())
        self.assertEqual(self.echeance(self.bail, 11).statut, Echeance.Statut.A_PAYER)
        self.assertEqual(LigneReleve.objects.get().statut, LigneReleve.Statut.A_TRAITER)

    def test_rejet_annule_le_paiement(self):
        l = self.ligne("VIR DURAND", "840")
        rapprochement.appliquer(l, self.proposer(l))
        rejet = self.ligne("REJET PRLV DURAND", "-840", date(2026, 11, 12))
        p = self.proposer(rejet)
        self.assertTrue(p.rejet)
        rapprochement.appliquer(rejet, p)
        self.assertTrue(Versement.objects.get().annule)
        self.assertEqual(self.echeance(self.bail, 11).statut, Echeance.Statut.A_PAYER)

    def test_valider_les_lignes_sures_en_un_clic(self):
        self.ligne("VIR DURAND", "840")
        self.ligne("VIR INCONNU", "12")
        r = self.client.post(f"/loyers/releve-de-banque/?compte={self.compte.pk}", {"action": "valider_sures"}, follow=True)
        self.assertContains(r, "1 ligne(s) sûre(s) validée(s)")
        self.assertEqual(LigneReleve.objects.filter(statut="rapprochee").count(), 1)

    def test_page_affiche_les_propositions(self):
        self.ligne("VIR JOHANNEL", "130")
        r = self.client.get(f"/loyers/releve-de-banque/?compte={self.compte.pk}&onglet=a_verifier")
        self.assertContains(r, "Loyers 10/2026 + 11/2026")
        self.assertContains(r, "Trésorerie (2072)")


class RapprochementDepensesTests(Base):
    def test_echeance_de_pret(self):
        pret = Pret.objects.create(proprietaire=self.proprio, bien=self.bien, banque="Banque", reference="P123XY", montant=D("10000"),
                                   taux=D("2"), duree_mois=120, date_premiere_echeance=date(2026, 1, 5))
        p = self.proposer(self.ligne("ECH PRET P123XY DU 05/11/26", "-92.01"))
        self.assertEqual((p.nature, p.pret, p.certitude), ("pret", pret, rapprochement.SURE))
        e = pret.echeancier()[10]
        montant = -(e["mensualite"] + e["assurance"])
        p = self.proposer(self.ligne("PRLV ECHEANCE", str(montant), date(2026, 11, 6)))
        self.assertEqual(p.pret, pret)

    def test_facture_du_meme_montant_payee_a_la_date_du_releve(self):
        f = PieceComptable.objects.create(bien=self.bien, date=date(2026, 10, 28), fournisseur="EDF", montant=D("86.40"), ligne="6")
        l = self.ligne("PRLV SEPA EDF FACT 1045", "-86.40", date(2026, 11, 12))
        p = self.proposer(l)
        self.assertEqual((p.piece, p.certitude), (f, rapprochement.SURE))
        rapprochement.appliquer(l, p)
        f.refresh_from_db()
        self.assertEqual(f.date_paiement, date(2026, 11, 12))
        rapprochement.derapprocher(LigneReleve.objects.get())
        f.refresh_from_db()
        self.assertIsNone(f.date_paiement)

    def test_frais_bancaires_cree_une_depense(self):
        l = self.ligne("COMMISSIONS", "-12.30")
        p = self.proposer(l)
        self.assertEqual((p.nature, p.bien), ("depense", self.bien))
        rapprochement.appliquer(l, p)
        piece = PieceComptable.objects.get()
        self.assertEqual((piece.montant, piece.date_paiement, piece.ligne), (D("12.30"), date(2026, 11, 8), "6"))

    def test_rien_trouve(self):
        self.assertEqual(self.proposer(self.ligne("CB MAGASIN BRICOLAGE", "-54.90")).certitude, rapprochement.AUCUNE)



FACTURE_FICTIVE = ("PLOMBERIE DUPONT SARL\n12 rue des Artisans 69000 Lyon\nFacture n° 2026-118 du 02/11/2026\n"
                   "Remplacement chauffe-eau, travaux de réparation\nTotal TTC 154,00 €")


class JustificatifTests(Base):
    """📎 Joindre la pièce sur une sortie d'argent du relevé."""
    URL = "/loyers/releve-de-banque/"

    def setUp(self):
        super().setUp()
        import tempfile

        from django.test import override_settings

        self._dossier = tempfile.TemporaryDirectory()
        self._reglage = override_settings(PRIVE_ROOT=self._dossier.name)
        self._reglage.enable()

    def tearDown(self):
        self._reglage.disable()
        self._dossier.cleanup()
        super().tearDown()

    def pdf(self, contenu=b"%PDF-1.4 fictif"):
        return SimpleUploadedFile("facture-plombier.pdf", contenu, content_type="application/pdf")

    def joindre(self, ligne, fichier, **extra):
        return self.client.post(f"{self.URL}?compte={self.compte.pk}", {"action": "joindre", "ligne": ligne.pk, "piece": fichier, **extra},
                                follow=True)

    def test_ligne_non_reconnue_cree_la_depense_avec_la_piece_lue_dans_le_pdf(self):
        l = self.ligne("CB PLOMBERIE 0211", "-154.00", date(2026, 11, 9))
        with mock.patch("patrimoine.lecture_factures.texte_pdf", return_value=FACTURE_FICTIVE):
            r = self.joindre(l, self.pdf())
        self.assertContains(r, "Justificatif joint")
        piece = PieceComptable.objects.get()
        self.assertEqual((piece.montant, piece.fournisseur, piece.ligne), (D("154.00"), "PLOMBERIE DUPONT SARL", "9"))
        self.assertEqual((piece.date, piece.date_paiement), (date(2026, 11, 2), date(2026, 11, 9)))  # facture / paiement
        self.assertTrue(piece.fichier and piece.empreinte)
        l.refresh_from_db()
        self.assertEqual((l.statut, l.nature, l.piece, l.cree), ("rapprochee", "depense", piece, True))

    def test_photo_sans_lecture_garde_le_libelle_du_releve(self):
        l = self.ligne("CB BRICO DEPOT", "-37.50")
        self.joindre(l, SimpleUploadedFile("ticket.jpg", b"\xff\xd8 photo fictive", content_type="image/jpeg"))
        piece = PieceComptable.objects.get()
        self.assertEqual((piece.montant, piece.fournisseur, piece.date), (D("37.50"), "CB BRICO DEPOT", date(2026, 11, 8)))
        self.assertTrue(piece.fichier.name.endswith("ticket.jpg"))

    def test_ligne_deja_rapprochee_le_fichier_va_dans_sa_facture(self):
        f = PieceComptable.objects.create(bien=self.bien, date=date(2026, 10, 28), fournisseur="EDF", montant=D("86.40"), ligne="6")
        l = self.ligne("PRLV SEPA EDF FACT 1045", "-86.40", date(2026, 11, 12))
        rapprochement.appliquer(l, self.proposer(l))
        r = self.client.get(f"{self.URL}?compte={self.compte.pk}&onglet=faites")
        self.assertContains(r, "Sans justificatif")
        self.assertContains(r, "1 sortie sans justificatif")
        self.assertContains(r, "📎 Joindre la pièce")
        self.joindre(l, self.pdf())
        f.refresh_from_db()
        self.assertTrue(f.fichier)
        self.assertEqual(PieceComptable.objects.count(), 1)
        r = self.client.get(f"{self.URL}?compte={self.compte.pk}&onglet=faites")
        self.assertNotContains(r, "Sans justificatif")
        self.assertContains(r, "📄 pièce")

    def test_facture_proposee_rapprochee_avec_la_piece(self):
        f = PieceComptable.objects.create(bien=self.bien, date=date(2026, 10, 28), fournisseur="EDF", montant=D("86.40"), ligne="6")
        l = self.ligne("PRLV SEPA EDF FACT 1045", "-86.40", date(2026, 11, 12))
        self.joindre(l, self.pdf())
        f.refresh_from_db()
        l.refresh_from_db()
        self.assertEqual((l.piece, l.nature, f.date_paiement), (f, "facture", date(2026, 11, 12)))
        self.assertTrue(f.fichier)

    def test_meme_fichier_deja_importe_relie_sans_doublon(self):
        contenu = b"%PDF-1.4 facture deja importee"
        f = PieceComptable.objects.create(bien=self.bien, date=date(2026, 11, 2), fournisseur="Plombier", montant=D("154"), ligne="9",
                                          fichier=self.pdf(contenu))
        l = self.ligne("CB PLOMBERIE 0211", "-154.00")
        self.joindre(l, self.pdf(contenu))
        l.refresh_from_db()
        self.assertEqual((PieceComptable.objects.count(), l.piece, l.nature), (1, f, "facture"))

    def test_plusieurs_biens_il_faut_choisir_le_bien(self):
        autre = Bien.objects.create(proprietaire=self.proprio, nom="Les Roses", adresse="2 rue", code_postal="69000", ville="Lyon")
        l = self.ligne("CB BRICO DEPOT", "-37.50")
        r = self.joindre(l, self.pdf())
        self.assertContains(r, "choisissez le bien")
        self.assertFalse(PieceComptable.objects.exists())
        self.joindre(l, self.pdf(), bien=autre.pk)
        self.assertEqual(PieceComptable.objects.get().bien, autre)

    def test_pas_de_trombone_sur_les_entrees_ni_les_prets(self):
        entree = self.ligne("VIR DURAND JEAN", "840.00")
        self.assertFalse(rapprochement.peut_joindre(entree))
        r = self.joindre(entree, self.pdf())
        self.assertContains(r, "Pièce non jointe")
        pret = Pret.objects.create(proprietaire=self.proprio, bien=self.bien, banque="Banque", reference="P9", montant=D("10000"),
                                   taux=D("2"), duree_mois=120, date_premiere_echeance=date(2026, 1, 5))
        l = self.ligne("ECHEANCE PRET", "-505.90")
        rapprochement.appliquer(l, rapprochement.Proposition("pret", rapprochement.VERIFIER, "Prêt", pret=pret))
        self.assertFalse(rapprochement.peut_joindre(l))

    def test_derapprocher_une_depense_creee_avec_sa_piece(self):
        l = self.ligne("CB BRICO DEPOT", "-37.50")
        self.joindre(l, self.pdf())
        rapprochement.derapprocher(LigneReleve.objects.get())
        self.assertFalse(PieceComptable.objects.exists())


class TresorerieTests(Base):
    def test_apport_et_remboursement_associe_aller_retour(self):
        a = self.ligne("VIR M MARTIN PAUL RENFLOUEMENT", "1000", date(2026, 11, 2))
        b = self.ligne("VIR VERS MARTIN PAUL", "-1000", date(2026, 11, 20))
        for l in (a, b):
            p = self.proposer(l)
            self.assertEqual((p.nature, p.certitude), ("tresorerie", rapprochement.SURE))
            rapprochement.appliquer(l, p)
        self.assertEqual(rapprochement.allers_retours([a, b])[a.pk], b)
        cc = rapprochement.comptes_courants(self.proprio)
        self.assertEqual((cc[0]["apports"], cc[0]["remboursements"], cc[0]["solde"]), (D("1000"), D("1000"), D("0")))
        self.assertFalse(Versement.objects.exists())
        self.assertFalse(PieceComptable.objects.exists())  # ni recette ni charge

    def test_virement_entre_mes_comptes(self):
        autre = CompteBancaire.objects.create(proprietaire=self.proprio, nom="Livret SCI")
        self.ligne("VIR VERS LIVRET", "500", compte=autre)
        p = self.proposer(self.ligne("VIR LIVRET", "-500"))
        self.assertEqual(p.nature, "tresorerie")
        self.assertIn("Livret SCI", p.associe)

    def test_regle_apprise(self):
        l = self.ligne("VIR SEPA K GARAGE", "65")
        r = self.client.post(f"/loyers/releve-de-banque/?compte={self.compte.pk}",
                             {"action": "choisir", "ligne": l.pk, "nature": "loyer", "bail": self.garage.pk, "retenir": "1", "motif": "K GARAGE"})
        self.assertEqual(r.status_code, 302)
        regle = RegleRapprochement.objects.get()
        self.assertEqual((regle.motif, regle.bail), ("K GARAGE", self.garage))
        p = self.proposer(self.ligne("VIR SEPA K GARAGE DEC", "65", date(2026, 12, 2)))
        self.assertEqual((p.bail, p.certitude), (self.garage, rapprochement.SURE))
        self.assertIn("règle apprise", p.pourquoi)


class RegleFiscaleTests(Base):
    def test_trois_logiques(self):
        self.assertEqual(self.compte.logique_fiscale, CompteBancaire.TRESORERIE_2072)
        cas = [(Proprietaire.Type.NOM_PROPRE, "", CompteBancaire.TRESORERIE_2044),
               (Proprietaire.Type.NOM_PROPRE, Bien.RegimeFiscal.LMNP, CompteBancaire.ENGAGEMENT_BIC),
               (Proprietaire.Type.SARL_FAMILLE, "", CompteBancaire.ENGAGEMENT_BIC),
               (Proprietaire.Type.SCI_IS, "", CompteBancaire.ENGAGEMENT_IS)]
        for type_, regime, attendu in cas:
            p = Proprietaire.objects.create(nom=f"P {type_} {regime}", type=type_)
            if regime:
                Bien.objects.create(proprietaire=p, nom="B", adresse="x", code_postal="1", ville="y", regime_fiscal=regime)
            self.assertEqual(CompteBancaire.objects.create(proprietaire=p, nom="c").logique_fiscale, attendu, (type_, regime))

    def test_fin_d_exercice_engagement(self):
        self.proprio.type = Proprietaire.Type.SCI_IS
        self.proprio.save()
        l = self.ligne("VIR DURAND", "840", date(2027, 1, 3))  # loyer reçu en janvier
        rapprochement.appliquer(l, rapprochement._loyer_du_bail(l, self.bail, True))
        PieceComptable.objects.create(bien=self.bien, date=date(2026, 12, 20), date_paiement=date(2027, 1, 10), fournisseur="Plombier",
                                      montant=D("300"), rubrique="autre")
        fin = rapprochement.fin_exercice(self.proprio, 2026)
        self.assertEqual(fin["total_creances"], D("840") * 2 + D("65") * 2)  # rien reçu avant le 31/12
        self.assertEqual(fin["total_dettes"], D("300"))
        with mock.patch("django.utils.timezone.localdate", return_value=date(2027, 2, 1)):
            r = self.client.get(f"/loyers/releve-de-banque/?compte={self.compte.pk}&annee=2026")
        self.assertContains(r, "Au 31/12/2026")
