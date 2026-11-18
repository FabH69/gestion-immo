"""Écrans de la location courte durée et des charges LMNP."""
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from ..models import ChargeLMNP, ChargeLCD, ParametresLCD, ReservationLCD, Lot
from .communs import LotsALouerMixin


@admin.register(ChargeLMNP)
class ChargeLMNPAdmin(admin.ModelAdmin):
    list_display = ["annee", "lot", "categorie", "libelle", "montant"]
    list_filter = ["annee", "lot__bien__proprietaire"]
    fields = ["lot", "annee", "categorie", "libelle", "montant"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "lot":
            kwargs["queryset"] = Lot.objects.filter(lmnp=True).select_related("bien")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("annee", timezone.localdate().year - 1)
        return initial

    def changelist_view(self, request, extra_context=None):
        from ..views.compta import onglets

        extra_context = {**(extra_context or {}), "onglets": onglets("lmnp", "amortissements"), "libelle_regime": "LMNP",
                         "lien_regime": reverse("compta_lmnp"), "title": "Charges et amortissements LMNP"}
        return super().changelist_view(request, extra_context)

    def response_add(self, request, obj, post_url_continue=None):
        if "_continue" not in request.POST and "_addanother" not in request.POST:
            return redirect(f"{reverse('biens_lmnp')}?annee={obj.annee}")
        return super().response_add(request, obj, post_url_continue)


@admin.register(ReservationLCD)
class ReservationLCDAdmin(LotsALouerMixin, admin.ModelAdmin):
    list_display = ["arrivee", "depart", "nuits", "voyageur", "plateforme", "lot", "montant_brut", "commission", "frais_service", "taxe_sejour", "net", "annulee"]
    list_filter = ["plateforme", "lot", "annulee"]
    search_fields = ["voyageur", "reference"]
    date_hierarchy = "arrivee"
    navigation_par = "mois"

    @admin.display(description="nuits")
    def nuits(self, obj):
        return obj.nuits

    @admin.display(description="net (€)")
    def net(self, obj):
        return obj.net


@admin.register(ChargeLCD)
class ChargeLCDAdmin(admin.ModelAdmin):
    date_hierarchy = "debut"
    navigation_par = "mois"
    list_display = ["libelle", "lot", "montant_mensuel", "debut", "fin"]
    list_filter = ["lot"]


@admin.register(ParametresLCD)
class ParametresLCDAdmin(LotsALouerMixin, admin.ModelAdmin):
    list_display = ["lot", "menage_par_sejour", "consommables_par_sejour", "commission_gestion"]
