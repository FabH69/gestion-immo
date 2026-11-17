# Fusion des branches de migrations : 0060_fusion_regimes_factures_electroniques (PR #20, identique ici)
# et 0060_libelles_retours (cette PR). Ainsi « migrate » marche quel que soit l'ordre de fusion de #15 et #20.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("patrimoine", "0060_fusion_regimes_factures_electroniques"),
        ("patrimoine", "0060_libelles_retours"),
    ]

    operations = []
