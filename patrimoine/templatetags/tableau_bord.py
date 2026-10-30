from django import template
from django.utils import timezone

from patrimoine.rappels import a_faire

register = template.Library()


@register.inclusion_tag("patrimoine/_a_faire.html", takes_context=True)
def bloc_a_faire(context):
    items = a_faire(timezone.localdate())
    return {"items": items, "nb_retard": sum(i["en_retard"] for i in items), "csrf_token": context.get("csrf_token")}
