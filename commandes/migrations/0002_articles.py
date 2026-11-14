"""Base articles : chaque ligne de commande porte un article, une quantité et un prix.

Les commandes existantes sont reprises : leur produit et leur prix deviennent un article.
"""
from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion

import commandes.models


def reprendre_commandes(apps, schema_editor):
    Article = apps.get_model("commandes", "Article")
    Commande = apps.get_model("commandes", "Commande")
    for c in Commande.objects.all():
        if c.statut == "ouverte":
            c.statut = "ferme"
            c.save(update_fields=["statut"])
        lignes = c.lignes.all()
        if not lignes.exists():
            continue
        article, _ = Article.objects.get_or_create(
            nom=c.produit or "Boîte de balles de tennis",
            prix_vente=c.prix_boite,
            defaults={"taux_commission": c.taux_commission},
        )
        lignes.update(article=article, prix_unitaire=c.prix_boite, taux_commission=c.taux_commission)


class Migration(migrations.Migration):

    dependencies = [
        ("commandes", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Article",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nom", models.CharField(help_text="Ex. « Tube de 4 balles Pulse »", max_length=200)),
                ("reference", models.CharField(blank=True, max_length=50, verbose_name="référence")),
                ("prix_vente", models.DecimalField(decimal_places=2, max_digits=8, verbose_name="prix de vente (€)")),
                ("taux_commission", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=5, verbose_name="ma commission (%)")),
                ("actif", models.BooleanField(default=True, help_text="Décochez pour ne plus le proposer dans les commandes.")),
                ("notes", models.TextField(blank=True)),
            ],
            options={"ordering": ["nom"]},
        ),
        migrations.RemoveConstraint(model_name="ligne", name="une_ligne_par_joueur"),
        migrations.RenameField(model_name="ligne", old_name="nb_boites", new_name="quantite"),
        migrations.AlterField(
            model_name="ligne", name="quantite",
            field=models.PositiveIntegerField(default=1, verbose_name="quantité"),
        ),
        migrations.AddField(
            model_name="ligne", name="article",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lignes", to="commandes.article"),
        ),
        migrations.AddField(
            model_name="ligne", name="prix_unitaire",
            field=models.DecimalField(decimal_places=2, max_digits=8, null=True, verbose_name="prix unitaire (€)"),
        ),
        migrations.AddField(
            model_name="ligne", name="taux_commission",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=5, verbose_name="commission (%)"),
        ),
        migrations.RunPython(reprendre_commandes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="ligne", name="article",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lignes", to="commandes.article"),
        ),
        migrations.AlterField(
            model_name="ligne", name="prix_unitaire",
            field=models.DecimalField(decimal_places=2, max_digits=8, verbose_name="prix unitaire (€)"),
        ),
        migrations.AlterModelOptions(name="ligne", options={"ordering": ["joueur__prenom", "joueur__nom", "article__nom"]}),
        migrations.RemoveField(model_name="commande", name="produit"),
        migrations.RemoveField(model_name="commande", name="prix_boite"),
        migrations.RemoveField(model_name="commande", name="taux_commission"),
        migrations.RemoveField(model_name="commande", name="date_limite"),
        migrations.AlterField(
            model_name="commande", name="statut",
            field=models.CharField(choices=[("ferme", "Ferme (validée)"), ("passee", "Passée au fournisseur"), ("recue", "Reçue, à distribuer"), ("terminee", "Terminée")], default="ferme", max_length=20),
        ),
        migrations.AlterField(
            model_name="commande", name="message_arrivee",
            field=models.TextField(default=commandes.models.MESSAGE_PAR_DEFAUT, help_text="Variables : {prenom}, {nom}, {detail}, {quantite}, {montant}, {lieu}, {commande}", verbose_name="message « commande arrivée »"),
        ),
    ]
