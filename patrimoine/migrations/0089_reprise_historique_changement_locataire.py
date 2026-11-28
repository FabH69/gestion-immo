from django.db import migrations


def refaire_la_repartition(apps, schema_editor):
    """Paiements repris de l'historique : le mois d'un changement de locataire, la part du locataire sortant lui revient
    (au lieu de partir en avance chez le nouveau et de couper chacun de ses loyers en deux). Une base sans paiement
    repris n'est pas touchée."""
    from patrimoine.reprise_historique import NOTE

    if not apps.get_model("patrimoine", "Versement").objects.filter(notes__startswith=NOTE).exists():
        return
    from patrimoine.reprise_historique import reconstruire

    reconstruire()


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0088_biens_geres_par_agence"),
    ]

    operations = [
        migrations.RunPython(refaire_la_repartition, migrations.RunPython.noop),
    ]
