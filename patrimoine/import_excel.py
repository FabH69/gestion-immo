"""Import de données depuis un modèle Excel : biens, lots, locataires et baux.

1. L'utilisateur télécharge un modèle vierge (`generer_modele`) : un onglet par type de données, colonnes obligatoires
   en orange avec une « * », facultatives en gris, une ligne d'aide sous les titres, des listes déroulantes et un
   onglet « Mode d'emploi ».
2. Il le remplit puis le renvoie (`importer`) : tout est vérifié ligne par ligne AVANT d'enregistrer. S'il y a la
   moindre erreur, rien n'est enregistré et la liste des erreurs (onglet, ligne, colonne) est rendue.

Les données sont écrites dans la base active : la vraie base, ou la base de test en mode test (même code).
Les listes de choix (types de lot, de bail, comptabilité…) sont lues dans les modèles : un choix ajouté plus tard
dans l'appli apparaît tout seul dans le modèle Excel.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Bail, Bien, Locataire, Lot, Proprietaire

LIGNE_TITRES, LIGNE_AIDE, PREMIERE_LIGNE = 1, 2, 3
MAX_LIGNES = 2000  # par onglet
TAILLE_MAX = 5 * 1024 * 1024
OUI = {"oui", "o", "x", "vrai", "1", "true", "yes"}
NON = {"non", "n", "faux", "0", "false", "no", ""}


@dataclass
class Colonne:
    cle: str
    titre: str
    obligatoire: bool = False
    aide: str = ""
    sorte: str = "texte"  # texte, date, montant, entier, oui_non, choix
    choix: list = field(default_factory=list)  # [(valeur enregistrée, libellé affiché)]
    largeur: int = 18

    @property
    def entete(self):
        return f"{self.titre} *" if self.obligatoire else self.titre


def _choix(modele, champ, sans_vide=True):
    return [(v, str(l)) for v, l in modele._meta.get_field(champ).choices if not (sans_vide and v == "")]


def onglets():
    """(nom de l'onglet, explication, colonnes), dans l'ordre d'import."""
    return [
        ("Biens", "Un bien = un immeuble ou une adresse. Il contient un ou plusieurs lots (onglet Lots).", [
            Colonne("nom", "Nom du bien", True, "Nom court, unique. ex. Immeuble Victor Hugo", largeur=26),
            Colonne("proprietaire", "Propriétaire", True, "Vous, une SCI… Créé s'il n'existe pas. ex. SCI Les Tilleuls", largeur=24),
            Colonne("adresse", "Adresse", True, "Numéro et rue. ex. 12 rue Victor Hugo", largeur=28),
            Colonne("code_postal", "Code postal", True, "ex. 69003", largeur=12),
            Colonne("ville", "Ville", True, "ex. Lyon"),
            Colonne("type_proprietaire", "Type de propriétaire", False,
                    "Seulement si le propriétaire est nouveau. Par défaut : Nom propre", "choix", _choix(Proprietaire, "type"), 22),
            Colonne("comptabilite", "Comptabilité", False,
                    "Par défaut : revenus fonciers", "choix", _choix(Bien, "comptabilite", sans_vide=False), 30),
            Colonne("date_acquisition", "Date d'acquisition", False, "jj/mm/aaaa", "date"),
            Colonne("prix_acquisition", "Prix d'acquisition (€)", False, "ex. 185000", "montant"),
            Colonne("type_habitat", "Type d'habitat", False, "", "choix", _choix(Bien, "type_habitat"), 24),
            Colonne("regime_juridique", "Régime juridique", False, "", "choix", _choix(Bien, "regime_juridique")),
            Colonne("notes", "Notes", False, "Texte libre", largeur=30),
        ]),
        ("Lots", "Un lot = ce qui se loue : appartement, maison, parking… Il appartient à un bien.", [
            Colonne("bien", "Bien", True, "Nom du bien, tel qu'écrit dans l'onglet Biens ou déjà dans l'appli", largeur=26),
            Colonne("designation", "Désignation du lot", True, "ex. Apt 2e gauche (unique dans le bien)", largeur=22),
            Colonne("type", "Type de lot", True, "Choisir dans la liste", "choix", _choix(Lot, "type"), 20),
            Colonne("surface", "Surface (m²)", False, "ex. 45,5", "montant", largeur=12),
            Colonne("nb_pieces", "Nombre de pièces", False, "ex. 2", "entier", largeur=12),
            Colonne("meuble", "Meublé", False, "Oui ou Non (par défaut : Non)", "oui_non", largeur=10),
            Colonne("etage", "Étage / porte", False, "ex. 2e gauche", largeur=14),
            Colonne("exploitation", "Mode d'exploitation", False,
                    "Par défaut : location classique", "choix", _choix(Lot, "exploitation"), 32),
            Colonne("loyer_propose", "Loyer proposé CC (€)", False, "Charges comprises, si le lot est à louer", "montant"),
            Colonne("classe_dpe", "Classe DPE", False, "A à G", "choix", _choix(Lot, "classe_dpe"), 10),
            Colonne("classe_ges", "Classe GES", False, "A à G", "choix", _choix(Lot, "classe_ges"), 10),
            Colonne("notes", "Notes", False, "Texte libre", largeur=30),
        ]),
        ("Locataires", "Une ligne par personne (deux colocataires = deux lignes).", [
            Colonne("nom", "Nom", True, "ex. Martin"),
            Colonne("prenom", "Prénom", False, "Conseillé, pour les reconnaître dans les baux. ex. Julie"),
            Colonne("civilite", "Civilité", False, "M. ou Mme", "choix", _choix(Locataire, "civilite"), 10),
            Colonne("email", "E-mail", False, "ex. julie.martin@mail.fr", largeur=26),
            Colonne("telephone", "Téléphone", False, "ex. 06 12 34 56 78", largeur=16),
            Colonne("date_naissance", "Date de naissance", False, "jj/mm/aaaa", "date"),
            Colonne("lieu_naissance", "Lieu de naissance", False, "ex. Lyon"),
            Colonne("nationalite", "Nationalité", False, "ex. française"),
            Colonne("adresse_actuelle", "Adresse actuelle", False, "Avant l'entrée dans les lieux", largeur=28),
            Colonne("notes", "Notes", False, "Texte libre", largeur=30),
        ]),
        ("Baux", "Un bail relie un lot à un ou plusieurs locataires (déjà dans l'appli ou dans l'onglet Locataires).", [
            Colonne("bien", "Bien", True, "Nom du bien", largeur=26),
            Colonne("lot", "Lot", False, "Désignation du lot. Vide si le bien n'a qu'un lot", largeur=22),
            Colonne("locataires", "Locataire(s)", True, "Prénom et nom. Plusieurs : séparés par ; ex. Julie Martin; Paul Durand", largeur=30),
            Colonne("type", "Type de bail", True, "Choisir dans la liste", "choix", _choix(Bail, "type"), 26),
            Colonne("date_debut", "Date de début", True, "jj/mm/aaaa", "date", largeur=14),
            Colonne("loyer_hc", "Loyer hors charges (€)", True, "Loyer actuel, par mois. ex. 650", "montant"),
            Colonne("charges", "Charges (€)", False, "Provision par mois. Par défaut : 0", "montant", largeur=12),
            Colonne("depot_garantie", "Dépôt de garantie (€)", False, "Par défaut : 0", "montant"),
            Colonne("date_fin", "Date de fin", False, "Seulement si le locataire est parti (bail terminé)", "date", largeur=14),
            Colonne("jour_echeance", "Jour d'échéance", False, "Jour du mois où le loyer est dû. Par défaut : 1", "entier", largeur=12),
            Colonne("notes", "Notes", False, "Texte libre", largeur=30),
        ]),
    ]


def _norm(texte):
    """Texte comparable : sans accents, sans majuscules, espaces simples."""
    texte = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texte).strip().lower()


def _titre_norm(texte):
    return _norm(str(texte or "").replace("*", ""))


# ---------- modèle vierge ----------

def generer_modele():
    """Classeur Excel vierge (octets) : mode d'emploi, un onglet par type de données, listes déroulantes."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    orange = PatternFill("solid", fgColor="F7B267")
    gris = PatternFill("solid", fgColor="E4E7EC")
    aide_fond = PatternFill("solid", fgColor="FAFAFA")
    gras = Font(bold=True)
    aide_police = Font(italic=True, color="667085", size=9)
    enroule = Alignment(wrap_text=True, vertical="top")

    classeur = Workbook()
    emploi = classeur.active
    emploi.title = "Mode d'emploi"
    listes = classeur.create_sheet("Listes")
    n_liste = 0
    lignes_emploi = [
        ("Importer vos données dans l'application", "titre"),
        ("", None),
        ("1. Remplissez les onglets Biens, Lots, Locataires et Baux (vous pouvez en laisser certains vides).", None),
        ("2. Une ligne = un élément. Commencez à la ligne 3 : la ligne 2 (en italique gris) est une aide, laissez-la.", None),
        ("3. Les colonnes en orange avec une * sont obligatoires. Les colonnes en gris sont facultatives.", None),
        ("4. Pour les colonnes à choix, utilisez la petite flèche de la cellule (liste déroulante).", None),
        ("5. Enregistrez le fichier, puis importez-le dans l'appli (menu Outils › Import Excel).", None),
        ("", None),
        ("Bon à savoir", "titre2"),
        ("• Tout est vérifié avant d'enregistrer : s'il y a une seule erreur, RIEN n'est enregistré et l'appli vous dit "
         "quelle ligne corriger.", None),
        ("• Les liens se font par les noms : un lot cite le nom de son bien, un bail cite le bien, le lot et les locataires. "
         "Ils peuvent être dans ce fichier ou déjà dans l'appli.", None),
        ("• Un élément déjà présent dans l'appli (même nom) n'est pas recréé : retirez sa ligne du fichier.", None),
        ("• Dates au format jj/mm/aaaa, montants en euros (ex. 650 ou 650,50).", None),
        ("• En mode test, l'import va dans la base de test : idéal pour essayer sans risque.", None),
        ("", None),
        ("Liste des colonnes", "titre2"),
    ]
    for texte, style in lignes_emploi:
        emploi.append([texte])
        cellule = emploi.cell(emploi.max_row, 1)
        if style == "titre":
            cellule.font = Font(bold=True, size=14)
        elif style == "titre2":
            cellule.font = Font(bold=True, size=12)
    emploi.append(["Onglet", "Colonne", "Obligatoire ?", "Aide", "Valeurs possibles"])
    for c in emploi[emploi.max_row]:
        c.font = gras
        c.fill = gris

    for nom, explication, colonnes in onglets():
        feuille = classeur.create_sheet(nom)
        for i, col in enumerate(colonnes, start=1):
            lettre = get_column_letter(i)
            titre = feuille.cell(LIGNE_TITRES, i, col.entete)
            titre.font, titre.fill = gras, (orange if col.obligatoire else gris)
            titre.alignment = Alignment(wrap_text=True, vertical="center")
            aide = feuille.cell(LIGNE_AIDE, i, ("Obligatoire" if col.obligatoire else "Facultatif") + (f" · {col.aide}" if col.aide else ""))
            aide.font, aide.fill, aide.alignment = aide_police, aide_fond, enroule
            feuille.column_dimensions[lettre].width = col.largeur
            plage = f"{lettre}{PREMIERE_LIGNE}:{lettre}{MAX_LIGNES + PREMIERE_LIGNE}"
            if col.sorte == "choix":
                n_liste += 1
                lettre_liste = get_column_letter(n_liste)
                listes.cell(1, n_liste, f"{nom} – {col.titre}").font = gras
                for j, (_, libelle) in enumerate(col.choix, start=2):
                    listes.cell(j, n_liste, libelle)
                dv = DataValidation(type="list", formula1=f"=Listes!${lettre_liste}$2:${lettre_liste}${len(col.choix) + 1}",
                                    allow_blank=True, showErrorMessage=True, errorTitle="Valeur non prévue",
                                    error="Choisissez une valeur dans la liste.")
                feuille.add_data_validation(dv)
                dv.add(plage)
            elif col.sorte == "oui_non":
                dv = DataValidation(type="list", formula1='"Oui,Non"', allow_blank=True)
                feuille.add_data_validation(dv)
                dv.add(plage)
            elif col.sorte == "date":
                for r in range(PREMIERE_LIGNE, PREMIERE_LIGNE + 200):
                    feuille.cell(r, i).number_format = "DD/MM/YYYY"
            elif col.sorte == "montant":
                for r in range(PREMIERE_LIGNE, PREMIERE_LIGNE + 200):
                    feuille.cell(r, i).number_format = "#,##0.00"
            emploi.append([nom, col.titre, "Oui" if col.obligatoire else "Non", col.aide,
                           " / ".join(l for _, l in col.choix) if col.choix else ("Oui / Non" if col.sorte == "oui_non" else "")])
            if col.obligatoire:
                emploi.cell(emploi.max_row, 3).fill = orange
        feuille.row_dimensions[LIGNE_TITRES].height = 32
        feuille.row_dimensions[LIGNE_AIDE].height = 42
        feuille.freeze_panes = f"A{PREMIERE_LIGNE}"
    for lettre, largeur in zip("ABCDE", (12, 26, 14, 60, 60)):
        emploi.column_dimensions[lettre].width = largeur
    for ligne in emploi.iter_rows(min_row=len(lignes_emploi) + 2):
        for c in ligne:
            c.alignment = enroule
    classeur.move_sheet(listes, offset=len(classeur.sheetnames) - 1 - classeur.sheetnames.index("Listes"))
    listes.sheet_state = "hidden"
    classeur.active = 0
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


# ---------- lecture et vérification ----------

@dataclass
class Erreur:
    onglet: str
    ligne: int | None
    colonne: str
    message: str


@dataclass
class Resultat:
    erreurs: list = field(default_factory=list)
    crees: dict = field(default_factory=dict)  # {"Biens": 2, ...}
    proprietaires_crees: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.erreurs

    @property
    def total(self):
        return sum(self.crees.values())


class _ValeurInvalide(Exception):
    pass


def _texte(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # code postal ou téléphone saisi comme un nombre
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    return str(v).strip()


def _lire(col, brut):
    """Valeur Python d'une cellule, ou _ValeurInvalide avec un message compréhensible."""
    if col.sorte == "date":
        if isinstance(brut, datetime):
            return brut.date()
        if isinstance(brut, date):
            return brut
        t = _texte(brut)
        if not t:
            return None
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(t, fmt).date()
            except ValueError:
                pass
        raise _ValeurInvalide(f"date illisible « {t} » (format attendu : jj/mm/aaaa)")
    if col.sorte in ("montant", "entier"):
        if isinstance(brut, bool):
            raise _ValeurInvalide("nombre attendu")
        if isinstance(brut, (int, float)):
            nombre = Decimal(str(brut))
        else:
            t = _texte(brut).replace("€", "").replace(" ", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
            if not t:
                return None
            try:
                nombre = Decimal(t)
            except InvalidOperation:
                raise _ValeurInvalide(f"nombre illisible « {_texte(brut)} »")
        if nombre < 0:
            raise _ValeurInvalide("le nombre ne peut pas être négatif")
        if col.sorte == "entier":
            if nombre != nombre.to_integral_value():
                raise _ValeurInvalide(f"nombre entier attendu (pas « {_texte(brut)} »)")
            return int(nombre)
        return nombre.quantize(Decimal("0.01"))
    t = _texte(brut)
    if col.sorte == "oui_non":
        if _norm(t) in OUI:
            return True
        if _norm(t) in NON:
            return False
        raise _ValeurInvalide(f"« {t} » : écrire Oui ou Non")
    if col.sorte == "choix":
        if not t:
            return None
        for valeur, libelle in col.choix:
            if _norm(t) in (_norm(libelle), _norm(valeur)):
                return valeur
        raise _ValeurInvalide(f"« {t} » n'est pas dans la liste ({' / '.join(l for _, l in col.choix)})")
    return t


def _lire_onglet(feuille, nom, colonnes, res):
    """Lignes remplies d'un onglet : [(n° de ligne Excel, {clé: valeur})]. Les erreurs sont ajoutées à res."""
    titres = {}
    for i, cellule in enumerate(next(feuille.iter_rows(min_row=LIGNE_TITRES, max_row=LIGNE_TITRES), [])):
        if cellule.value is not None:
            titres[_titre_norm(cellule.value)] = i
    positions = {}
    for col in colonnes:
        if _titre_norm(col.titre) in titres:
            positions[col.cle] = titres[_titre_norm(col.titre)]
        elif col.obligatoire:
            res.erreurs.append(Erreur(nom, None, col.titre, "colonne obligatoire absente : ne supprimez pas les colonnes du modèle"))
    if any(e.onglet == nom and e.ligne is None for e in res.erreurs):
        return []
    lignes = []
    for n, valeurs in enumerate(feuille.iter_rows(min_row=LIGNE_AIDE, values_only=True), start=LIGNE_AIDE):
        cellules = {c.cle: (valeurs[positions[c.cle]] if positions.get(c.cle) is not None and positions[c.cle] < len(valeurs) else None)
                    for c in colonnes}
        remplies = [_texte(v) for v in cellules.values() if _texte(v)]
        if not remplies:
            continue
        if all(t.startswith(("Obligatoire", "Facultatif")) for t in remplies):
            continue  # ligne d'aide du modèle
        if len(lignes) >= MAX_LIGNES:
            res.erreurs.append(Erreur(nom, n, "", f"trop de lignes (maximum {MAX_LIGNES} par onglet) : découpez le fichier"))
            break
        ligne = {"_erreurs": set()}  # cases déjà signalées
        for col in colonnes:
            try:
                ligne[col.cle] = _lire(col, cellules[col.cle])
            except _ValeurInvalide as e:
                res.erreurs.append(Erreur(nom, n, col.titre, str(e)))
                ligne[col.cle] = None
                ligne["_erreurs"].add(col.cle)
                continue
            if col.obligatoire and ligne[col.cle] in (None, ""):
                res.erreurs.append(Erreur(nom, n, col.titre, "case obligatoire vide"))
                ligne["_erreurs"].add(col.cle)
        lignes.append((n, ligne))
    return lignes


def _champs(ligne, *sauf):
    """Valeurs à donner au modèle : cases remplies, hors colonnes de liaison."""
    return {k: v for k, v in ligne.items() if k != "_erreurs" and k not in sauf and v is not None}


def _verifier_modele(obj, nom, n, res, ligne=None, exclure=(), titres=None):
    """Contrôles de l'appli (longueurs, e-mail…) avant d'enregistrer. Une case déjà signalée ne l'est pas deux fois."""
    colonnes = titres or {c.cle: c.titre for o, _, cols in onglets() if o == nom for c in cols}
    deja = (ligne or {}).get("_erreurs", set())
    try:
        obj.full_clean(exclude=list(exclure) + list(deja), validate_unique=False, validate_constraints=False)
    except ValidationError as e:
        for champ, messages in e.message_dict.items():
            titre = colonnes.get(champ) or (obj._meta.get_field(champ).verbose_name if champ != "__all__" else "")
            for m in messages:
                res.erreurs.append(Erreur(nom, n, str(titre), m))


def _cles_personne(nom, prenom):
    nom, prenom = _norm(nom), _norm(prenom)
    return {f"{prenom} {nom}".strip(), f"{nom} {prenom}".strip()}


def importer(fichier):
    """Vérifie tout le classeur, puis enregistre tout (ou rien s'il y a une erreur). Renvoie un Resultat."""
    from openpyxl import load_workbook

    from . import base_essai

    res = Resultat()
    try:
        classeur = load_workbook(fichier, data_only=True)
    except Exception:
        res.erreurs.append(Erreur("", None, "", "fichier illisible : il faut un fichier Excel (.xlsx), par exemple le modèle téléchargé ici"))
        return res
    definitions = onglets()
    noms_onglets = {_norm(n): n for n in classeur.sheetnames}
    donnees = {}
    for nom, _, colonnes in definitions:
        reel = noms_onglets.get(_norm(nom))
        donnees[nom] = _lire_onglet(classeur[reel], nom, colonnes, res) if reel else []
    if not any(donnees.values()) and not res.erreurs:
        res.erreurs.append(Erreur("", None, "", "le fichier ne contient aucune ligne à importer (commencez à la ligne 3 de chaque onglet)"))
        return res

    # --- Biens ---
    proprietaires = {_norm(p.nom): p for p in Proprietaire.objects.all()}
    nouveaux_proprietaires = {}
    biens_existants = {}
    for b in Bien.objects.all():
        biens_existants.setdefault(_norm(b.nom), []).append(b)
    biens_fichier = {}  # nom normalisé -> Bien non enregistré
    a_creer_biens = []
    for n, l in donnees["Biens"]:
        if not l["nom"]:
            continue
        cle = _norm(l["nom"])
        if cle in biens_existants:
            res.erreurs.append(Erreur("Biens", n, "Nom du bien", f"« {l['nom']} » existe déjà dans l'appli : retirez cette ligne"))
            continue
        if cle in biens_fichier:
            res.erreurs.append(Erreur("Biens", n, "Nom du bien", f"« {l['nom']} » apparaît deux fois dans l'onglet"))
            continue
        bien = Bien(**_champs(l, "proprietaire", "type_proprietaire"))
        _verifier_modele(bien, "Biens", n, res, l, exclure=["proprietaire", "syndic"])
        if l["proprietaire"]:
            cle_p = _norm(l["proprietaire"])
            if cle_p not in proprietaires and cle_p not in nouveaux_proprietaires:
                p = Proprietaire(nom=l["proprietaire"], type=l["type_proprietaire"] or Proprietaire.Type.NOM_PROPRE)
                _verifier_modele(p, "Biens", n, res, titres={"nom": "Propriétaire", "type": "Type de propriétaire"})
                nouveaux_proprietaires[cle_p] = p
        biens_fichier[cle] = bien
        a_creer_biens.append((bien, _norm(l["proprietaire"])))

    def trouver_bien(nom_bien, onglet, n):
        cle = _norm(nom_bien)
        if cle in biens_fichier:
            return biens_fichier[cle]
        trouves = biens_existants.get(cle, [])
        if len(trouves) == 1:
            return trouves[0]
        if len(trouves) > 1:
            res.erreurs.append(Erreur(onglet, n, "Bien", f"plusieurs biens s'appellent « {nom_bien} » dans l'appli : renommez-en un"))
        else:
            res.erreurs.append(Erreur(onglet, n, "Bien", f"bien « {nom_bien} » introuvable (ni dans l'onglet Biens, ni dans l'appli)"))
        return None

    # --- Lots ---
    cle_bien = lambda b: ("pk", b.pk) if b.pk else ("nouveau", id(b))
    lots_par_bien = {}  # bien -> {désignation normalisée: Lot}
    for lot in Lot.objects.all():
        lots_par_bien.setdefault(("pk", lot.bien_id), {})[_norm(lot.designation)] = lot
    a_creer_lots = []
    for n, l in donnees["Lots"]:
        bien = trouver_bien(l["bien"], "Lots", n) if l["bien"] else None
        if bien is None or not l["designation"]:
            continue
        deja = lots_par_bien.setdefault(cle_bien(bien), {})
        cle = _norm(l["designation"])
        if cle in deja:
            ou = "dans l'appli" if deja[cle].pk else "dans l'onglet"
            res.erreurs.append(Erreur("Lots", n, "Désignation du lot", f"le lot « {l['designation']} » existe déjà {ou} pour ce bien"))
            continue
        lot = Lot(**_champs(l, "bien"))
        _verifier_modele(lot, "Lots", n, res, l, exclure=["bien"])
        deja[cle] = lot
        a_creer_lots.append((lot, bien))

    # --- Locataires ---
    existants = list(Locataire.objects.all())
    identites = {(_norm(x.prenom), _norm(x.nom)): x for x in existants}  # pour repérer les doublons
    a_creer_locataires = []
    for n, l in donnees["Locataires"]:
        if not l["nom"]:
            continue
        identite = (_norm(l["prenom"]), _norm(l["nom"]))
        if identite in identites:
            ou = "dans l'appli" if identites[identite].pk else "plus haut dans l'onglet"
            qui = f"{l['prenom']} {l['nom']}".strip()
            res.erreurs.append(Erreur("Locataires", n, "Nom", f"« {qui} » existe déjà {ou} : retirez cette ligne"))
            continue
        loc = Locataire(**_champs(l))
        _verifier_modele(loc, "Locataires", n, res, l)
        identites[identite] = loc
        a_creer_locataires.append(loc)
    personnes = {}  # « prénom nom », « nom prénom » ou le nom seul -> [Locataire] (le nom seul suffit sans homonyme)
    for loc in existants + a_creer_locataires:
        for c in _cles_personne(loc.nom, loc.prenom) | {_norm(loc.nom)}:
            personnes.setdefault(c, []).append(loc)

    # --- Baux ---
    a_creer_baux = []
    for n, l in sorted(donnees["Baux"], key=lambda x: (x[1]["date_debut"] or date.max)):
        bien = trouver_bien(l["bien"], "Baux", n) if l["bien"] else None
        lot = None
        if bien is not None:
            lots = lots_par_bien.get(cle_bien(bien), {})
            if l["lot"]:
                lot = lots.get(_norm(l["lot"]))
                if lot is None:
                    res.erreurs.append(Erreur("Baux", n, "Lot", f"lot « {l['lot']} » introuvable dans le bien « {l['bien']} »"))
            elif len(lots) == 1:
                lot = next(iter(lots.values()))
            else:
                res.erreurs.append(Erreur("Baux", n, "Lot", "le bien a plusieurs lots (ou aucun) : indiquez le lot"
                                          if lots else "ce bien n'a aucun lot : ajoutez-le dans l'onglet Lots"))
        locataires = []
        for nom_loc in [x.strip() for x in re.split(r"[;,]", l["locataires"] or "") if x.strip()]:
            trouves = list({id(x): x for x in personnes.get(_norm(nom_loc), [])}.values())  # sans doublon
            if len(trouves) == 1:
                locataires.append(trouves[0])
            elif trouves:
                res.erreurs.append(Erreur("Baux", n, "Locataire(s)", f"plusieurs locataires correspondent à « {nom_loc} » : écrivez prénom et nom"))
            else:
                res.erreurs.append(Erreur("Baux", n, "Locataire(s)", f"locataire « {nom_loc} » introuvable (ni dans l'onglet Locataires, ni dans l'appli)"))
        if l["date_debut"] and l["date_fin"] and l["date_fin"] < l["date_debut"]:
            res.erreurs.append(Erreur("Baux", n, "Date de fin", "la date de fin est avant la date de début"))
        if l["jour_echeance"] is not None and not 1 <= l["jour_echeance"] <= 31:
            res.erreurs.append(Erreur("Baux", n, "Jour d'échéance", "un jour entre 1 et 31"))
        valeurs = _champs(l, "bien", "lot", "locataires")
        bail = Bail(**valeurs)
        bail.actif = not (bail.date_fin and bail.date_fin < date.today())
        if bail.loyer_initial is None:
            bail.loyer_initial = bail.loyer_hc
        _verifier_modele(bail, "Baux", n, res, l, exclure=["lot", "locataires"])
        a_creer_baux.append((bail, lot, locataires))

    if res.erreurs:
        res.erreurs.sort(key=lambda e: ([d[0] for d in definitions].index(e.onglet) if e.onglet in [d[0] for d in definitions] else -1,
                                        e.ligne or 0))
        return res

    # --- Enregistrement : tout ou rien, dans la base active (vraie base ou base de test) ---
    with transaction.atomic(using=base_essai.active()):
        for p in nouveaux_proprietaires.values():
            p.save()
            res.proprietaires_crees.append(p.nom)
        proprietaires.update(nouveaux_proprietaires)
        for bien, cle_p in a_creer_biens:
            bien.proprietaire = proprietaires[cle_p]
            bien.save()
        for lot, bien in a_creer_lots:
            lot.bien = bien
            lot.save()
        for loc in a_creer_locataires:
            loc.save()
        for bail, lot, locataires in a_creer_baux:
            bail.lot = lot
            bail.save()
            bail.locataires.set(locataires)
    res.crees = {"Biens": len(a_creer_biens), "Lots": len(a_creer_lots), "Locataires": len(a_creer_locataires), "Baux": len(a_creer_baux)}
    return res

