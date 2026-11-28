from django.db import migrations


def nettoyer(apps, schema_editor):
    """Logements avec un historique repris : loyers en dehors des dates de leur bail retirés, avec leurs paiements
    « Supposé réglé » et les doublons de l'historique (ex. Saint Pierre 2 : 113,80 € de M. Lassus en avril 2025,
    parti le 30/06/2024). Une base sans historique repris n'est pas touchée."""
    from patrimoine.models import Lot
    from patrimoine.reprise_historique import NOTE, nettoyer_hors_bail

    lots = Lot.objects.filter(baux__versements__notes__startswith=NOTE).distinct()
    if lots.exists():
        nettoyer_hors_bail(list(lots))


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0091_merge_20261128_1745"),
    ]

    operations = [
        migrations.RunPython(nettoyer, migrations.RunPython.noop),
    ]
