import django.db.models.deletion
from django.db import migrations, models


def rattacher_paiements_orphelins(apps, schema_editor):
    """Dernier filet : un paiement sans paiement reçu en devient un, avant que la base l'interdise."""
    Paiement = apps.get_model("patrimoine", "Paiement")
    Versement = apps.get_model("patrimoine", "Versement")
    for p in Paiement.objects.filter(versement__isnull=True).select_related("echeance"):
        v = Versement.objects.create(bail_id=p.echeance.bail_id, date=p.date, montant=p.montant, mode=p.mode, notes=p.notes[:255])
        p.versement = v
        p.save(update_fields=["versement"])


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0081_reprise_historique_mois_par_mois"),
    ]

    operations = [
        migrations.RunPython(rattacher_paiements_orphelins, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="paiement",
            name="versement",
            field=models.ForeignKey(help_text="Paiement reçu dont ce paiement est une part", on_delete=django.db.models.deletion.CASCADE,
                                    related_name="paiements", to="patrimoine.versement", verbose_name="paiement reçu"),
        ),
    ]
