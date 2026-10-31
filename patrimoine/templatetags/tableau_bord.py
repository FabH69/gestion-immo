from django import template
from django.utils import timezone

from patrimoine.rappels import HISTORIQUE_JOURS, a_faire, deja_fait

register = template.Library()


@register.inclusion_tag("patrimoine/_a_faire.html", takes_context=True)
def bloc_a_faire(context):
    aujourdhui = timezone.localdate()
    request = context.get("request")
    vue = "fait" if request and request.GET.get("vue") == "fait" else "a_faire"
    items = a_faire(aujourdhui)
    contexte = {"vue": vue, "nb_a_faire": len(items), "nb_retard": sum(i["en_retard"] for i in items),
                "historique_jours": HISTORIQUE_JOURS, "csrf_token": context.get("csrf_token")}
    contexte["items"] = deja_fait(aujourdhui) if vue == "fait" else items
    return contexte


@register.filter
def get_item(conteneur, cle):
    """Accès à un élément de dictionnaire ou de liste depuis un gabarit."""
    try:
        return conteneur[cle]
    except (KeyError, IndexError, TypeError):
        return None


@register.filter
def negatif(valeur):
    """Montant de dépense affiché en négatif, arrondi à l'euro, sans « −0 »."""
    arrondi = round(valeur or 0)
    if not arrondi:
        return "0"
    return "−" + f"{arrondi:,}".replace(",", "\u202f")
