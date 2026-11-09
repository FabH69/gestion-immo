"""Écrans des propriétaires, syndics, biens et lots."""
from decimal import Decimal

from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from ..models import Syndic, Bail, Bien, PhotoLot, Lot, Proprietaire
from .communs import euros_liste


class BienInline(admin.TabularInline):
    model = Bien
    fields = ["nom", "ville"]
    extra = 0
    show_change_link = True


class PhotoInline(admin.TabularInline):
    model = PhotoLot
    fields = ["apercu", "image", "legende", "ordre"]
    readonly_fields = ["apercu"]
    extra = 3
    verbose_name_plural = "Photos (pour les annonces)"

    @admin.display(description="aperçu")
    def apercu(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="height:70px;border-radius:8px">', obj.image.url)
        return "–"


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
    list_display = ["nom", "type", "siret", "ville", "nb_biens"]
    list_filter = ["type"]
    inlines = [BienInline]
    fields = ["nom", ("type", "pourcentage_detention"), "representant", ("siret", "rcs", "date_immatriculation"), "adresse", ("code_postal", "ville"), ("email", "telephone"), "iban", "notes"]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        champ = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "adresse":
            champ.widget.attrs.update(rows=2)
        return champ

    @admin.display(description="biens")
    def nb_biens(self, obj):
        return obj.biens.count()


@admin.register(Syndic)
class SyndicAdmin(admin.ModelAdmin):
    list_display = ["nom", "ville", "telephone", "contact_nom", "contact_telephone", "lien_email", "lien_site", "liste_biens"]
    search_fields = ["nom", "ville", "contact_nom"]
    fieldsets = [
        (None, {"fields": ["nom", "adresse", ("code_postal", "ville"), ("email", "telephone"), "site"]}),
        ("Contact principal", {"fields": ["contact_nom", ("contact_email", "contact_telephone")]}),
        (None, {"fields": ["notes"]}),
    ]

    @admin.display(description="e-mail du contact")
    def lien_email(self, obj):
        email = obj.contact_email or obj.email
        return format_html('<a href="mailto:{}">{}</a>', email, email) if email else "–"

    @admin.display(description="site")
    def lien_site(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener">Ouvrir</a>', obj.site) if obj.site else "–"

    @admin.display(description="biens")
    def liste_biens(self, obj):
        return ", ".join(b.nom for b in obj.biens.all()) or "–"


class VenduFilter(admin.SimpleListFilter):
    """Biens et lots non vendus affichés par défaut ; les vendus restent consultables (historique conservé)."""
    title = "vente"
    parameter_name = "vente"

    def lookups(self, request, model_admin):
        return [("non", "Non vendus"), ("oui", "Vendus"), ("tous", "Tous")]

    def choices(self, changelist):
        valeur = self.value() or "non"
        for code, libelle in self.lookup_choices:
            yield {"selected": valeur == code, "query_string": changelist.get_query_string({self.parameter_name: code}), "display": libelle}

    def queryset(self, request, queryset):
        from django.db.models import Q
        vendu = Q(vendu=True) | Q(bien__vendu=True) if queryset.model is Lot else Q(vendu=True)
        valeur = self.value() or "non"
        if valeur == "non":
            return queryset.exclude(vendu)
        if valeur == "oui":
            return queryset.filter(vendu)
        return queryset


@admin.register(Bien)
class BienAdmin(admin.ModelAdmin):
    list_display = ["nom", "ville", "proprietaire", "syndic", "nb_lots", "loyers_mois", "rendement"]
    list_filter = [VenduFilter, "proprietaire"]

    def get_list_display(self, request):
        """Colonnes de vente seulement quand on regarde les biens vendus (sinon des croix rouges partout)."""
        vente = request.GET.get("vente") or "non"
        if vente == "oui":
            return ["nom", "ville", "proprietaire", "nb_lots", "date_vente"]
        if vente == "tous":
            return self.list_display + ["vendu", "date_vente"]
        return self.list_display

    def get_queryset(self, request):
        from django.db.models import Prefetch
        return super().get_queryset(request).select_related("proprietaire", "syndic").prefetch_related(
            "lots", Prefetch("lots__baux", queryset=Bail.objects.filter(actif=True), to_attr="baux_actifs"))

    def _loyers_hc(self, obj):
        return sum((b.loyer_hc or 0 for lot in obj.lots.all() for b in lot.baux_actifs), Decimal(0))

    @admin.display(description="loyers HC / mois")
    def loyers_mois(self, obj):
        total = self._loyers_hc(obj)
        return f"{total:,.0f} €".replace(",", " ") if total else "–"

    @admin.display(description="rendement brut")
    def rendement(self, obj):
        total = self._loyers_hc(obj)
        if not total or not obj.prix_acquisition:
            return "–"
        return f"{total * 12 / obj.prix_acquisition * 100:.1f} %".replace(".", ",")
    search_fields = ["nom", "adresse", "ville"]
    inlines = [LotInline]
    fieldsets = [
        (None, {"fields": ["proprietaire", "nom", "adresse", ("code_postal", "ville"), "syndic", ("date_acquisition", "prix_acquisition"),
                           "comptabilite"]}),
        ("L'immeuble (repris dans les baux)", {"fields": [("type_habitat", "regime_juridique")]}),
        ("Équipements à usage commun", {"classes": ["cases-a-cocher"], "fields": [
            ("gardiennage", "ascenseur", "interphone", "local_velos"),
            ("chauffage_collectif", "eau_chaude_collective", "antenne_tv", "fibre"),
            ("vide_ordures", "espaces_verts")]}),
        ("Zone tendue", {"classes": ["cases-a-cocher"], "fields": [("zone_tendue", "encadrement_loyers")],
                         "description": format_html(
                             "<b>Zone tendue</b> : commune où la hausse du loyer à la relocation est limitée (décret annuel).<br>"
                             "<b>Encadrement des loyers</b> : loyer de référence majoré fixé par arrêté préfectoral (Lyon, Villeurbanne, Paris…).<br>"
                             "Si l'une des cases est cochée, le bail contient le paragraphe « zone tendue » : les chiffres "
                             "(loyer de référence, complément, loyer du précédent locataire) se saisissent dans chaque bail.")}),
        ("Usage", {"classes": ["cases-a-cocher"], "fields": ["residence_principale"]}),
        ("Vente", {"classes": ["cases-a-cocher"], "fields": [("vendu", "date_vente")],
                   "description": "Un bien vendu (et tous ses lots) n'est plus proposé pour les baux, candidatures et annonces, "
                                  "mais reste dans les états des loyers. La date de vente est facultative."}),
        ("Notes", {"fields": ["notes"]}),
    ]

    def save_model(self, request, obj, form, change):
        if obj.date_vente:
            obj.vendu = True
        avant = Bien.objects.filter(pk=obj.pk).values("vendu", "date_vente").first() if change else None
        super().save_model(request, obj, form, change)
        if obj.vendu:
            # tous les lots du bien passent en vendu ; la date du bien complète celles qui manquent
            obj.lots.filter(vendu=False).update(vendu=True)
            if obj.date_vente:
                obj.lots.filter(date_vente__isnull=True).update(date_vente=obj.date_vente)
        elif avant and avant["vendu"]:
            # case décochée : on annule ce que la vente du bien avait fait (sans toucher aux lots vendus à part)
            lots = obj.lots.filter(date_vente=avant["date_vente"]) if avant["date_vente"] else obj.lots.filter(date_vente__isnull=True)
            lots.update(vendu=False, date_vente=None)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "notes" in form.base_fields:
            form.base_fields["notes"].widget.attrs.update(rows=3)
        if "date_vente" in form.base_fields:
            form.base_fields["date_vente"].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        return form

    @admin.display(description="lots")
    def nb_lots(self, obj):
        return len(obj.lots.all())


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ["designation", "bien_nom", "type_lot", "surface", "exploitation_pill", "locataire_actuel", "loyer_actuel"]
    list_filter = [VenduFilter, "bien__proprietaire"]
    search_fields = ["designation", "bien__nom"]
    inlines = [BailInline, PhotoInline]
    readonly_fields = ["outils_relocation"]

    @admin.display(description="outils")
    def outils_relocation(self, obj):
        if not obj.pk:
            return "–"
        bail = obj.bail_actif
        loyer = obj.loyer_propose or (bail.loyer_cc if bail else "")
        charges = bail.charges if bail else ""
        depot = bail.depot_garantie if bail else ""
        url_annonce = f"{reverse('admin:patrimoine_annonce_add')}?lot={obj.pk}&loyer_cc={loyer}&charges={charges}&depot_garantie={depot}"
        url_candidature = f"{reverse('admin:patrimoine_candidature_add')}?lot={obj.pk}"
        return format_html('<a class="button" href="{}">Créer une annonce</a> <a class="button" href="{}">Envoyer un formulaire de candidature</a>',
                           url_annonce, url_candidature)
    fieldsets = [
        (None, {"classes": ["identite-lot"], "fields": ["bien", "designation", ("type", "surface", "nb_pieces", "meuble"), "etage", ("exploitation", "lmnp")]}),
        ("Caractéristiques (reprises dans le bail)", {"fields": ["annee_construction", ("mode_chauffage", "chauffage"), ("mode_eau_chaude", "eau_chaude"),
                                                              ("code_porte", "cave", "parking"), "classe_dpe", "equipements", "annexes", "clause_particuliere"],
                                                   "description": "Le régime de copropriété, les équipements communs et la zone tendue se règlent sur la fiche du bien."}),
        ("Chaudière gaz", {"fields": [("chaudiere_gaz", "date_entretien_chaudiere")]}),
        ("Relocation et annonce", {"fields": ["loyer_propose", ("classe_ges", "energie_annee"), ("energie_min", "energie_max"), "outils_relocation"]}),
        ("État des lieux", {"fields": ["pieces"]}),
        ("Compteurs", {"fields": ["pdl"]}),
        ("Vente", {"fields": [("vendu", "date_vente")]}),
        ("Notes", {"fields": ["notes"]}),
    ]

    def save_model(self, request, obj, form, change):
        if obj.date_vente:
            obj.vendu = True
        super().save_model(request, obj, form, change)

    @admin.display(description="locataire")
    def locataire_actuel(self, obj):
        if obj.vendu or obj.bien.vendu:
            return format_html('<span class="pill gris">Vendu{}</span>', f" le {obj.date_vente:%d/%m/%Y}" if obj.date_vente else "")
        bail = obj.bail_actif
        if not bail and obj.exploitation == "courte":
            return format_html('<span class="pill bleu">Courte durée</span>')
        return ", ".join(str(l) for l in bail.locataires.all()) if bail else format_html('<span class="pill orange">Vacant</span>')

    @admin.display(description="loyer CC")
    def loyer_actuel(self, obj):
        bail = obj.bail_actif
        return euros_liste(bail.loyer_cc) if bail else "–"

    @admin.display(description="bien", ordering="bien__nom")
    def bien_nom(self, obj):
        return "" if obj.bien.nom.strip().lower() == obj.designation.strip().lower() else obj.bien.nom

    @admin.display(description="type", ordering="type")
    def type_lot(self, obj):
        return f"{obj.get_type_display()}{' meublé' if obj.meuble else ''}"

    @admin.display(description="exploitation", ordering="exploitation")
    def exploitation_pill(self, obj):
        if obj.exploitation == "courte":
            return format_html('<span class="pill bleu">Courte durée</span>{}', format_html(' <span class="pill gris">LMNP</span>') if obj.lmnp else "")
        return format_html('<span class="pill gris">Bail</span>{}', format_html(' <span class="pill gris">LMNP</span>') if obj.lmnp else "")
