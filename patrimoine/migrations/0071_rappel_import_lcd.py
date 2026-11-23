"""Rappel mensuel (le 5) « Importer les réservations du mois précédent » pour chaque lot déjà en courte durée."""
from django.db import migrations

TITRE_IMPORT_LCD = "Importer les réservations du mois précédent (Airbnb, Booking)"  # comme rappels.TITRE_IMPORT_LCD
JOUR_IMPORT_LCD = 5


def creer(apps, schema_editor):
    from datetime import date

    from django.utils import timezone

    Lot = apps.get_model("patrimoine", "Lot")
    Rappel = apps.get_model("patrimoine", "Rappel")
    aujourdhui = timezone.localdate()
    echeance = date(aujourdhui.year, aujourdhui.month, JOUR_IMPORT_LCD)
    if echeance < aujourdhui:
        echeance = date(aujourdhui.year + (aujourdhui.month == 12), aujourdhui.month % 12 + 1, JOUR_IMPORT_LCD)
    for lot in Lot.objects.filter(exploitation="courte", vendu=False, bien__vendu=False).select_related("bien"):
        nom = lot.designation if lot.designation.strip().lower() == lot.bien.nom.strip().lower() else f"{lot.bien.nom} – {lot.designation}"
        titre = f"{TITRE_IMPORT_LCD} – {nom}"
        if Rappel.objects.filter(bien=lot.bien, titre=titre).exists():
            continue
        Rappel.objects.create(
            titre=titre, date=echeance, categorie="administratif", bien=lot.bien, periodicite="mensuel", prevenir_jours=0,
            dans_agenda=True,
            notes="Téléchargez les exports Airbnb et Booking du mois qui vient de se terminer, puis importez-les dans "
                  "Courte durée › Suivi mensuel (choisir le logement, bouton d'import). Cochez « fait » : le rappel passe au 5 du mois suivant.")


class Migration(migrations.Migration):
    dependencies = [
        ("patrimoine", "0070_depenses_recurrentes"),
    ]

    operations = [migrations.RunPython(creer, migrations.RunPython.noop)]
