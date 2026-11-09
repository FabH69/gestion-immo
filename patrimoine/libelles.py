"""Libellés en bon français pour les écrans de l'administration : articles, titres, messages."""
import re

from django.utils.safestring import SafeString, mark_safe

# Premier mot des noms féminins (les autres sont masculins).
FEMININS = {"annonce", "candidature", "tâche", "photo", "réservation", "charge", "note", "facture", "révision",
            "pièce", "relance", "quittance", "permission"}
PLURIELS = {"paramètres"}
VOYELLES = "aàâäeéèêëiîïoôöuùûüyh"


def _premier(nom):
    return str(nom).split()[0].lower() if str(nom).split() else ""


def un(nom):
    """« un bail », « une annonce », « des paramètres courte durée »."""
    premier = _premier(nom)
    if premier in PLURIELS:
        return f"des {nom}"
    return f"{'une' if premier in FEMININS else 'un'} {nom}"


def le(nom):
    """« le bail », « la tâche », « l'annonce », « les paramètres courte durée »."""
    nom = str(nom)
    premier = _premier(nom)
    if premier in PLURIELS:
        return f"les {nom}"
    if nom[:1].lower() in VOYELLES:
        return f"l'{nom}"
    return f"{'la' if premier in FEMININS else 'le'} {nom}"


def ajouter(nom):
    return f"Ajouter {un(nom)}"


def modifier(nom):
    return f"Modifier {le(nom)}"


# Messages de Django après un enregistrement : « L'objet loyer « X » a été ajouté avec succès. » → « Enregistrement effectué : « X ». »
_MESSAGE = re.compile(r"^L['’]objet (?P<nom>.+?) «\s*(?P<obj>.*?)\s*» a été (?P<verbe>ajouté|modifié|supprimé) avec succès\.(?P<suite>.*)$", re.S)
_DEBUT = {"ajouté": "Enregistrement effectué", "modifié": "Modifications enregistrées", "supprimé": "Suppression effectuée"}


def message(texte):
    m = _MESSAGE.match(str(texte))
    if not m:
        return texte
    suite = m["suite"].strip()
    if "éditer" in suite:
        suite = " Vous pouvez continuer ci-dessous."
    elif "ajouter un" in suite:
        suite = " Vous pouvez en saisir un autre ci-dessous."
    elif suite:
        suite = " " + suite
    nouveau = f"{_DEBUT[m['verbe']]}\u00a0: «\u00a0{m['obj']}\u00a0».{suite}"
    return mark_safe(nouveau) if isinstance(texte, SafeString) else nouveau
