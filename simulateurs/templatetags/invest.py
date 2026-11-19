"""Affichage des montants du module investisseur : « 1 234 € », « +47 € », « 8,11 % »."""
from django import template

register = template.Library()
ESPACE = " "  # espace fine insécable, comme les montants de Django en français


def _groupe(valeur, decimales=0):
    texte = f"{abs(valeur):,.{decimales}f}".replace(",", ESPACE).replace(".", ",")
    return ("-" if valeur < 0 and texte.strip("0,  ") else "") + texte


@register.filter
def euros(valeur, decimales=0):
    if valeur is None or valeur == "":
        return "–"
    return f"{_groupe(float(valeur), int(decimales))} €"


@register.filter
def euros_signe(valeur):
    if valeur is None:
        return "–"
    v = float(valeur)
    return ("+" if round(v) > 0 else "") + euros(v)


@register.filter
def pct(valeur, decimales=1):
    """0,0811 → « 8,1 % »."""
    if valeur is None or valeur == "":
        return "–"
    return f"{_groupe(float(valeur) * 100, int(decimales))} %"


@register.filter
def saisie(valeur):
    """Valeur d'un champ de saisie : 190000.0 → « 190000 », 4.2 → « 4,2 », 0 → « 0 »."""
    if isinstance(valeur, bool) or valeur is None:
        return ""
    if isinstance(valeur, (int, float)):
        v = round(float(valeur), 4)
        return str(int(v)) if v == int(v) else f"{v:g}".replace(".", ",")
    return valeur


@register.filter
def signe_classe(valeur):
    return "pos" if valeur is not None and float(valeur) >= 0 else "neg"


@register.filter
def attribut(objet, nom):
    return getattr(objet, nom, "")


@register.filter
def div_ou_zero(a, b):
    try:
        return float(a) / float(b) if float(b) else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def index(liste, i):
    try:
        return liste[int(i)]
    except (IndexError, TypeError, ValueError):
        return ""
