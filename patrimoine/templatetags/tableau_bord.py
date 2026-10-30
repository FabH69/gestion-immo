from datetime import timedelta

from django import template
from django.utils import timezone

from patrimoine.models import Tache

register = template.Library()


@register.inclusion_tag("patrimoine/_taches_a_venir.html")
def taches_a_venir(jours=30):
    aujourdhui = timezone.localdate()
    taches = (
        Tache.objects.filter(faite=False, changement__termine=False, echeance__lte=aujourdhui + timedelta(days=jours))
        .select_related("changement__lot__bien")
        .order_by("echeance")[:10]
    )
    return {"taches": taches, "aujourdhui": aujourdhui}
