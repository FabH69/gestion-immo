"""Modèle d'état des lieux généré depuis la configuration du logement : pièces (entrée, séjour, chambres, salles de bains, WC, garage, cave…)
et, pour chaque pièce, les éléments à contrôler qui lui sont propres (évier et hotte en cuisine, cuvette et chasse d'eau aux WC…)."""
import unicodedata

from .documents import ELEMENTS_PIECE

_BASE = ["Sol", "Murs", "Plafond", "Porte(s)", "Fenêtres / vitrages", "Volets / stores", "Prises et interrupteurs", "Éclairage", "Chauffage / radiateur"]
_EAU = ["Sol", "Murs / faïence", "Plafond", "Porte", "Fenêtre / vitrage", "Prises et interrupteurs", "Éclairage", "Chauffage / sèche-serviettes", "Ventilation (VMC)"]

# (code, nom de la pièce, libellé du champ de configuration, éléments à contrôler, mots qui la reconnaissent dans un nom saisi à la main)
TYPES_PIECES = [
    ("entree", "Entrée", "Entrée", ["Sol", "Murs", "Plafond", "Porte d'entrée et serrure", "Interphone / sonnette", "Prises et interrupteurs", "Éclairage", "Placard"], ["entree", "hall"]),
    ("degagement", "Dégagement", "Dégagement / couloir", ["Sol", "Murs", "Plafond", "Prises et interrupteurs", "Éclairage", "Placard"], ["degagement", "couloir", "palier"]),
    ("sejour", "Séjour", "Séjour / salon", _BASE, ["sejour", "salon", "living", "piece de vie", "piece principale"]),
    ("salle_a_manger", "Salle à manger", "Salle à manger", _BASE, ["salle a manger"]),
    ("cuisine", "Cuisine", "Cuisine", _BASE + ["Évier et robinetterie", "Plan de travail", "Crédence / faïence", "Plaques de cuisson", "Four", "Hotte",
                                               "Placards et meubles de cuisine", "Ventilation (VMC)"], ["cuisine", "kitchenette"]),
    ("chambre", "Chambre", "Chambres", _BASE + ["Placard / penderie"], ["chambre"]),
    ("bureau", "Bureau", "Bureau", _BASE, ["bureau"]),
    ("salle_bain", "Salle de bains", "Salles de bains", _EAU + ["Baignoire", "Douche / paroi", "Lavabo / vasque", "Robinetterie", "Meuble et miroir"], ["salle de bain", "sdb"]),
    ("salle_eau", "Salle d'eau", "Salles d'eau", _EAU + ["Douche / paroi", "Lavabo / vasque", "Robinetterie", "Meuble et miroir"], ["salle d'eau", "salle d eau"]),
    ("wc", "WC", "WC", ["Sol", "Murs", "Plafond", "Porte", "Cuvette et abattant", "Chasse d'eau", "Lave-mains", "Éclairage", "Ventilation (VMC)"], ["wc", "toilette"]),
    ("buanderie", "Buanderie", "Buanderie / cellier", ["Sol", "Murs", "Plafond", "Porte", "Arrivée et évacuation d'eau", "Prises et interrupteurs", "Éclairage"], ["buanderie", "cellier"]),
    ("dressing", "Dressing", "Dressing", ["Sol", "Murs", "Plafond", "Porte(s)", "Étagères / penderie", "Éclairage"], ["dressing"]),
    ("balcon", "Balcon / terrasse", "Balcons / terrasses", ["Sol / revêtement", "Garde-corps", "Murs / façade", "Éclairage extérieur", "Prise extérieure"], ["balcon", "terrasse", "loggia"]),
    ("jardin", "Jardin", "Jardin", ["Pelouse", "Plantations / haies", "Clôture", "Portail / portillon", "Allées", "Abri de jardin", "Robinet extérieur"], ["jardin", "cour"]),
    ("garage", "Garage", "Garages", ["Porte de garage", "Télécommande / clé", "Sol", "Murs", "Plafond", "Prises et interrupteurs", "Éclairage"], ["garage", "box"]),
    ("parking", "Place de parking", "Places de parking", ["Sol / marquage", "Accès (badge, télécommande)", "Éclairage"], ["parking", "stationnement"]),
    ("cave", "Cave", "Caves", ["Porte et serrure", "Sol", "Murs", "Plafond", "Éclairage"], ["cave"]),
    ("grenier", "Grenier", "Grenier / combles", ["Accès (trappe, escalier)", "Sol / plancher", "Charpente / isolation", "Éclairage"], ["grenier", "comble"]),
]
CODES = [t[0] for t in TYPES_PIECES]
_PAR_CODE = {t[0]: t for t in TYPES_PIECES}
MAXI = 20


def _simplifier(texte):
    sans_accent = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(sans_accent.lower().replace("’", "'").split())


def elements_pour(piece):
    """Éléments à contrôler pour une pièce, reconnue par son nom (« Chambre 2 », « Salle d'eau », « Cave n° 12 »…), sinon la liste habituelle."""
    nom = _simplifier(piece)
    for code, _, _, elements, mots in TYPES_PIECES:
        if any(nom.startswith(m) or f" {m}" in f" {nom}" for m in mots):
            return list(elements)
    return list(ELEMENTS_PIECE)


def nettoyer(configuration):
    """{code: nombre} limité aux codes connus et aux nombres de 1 à 20."""
    propre = {}
    for code, valeur in (configuration or {}).items():
        try:
            nombre = int(valeur)
        except (TypeError, ValueError):
            continue
        if code in _PAR_CODE and nombre > 0:
            propre[code] = min(nombre, MAXI)
    return propre


def pieces_configurees(configuration):
    """Noms des pièces dans l'ordre de visite : « Chambre » s'il n'y en a qu'une, sinon « Chambre 1 », « Chambre 2 »…"""
    configuration = nettoyer(configuration)
    pieces = []
    for code, nom, _, _, _ in TYPES_PIECES:
        nombre = configuration.get(code, 0)
        pieces += [nom] if nombre == 1 else [f"{nom} {i}" for i in range(1, nombre + 1)]
    return pieces


def modele(lot):
    """[(pièce, [éléments])] : la configuration du logement si elle est remplie (plus les autres pièces saisies à la main),
    sinon la liste saisie à la main, sinon une liste standard selon le nombre de pièces."""
    return [(p, elements_pour(p)) for p in lot.liste_pieces()]
