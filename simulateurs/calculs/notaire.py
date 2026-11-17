"""Frais de notaire (« frais d'acquisition ») d'un achat immobilier en 2026.

Ce que l'acheteur paie au notaire se décompose en :
- droits de mutation (impôts reversés au département, à la commune et à l'État) : la plus grosse part ;
- émoluments du notaire : barème réglementé par tranches, plus TVA à 20 % ;
- contribution de sécurité immobilière (0,10 % du prix, 15 € au minimum) ;
- formalités et débours (cadastre, urbanisme, copies…) : un forfait estimé, modifiable.

Le mobilier vendu avec le logement (liste détaillée annexée à l'acte) n'est pas taxé : il est retiré de la base.

À METTRE À JOUR chaque année (ou quand un département change son taux) : TAUX_DEPARTEMENTAUX et MISE_A_JOUR.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from . import EURO, arrondi

MISE_A_JOUR = "taux départementaux en vigueur au 1er juin 2026 (tableau DGFiP)"

# Ancien : part départementale (taux par défaut depuis la loi de finances 2025, plafond relevé à 5 %)
TAUX_DEPARTEMENTAL_PAR_DEFAUT = Decimal("5.00")
# Primo-accédants (achat de la résidence principale) : la hausse de 2025 ne s'applique pas.
TAUX_PRIMO_ACCEDANT = Decimal("4.50")
# Départements qui n'ont pas relevé leur taux à 5 %
TAUX_DEPARTEMENTAUX = {
    "05": Decimal("4.50"), "06": Decimal("4.50"), "07": Decimal("4.50"), "16": Decimal("4.50"),
    "26": Decimal("4.50"), "36": Decimal("3.80"), "48": Decimal("4.50"), "60": Decimal("4.50"),
    "65": Decimal("4.50"), "71": Decimal("4.50"), "971": Decimal("4.50"), "976": Decimal("4.50"),
}
TAUX_COMMUNAL = Decimal("1.20")
FRAIS_ASSIETTE = Decimal("2.37")  # % prélevé par l'État sur la part départementale
# Neuf (vente en l'état futur d'achèvement ou logement de moins de 5 ans vendu avec TVA) : taxe de publicité
# foncière réduite, calculée sur le prix hors TVA.
TAUX_PUBLICITE_FONCIERE_NEUF = Decimal("0.70")
TVA_NEUF = Decimal("20")

# Émoluments proportionnels du notaire pour une vente (art. A444-91 du code de commerce), hors TVA
BAREME_EMOLUMENTS = [
    (Decimal("6500"), Decimal("3.870")),
    (Decimal("17000"), Decimal("1.596")),
    (Decimal("60000"), Decimal("1.064")),
    (None, Decimal("0.799")),
]
TVA = Decimal("20")
TAUX_CSI = Decimal("0.10")
CSI_MINIMUM = Decimal("15")
FORMALITES_PAR_DEFAUT = Decimal("1200")

DEPARTEMENTS = [
    ("01", "Ain"), ("02", "Aisne"), ("03", "Allier"), ("04", "Alpes-de-Haute-Provence"), ("05", "Hautes-Alpes"),
    ("06", "Alpes-Maritimes"), ("07", "Ardèche"), ("08", "Ardennes"), ("09", "Ariège"), ("10", "Aube"),
    ("11", "Aude"), ("12", "Aveyron"), ("13", "Bouches-du-Rhône"), ("14", "Calvados"), ("15", "Cantal"),
    ("16", "Charente"), ("17", "Charente-Maritime"), ("18", "Cher"), ("19", "Corrèze"), ("2A", "Corse-du-Sud"),
    ("2B", "Haute-Corse"), ("21", "Côte-d'Or"), ("22", "Côtes-d'Armor"), ("23", "Creuse"), ("24", "Dordogne"),
    ("25", "Doubs"), ("26", "Drôme"), ("27", "Eure"), ("28", "Eure-et-Loir"), ("29", "Finistère"), ("30", "Gard"),
    ("31", "Haute-Garonne"), ("32", "Gers"), ("33", "Gironde"), ("34", "Hérault"), ("35", "Ille-et-Vilaine"),
    ("36", "Indre"), ("37", "Indre-et-Loire"), ("38", "Isère"), ("39", "Jura"), ("40", "Landes"),
    ("41", "Loir-et-Cher"), ("42", "Loire"), ("43", "Haute-Loire"), ("44", "Loire-Atlantique"), ("45", "Loiret"),
    ("46", "Lot"), ("47", "Lot-et-Garonne"), ("48", "Lozère"), ("49", "Maine-et-Loire"), ("50", "Manche"),
    ("51", "Marne"), ("52", "Haute-Marne"), ("53", "Mayenne"), ("54", "Meurthe-et-Moselle"), ("55", "Meuse"),
    ("56", "Morbihan"), ("57", "Moselle"), ("58", "Nièvre"), ("59", "Nord"), ("60", "Oise"), ("61", "Orne"),
    ("62", "Pas-de-Calais"), ("63", "Puy-de-Dôme"), ("64", "Pyrénées-Atlantiques"), ("65", "Hautes-Pyrénées"),
    ("66", "Pyrénées-Orientales"), ("67", "Bas-Rhin"), ("68", "Haut-Rhin"), ("69", "Rhône"), ("70", "Haute-Saône"),
    ("71", "Saône-et-Loire"), ("72", "Sarthe"), ("73", "Savoie"), ("74", "Haute-Savoie"), ("75", "Paris"),
    ("76", "Seine-Maritime"), ("77", "Seine-et-Marne"), ("78", "Yvelines"), ("79", "Deux-Sèvres"), ("80", "Somme"),
    ("81", "Tarn"), ("82", "Tarn-et-Garonne"), ("83", "Var"), ("84", "Vaucluse"), ("85", "Vendée"), ("86", "Vienne"),
    ("87", "Haute-Vienne"), ("88", "Vosges"), ("89", "Yonne"), ("90", "Territoire de Belfort"), ("91", "Essonne"),
    ("92", "Hauts-de-Seine"), ("93", "Seine-Saint-Denis"), ("94", "Val-de-Marne"), ("95", "Val-d'Oise"),
    ("971", "Guadeloupe"), ("972", "Martinique"), ("973", "Guyane"), ("974", "La Réunion"), ("976", "Mayotte"),
]
NOMS_DEPARTEMENTS = dict(DEPARTEMENTS)

ANCIEN, NEUF = "ancien", "neuf"


def taux_departemental(departement, primo_accedant=False):
    """Taux de la part départementale (ancien), en %."""
    taux = TAUX_DEPARTEMENTAUX.get(departement, TAUX_DEPARTEMENTAL_PAR_DEFAUT)
    if primo_accedant:
        taux = min(taux, TAUX_PRIMO_ACCEDANT)
    return taux


def emoluments_ht(base):
    """Émoluments proportionnels du notaire, hors TVA, selon le barème par tranches."""
    total, plancher = Decimal(0), Decimal(0)
    for plafond, taux in BAREME_EMOLUMENTS:
        haut = base if plafond is None else min(base, plafond)
        if haut > plancher:
            total += (haut - plancher) * taux / 100
        if plafond is None or base <= plafond:
            break
        plancher = plafond
    return arrondi(total)


@dataclass
class Ligne:
    libelle: str
    montant: Decimal
    detail: str = ""


@dataclass
class FraisNotaire:
    prix: Decimal
    type_bien: str
    departement: str
    base: Decimal
    droits: list = field(default_factory=list)
    notaire: list = field(default_factory=list)

    @property
    def total_droits(self):
        return sum((l.montant for l in self.droits), Decimal(0))

    @property
    def total_notaire(self):
        return sum((l.montant for l in self.notaire), Decimal(0))

    @property
    def total(self):
        return self.total_droits + self.total_notaire

    @property
    def pourcentage(self):
        return arrondi(self.total * 100 / self.prix) if self.prix else Decimal(0)

    @property
    def cout_total(self):
        """Prix + frais : le budget de l'acquisition."""
        return self.prix + self.total


def _pct(taux):
    return f"{taux:.2f} %".replace(".", ",")


def calculer(prix, type_bien=ANCIEN, departement="69", primo_accedant=False, mobilier=Decimal(0),
             formalites=FORMALITES_PAR_DEFAUT):
    """Estimation des frais de notaire. `prix` : prix net vendeur (TTC dans le neuf), hors frais d'agence à la charge
    de l'acheteur. Montants en euros (Decimal)."""
    prix = Decimal(prix)
    mobilier = min(Decimal(mobilier or 0), prix)
    base = prix - mobilier
    r = FraisNotaire(prix=prix, type_bien=type_bien, departement=departement, base=base)

    if type_bien == NEUF:
        base_ht = arrondi(base * 100 / (100 + TVA_NEUF), EURO)
        tpf = arrondi(base_ht * TAUX_PUBLICITE_FONCIERE_NEUF / 100, EURO)
        r.droits.append(Ligne("Taxe de publicité foncière", tpf, f"{_pct(TAUX_PUBLICITE_FONCIERE_NEUF)} du prix hors TVA"))
        r.droits.append(Ligne("Frais d'assiette et de recouvrement", arrondi(tpf * FRAIS_ASSIETTE / 100, EURO),
                              f"{_pct(FRAIS_ASSIETTE)} de la taxe"))
        base_notaire = base_ht
    else:
        taux = taux_departemental(departement, primo_accedant)
        dep = arrondi(base * taux / 100, EURO)
        r.droits.append(Ligne("Taxe départementale", dep, f"{_pct(taux)}"
                              + (" (primo-accédant)" if primo_accedant and taux < taux_departemental(departement) else "")))
        r.droits.append(Ligne("Taxe communale", arrondi(base * TAUX_COMMUNAL / 100, EURO), _pct(TAUX_COMMUNAL)))
        r.droits.append(Ligne("Frais d'assiette et de recouvrement", arrondi(dep * FRAIS_ASSIETTE / 100, EURO),
                              f"{_pct(FRAIS_ASSIETTE)} de la taxe départementale"))
        base_notaire = base

    emol = emoluments_ht(base_notaire)
    r.notaire.append(Ligne("Émoluments du notaire (HT)", emol, "barème réglementé par tranches"))
    r.notaire.append(Ligne("TVA sur les émoluments", arrondi(emol * TVA / 100), _pct(TVA)))
    r.notaire.append(Ligne("Contribution de sécurité immobilière", max(arrondi(base_notaire * TAUX_CSI / 100), CSI_MINIMUM),
                           f"{_pct(TAUX_CSI)}, 15 € minimum"))
    r.notaire.append(Ligne("Formalités et débours (estimation)", arrondi(Decimal(formalites or 0)),
                           "cadastre, urbanisme, copies, envois…"))
    return r
