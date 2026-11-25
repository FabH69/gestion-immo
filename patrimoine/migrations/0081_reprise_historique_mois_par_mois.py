from django.db import migrations


def refaire_la_repartition(apps, schema_editor):
    """Paiements repris de l'historique (0080) : chacun règle d'abord le loyer de son propre mois, les mois à 0 € sont
    retirés (voir patrimoine/reprise_historique.py). Une base sans paiement repris n'est pas touchée."""
    from patrimoine.reprise_historique import NOTE

    if not apps.get_model("patrimoine", "Versement").objects.filter(notes__startswith=NOTE).exists():
        return
    from patrimoine.reprise_historique import reconstruire

    reconstruire()


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0080_historique_en_paiements_recus"),
    ]

    operations = [
        migrations.RunPython(refaire_la_repartition, migrations.RunPython.noop),
    ]
