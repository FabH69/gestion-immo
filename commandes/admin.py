from django.contrib import admin

from .models import Commande, Joueur, Ligne


@admin.register(Joueur)
class JoueurAdmin(admin.ModelAdmin):
    list_display = ["prenom", "nom", "telephone", "email", "club", "actif"]
    list_filter = ["actif", "club"]
    search_fields = ["prenom", "nom", "telephone", "email"]


class LigneInline(admin.TabularInline):
    model = Ligne
    fields = ["joueur", "nb_boites", "paye", "remis", "prevenu_le", "notes"]
    extra = 0
    autocomplete_fields = ["joueur"]


@admin.register(Commande)
class CommandeAdmin(admin.ModelAdmin):
    list_display = ["nom", "statut", "date_creation", "prix_boite", "taux_commission"]
    list_filter = ["statut"]
    search_fields = ["nom"]
    inlines = [LigneInline]
