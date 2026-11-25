from django.db import migrations


def refaire_la_repartition(apps, schema_editor):
    """Paiements repris de l'historique : un paiement arrivé en retard règle aussi les loyers plus anciens restés impayés
    (au lieu de partir en avance sur les mois suivants). Une base sans paiement repris n'est pas touchée."""
    from patrimoine.reprise_historique import NOTE

    if not apps.get_model("patrimoine", "Versement").objects.filter(notes__startswith=NOTE).exists():
        return
    from patrimoine.reprise_historique import reconstruire

    reconstruire()


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0082_paiement_toujours_recu"),
    ]

    operations = [
        migrations.RunPython(refaire_la_repartition, migrations.RunPython.noop),
    ]
