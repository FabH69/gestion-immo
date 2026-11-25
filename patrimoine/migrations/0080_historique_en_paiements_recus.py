from django.db import migrations


def convertir_historique(apps, schema_editor):
    """Loyers encaissés repris d'un ancien suivi : chacun devient un paiement reçu, réparti sur les loyers qu'il couvre
    (voir patrimoine/reprise_historique.py). Une base sans historique (base neuve, tests) n'est pas touchée."""
    if not apps.get_model("patrimoine", "EncaissementHistorique").objects.exists():
        return
    from patrimoine.reprise_historique import convertir

    convertir()


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0079_merge_encaissement_preferences_accueil"),
    ]

    operations = [
        migrations.RunPython(convertir_historique, migrations.RunPython.noop),
    ]
