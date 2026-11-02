from django.contrib import admin

from .models import Article, Commande, Joueur, Ligne


@admin.register(Joueur)
class JoueurAdmin(admin.ModelAdmin):
    list_display = ["prenom", "nom", "telephone", "email", "club", "actif"]
    list_filter = ["actif", "club"]
    search_fields = ["prenom", "nom", "telephone", "email"]


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ["nom", "reference", "prix_vente", "taux_commission", "actif"]
    list_filter = ["actif"]
    search_fields = ["nom", "reference"]


class LigneInline(admin.TabularInline):
    model = Ligne
    fields = ["joueur", "article", "quantite", "prix_unitaire", "taux_commission", "paye", "remis", "prevenu_le", "notes"]
    extra = 0
    autocomplete_fields = ["joueur", "article"]


@admin.register(Commande)
class CommandeAdmin(admin.ModelAdmin):
    list_display = ["nom", "statut", "date_creation"]
    list_filter = ["statut"]
    search_fields = ["nom"]
    inlines = [LigneInline]
