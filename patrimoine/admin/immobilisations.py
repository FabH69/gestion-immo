"""Fiche d'une immobilisation (une fiche par immobilisation) avec son plan d'amortissement."""
from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .. import amortissements
from ..models import Bien, DeclarationFaite, Immobilisation, Lot, ReportAmortissements
from .communs import euros_liste

CHAMPS_DU_PLAN = ("valeur", "duree", "mise_en_service", "annee_reprise", "amortissements_anterieurs", "date_sortie")


class ImmobilisationForm(forms.ModelForm):
    class Meta:
        model = Immobilisation
        fields = "__all__"
        widgets = {f: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
                   for f in ("date_achat", "mise_en_service", "garantie_jusqua", "date_sortie")} | {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "bien" in self.fields:
            self.fields["bien"].queryset = Bien.objects.filter(comptabilite__in=("lmnp", "societe")).select_related("proprietaire")
            self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}"
        if "lot" in self.fields:
            self.fields["lot"].queryset = Lot.objects.filter(bien__comptabilite__in=("lmnp", "societe")).select_related("bien")
            self.fields["lot"].label_from_instance = lambda l: f"{l.bien.nom} – {l.designation}"
        if "piece" in self.fields:
            self.fields["piece"].widget = forms.HiddenInput()

    def clean(self):
        d = super().clean()
        bien, lot = d.get("bien"), d.get("lot")
        if bien and lot and lot.bien_id != bien.pk:
            self.add_error("lot", "Ce lot n'appartient pas au bien choisi.")
        if d.get("valeur") is not None and d.get("valeur") <= 0:
            self.add_error("valeur", "La valeur d'origine doit être positive.")
        if d.get("valeur") is not None and (d.get("amortissements_anterieurs") or 0) > d["valeur"]:
            self.add_error("amortissements_anterieurs", "Plus que la valeur d'origine.")
        if d.get("date_sortie") and d.get("mise_en_service") and d["date_sortie"] < d["mise_en_service"]:
            self.add_error("date_sortie", "Avant la mise en service.")
        if (d.get("amortissements_anterieurs") or 0) and not d.get("annee_reprise"):
            self.add_error("annee_reprise", "Indiquez l'année à partir de laquelle l'appli calcule (les cumuls sont ceux d'avant).")
        return d


@admin.register(Immobilisation)
class ImmobilisationAdmin(admin.ModelAdmin):
    form = ImmobilisationForm
    list_display = ["numero", "designation", "bien", "categorie", "mise_en_service", "valeur_affichee", "duree_affichee", "dotation_annee", "origine"]
    list_display_links = ["numero", "designation"]
    list_filter = ["bien__comptabilite", "bien__proprietaire", "bien", "categorie", "origine"]
    search_fields = ["designation", "numero", "fournisseur", "bien__nom", "notes"]
    date_hierarchy = "mise_en_service"
    fieldsets = [
        ("Identification", {"fields": ["designation", ("bien", "lot"), ("categorie", "numero"), "origine", "piece"]}),
        ("Acquisition", {"fields": [("fournisseur", "date_achat"), ("valeur", "mise_en_service"), "garantie_jusqua"]}),
        ("Amortissement", {"fields": ["duree", ("annee_reprise", "amortissements_anterieurs")],
                           "description": "Linéaire, au prorata la première année (année de 360 jours). Reprise : laissez vide si l'appli "
                                          "doit tout calculer depuis la mise en service ; sinon indiquez la première année calculée "
                                          "par l'appli et le cumul du comptable au 31/12 de l'année d'avant."}),
        ("Sortie", {"classes": ["collapse"], "fields": [("date_sortie", "motif_sortie"), "prix_cession"]}),
        (None, {"fields": ["notes"]}),
    ]

    @admin.display(description="valeur", ordering="valeur")
    def valeur_affichee(self, obj):
        return euros_liste(obj.valeur)

    @admin.display(description="durée", ordering="duree")
    def duree_affichee(self, obj):
        return f"{obj.duree} ans" if obj.duree else "non amorti"

    @admin.display(description="dotation de l'année")
    def dotation_annee(self, obj):
        return euros_liste(amortissements.dotation(obj, timezone.localdate().year))

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("duree", amortissements.duree_proposee(initial.get("categorie", "mobilier")))
        initial.setdefault("mise_en_service", timezone.localdate())
        return initial

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        immo = Immobilisation.objects.filter(pk=object_id).select_related("bien", "piece").first() if object_id and str(object_id).isdigit() else None
        extra = {"durees": amortissements.DUREES}
        if immo:
            annee = timezone.localdate().year
            lignes = amortissements.plan(immo)
            declares = DeclarationFaite.verrous(blocages=False)
            for l in lignes:
                l["declaree"] = not l["reprise"] and bool(declares.get(l["annee"], {}).get(immo.bien_id))
                l["courante"] = l["annee"] == annee
            extra.update({"plan": lignes, "annuel": (immo.valeur / immo.duree) if immo.duree else None,
                          "fin_plan": lignes[-1]["annee"] if lignes else None, "une_declaree": any(l["declaree"] for l in lignes), "annee_courante": annee,
                          "dotation_courante": amortissements.dotation(immo, annee),
                          "plus_value": (immo.prix_cession - amortissements.situation(immo, immo.date_sortie.year)["vnc"])
                          if immo.date_sortie and immo.prix_cession is not None else None,
                          "lien_registre": _lien_registre(immo)})
        return super().changeform_view(request, object_id, form_url, {**(extra_context or {}), **extra})

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and any(c in form.changed_data for c in CHAMPS_DU_PLAN):
            declares = DeclarationFaite.verrous(blocages=False)
            touchees = sorted({l["annee"] for l in amortissements.plan(obj) if not l["reprise"]
                               and declares.get(l["annee"], {}).get(obj.bien_id)})
            if touchees:
                messages.warning(request, f"Attention : {', '.join(map(str, touchees))} déjà déclarée"
                                          f"{'s' if len(touchees) > 1 else ''}. Les dotations de ces années ont pu changer ; "
                                          "la déclaration faite garde les anciens montants.")


def _lien_registre(immo):
    from .. import dossiers

    d = dossiers.dossier_du_bien(immo.bien_id)
    return reverse("immobilisations", args=[d["code"]]) if d and dossiers.amortissable(d) else ""


@admin.register(ReportAmortissements)
class ReportAmortissementsAdmin(admin.ModelAdmin):
    list_display = ["dossier", "annee", "montant"]
