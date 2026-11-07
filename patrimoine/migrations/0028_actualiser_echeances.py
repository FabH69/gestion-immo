from django.db import migrations


def loyer_au(bail, jour):
    appliquees = bail.revisions.filter(appliquee=True)
    revision = appliquees.filter(date_effet__lte=jour).order_by("-date_effet").first()
    if revision:
        return revision.nouveau_loyer
    suivante = appliquees.filter(date_effet__gt=jour).order_by("date_effet").first()
    if suivante:
        return suivante.ancien_loyer
    if bail.loyer_initial is not None and bail.date_derniere_revision and jour < bail.date_derniere_revision:
        return bail.loyer_initial
    return bail.loyer_hc


def actualiser(apps, schema_editor):
    """Échéances déjà générées et pas encore réglées : loyer en vigueur ce mois-là (révisions appliquées après coup)."""
    Echeance = apps.get_model("patrimoine", "Echeance")
    for e in Echeance.objects.filter(paiements__isnull=True).select_related("bail"):
        loyer = loyer_au(e.bail, e.periode)
        if e.loyer_hc != loyer:
            e.loyer_hc = loyer
            e.save(update_fields=["loyer_hc"])


class Migration(migrations.Migration):
    dependencies = [("patrimoine", "0027_justificatifs_et_mails")]
    operations = [migrations.RunPython(actualiser, migrations.RunPython.noop)]
