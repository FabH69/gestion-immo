from datetime import date, timedelta

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import Bail, Bien, Echeance, Locataire, Lot, Paiement, Proprietaire

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


class PaiementInline(admin.TabularInline):
    model = Paiement
    fields = ["date", "montant", "mode", "notes"]
    extra = 1


class EnRetardFilter(admin.SimpleListFilter):
    title = "retard"
    parameter_name = "retard"

    def lookups(self, request, model_admin):
        return [("oui", "Impayés en retard")]

    def queryset(self, request, queryset):
        if self.value() == "oui":
            return queryset.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=timezone.localdate())
        return queryset


@admin.register(Echeance)
class EcheanceAdmin(admin.ModelAdmin):
    list_display = ["mois", "lot", "liste_locataires", "total", "paye", "reste", "statut_colore", "document"]
    list_filter = [EnRetardFilter, "statut", "bail__lot__bien__proprietaire", "bail__lot__bien"]
    search_fields = ["bail__lot__designation", "bail__lot__bien__nom", "bail__locataires__nom"]
    date_hierarchy = "periode"
    readonly_fields = ["statut", "document"]
    fields = ["bail", "periode", "loyer_hc", "charges", "date_exigibilite", "statut", "document"]
    inlines = [PaiementInline]
    actions = ["marquer_paye"]
    change_list_template = "admin/patrimoine/echeance/change_list.html"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("bail__lot__bien").prefetch_related("bail__locataires", "paiements")

    def get_urls(self):
        return [
            path("generer/", self.admin_site.admin_view(self.generer_view), name="patrimoine_echeance_generer"),
        ] + super().get_urls()

    def generer_view(self, request):
        aujourdhui = timezone.localdate()
        mois = request.GET.get("mois")
        if mois:
            annee, m = map(int, mois.split("-"))
            periode = date(annee, m, 1)
        else:
            periode = aujourdhui.replace(day=1)
        n = Echeance.generer_pour_mois(periode)
        self.message_user(request, f"{n} échéance(s) créée(s) pour {periode:%m/%Y}.", messages.SUCCESS)
        return redirect("admin:patrimoine_echeance_changelist")

    def changelist_view(self, request, extra_context=None):
        aujourdhui = timezone.localdate().replace(day=1)
        suivant = (aujourdhui.replace(day=28) + timedelta(days=4)).replace(day=1)
        extra_context = {
            **(extra_context or {}),
            "mois_courant": aujourdhui,
            "mois_suivant": suivant,
        }
        return super().changelist_view(request, extra_context)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.mettre_a_jour_statut()

    @admin.display(description="mois", ordering="periode")
    def mois(self, obj):
        return obj.periode.strftime("%m/%Y")

    @admin.display(description="lot", ordering="bail__lot__bien__nom")
    def lot(self, obj):
        return obj.bail.lot

    @admin.display(description="locataires")
    def liste_locataires(self, obj):
        return ", ".join(str(l) for l in obj.bail.locataires.all())

    @admin.display(description="dû (€)")
    def total(self, obj):
        return obj.total

    @admin.display(description="payé (€)")
    def paye(self, obj):
        return sum(p.montant for p in obj.paiements.all())

    @admin.display(description="reste (€)")
    def reste(self, obj):
        return obj.total - self.paye(obj)

    @admin.display(description="statut", ordering="statut")
    def statut_colore(self, obj):
        couleurs = {"paye": "#2e7d32", "partiel": "#ef6c00", "a_payer": "#c62828"}
        libelle = obj.get_statut_display()
        if obj.statut != "paye" and obj.date_exigibilite < timezone.localdate():
            libelle += " (en retard)"
        return format_html('<strong style="color:{}">{}</strong>', couleurs[obj.statut], libelle)

    @admin.display(description="document")
    def document(self, obj):
        if not obj.pk:
            return "–"
        libelle = "Quittance PDF" if obj.statut == "paye" else "Avis d'échéance PDF"
        return format_html('<a href="{}" target="_blank">{}</a>', reverse("document_echeance", args=[obj.pk]), libelle)

    @admin.action(description="Marquer comme payé intégralement (virement, aujourd'hui)")
    def marquer_paye(self, request, queryset):
        n = 0
        for e in queryset.exclude(statut=Echeance.Statut.PAYE):
            Paiement.objects.create(echeance=e, date=timezone.localdate(), montant=e.reste_du)
            n += 1
        self.message_user(request, f"{n} échéance(s) marquée(s) comme payée(s).", messages.SUCCESS)
