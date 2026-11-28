"""Modèle d'état des lieux généré depuis la configuration du logement : pièces (entrée, séjour, chambres, salles de bains, WC, garage, cave…)
et, pour chaque pièce, les éléments à contrôler qui lui sont propres (évier et hotte en cuisine, cuvette et chasse d'eau aux WC…)."""
import unicodedata

from .documents import ELEMENTS_PIECE

_BASE = ["Sol", "Murs", "Plafond", "Porte(s)", "Fenêtres / vitrages", "Volets / stores", "Prises et interrupteurs", "Éclairage", "Chauffage / radiateur"]
_SANS_CHAUFFAGE = [e for e in _BASE if e != "Chauffage / radiateur"]  # le radiateur est alors un équipement à cocher
_EAU = ["Sol", "Murs / faïence", "Plafond", "Porte", "Fenêtre / vitrage", "Prises et interrupteurs", "Éclairage", "Ventilation (VMC)"]

# (code, nom de la pièce, libellé du champ de configuration, éléments à contrôler, mots qui la reconnaissent dans un nom saisi à la main)
TYPES_PIECES = [
    ("entree", "Entrée", "Entrée", ["Sol", "Murs", "Plafond", "Porte d'entrée et serrure", "Interphone / sonnette", "Prises et interrupteurs", "Éclairage", "Placard"], ["entree", "hall"]),
    ("degagement", "Dégagement", "Dégagement / couloir", ["Sol", "Murs", "Plafond", "Prises et interrupteurs", "Éclairage", "Placard"], ["degagement", "couloir", "palier"]),
    ("sejour", "Séjour", "Séjour / salon", _SANS_CHAUFFAGE, ["sejour", "salon", "living", "piece de vie", "piece principale"]),
    ("salle_a_manger", "Salle à manger", "Salle à manger", _BASE, ["salle a manger"]),
    ("cuisine", "Cuisine", "Cuisine", _BASE + ["Évier et robinetterie", "Plan de travail", "Crédence / faïence", "Placards et meubles de cuisine",
                                               "Ventilation (VMC)"], ["cuisine", "kitchenette"]),
    ("chambre", "Chambre", "Chambres", _SANS_CHAUFFAGE, ["chambre"]),
    ("bureau", "Bureau", "Bureau", _BASE, ["bureau"]),
    ("salle_bain", "Salle de bains", "Salles de bains", _EAU + ["Baignoire", "Douche / paroi", "Lavabo / vasque", "Robinetterie"], ["salle de bain", "sdb"]),
    ("salle_eau", "Salle d'eau", "Salles d'eau", _EAU + ["Douche / paroi", "Lavabo / vasque", "Robinetterie"], ["salle d'eau", "salle d eau"]),
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

PLAQUES = [("", "–"), ("gaz", "gaz"), ("electrique", "électriques"), ("vitroceramique", "vitrocéramique"), ("induction", "induction"), ("mixte", "mixtes gaz / électrique")]
_SALLE_EAU = [("miroir", "Miroir", "oui"), ("seche_serviette", "Sèche-serviettes", "oui"), ("meuble", "Meuble sous vasque / rangement", "oui")]
# Équipements à cocher par type de pièce, ajoutés aux éléments classiques : (code, élément de l'état des lieux, genre)
# genre « oui » = case à cocher ; la cuisine a en plus le type de plaques et le nombre de feux.
EQUIPEMENTS = {
    "cuisine": [("plaques", "Plaques de cuisson", "oui"), ("hotte", "Hotte", "oui"), ("four", "Four", "oui"), ("micro_ondes", "Four à micro-ondes", "oui"),
                ("refrigerateur", "Réfrigérateur", "oui"), ("lave_vaisselle", "Lave-vaisselle", "oui"), ("lave_linge", "Lave-linge", "oui")],
    "salle_bain": _SALLE_EAU,
    "salle_eau": _SALLE_EAU,
    "chambre": [("placard", "Placard / penderie", "oui"), ("radiateur", "Radiateur", "oui")],
    "sejour": [("radiateur", "Radiateur", "oui")],
}
# Sans réglage enregistré (logements créés avant), on propose les équipements les plus courants.
PAR_DEFAUT = {"cuisine": {"plaques", "hotte", "four"}, "salle_bain": {"miroir", "seche_serviette", "meuble"}, "salle_eau": {"miroir", "seche_serviette", "meuble"},
              "chambre": {"placard", "radiateur"}, "sejour": {"radiateur"}}
_PAR_CODE = {t[0]: t for t in TYPES_PIECES}
MAXI = 20


def _simplifier(texte):
    sans_accent = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(sans_accent.lower().replace("’", "'").split())


def type_de_piece(piece):
    """Code du type de pièce reconnu dans son nom (« Chambre 2 », « Salle d'eau », « Cave n° 12 »…), ou None."""
    nom = _simplifier(piece)
    for code, _, _, _, mots in TYPES_PIECES:
        if any(nom.startswith(m) or f" {m}" in f" {nom}" for m in mots):
            return code
    return None


def nettoyer_equipements(equipements):
    """{type: {code: True/False, "plaques_type": …, "feux": n}} limité à ce que le catalogue connaît."""
    propre = {}
    for type_piece, reglage in (equipements or {}).items():
        if type_piece not in EQUIPEMENTS or not isinstance(reglage, dict):
            continue
        valeurs = {code: bool(reglage.get(code)) for code, _, _ in EQUIPEMENTS[type_piece]}
        if type_piece == "cuisine":
            if reglage.get("plaques_type") in dict(PLAQUES) and reglage.get("plaques_type"):
                valeurs["plaques_type"] = reglage["plaques_type"]
            try:
                feux = int(reglage.get("feux") or 0)
            except (TypeError, ValueError):
                feux = 0
            if 0 < feux <= 10:
                valeurs["feux"] = feux
        propre[type_piece] = valeurs
    return propre


def equipements_de(type_piece, equipements):
    """Éléments « équipement » d'une pièce : ceux cochés sur la fiche du logement, sinon les équipements courants."""
    if type_piece not in EQUIPEMENTS:
        return []
    reglage = (equipements or {}).get(type_piece)
    resultat = []
    for code, libelle, _ in EQUIPEMENTS[type_piece]:
        if reglage is None:
            present = code in PAR_DEFAUT.get(type_piece, ())
        else:
            present = bool(reglage.get(code))
        if not present:
            continue
        if code == "plaques" and reglage:
            precision = [dict(PLAQUES)[reglage["plaques_type"]]] if reglage.get("plaques_type") else []
            if reglage.get("feux"):
                precision.append(f"{reglage['feux']} feu{'x' if reglage['feux'] > 1 else ''}")
            if precision:
                libelle = f"{libelle} ({', '.join(precision)})"
        resultat.append(libelle)
    return resultat


def elements_pour(piece, equipements=None):
    """Éléments à contrôler pour une pièce : ses éléments classiques puis ses équipements, sinon la liste habituelle."""
    code = type_de_piece(piece)
    if code is None:
        return list(ELEMENTS_PIECE)
    return list(_PAR_CODE[code][3]) + equipements_de(code, equipements)


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
    equipements = (lot.configuration or {}).get("equipements")
    return [(p, elements_pour(p, equipements)) for p in lot.liste_pieces()]
