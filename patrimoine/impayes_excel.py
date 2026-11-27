"""Compléter les loyers impayés depuis Excel.

1. `generer()` : un classeur avec une ligne par loyer encore dû (échu avant aujourd'hui), plus les mois d'un bail qui
   n'ont aucun loyer dans l'appli (trou entre deux loyers, ex. une année jamais reprise). Colonnes à remplir : date du
   règlement, montant reçu, mode. Quand l'ancien suivi (historique) a un encaissement ce mois-là, il est pré-rempli.
2. `lire()` : vérifie le fichier rempli ligne par ligne, sans rien enregistrer (aperçu).
3. `enregistrer()` : chaque ligne devient un vrai paiement reçu, qui règle d'abord son loyer puis le plus ancien encore
   dû (comme « Encaisser ») ; la ligne de l'ancien suivi de ce mois-là est retirée (le paiement la remplace).

Règle de base : seul un règlement reçu rend un loyer payé. Ce fichier ne fait que créer des paiements reçus."""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.db import transaction

from .import_excel import Colonne, _lire, _texte, _ValeurInvalide

ONGLET = "Impayés"
PREMIERE_LIGNE = 3
NOTE = "Complété depuis le fichier des impayés"
REF = re.compile(r"^(?:L(\d+)|B(\d+)-(\d{4})-(\d{2}))$")


def _modes():
    from .models import ModePaiement

    return list(ModePaiement.choices)


def colonnes():
    return [
        Colonne("ref", "Réf.", aide="Ne pas modifier", largeur=14),
        Colonne("logement", "Logement", aide="Bien – lot", largeur=30),
        Colonne("locataire", "Locataire", largeur=26),
        Colonne("mois", "Mois du loyer", aide="mm/aaaa", largeur=12),
        Colonne("du", "Reste dû (€)", sorte="montant", largeur=12),
        Colonne("date", "Date du règlement", True, "jj/mm/aaaa", sorte="date", largeur=16),
        Colonne("montant", "Montant reçu (€)", True, "ex. 650 ou 650,50", sorte="montant", largeur=15),
        Colonne("mode", "Mode", aide="Liste : Virement par défaut", sorte="choix", choix=_modes(), largeur=16),
        Colonne("note", "Note", aide="Facultatif", largeur=28),
    ]


def _mois_suivant(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _nom_logement(bail):
    return f"{bail.lot.bien.nom} – {bail.lot.designation}"


def _noms(bail):
    return ", ".join(str(l) for l in bail.locataires.all())


def lignes_a_completer(aujourdhui=None):
    """[(réf, bail, mois, reste dû, historique ou None)], par logement puis par mois."""
    from .models import Bail, Echeance, EncaissementHistorique

    aujourdhui = aujourdhui or date.today()
    limite = aujourdhui.replace(day=1)
    historique = {(h.lot_id, h.mois.year, h.mois.month): h for h in EncaissementHistorique.objects.all()}
    lignes = []
    dus = (Echeance.objects.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=aujourdhui)
           .select_related("bail__lot__bien").prefetch_related("paiements"))
    for e in dus:
        reste = e.reste_du
        if reste > 0:
            h = historique.get((e.bail.lot_id, e.periode.year, e.periode.month)) if e.nature == Echeance.Nature.LOYER else None
            lignes.append((f"L{e.pk}", e.bail, e.periode, reste, h))
    # mois sans aucun loyer entre le premier loyer du bail et sa fin (ou le mois dernier)
    for bail in Bail.objects.select_related("lot__bien").prefetch_related("locataires"):
        periodes = set(bail.echeances.filter(nature=Echeance.Nature.LOYER).values_list("periode", flat=True))
        if not periodes:
            continue
        mois, fin = min(periodes), limite
        if bail.date_fin:
            fin = min(fin, bail.date_fin.replace(day=1))
        while mois <= fin:
            if mois not in periodes and mois < limite:
                loyer, charges = bail.montants_du_mois(mois)
                if loyer + charges > 0:
                    lignes.append((f"B{bail.pk}-{mois:%Y-%m}", bail, mois, loyer + charges,
                                   historique.get((bail.lot_id, mois.year, mois.month))))
            mois = _mois_suivant(mois)
    lignes.sort(key=lambda l: (_nom_logement(l[1]), l[1].date_debut, l[2]))
    return lignes


def generer(aujourdhui=None):
    """Classeur Excel (octets) des loyers à compléter."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    cols = colonnes()
    orange = PatternFill("solid", fgColor="F7B267")
    gris = PatternFill("solid", fgColor="E4E7EC")
    a_remplir = PatternFill("solid", fgColor="FFF8E6")
    pre_rempli = PatternFill("solid", fgColor="E3F2E1")
    classeur = Workbook()
    f = classeur.active
    f.title = ONGLET
    f.append([c.entete for c in cols])
    f.append([c.aide for c in cols])
    for i, c in enumerate(cols, start=1):
        f.cell(1, i).fill = orange if c.cle in ("date", "montant", "mode", "note") else gris
        f.cell(1, i).font = Font(bold=True)
        f.cell(2, i).font = Font(italic=True, color="667085", size=9)
        f.column_dimensions[get_column_letter(i)].width = c.largeur
    lignes = lignes_a_completer(aujourdhui)
    for ref, bail, mois, du, h in lignes:
        f.append([ref, _nom_logement(bail), _noms(bail), f"{mois:%m/%Y}", float(du),
                  mois if h else None, float(h.montant) if h else None, "Virement" if h else None,
                  "Repris de l'ancien suivi : à vérifier" if h else None])
        n = f.max_row
        f.cell(n, 6).number_format = "DD/MM/YYYY"
        f.cell(n, 5).number_format = f.cell(n, 7).number_format = '#,##0.00 "€"'
        for c in (6, 7, 8, 9):
            f.cell(n, c).fill = pre_rempli if h else a_remplir
    f.freeze_panes = "B3"
    if lignes:
        modes = DataValidation(type="list", formula1='"' + ",".join(l for _, l in _modes()) + '"', allow_blank=True)
        f.add_data_validation(modes)
        modes.add(f"H{PREMIERE_LIGNE}:H{f.max_row}")
    aide = classeur.create_sheet("Mode d'emploi")
    for texte in [
        "Compléter les loyers impayés",
        "",
        "1. Une ligne = un loyer encore dû (ou un mois sans loyer dans l'appli). Ne modifiez pas la colonne « Réf. ».",
        "2. Pour chaque loyer réellement payé, remplissez la date du règlement et le montant reçu (le mode est Virement si vide).",
        "3. Laissez vides les lignes qui n'ont pas été payées : elles restent impayées.",
        "4. Les lignes en vert sont pré-remplies avec votre ancien suivi : vérifiez-les, corrigez ou videz-les.",
        "5. Enregistrez, puis importez le fichier dans l'appli (Relances d'impayés › Compléter avec Excel).",
        "",
        "Avant d'enregistrer, l'appli montre un aperçu : rien n'est créé tant que vous n'avez pas validé.",
        "Chaque ligne devient un paiement reçu, visible dans « Paiements reçus » avec la note « " + NOTE + " ».",
        "Un montant supérieur au loyer règle aussi le plus ancien loyer encore dû ; ce qui reste est gardé en avance.",
    ]:
        aide.append([texte])
    aide["A1"].font = Font(bold=True, size=14)
    aide.column_dimensions["A"].width = 120
    for ligne in aide.iter_rows(min_row=3):
        ligne[0].alignment = Alignment(wrap_text=True)
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


@dataclass
class Ligne:
    n: int
    ref: str
    date: date
    montant: Decimal
    mode: str
    note: str
    bail: object = None
    mois: date = None
    echeance: object = None
    du: Decimal = Decimal(0)
    ignoree: str = ""

    @property
    def logement(self):
        return _nom_logement(self.bail)

    @property
    def locataires(self):
        return _noms(self.bail)

    @property
    def mode_libelle(self):
        return dict(_modes()).get(self.mode, self.mode)

    @property
    def resultat(self):
        if self.ignoree:
            return self.ignoree
        if self.montant >= self.du:
            return "Payé" + (f" (+ {self.montant - self.du:.2f} € sur le plus ancien encore dû ou en avance)".replace(".", ",")
                             if self.montant > self.du else "")
        return f"Partiel : reste {self.du - self.montant:.2f} € dû".replace(".", ",")

    def en_session(self):
        return {"n": self.n, "ref": self.ref, "date": self.date.isoformat(), "montant": str(self.montant), "mode": self.mode,
                "note": self.note}


@dataclass
class Lecture:
    lignes: list = field(default_factory=list)
    erreurs: list = field(default_factory=list)  # (ligne, message)

    @property
    def a_enregistrer(self):
        return [l for l in self.lignes if not l.ignoree]

    @property
    def total(self):
        return sum((l.montant for l in self.a_enregistrer), Decimal(0))


def _resoudre(ligne, aujourdhui):
    """Retrouve le loyer (ou le mois sans loyer) d'une réf. ; message d'erreur ou None."""
    from .models import Bail, Echeance

    m = REF.match(ligne.ref)
    if not m:
        return f"réf. « {ligne.ref} » inconnue : ne modifiez pas la colonne Réf."
    if m.group(1):
        e = Echeance.objects.filter(pk=m.group(1)).select_related("bail__lot__bien").first()
        if e is None:
            return f"le loyer {ligne.ref} n'existe plus : téléchargez un nouveau fichier"
        ligne.bail, ligne.mois, ligne.echeance = e.bail, e.periode, e
    else:
        bail = Bail.objects.filter(pk=m.group(2)).select_related("lot__bien").first()
        if bail is None:
            return f"le bail de la réf. {ligne.ref} n'existe plus : téléchargez un nouveau fichier"
        try:
            ligne.mois = date(int(m.group(3)), int(m.group(4)), 1)
        except ValueError:
            return f"réf. « {ligne.ref} » illisible"
        ligne.bail = bail
        ligne.echeance = bail.echeances.filter(periode=ligne.mois, nature=Echeance.Nature.LOYER).first()
    if ligne.echeance:
        ligne.du = ligne.echeance.reste_du
        if ligne.du <= 0:
            ligne.ignoree = "Déjà payé : ignoré"
    else:
        loyer, charges = ligne.bail.montants_du_mois(ligne.mois)
        ligne.du = loyer + charges
    if ligne.date > aujourdhui:
        return "la date du règlement est dans le futur"
    return None


def lire(fichier, aujourdhui=None):
    """Lit le fichier rempli : lignes valides et erreurs (ligne Excel, message). N'enregistre rien."""
    from openpyxl import load_workbook

    aujourdhui = aujourdhui or date.today()
    res = Lecture()
    try:
        classeur = load_workbook(fichier, data_only=True, read_only=True)
    except Exception:
        res.erreurs.append((None, "ce fichier n'est pas un classeur Excel (.xlsx) lisible"))
        return res
    feuille = classeur[ONGLET] if ONGLET in classeur.sheetnames else classeur.worksheets[0]
    cols = {c.cle: (i, c) for i, c in enumerate(colonnes())}
    vus = set()
    for n, valeurs in enumerate(feuille.iter_rows(min_row=PREMIERE_LIGNE, values_only=True), start=PREMIERE_LIGNE):
        valeurs = list(valeurs) + [None] * (len(cols) - len(valeurs))
        brut = {cle: valeurs[i] for cle, (i, _) in cols.items()}
        if brut["date"] in (None, "") and brut["montant"] in (None, ""):
            continue  # pas payé : la ligne reste impayée
        lu, erreur = {}, None
        for cle in ("date", "montant", "mode"):
            try:
                lu[cle] = _lire(cols[cle][1], brut[cle])
            except _ValeurInvalide as ex:
                erreur = f"{cols[cle][1].titre} : {ex}"
                break
        if erreur is None and lu["date"] is None:
            erreur = "date du règlement manquante"
        if erreur is None and not lu["montant"]:
            erreur = "montant reçu manquant"
        ref = _texte(brut["ref"])
        if erreur is None and ref in vus:
            erreur = f"la réf. {ref} apparaît deux fois"
        if erreur:
            res.erreurs.append((n, erreur))
            continue
        vus.add(ref)
        ligne = Ligne(n=n, ref=ref, date=lu["date"], montant=lu["montant"], mode=lu["mode"] or "virement",
                      note=_texte(brut["note"])[:255])
        erreur = _resoudre(ligne, aujourdhui)
        if erreur:
            res.erreurs.append((n, erreur))
        else:
            res.lignes.append(ligne)
    return res


def depuis_session(donnees, aujourdhui=None):
    """Reconstruit la lecture gardée entre l'aperçu et la validation (revérifiée : la base a pu changer)."""
    aujourdhui = aujourdhui or date.today()
    res = Lecture()
    for d in donnees:
        ligne = Ligne(n=d["n"], ref=d["ref"], date=date.fromisoformat(d["date"]), montant=Decimal(d["montant"]),
                      mode=d["mode"], note=d["note"])
        erreur = _resoudre(ligne, aujourdhui)
        if erreur:
            res.erreurs.append((ligne.n, erreur))
        else:
            res.lignes.append(ligne)
    return res


@transaction.atomic
def enregistrer(lecture):
    """Crée un paiement reçu par ligne. Renvoie le nombre de paiements créés."""
    from .models import EncaissementHistorique, Versement
    from .reprise_historique import _echeance

    n = 0
    for ligne in lecture.a_enregistrer:
        e = ligne.echeance or _echeance(ligne.bail, ligne.mois)
        e.refresh_from_db()
        v = Versement.objects.create(bail=ligne.bail, date=ligne.date, montant=ligne.montant, mode=ligne.mode,
                                     notes=(f"{NOTE} – {ligne.note}" if ligne.note else NOTE)[:255])
        v.repartir(Versement.repartition_proposee(ligne.bail, ligne.montant, priorite=e))
        # l'ancien suivi de ce mois est remplacé par le paiement (sinon les états le compteraient en plus)
        EncaissementHistorique.objects.filter(lot=ligne.bail.lot, mois__year=ligne.mois.year, mois__month=ligne.mois.month).delete()
        n += 1
    return n
