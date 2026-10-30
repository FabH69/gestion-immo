from django.contrib import admin

from .models import Bail, Bien, Locataire, Lot, Proprietaire

admin.site.site_header = "Gestion immobilière"
admin.site.site_title = "Gestion immobilière"
admin.site.index_title = "Tableau de bord"


class BienInline(admin.TabularInline):
    model = Bien
    fields = ["nom", "ville"]
    extra = 0
    show_change_link = True


class LotInline(admin.TabularInline):
    model = Lot
    fields = ["designation", "type", "surface", "meuble"]
    extra = 0
    show_change_link = True


class BailInline(admin.TabularInline):
    model = Bail
    fields = ["type", "date_debut", "date_fin", "loyer_hc", "charges", "actif"]
    extra = 0
    show_change_link = True


@admin.register(Proprietaire)
class ProprietaireAdmin(admin.ModelAdmin):
    list_display = ["nom", "type", "nb_biens"]
    list_filter = ["type"]
    inlines = [BienInline]

    @admin.display(description="biens")
    def nb_biens(self, obj):
        return obj.biens.count()


@admin.register(Bien)
class BienAdmin(admin.ModelAdmin):
    list_display = ["nom", "ville", "proprietaire", "nb_lots"]
    list_filter = ["proprietaire", "ville"]
    search_fields = ["nom", "adresse", "ville"]
    inlines = [LotInline]

    @admin.display(description="lots")
    def nb_lots(self, obj):
        return obj.lots.count()


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ["designation", "bien", "type", "surface", "meuble", "locataire_actuel", "loyer_actuel"]
    list_filter = ["bien__proprietaire", "bien", "type", "meuble"]
    search_fields = ["designation", "bien__nom"]
    inlines = [BailInline]

    @admin.display(description="locataire")
    def locataire_actuel(self, obj):
        bail = obj.bail_actif
        return ", ".join(str(l) for l in bail.locataires.all()) if bail else "Vacant"

    @admin.display(description="loyer CC (€)")
    def loyer_actuel(self, obj):
        bail = obj.bail_actif
        return bail.loyer_cc if bail else "–"


@admin.register(Locataire)
class LocataireAdmin(admin.ModelAdmin):
    list_display = ["__str__", "email", "telephone"]
    search_fields = ["nom", "prenom", "email"]


@admin.register(Bail)
class BailAdmin(admin.ModelAdmin):
    list_display = ["lot", "liste_locataires", "type", "date_debut", "loyer_hc", "charges", "loyer_cc", "actif"]
    list_filter = ["actif", "type", "lot__bien__proprietaire"]
    search_fields = ["lot__designation", "lot__bien__nom", "locataires__nom"]
    filter_horizontal = ["locataires"]
    date_hierarchy = "date_debut"

    @admin.display(description="locataires")
    def liste_locataires(self, obj):
        return ", ".join(str(l) for l in obj.locataires.all())

    @admin.display(description="loyer CC (€)")
    def loyer_cc(self, obj):
        return obj.loyer_cc
