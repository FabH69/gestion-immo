from pathlib import Path

from django.core.files.base import ContentFile
from django.db import migrations

MODELES = Path(__file__).resolve().parent.parent / "modeles"
NOUVEAUX = [
    ("Bail location nue", "bail_nu", "bail_nu.docx"),
    ("Bail meublé", "bail_meuble", "bail_meuble.docx"),
    ("Acte de cautionnement", "caution", "acte_cautionnement.docx"),
    ("État des lieux d'entrée et de sortie", "edl", "etat_des_lieux.docx"),
    ("Inventaire des meubles", "inventaire", "inventaire_meubles.docx"),
    ("Reçu du dépôt de garantie", "recu_depot", "recu_depot_garantie.docx"),
]


def charger(apps, schema_editor):
    """Modèles à la nouvelle présentation (style du bail Vu). L'ancienne version est gardée, désactivée."""
    from django.conf import settings

    if not getattr(settings, "CHARGER_MODELES", True):  # pas pendant les tests
        return
    ModeleDocument = apps.get_model("patrimoine", "ModeleDocument")
    for nom, type_doc, fichier in NOUVEAUX:
        if not (MODELES / fichier).exists():
            continue
        for ancien in ModeleDocument.objects.filter(nom=nom):
            ancien.nom, ancien.actif = f"{nom} (ancienne présentation)", False
            ancien.save()
        m = ModeleDocument(nom=nom, type=type_doc, notes="Nouvelle présentation (novembre 2026)")
        m.fichier.save(fichier, ContentFile((MODELES / fichier).read_bytes()), save=False)
        m.save()


class Migration(migrations.Migration):
    dependencies = [("patrimoine", "0032_proprietaire_contact")]
    operations = [migrations.RunPython(charger, migrations.RunPython.noop)]
