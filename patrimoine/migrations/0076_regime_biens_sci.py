"""Les biens d'une SCI à l'IR prennent le régime de la SCI (revenus fonciers, réel) ; ceux d'une SCI à l'IS passent à l'IS.

Passe après 0075_immobilisations (PR 70).
"""
from django.db import migrations

REGIMES = {"sci_ir": ("sci_ir", "foncier"), "sci_is": ("societe_is", "societe")}


def aligner(apps, schema_editor):
    Bien = apps.get_model("patrimoine", "Bien")
    for type_proprio, (regime, compta) in REGIMES.items():
        Bien.objects.filter(proprietaire__type=type_proprio).update(
            regime_fiscal=regime, comptabilite=compta, option_fiscale="reel", regime_a_verifier=False)


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0075_immobilisations"),
    ]

    operations = [
        migrations.RunPython(aligner, migrations.RunPython.noop),
    ]
