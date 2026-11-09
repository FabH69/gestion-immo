from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from urllib.parse import quote
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.html import format_html, format_html_join

from .models import ModeleAnnonce, PieceComptable, DocumentLocataire, ModeleDocument, ParametresMail, PieceCandidature, mois_depot_maximum, GarantCandidature, EncaissementHistorique, ChargeLMNP, SiteUtile, Syndic, Annonce, ChargeLCD, ParametresLCD, ReservationLCD, Bail, Bien, Candidature, ChangementLocataire, Garant, PhotoLot, Echeance, EtatDesLieux, MouvementDepot, Pret, Rappel, RevisionLoyer, Locataire, Lot, Paiement, Proprietaire, Tache, TacheParDefaut

admin.site.site_header = "Gestion immobilière"
admin.site.enable_nav_sidebar = False  # remplacé par le menu du bandeau
admin.site.site_title = "Gestion immobilière"
admin.site.index_title = "Tableau de bord"

_changelist_view_django = admin.ModelAdmin.changelist_view


def _changelist_view_titre_simple(self, request, extra_context=None):
    """Titre de liste « Loyers » au lieu de « Sélectionnez l'objet loyer à changer »."""
    extra_context = {"title": self.model._meta.verbose_name_plural.capitalize(), **(extra_context or {})}
    return _changelist_view_django(self, request, extra_context)


admin.ModelAdmin.changelist_view = _changelist_view_titre_simple


class LotsALouerMixin:
    """Ne propose que les lots non vendus ; un lot vendu déjà choisi reste affiché sur sa fiche."""

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "lot":
            from django.db.models import Q
            actuel = None
            object_id = request.resolver_match.kwargs.get("object_id") if request.resolver_match else None
            if object_id:
                actuel = self.model.objects.filter(pk=object_id).values_list("lot_id", flat=True).first()
            kwargs["queryset"] = Lot.objects.filter(Q(vendu=False, bien__vendu=False) | Q(pk=actuel)).select_related("bien")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


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


class GarantInline(admin.StackedInline):
    model = Garant
    extra = 0
    classes = ["fiche-compacte"]

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        champs = formset.form.base_fields
        if "adresse" in champs:
            champs["adresse"].widget.attrs.update(rows=2)
        if "date_naissance" in champs:
            champs["date_naissance"].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        return formset
    fields = [("civilite", "prenom", "nom"), ("date_naissance", "lieu_naissance"), "adresse", ("email", "telephone"),
              ("lien", "profession", "revenus_mensuels"), ("piece_identite_numero", "piece_identite_fichier"),
              ("type_caution", "montant_max", "duree_mois"), "acte"]
    readonly_fields = ["acte"]

    @admin.display(description="acte de cautionnement")
    def acte(self, obj):
        if obj.pk and obj.bail_id:
            return format_html('<a href="{}" target="_blank">Acte de cautionnement PDF</a>', reverse("acte_cautionnement", args=[obj.pk]))
        return "Disponible une fois le garant enregistré et rattaché à un bail."


class GarantBailInline(GarantInline):
    fields = ["locataire"] + GarantInline.fields
    verbose_name_plural = "Garants (cautions)"


class MouvementDepotInline(admin.TabularInline):
    model = MouvementDepot
    fields = ["date", "type", "montant", "mode", "justification", "recu"]
    readonly_fields = ["recu"]
    extra = 0
    verbose_name_plural = "Dépôt de garantie (compte séparé)"

    @admin.display(description="reçu")
    def recu(self, obj):
        if obj.pk and obj.type == MouvementDepot.Type.ENCAISSEMENT:
            return format_html('<a class="bouton-recu" href="{}">Créer le reçu</a>', reverse("recu_depot_mouvement", args=[obj.pk]))
        return "–"


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


@admin.register(EncaissementHistorique)
class EncaissementHistoriqueAdmin(admin.ModelAdmin):
    list_display = ["mois", "lot", "montant", "charges", "note"]
    list_filter = ["lot__bien__proprietaire"]
    date_hierarchy = "mois"
    navigation_par = "mois"


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

    def response_add(self, request, obj, post_url_continue=None):
        if "_continue" not in request.POST and "_addanother" not in request.POST:
            return redirect(f"{reverse('biens_lmnp')}?annee={obj.annee}")
        return super().response_add(request, obj, post_url_continue)


@admin.register(SiteUtile)
class SiteUtileAdmin(admin.ModelAdmin):
    list_display = ["nom", "url", "ordre"]
    list_editable = ["ordre"]
    fields = ["nom", "url", "ordre"]


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
    list_display = ["nom", "ville", "proprietaire", "syndic", "nb_lots", "vendu", "date_vente"]
    list_filter = [VenduFilter, "proprietaire"]
    search_fields = ["nom", "adresse", "ville"]
    inlines = [LotInline]
    fieldsets = [
        (None, {"fields": ["proprietaire", "nom", "adresse", ("code_postal", "ville"), "syndic", ("date_acquisition", "prix_acquisition")]}),
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
        return obj.lots.count()


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
        return obj.bien.nom

    @admin.display(description="type", ordering="type")
    def type_lot(self, obj):
        return f"{obj.get_type_display()}{' meublé' if obj.meuble else ''}"

    @admin.display(description="exploitation", ordering="exploitation")
    def exploitation_pill(self, obj):
        if obj.exploitation == "courte":
            return format_html('<span class="pill bleu">Courte durée</span>{}', format_html(' <span class="pill gris">LMNP</span>') if obj.lmnp else "")
        return format_html('<span class="pill gris">Bail</span>{}', format_html(' <span class="pill gris">LMNP</span>') if obj.lmnp else "")


class PresentFilter(admin.SimpleListFilter):
    title = "statut"
    parameter_name = "present"

    def lookups(self, request, model_admin):
        return [("oui", "Présents"), ("non", "Partis"), ("tous", "Tous")]

    def choices(self, changelist):
        """« Actifs » est le choix par défaut : pas de lien « Tout » de Django, remplacé par « Tous »."""
        valeur = self.value() or "oui"
        for code, libelle in self.lookup_choices:
            yield {"selected": valeur == code, "query_string": changelist.get_query_string({self.parameter_name: code}), "display": libelle}

    def queryset(self, request, queryset):
        valeur = self.value() or "oui"
        if valeur == "oui":
            return queryset.filter(baux__actif=True).distinct()
        if valeur == "non":
            return queryset.exclude(baux__actif=True)
        return queryset


class BailEnCoursFilter(admin.SimpleListFilter):
    """Baux en cours affichés par défaut."""
    title = "statut"
    parameter_name = "statut"

    def lookups(self, request, model_admin):
        return [("actifs", "En cours"), ("termines", "Terminés"), ("tous", "Tous")]

    def choices(self, changelist):
        valeur = self.value() or "actifs"
        for code, libelle in self.lookup_choices:
            yield {"selected": valeur == code, "query_string": changelist.get_query_string({self.parameter_name: code}), "display": libelle}

    def queryset(self, request, queryset):
        valeur = self.value() or "actifs"
        if valeur == "actifs":
            return queryset.filter(actif=True)
        if valeur == "termines":
            return queryset.filter(actif=False)
        return queryset


class EcheanceStatutFilter(admin.SimpleListFilter):
    """Loyers pas encore réglés (à payer ou payés partiellement) affichés par défaut."""
    title = "statut"
    parameter_name = "reglement"

    def lookups(self, request, model_admin):
        return [("non_regles", "À payer ou payé partiellement"), ("a_payer", "À payer"), ("partiel", "Payé partiellement"),
                ("paye", "Payé"), ("tous", "Tous")]

    def choices(self, changelist):
        valeur = self.value() or "non_regles"
        for code, libelle in self.lookup_choices:
            yield {"selected": valeur == code, "query_string": changelist.get_query_string({self.parameter_name: code}), "display": libelle}

    def queryset(self, request, queryset):
        valeur = self.value() or "non_regles"
        if valeur == "non_regles":
            return queryset.filter(statut__in=[Echeance.Statut.A_PAYER, Echeance.Statut.PARTIEL])
        if valeur in ("a_payer", "partiel", "paye"):
            return queryset.filter(statut=valeur)
        return queryset


class LocataireForm(forms.ModelForm):
    """Fiche locataire avec rattachement à un lot et déclaration de sortie."""

    lot = forms.ModelChoiceField(
        Lot.objects.filter(vendu=False, bien__vendu=False).select_related("bien"), required=False, label="logement loué",
        help_text="Rattache le locataire au bail en cours de ce lot. S'il n'y en a pas, un bail est créé : complétez-le ensuite.",
    )
    date_entree = forms.DateField(
        label="date d'entrée", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Utilisée si un nouveau bail est créé (aujourd'hui par défaut).",
    )
    sortie = forms.BooleanField(label="Le locataire quitte le logement", required=False)
    date_sortie = forms.DateField(label="date de sortie", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    nouvelle_rue = forms.CharField(label="nouvelle adresse", required=False, max_length=200)
    nouveau_code_postal = forms.CharField(label="code postal", required=False, max_length=10)
    nouvelle_ville = forms.CharField(label="ville", required=False, max_length=100)

    class Meta:
        model = Locataire
        exclude = ["garant"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bail = self.bail_actif()
        if bail:
            self.fields["lot"].initial = bail.lot_id
            self.fields["date_entree"].initial = bail.date_debut

    def bail_actif(self):
        if not self.instance.pk:
            return None
        return self.instance.baux.filter(actif=True).select_related("lot").order_by("-date_debut").first()

    def clean(self):
        donnees = super().clean()
        if donnees.get("sortie"):
            if not self.bail_actif():
                self.add_error("sortie", "Ce locataire n'a pas de bail en cours.")
            if not donnees.get("date_sortie"):
                self.add_error("date_sortie", "Indiquez la date de sortie.")
        return donnees


class DocumentLocataireInline(admin.TabularInline):
    model = DocumentLocataire
    extra = 0
    fields = ["type", "titre", "date", "fichier"]
    verbose_name_plural = "Ranger un document (bail signé, attestation d'assurance…)"


@admin.register(Locataire)
class LocataireAdmin(admin.ModelAdmin):
    form = LocataireForm
    list_display = ["fiche", "statut_present", "contact", "bouton_contacter"]
    list_display_links = ["fiche"]
    list_filter = [PresentFilter, "baux__lot__bien__proprietaire"]
    search_fields = ["nom", "prenom", "email"]
    inlines = [DocumentLocataireInline, GarantInline]
    readonly_fields = ["encart_documents"]

    class Media:
        js = ["patrimoine/sortie_locataire.js"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for nom in ("adresse_actuelle", "notes"):
            if nom in form.base_fields:
                form.base_fields[nom].widget.attrs.update(rows=2)
        if "date_naissance" in form.base_fields:
            form.base_fields["date_naissance"].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        return form

    def get_fieldsets(self, request, obj=None):
        identite = ("Identité", {"classes": ["fiche-compacte"], "fields": [
            ("civilite", "prenom", "nom"), ("email", "telephone"), ("date_naissance", "lieu_naissance", "nationalite"), "adresse_actuelle"]})
        logement = ("Logement", {"classes": ["fiche-compacte"], "fields": [("lot", "date_entree")]})
        notes = ("Notes", {"classes": ["fiche-compacte"], "fields": ["notes"]})
        if not obj:
            return [identite, logement, notes]
        documents = ("Documents", {"classes": ["encart-documents"], "fields": ["encart_documents"]})
        if obj.baux.filter(actif=True).exists():
            sortie = ("Sortie du locataire", {"fields": ["sortie", "date_sortie", ("nouvelle_rue", "nouveau_code_postal", "nouvelle_ville")],
                                              "classes": ["sortie-locataire", "fiche-compacte"],
                                              "description": "Cochez pour enregistrer le départ : le bail est clôturé et les rappels de fin de bail sont créés (restitution du dépôt de garantie, fin de la garantie Visale)."})
            return [documents, identite, logement, notes, sortie]
        return [documents, identite, logement, notes]

    @admin.display(description="")
    def encart_documents(self, obj):
        """Encart : documents à générer pour chaque bail, puis documents rangés sur la fiche."""
        if not obj.pk:
            return ""
        blocs = []
        for bail in obj.baux.select_related("lot__bien").order_by("-actif", "-date_debut")[:3]:
            liens = [(reverse("admin:patrimoine_bail_change", args=[bail.pk]), "Fiche du bail", "")]
            for m in ModeleDocument.objects.filter(actif=True):
                if not m.convient(bail) or (m.type == ModeleDocument.Type.CAUTION and not bail.garants.exists()):
                    continue
                url = reverse("remplir_modele", args=[m.pk, bail.pk])
                if m.type == ModeleDocument.Type.EDL:
                    liens += [(url + "?edl=entree", f"{m.nom} – entrée", "W"), (url + "?edl=sortie", f"{m.nom} – sortie", "W")]
                else:
                    liens.append((url, m.nom, "W"))
            liens.append((reverse("document_bail", args=[bail.pk]), "Bail PDF (modèle de l'appli)", "PDF"))
            derniere = bail.echeances.order_by("-periode").first()
            if derniere:
                libelle = "Quittance" if derniere.statut == "paye" else "Avis d'échéance"
                liens.append((reverse("document_echeance", args=[derniere.pk]), f"{libelle} {derniere.periode:%m/%Y}", "PDF"))
            titre = f"{'Bail en cours' if bail.actif else 'Ancien bail'} – {bail.lot} – depuis le {bail.date_debut:%d/%m/%Y}"
            blocs.append(format_html('<div class="doc-bail"><div class="doc-titre">{}</div><ul>{}</ul></div>', titre, format_html_join(
                "", '<li><span class="doc-type">{}</span><a href="{}"{}>{}</a></li>',
                ((type_ or "↗", url, format_html(' target="_blank"') if type_ == "PDF" else "", libelle) for url, libelle, type_ in liens))))
        ranges = list(obj.documents.all())
        if ranges:
            blocs.append(format_html('<div class="doc-bail"><div class="doc-titre">Documents rangés</div><ul>{}</ul></div>', format_html_join(
                "", '<li><span class="doc-type">{}</span><a href="{}" target="_blank">{}</a> <span class="doc-date">{}</span></li>',
                ((d.fichier.name.rsplit(".", 1)[-1].upper()[:4], d.fichier.url, d, d.date.strftime("%d/%m/%Y")) for d in ranges))))
        else:
            blocs.append(format_html('<p class="help">Aucun document rangé : ajoutez le bail signé, l\'attestation d\'assurance… dans « Ranger un document » plus bas.</p>'))
        return format_html("{}", format_html_join("", "{}", ((b,) for b in blocs)))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        donnees = form.cleaned_data
        bail = form.bail_actif()
        if donnees.get("sortie") and bail:
            rue, cp, ville = (donnees.get(k, "").strip() for k in ("nouvelle_rue", "nouveau_code_postal", "nouvelle_ville"))
            if rue or cp or ville:
                adresse = "\n".join(x for x in (rue, f"{cp} {ville}".strip()) if x)
                obj.adresse_actuelle = adresse
                obj.save(update_fields=["adresse_actuelle"])
                edl = bail.etats_des_lieux.filter(type="sortie").first()
                if edl and not edl.nouvelle_adresse:
                    edl.nouvelle_adresse = adresse
                    edl.save(update_fields=["nouvelle_adresse"])
            self._enregistrer_sortie(request, obj, bail, donnees["date_sortie"])
            return
        lot = donnees.get("lot")
        if lot and (not bail or bail.lot_id != lot.pk):
            self._rattacher(request, obj, lot, donnees.get("date_entree"))

    def _rattacher(self, request, locataire, lot, date_entree):
        bail = lot.baux.filter(actif=True).order_by("-date_debut").first()
        if bail:
            bail.locataires.add(locataire)
            messages.success(request, f"{locataire} est rattaché au bail en cours de {lot}.")
            return
        bail = Bail.objects.create(
            lot=lot, type=Bail.Type.MEUBLE if lot.meuble else Bail.Type.NU, date_debut=date_entree or date.today(),
            loyer_hc=lot.loyer_propose or 0, notes="Bail créé depuis la fiche locataire : à compléter.",
        )
        bail.locataires.add(locataire)
        lien = reverse("admin:patrimoine_bail_change", args=[bail.pk])
        messages.warning(request, format_html('Nouveau bail créé pour {} : <a href="{}">complétez le loyer, les charges, le dépôt et la garantie</a>.', lot, lien))

    def _enregistrer_sortie(self, request, locataire, bail, date_sortie):
        autres = [str(l) for l in bail.locataires.exclude(pk=locataire.pk)]
        if autres:
            bail.locataires.remove(locataire)
            bail.notes = (bail.notes + "\n" if bail.notes else "") + f"{locataire} est parti le {date_sortie:%d/%m/%Y}."
            bail.save(update_fields=["notes"])
            messages.success(request, f"Sortie de {locataire} enregistrée. Le bail continue avec {', '.join(autres)}.")
            return
        bail.date_fin = date_sortie
        bail.actif = False
        bail.save(update_fields=["date_fin", "actif"])
        creees = []
        depot = bail.depot_detenu
        if depot > 0:
            Rappel.objects.create(
                titre=f"Restituer le dépôt de garantie de {locataire} ({depot:.2f} €) – {bail.lot}",
                date=date_sortie + timedelta(days=30), categorie=Rappel.Categorie.ADMINISTRATIF, bien=bail.lot.bien,
                notes="Délai légal : 1 mois après la remise des clés si l'état des lieux de sortie est conforme, 2 mois sinon (retenues justifiées par devis ou factures). Enregistrez la restitution dans Loyers › Dépôts.",
            )
            creees.append("restitution du dépôt de garantie")
        if bail.type_garantie == "visale":
            Rappel.objects.create(
                titre=f"Mettre fin à la garantie Visale de {locataire} – {bail.lot}",
                date=date_sortie, categorie=Rappel.Categorie.ADMINISTRATIF, bien=bail.lot.bien,
                notes=f"Déclarer la fin du bail sur visale.fr (espace bailleur).{' Visa : ' + bail.detail_garantie if bail.detail_garantie else ''}",
            )
            creees.append("fin de la garantie Visale")
        texte = f"Sortie de {locataire} le {date_sortie:%d/%m/%Y} enregistrée, bail de {bail.lot} clôturé."
        if creees:
            texte += " Rappels créés : " + ", ".join(creees) + "."
        messages.success(request, texte)

    @admin.display(description="présent", boolean=True)
    def present(self, obj):
        """Présent = au moins un bail en cours (décocher « actif » sur le bail au départ du locataire)."""
        return any(b.actif for b in obj.baux.all())

    @admin.display(description="logement")
    def logement(self, obj):
        baux = sorted(obj.baux.all(), key=lambda b: (b.actif, b.date_debut), reverse=True)
        return str(baux[0].lot) if baux else "–"

    @admin.display(description="sorti le")
    def date_sortie(self, obj):
        baux = list(obj.baux.all())
        if not baux or any(b.actif for b in baux):
            return "–"
        fins = [b.date_fin for b in baux if b.date_fin]
        return max(fins).strftime("%d/%m/%Y") if fins else "date inconnue"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("baux__lot__bien", "garants")

    @admin.display(description="garants")
    def liste_garants(self, obj):
        return ", ".join(dict.fromkeys(str(g) for g in obj.garants.all())) or "–"

    @admin.display(description="locataire", ordering="nom")
    def fiche(self, obj):
        return format_html('<span class="cellule-personne"><span><b>{}</b><small>{}</small></span></span>', obj, self.logement(obj))

    @admin.display(description="statut")
    def statut_present(self, obj):
        if self.present(obj):
            return format_html('<span class="pill ok">Présent</span>')
        return format_html('<span class="pill gris">Parti</span>')

    @admin.display(description="")
    def bouton_contacter(self, obj):
        if not obj.email:
            return format_html('<span class="quiet">pas d\'e-mail</span>')
        return format_html('<a class="bouton-encaisser" href="{}">Contacter</a>', reverse("admin:patrimoine_locataire_contacter", args=[obj.pk]))

    def get_urls(self):
        return [path("<int:pk>/contacter/", self.admin_site.admin_view(self.contacter_view), name="patrimoine_locataire_contacter")] + super().get_urls()

    def contacter_view(self, request, pk):
        from . import contact, mails

        locataire = get_object_or_404(Locataire, pk=pk)
        retour = request.POST.get("retour") or request.GET.get("retour") or reverse("admin:patrimoine_locataire_changelist")
        if not retour.startswith("/"):
            retour = reverse("admin:patrimoine_locataire_changelist")
        motifs = contact.motifs(locataire)
        if request.method == "POST":
            sujet, texte = request.POST.get("sujet", "").strip(), request.POST.get("message", "").strip()
            if not (sujet and texte):
                messages.error(request, "Le sujet et le message sont obligatoires.")
            elif not mails.reglage():
                messages.error(request, "Réglez d'abord l'envoi des mails (Réglages › Mails).")
            elif mails.envoyer([locataire.email], sujet, texte):
                messages.success(request, f"Mail envoyé à {locataire} ({locataire.email}).")
                return redirect(retour)
            else:
                messages.error(request, "Le mail n'est pas parti : vérifiez le réglage Réglages › Mails.")
        contexte = {**self.admin_site.each_context(request), "title": f"Contacter {locataire}", "locataire": locataire,
                    "motifs": motifs, "opts": self.model._meta, "retour": retour,
                    "sujet": request.POST.get("sujet", ""), "message": request.POST.get("message", ""),
                    "motif_choisi": request.POST.get("motif", "")}
        return render(request, "admin/patrimoine/locataire/contacter.html", contexte)

    @admin.display(description="contact")
    def contact(self, obj):
        if not obj.email and not obj.telephone:
            return "–"
        return format_html('<span class="cellule-contact">{}{}</span>',
                           format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email) if obj.email else "",
                           format_html('<small>{}</small>', obj.telephone) if obj.telephone else "")


class BailForm(forms.ModelForm):
    class Meta:
        model = Bail
        fields = "__all__"
        widgets = {nom: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d") for nom in ("date_loyer_precedent", "date_revision_precedente", "reduction_debut", "reduction_fin")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "locataires" in self.fields:
            # seuls les locataires sans bail (nouvellement créés) et ceux déjà sur ce bail sont proposés
            deja = self.instance.locataires.all() if self.instance.pk else Locataire.objects.none()
            self.fields["locataires"].queryset = (Locataire.objects.filter(baux__isnull=True) | Locataire.objects.filter(pk__in=deja)).distinct()
        if "depot_garantie" in self.fields:
            self.fields["depot_garantie"].help_text = ("Maximum légal : 1 mois de loyer hors charges en location nue, 2 mois en meublé, "
                                                       "aucun en bail mobilité. Calculé automatiquement à la saisie du loyer.")

    def clean(self):
        donnees = super().clean()
        r_montant, r_debut, r_fin = donnees.get("reduction_montant"), donnees.get("reduction_debut"), donnees.get("reduction_fin")
        if r_montant and not r_debut:
            self.add_error("reduction_debut", "Indiquez à partir de quand la réduction s'applique.")
        if (r_debut or r_fin) and not r_montant:
            self.add_error("reduction_montant", "Indiquez le montant de la réduction.")
        if r_debut and r_fin and r_fin < r_debut:
            self.add_error("reduction_fin", "La fin doit être après le début.")
        mois = mois_depot_maximum(donnees.get("type"))
        loyer, depot = donnees.get("loyer_initial") or donnees.get("loyer_hc"), donnees.get("depot_garantie")
        if mois is not None and loyer is not None and depot is not None and depot > loyer * mois:
            maximum = loyer * mois
            self.add_error("depot_garantie", f"Le dépôt ne peut pas dépasser {maximum:.2f} € pour ce type de bail "
                                             f"({mois} mois de loyer hors charges)." if mois else
                                             "Le dépôt de garantie est interdit en bail mobilité.")
        lot = donnees.get("lot")
        majore = donnees.get("loyer_reference_majore")
        if lot and lot.bien.encadrement_loyers and majore and lot.surface and loyer is not None:
            plafond = (majore * lot.surface).quantize(Decimal("0.01"))
            complement = donnees.get("complement_loyer") or Decimal(0)
            if loyer - complement > plafond + Decimal("0.01"):
                self.add_error("loyer_reference_majore",
                               f"Le loyer hors complément ({loyer - complement:.2f} €) dépasse le loyer de référence majoré "
                               f"× surface ({majore} € × {lot.surface} m² = {plafond:.2f} €). Baissez le loyer ou justifiez un complément de loyer.")
            if complement and not donnees.get("complement_loyer_motif"):
                self.add_error("complement_loyer_motif", "Indiquez ce qui justifie le complément de loyer (obligatoire).")
        return donnees


@admin.register(Bail)
class BailAdmin(LotsALouerMixin, admin.ModelAdmin):
    form = BailForm

    class Media:
        js = ["patrimoine/depot_garantie.js", "patrimoine/heures_creuses.js"]
    list_display = ["liste_locataires", "lot", "type", "date_debut", "colonne_loyer_initial", "colonne_loyer_actuel", "charges", "loyer_cc", "actif"]
    list_display_links = ["liste_locataires", "lot"]
    list_filter = [BailEnCoursFilter, "lot__bien__proprietaire"]
    search_fields = ["lot__designation", "lot__bien__nom", "locataires__nom"]
    filter_horizontal = ["locataires"]
    date_hierarchy = "date_debut"
    readonly_fields = ["documents", "recu_depot", "historique"]
    fieldsets = [
        (None, {"fields": ["lot", "locataires", ("type", "destination"), ("date_debut", "date_fin"), "actif"]}),
        ("Conditions financières", {"fields": [("loyer_initial", "loyer_hc"), ("charges", "mode_charges"), ("depot_garantie", "recu_depot"), "jour_echeance", "paiement_a_echoir"]}),
        ("Réduction temporaire", {"classes": ["identite-lot"], "fields": [("reduction_montant", "reduction_debut", "reduction_fin")],
                                  "description": "Montant déduit du loyer hors charges chaque mois entre ces deux dates (au prorata des jours si la période "
                                                 "commence ou finit en cours de mois). Les loyers non encore réglés sont recalculés à l'enregistrement."}),
        ("Garantie", {"fields": [("type_garantie", "detail_garantie")], "description": "Les garants se saisissent en bas de page."}),
        ("Relevés des compteurs", {"fields": [("pdl_electricite", "index_elec_entree", "index_elec_sortie"),
                                              ("heures_creuses", "plages_heures_creuses"),
                                              ("pce_gaz", "index_gaz_entree", "index_gaz_sortie"),
                                              ("compteur_eau", "index_eau_froide_entree", "index_eau_froide_sortie"),
                                              ("compteur_eau_chaude", "index_eau_chaude_entree", "index_eau_chaude_sortie")],
                                   "classes": ["releves-compteurs"],
                                   "description": "Index relevés à l'entrée et à la sortie : ils sont repris automatiquement dans les états des lieux "
                                                  "(et inversement, un index saisi dans un état des lieux complète le bail)."}),
        ("Zone tendue", {"classes": ["collapse"], "fields": [("loyer_reference", "loyer_reference_majore"), ("complement_loyer", "complement_loyer_motif"),
                                                            ("loyer_precedent", "date_loyer_precedent", "date_revision_precedente")],
                         "description": "À remplir si le bien est en zone tendue ou soumis à l'encadrement des loyers (cases sur la fiche du bien). "
                                        "Loyer de référence : sur le site de la préfecture ou de la métropole (ex. Lyon : loyers.grandlyon.com)."}),
        ("Révision annuelle et rappels", {"fields": ["revision_annuelle", ("irl_trimestre", "irl_valeur"), "indice_reference", "date_derniere_revision", ("date_attestation_assurance", "rappel_assurance")]}),
        ("Historique des révisions", {"fields": ["historique"]}),
        ("Clauses particulières", {"fields": ["notes"]}),
        ("Documents pré-remplis", {"fields": ["documents"]}),
    ]
    inlines = [GarantBailInline, MouvementDepotInline]

    @admin.display(description="reçu")
    def recu_depot(self, obj):
        if not obj.pk:
            return "Enregistrez le bail pour créer le reçu."
        return format_html('<a class="button" href="{}">Créer le reçu (Word)</a>', reverse("recu_depot_bail", args=[obj.pk]))

    @admin.display(description="documents")
    def documents(self, obj):
        if not obj.pk:
            return "Enregistrez le bail pour générer les documents."
        liens = [
            (reverse("revision_loyer", args=[obj.pk]), "Réviser le loyer"),
            (reverse("document_bail", args=[obj.pk]), "Bail PDF"),
            (reverse("document_edl_vierge", args=[obj.pk, "entree"]), "Fiche d'état des lieux d'entrée"),
            (reverse("document_edl_vierge", args=[obj.pk, "sortie"]), "Fiche d'état des lieux de sortie"),
        ]
        if obj.lot.meuble or obj.type in (Bail.Type.MEUBLE, Bail.Type.ETUDIANT, Bail.Type.MOBILITE):
            liens += [
                (reverse("document_inventaire", args=[obj.pk, "entree"]), "Inventaire du mobilier (entrée)"),
                (reverse("document_inventaire", args=[obj.pk, "sortie"]), "Inventaire du mobilier (sortie)"),
            ]
        mes_modeles = []
        for m in ModeleDocument.objects.filter(actif=True):
            if not m.convient(obj):
                continue
            url = reverse("remplir_modele", args=[m.pk, obj.pk])
            if m.type == ModeleDocument.Type.EDL:
                mes_modeles += [(url + "?edl=entree", f"{m.nom} – entrée"), (url + "?edl=sortie", f"{m.nom} – sortie")]
            elif m.type == ModeleDocument.Type.CAUTION and not obj.garants.exists():
                continue
            else:
                mes_modeles.append((url, m.nom))
        bloc_word = ""
        if mes_modeles:
            bloc_word = format_html(
                "<p style='margin-top:10px'><strong>Mes modèles Word :</strong> {}</p>",
                format_html(" · ".join('<a href="{}">{}</a>' for _ in mes_modeles), *[x for l in mes_modeles for x in l]))
        return format_html(
            "{}{}<p class='help'>Les champs manquants apparaissent « à compléter » : complétez la fiche du lot, du locataire ou du propriétaire. "
            "Vérifiez le bail avec le modèle officiel (décret n° 2015-587) avant signature. Vos modèles Word se gèrent dans le menu Modèles.</p>",
            format_html(" · ".join('<a href="{}" target="_blank">{}</a>' for _ in liens), *[x for l in liens for x in l]), bloc_word,
        )

    @admin.display(description="locataires", ordering="locataires__nom")
    def liste_locataires(self, obj):
        return ", ".join(str(l) for l in obj.locataires.all()) or "(sans locataire)"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("lot__bien").prefetch_related("locataires")

    @admin.display(description="loyer initial (€)", ordering="loyer_initial")
    def colonne_loyer_initial(self, obj):
        return obj.loyer_initial

    @admin.display(description="loyer actuel (€)", ordering="loyer_hc")
    def colonne_loyer_actuel(self, obj):
        return obj.loyer_hc

    STATUTS_REVISION = {
        "appliquee": ("Appliquée", "ok"), "calculee": ("Calculée, pas encore appliquée", "attention"),
        "sans_detail": ("Faite (détail non enregistré)", ""), "a_faire": ("À faire", "attention"),
        "manquee": ("Non appliquée", "alerte"), "pas_de_clause": ("Pas de révision prévue au bail", ""),
    }

    @admin.display(description="")
    def historique(self, obj):
        if not obj.pk:
            return "–"
        lignes = obj.historique_revisions()
        euros = lambda v: f"{v:,.2f} €".replace(",", " ").replace(".", ",") if v is not None else ""
        corps = []
        for l in reversed(lignes):
            r, (statut, classe) = l["revision"], self.STATUTS_REVISION[l["statut"]]
            if r:
                cellules = (euros(r.ancien_loyer), f"{r.ancien_irl}", f"{r.nouvel_irl} ({r.trimestre})",
                            f"+{l['pourcentage']:.2f} %".replace(".", ",") if l["pourcentage"] is not None else "", euros(r.nouveau_loyer), f"{r.date_effet:%d/%m/%Y}")
            else:
                cellules = ("", "", "", "", "", f"{l['anniversaire']:%d/%m/%Y}")
            corps.append(format_html("<tr><td>{}</td>" + "<td>{}</td>" * 6 + '<td><span class="statut-revision {}">{}</span></td></tr>',
                                     l["anniversaire"].year, *cellules, classe, statut))
        if not corps:
            return "Première révision à la date anniversaire du bail, le " + obj.prochaine_revision().strftime("%d/%m/%Y") if obj.prochaine_revision() else "Aucune révision."
        tableau = format_html(
            '<table class="historique-revisions"><thead><tr><th>Année</th><th>Loyer avant révision</th><th>Indice de référence</th>'
            "<th>Nouvel indice</th><th>Hausse</th><th>Nouveau loyer</th><th>Date d'application</th><th>Statut</th></tr></thead><tbody>{}</tbody></table>",
            format_html("".join(["{}"] * len(corps)), *corps),
        )
        lien = reverse("admin:patrimoine_revisionloyer_changelist") + f"?bail__id__exact={obj.pk}"
        return format_html('{}<p class="help">Loyer initial {} · loyer actuel {}. <a href="{}">Voir les révisions enregistrées</a> · <a href="{}">Réviser le loyer</a></p>',
                           tableau, euros(obj.loyer_initial), euros(obj.loyer_hc), lien, reverse("revision_loyer", args=[obj.pk]))

    @admin.display(description="loyer CC (€)")
    def loyer_cc(self, obj):
        return obj.loyer_cc


class PaiementInline(admin.TabularInline):
    model = Paiement
    fields = ["date", "montant", "mode", "notes"]
    extra = 1


def euros_liste(montant):
    """Montant pour les listes : « 1 234,50 € »."""
    texte = f"{montant:,.2f}".replace(",", "\u202f").replace(".", ",")
    return f"{texte}\u00a0€"


class EnRetardFilter(admin.SimpleListFilter):
    title = "retard"
    parameter_name = "retard"

    def lookups(self, request, model_admin):
        return [("oui", "Impayés en retard")]

    def queryset(self, request, queryset):
        if self.value() == "oui":
            return queryset.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=timezone.localdate())
        return queryset


class ModifierEcheancesForm(forms.Form):
    loyer_hc = forms.DecimalField(label="Nouveau loyer HC (€)", required=False, max_digits=10, decimal_places=2, min_value=0)
    charges = forms.DecimalField(label="Nouvelles charges (€)", required=False, max_digits=10, decimal_places=2, min_value=0)
    date_exigibilite = forms.DateField(label="Nouvelle date d'exigibilité", required=False,
                                       widget=forms.DateInput(attrs={"type": "date"}))


class BailEcheanceFilter(admin.SimpleListFilter):
    """Loyers : liste déroulante des baux (logement – locataires), baux en cours d'abord, à droite de la liste."""
    title = "bail"
    parameter_name = "bail"
    a_droite = True

    def lookups(self, request, model_admin):
        baux = Bail.objects.select_related("lot__bien").prefetch_related("locataires").order_by("-actif", "lot__bien__nom", "lot__designation", "-date_debut")
        choix = []
        for b in baux:
            noms = ", ".join(f"{l.prenom} {l.nom}".strip() for l in b.locataires.all())
            libelle = f"{b.lot} – {noms}" if noms else str(b.lot)
            choix.append((str(b.pk), libelle if b.actif else f"{libelle} (terminé)"))
        return choix

    def queryset(self, request, queryset):
        return queryset.filter(bail_id=self.value()) if self.value() else queryset


@admin.register(Echeance)
class EcheanceAdmin(admin.ModelAdmin):
    list_display = ["locataire_et_logement", "echeance_le", "total", "paye", "reste", "statut_colore", "actions_ligne"]
    list_display_links = ["locataire_et_logement"]
    list_filter = [EcheanceStatutFilter, EnRetardFilter, BailEcheanceFilter]
    filtres_en_liste = ("bail",)  # liste déroulante des baux, à droite
    filtres_caches = ("reglement", "retard")  # remplacés par les raccourcis en haut de la liste
    search_fields = ["bail__lot__designation", "bail__lot__bien__nom", "bail__locataires__nom"]
    date_hierarchy = "periode"
    navigation_par = "mois"
    readonly_fields = ["statut", "document"]
    fields = ["bail", "periode", ("loyer_hc", "charges"), "date_exigibilite", "statut", "document"]
    inlines = [PaiementInline]
    actions = ["modifier", "marquer_paye"]
    change_list_template = "admin/patrimoine/echeance/change_list.html"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("bail__lot__bien").prefetch_related("bail__locataires", "paiements")

    def get_urls(self):
        return [
            path("generer/", self.admin_site.admin_view(self.generer_view), name="patrimoine_echeance_generer"),
            path("<int:pk>/encaisser/", self.admin_site.admin_view(self.encaisser_view), name="patrimoine_echeance_encaisser"),
        ] + super().get_urls()

    def encaisser_view(self, request, pk):
        """Bouton « Encaisser » d'une ligne : enregistre le reste dû comme payé aujourd'hui, par virement."""
        if request.method != "POST":
            return redirect("admin:patrimoine_echeance_change", pk)
        e = Echeance.objects.get(pk=pk)
        if e.reste_du > 0:
            Paiement.objects.create(echeance=e, date=timezone.localdate(), montant=e.reste_du)
            self.message_user(request, f"{e} : {e.total} € encaissés aujourd'hui (virement). Pour un autre mode ou une autre date, ouvrez le loyer.",
                              messages.SUCCESS)
        return redirect(request.POST.get("retour") or "admin:patrimoine_echeance_changelist")

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
        reponse = super().changelist_view(request, extra_context)
        cl = getattr(reponse, "context_data", {}).get("cl") if hasattr(reponse, "context_data") else None
        if cl is not None:
            # résumé des loyers affichés (filtres compris)
            lignes = list(cl.queryset.prefetch_related("paiements"))
            du = sum((e.total for e in lignes), Decimal(0))
            paye = sum((sum((p.montant for p in e.paiements.all()), Decimal(0)) for e in lignes), Decimal(0))
            jour = timezone.localdate()
            retard = [e for e in lignes if e.statut != Echeance.Statut.PAYE and e.date_exigibilite < jour]
            reponse.context_data["resume"] = {
                "du": du, "paye": paye, "reste": du - paye, "nb": len(lignes),
                "retard": sum((e.total - sum((p.montant for p in e.paiements.all()), Decimal(0)) for e in retard), Decimal(0)),
                "nb_retard": len(retard),
            }
            params = request.GET.copy()
            for cle in ("reglement", "retard", "p"):
                params.pop(cle, None)
            base = params.urlencode()
            actuel = "retard" if request.GET.get("retard") == "oui" else request.GET.get("reglement", "non_regles")
            reponse.context_data["raccourcis"] = [
                {"libelle": libelle, "url": "?" + "&".join(x for x in (base, q) if x), "actif": actuel == code}
                for code, libelle, q in [("non_regles", "À encaisser", ""), ("retard", "En retard", "retard=oui"),
                                         ("paye", "Payés", "reglement=paye"), ("tous", "Tous", "reglement=tous")]]
        return reponse

    def save_model(self, request, obj, form, change):
        if change and {"loyer_hc", "charges"} & set(form.changed_data):
            obj.ajustee = True
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.mettre_a_jour_statut()

    @admin.display(description="mois", ordering="periode")
    def mois(self, obj):
        return obj.periode.strftime("%m/%Y")

    @admin.display(description="locataire et logement", ordering="bail__lot__bien__nom")
    def locataire_et_logement(self, obj):
        noms = [str(l) for l in obj.bail.locataires.all()]
        return format_html('<span class="cellule-personne"><span><b>{}</b><small>{}</small></span></span>', ", ".join(noms) or "–", obj.bail.lot)

    @admin.display(description="échéance", ordering="date_exigibilite")
    def echeance_le(self, obj):
        return obj.date_exigibilite.strftime("%d/%m/%Y")

    @admin.display(description="")
    def actions_ligne(self, obj):
        libelle = "Quittance" if obj.statut == "paye" else "Avis PDF"
        pdf = format_html('<a class="lien-action" href="{}" target="_blank">{}</a>', reverse("document_echeance", args=[obj.pk]), libelle)
        if obj.statut == "paye":
            return pdf
        if obj.date_exigibilite < timezone.localdate():
            pdf = format_html('{} <a class="lien-action" href="{}?bail={}">Relancer</a>', pdf, reverse("relances_impayes"), obj.bail_id)
        return format_html('<form method="post" action="{}" class="form-encaisser"><input type="hidden" name="csrfmiddlewaretoken" value="{}">'
                           '<button type="submit" class="bouton-encaisser" title="Enregistrer le paiement du reste dû, aujourd\'hui, par virement">Encaisser</button></form>{}',
                           reverse("admin:patrimoine_echeance_encaisser", args=[obj.pk]), self._csrf, pdf)

    def get_changelist_instance(self, request):
        from django.middleware.csrf import get_token
        self._csrf = get_token(request)
        return super().get_changelist_instance(request)

    @admin.display(description="lot", ordering="bail__lot__bien__nom")
    def lot(self, obj):
        return obj.bail.lot

    @admin.display(description="locataires")
    def liste_locataires(self, obj):
        return ", ".join(str(l) for l in obj.bail.locataires.all())

    @admin.display(description="dû")
    def total(self, obj):
        return euros_liste(obj.total)

    def _paye(self, obj):
        return sum((p.montant for p in obj.paiements.all()), Decimal(0))

    @admin.display(description="payé")
    def paye(self, obj):
        return euros_liste(self._paye(obj))

    @admin.display(description="reste dû")
    def reste(self, obj):
        reste = obj.total - self._paye(obj)
        return format_html("<b>{}</b>", euros_liste(reste)) if reste else "–"

    @admin.display(description="statut", ordering="statut")
    def statut_colore(self, obj):
        if obj.statut == "paye":
            return format_html('<span class="pill ok">✓ Payé</span>')
        retard = (timezone.localdate() - obj.date_exigibilite).days
        libelle = "Partiel" if obj.statut == "partiel" else "À payer"
        if retard > 0:
            return format_html('<span class="pill rouge" title="{} – {} jour(s) de retard">Retard · {} j</span>', libelle, retard, retard)
        return format_html('<span class="pill orange">{}</span>', libelle)

    @admin.display(description="document")
    def document(self, obj):
        if not obj.pk:
            return "–"
        libelle = "Quittance PDF" if obj.statut == "paye" else "Avis d'échéance PDF"
        return format_html('<a href="{}" target="_blank">{}</a>', reverse("document_echeance", args=[obj.pk]), libelle)

    @admin.display(description="")
    def lien_modifier(self, obj):
        return format_html('<a href="{}">Modifier</a>', reverse("admin:patrimoine_echeance_change", args=[obj.pk]))

    @admin.action(description="Modifier")
    def modifier(self, request, queryset):
        """Une échéance cochée : ouvre sa fiche. Plusieurs : formulaire pour changer loyer, charges ou date en une fois."""
        if queryset.count() == 1:
            return redirect("admin:patrimoine_echeance_change", queryset.first().pk)
        form = ModifierEcheancesForm(request.POST if "appliquer" in request.POST else None)
        if form.is_valid():
            changements = {k: v for k, v in form.cleaned_data.items() if v is not None}
            if not changements:
                self.message_user(request, "Rien à changer : remplissez au moins une case.", messages.WARNING)
                return None
            n = 0
            for e in queryset:
                for champ, valeur in changements.items():
                    setattr(e, champ, valeur)
                if {"loyer_hc", "charges"} & set(changements):
                    e.ajustee = True
                e.save()
                e.mettre_a_jour_statut()
                n += 1
            self.message_user(request, f"{n} échéance(s) modifiée(s).", messages.SUCCESS)
            return None
        return render(request, "admin/patrimoine/echeance/modifier.html", {
            **self.admin_site.each_context(request),
            "title": "Modifier les échéances cochées",
            "opts": self.model._meta,
            "echeances": queryset.select_related("bail__lot__bien").prefetch_related("bail__locataires"),
            "form": form,
            "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
        })

    @admin.action(description="Marquer comme payé intégralement (virement, aujourd'hui)")
    def marquer_paye(self, request, queryset):
        n = 0
        for e in queryset.exclude(statut=Echeance.Statut.PAYE):
            Paiement.objects.create(echeance=e, date=timezone.localdate(), montant=e.reste_du)
            n += 1
        self.message_user(request, f"{n} échéance(s) marquée(s) comme payée(s).", messages.SUCCESS)


class TacheInlineBase(admin.TabularInline):
    model = Tache
    fields = ["faite", "libelle", "aide", "echeance", "commentaire", "date_faite"]
    readonly_fields = ["aide", "date_faite"]
    extra = 0
    phase = None

    class Media:
        css = {"all": ["patrimoine/checklist.css"]}

    def get_queryset(self, request):
        return super().get_queryset(request).filter(phase=self.phase)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields["libelle"].widget.attrs.update(size=50)
        formset.form.base_fields["commentaire"].widget.attrs.update(size=25)
        phase = self.phase

        class FormsetPhase(formset):
            def save_new(self, form, commit=True):
                form.instance.phase = phase
                return super().save_new(form, commit)

        return FormsetPhase


class TacheSortieInline(TacheInlineBase):
    phase = Tache.Phase.DEPART
    verbose_name = "tâche de sortie"
    verbose_name_plural = "1. Sortie du locataire"


class TacheEntreeInline(TacheInlineBase):
    phase = Tache.Phase.ARRIVEE
    verbose_name = "tâche d'entrée"
    verbose_name_plural = "2. Entrée du nouveau locataire"


@admin.register(ChangementLocataire)
class ChangementLocataireAdmin(LotsALouerMixin, admin.ModelAdmin):
    date_hierarchy = "date_depart"
    navigation_par = "annee"
    list_display = ["lot", "locataire_sortant", "date_conge", "date_depart", "date_arrivee", "avancement_affiche", "prochaine_tache", "termine"]
    list_filter = ["termine", "lot__bien__proprietaire"]
    fields = ["lot", "bail_sortant", "date_conge", "date_depart", "date_arrivee", "termine", "notes"]
    inlines = [TacheSortieInline, TacheEntreeInline]

    @admin.display(description="locataire sortant")
    def locataire_sortant(self, obj):
        return ", ".join(str(l) for l in obj.bail_sortant.locataires.all()) if obj.bail_sortant else "–"

    @admin.display(description="avancement")
    def avancement_affiche(self, obj):
        faites, total = obj.avancement
        pct = round(100 * faites / total) if total else 0
        return format_html(
            '<div style="width:90px;background:#eee;border-radius:3px"><div style="width:{}%;background:#2e7d32;height:8px;border-radius:3px"></div></div>{} / {}',
            pct, faites, total,
        )

    @admin.display(description="prochaine tâche")
    def prochaine_tache(self, obj):
        t = obj.taches.filter(faite=False).exclude(echeance=None).order_by("echeance").first()
        if not t:
            return "–"
        couleur = "#c62828" if t.echeance < timezone.localdate() else "inherit"
        return format_html('<span style="color:{}">{} – {}</span>', couleur, t.echeance.strftime("%d/%m/%Y"), t.libelle)


class TacheEnRetardFilter(admin.SimpleListFilter):
    title = "échéance"
    parameter_name = "quand"

    def lookups(self, request, model_admin):
        return [("retard", "En retard"), ("30j", "Dans les 30 jours")]

    def queryset(self, request, queryset):
        aujourdhui = timezone.localdate()
        if self.value() == "retard":
            return queryset.filter(faite=False, echeance__lt=aujourdhui)
        if self.value() == "30j":
            return queryset.filter(faite=False, echeance__gte=aujourdhui, echeance__lte=aujourdhui + timedelta(days=30))
        return queryset


@admin.register(Tache)
class TacheAdmin(admin.ModelAdmin):
    date_hierarchy = "echeance"
    navigation_par = "mois"
    list_display = ["echeance", "libelle", "changement", "phase", "faite"]
    list_editable = ["faite"]
    list_filter = [TacheEnRetardFilter, "faite", "changement__lot__bien__proprietaire"]
    list_display_links = ["libelle"]
    search_fields = ["libelle", "changement__lot__designation", "changement__lot__bien__nom"]
    ordering = ["faite", "echeance"]
    fields = ["changement", "phase", "libelle", "aide", "echeance", "faite", "date_faite", "commentaire"]
    readonly_fields = ["date_faite"]

    def get_model_perms(self, request):
        # Accessible depuis l'accueil et les changements de locataire, sans encombrer le menu.
        return {}


class TacheActiveFilter(admin.SimpleListFilter):
    """Par défaut, seules les tâches actives sont listées."""
    title = "active"
    parameter_name = "active"

    def lookups(self, request, model_admin):
        return [("oui", "Actives"), ("non", "Désactivées"), ("toutes", "Toutes")]

    def choices(self, changelist):
        for valeur, libelle in self.lookup_choices:
            yield {"selected": (self.value() or "oui") == valeur, "display": libelle,
                   "query_string": changelist.get_query_string({self.parameter_name: valeur})}

    def queryset(self, request, queryset):
        valeur = self.value() or "oui"
        if valeur == "toutes":
            return queryset
        return queryset.filter(actif=valeur == "oui")


@admin.register(TacheParDefaut)
class TacheParDefautAdmin(admin.ModelAdmin):
    """Réglages : tâches ajoutées à chaque nouveau changement de locataire."""
    list_display = ["libelle", "phase", "quand", "ordre", "actif"]
    list_editable = ["ordre", "actif"]
    list_filter = [TacheActiveFilter, "phase"]
    list_display_links = ["libelle"]
    search_fields = ["libelle"]
    fields = ["phase", "libelle", "aide", ("decalage_jours", "reference"), "ordre", "actif"]

    @admin.display(description="échéance")
    def quand(self, obj):
        j = obj.decalage_jours
        de, le = {"conge": ("de la", "la"), "depart": ("du", "le"), "arrivee": ("de l'", "l'")}[obj.reference]
        libelle = obj.get_reference_display()
        if j == 0:
            return f"le jour {de}{'' if de.endswith(chr(39)) else ' '}{libelle}"
        return f"{abs(j)} j {'après' if j > 0 else 'avant'} {le}{'' if le.endswith(chr(39)) else ' '}{libelle}"

    def changelist_view(self, request, extra_context=None):
        messages.info(request, "Ces tâches sont ajoutées à chaque nouveau changement de locataire. "
                               "Les changements déjà créés gardent leur liste.")
        return super().changelist_view(request, extra_context)


@admin.register(EtatDesLieux)
class EtatDesLieuxAdmin(admin.ModelAdmin):
    date_hierarchy = "date"
    navigation_par = "annee"
    list_display = ["date", "type", "bail", "document"]
    list_filter = ["type", "bail__lot__bien__proprietaire"]
    readonly_fields = ["document"]
    fields = ["bail", "type", "date", ("compteur_electricite", "compteur_gaz"), ("compteur_eau_froide", "compteur_eau_chaude"), "nb_cles", "nouvelle_adresse", "observations", "document"]

    @admin.display(description="document")
    def document(self, obj):
        if not obj.pk:
            return "Enregistrez pour générer la fiche pré-remplie."
        return format_html('<a href="{}" target="_blank">Fiche PDF pré-remplie</a>', reverse("document_edl", args=[obj.pk]))


class DepotEnCoursFilter(admin.SimpleListFilter):
    title = "garanties"
    parameter_name = "en_cours"

    def lookups(self, request, model_admin):
        return [("oui", "En cours"), ("non", "Restituées")]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        en_cours = [b.pk for b in Bail.objects.prefetch_related("mouvements_depot") if b.depot_detenu > 0]
        return queryset.filter(bail__in=en_cours) if self.value() == "oui" else queryset.exclude(bail__in=en_cours)


@admin.register(MouvementDepot)
class MouvementDepotAdmin(admin.ModelAdmin):
    navigation_par = "annee"
    list_display = ["date", "type", "montant_affiche", "logement", "locataires", "proprietaire", "mode", "justification", "recu"]
    list_select_related = ["bail__lot__bien__proprietaire"]

    @admin.display(description="logement", ordering="bail__lot__bien__nom")
    def logement(self, obj):
        return str(obj.bail.lot)

    @admin.display(description="locataire")
    def locataires(self, obj):
        return ", ".join(str(l) for l in obj.bail.locataires.all()) or "–"

    @admin.display(description="propriétaire", ordering="bail__lot__bien__proprietaire__nom")
    def proprietaire(self, obj):
        return obj.bail.lot.bien.proprietaire
    list_filter = [DepotEnCoursFilter, "bail__lot__bien__proprietaire"]
    search_fields = ["bail__lot__designation", "bail__locataires__nom"]
    date_hierarchy = "date"
    change_list_template = "admin/patrimoine/mouvementdepot/change_list.html"

    @admin.display(description="montant (€)", ordering="montant")
    def montant_affiche(self, obj):
        return obj.montant_signe

    @admin.display(description="reçu")
    def recu(self, obj):
        if obj.type == MouvementDepot.Type.ENCAISSEMENT:
            return format_html('<a class="bouton-recu" href="{}">Créer le reçu</a>', reverse("recu_depot_mouvement", args=[obj.pk]))
        return "–"

    def changelist_view(self, request, extra_context=None):
        soldes = []
        for proprio in Proprietaire.objects.all():
            mouvements = MouvementDepot.objects.filter(bail__lot__bien__proprietaire=proprio)
            solde = sum((m.montant_signe for m in mouvements), 0)
            if mouvements.exists():
                soldes.append((proprio, solde))
        nb_en_cours = sum(1 for b in Bail.objects.prefetch_related("mouvements_depot") if b.depot_detenu > 0)
        extra_context = {**(extra_context or {}), "soldes": soldes, "total_detenu": sum(s for _, s in soldes), "nb_en_cours": nb_en_cours}
        return super().changelist_view(request, extra_context)


@admin.register(Pret)
class PretAdmin(admin.ModelAdmin):
    list_display = ["fiche_pret", "proprietaire", "bien", "taux_affiche", "mensualite", "fin", "restant_du", "amortissement"]
    list_display_links = ["fiche_pret"]
    list_filter = ["proprietaire"]
    readonly_fields = ["calculs"]
    fieldsets = [
        (None, {"fields": ["proprietaire", "bien", "banque", ("reference", "type")]}),
        ("Conditions", {"fields": [("montant", "taux", "duree_mois"), "date_premiere_echeance", "assurance_mensuelle", "taux_assurance"]}),
        ("Recalage sur la banque", {"fields": [("capital_constate", "capital_constate_le")],
                                     "description": "Facultatif : capital restant dû indiqué par la banque à une date, si le calcul s'en écarte."}),
        ("Calculs", {"fields": ["calculs"]}),
        ("Notes", {"fields": ["notes"]}),
    ]
    change_list_template = "admin/patrimoine/pret/change_list.html"

    @admin.display(description="taux")
    def taux_affiche(self, obj):
        return f"{obj.taux:.3f} %".replace(".", ",")

    @admin.display(description="prêt", ordering="banque")
    def fiche_pret(self, obj):
        return format_html('<span class="cellule-personne"><span><b>{}</b><small>n° {} · {}</small></span></span>',
                           obj.banque, obj.reference or "–", euros_liste(obj.montant))

    @admin.display(description="mensualité")
    def mensualite(self, obj):
        return euros_liste(obj.mensualite_totale)

    @admin.display(description="fin")
    def fin(self, obj):
        return obj.date_derniere_echeance.strftime("%m/%Y")

    @admin.display(description="restant dû")
    def restant_du(self, obj):
        return format_html("<b>{}</b>", euros_liste(obj.capital_restant_du(timezone.localdate())))

    @admin.display(description="tableau")
    def amortissement(self, obj):
        return format_html('<a href="{}" target="_blank">PDF</a>', reverse("document_amortissement", args=[obj.pk]))

    @admin.display(description="résultats")
    def calculs(self, obj):
        if not obj.pk:
            return "Enregistrez le prêt pour voir les calculs."
        return format_html(
            "Mensualité hors assurance : <strong>{} €</strong> · Dernière échéance : {} · Capital restant dû aujourd'hui : <strong>{} €</strong><br>"
            '<a href="{}" target="_blank">Tableau d\'amortissement PDF</a>',
            obj.mensualite_hors_assurance, obj.date_derniere_echeance.strftime("%d/%m/%Y"),
            obj.capital_restant_du(timezone.localdate()), reverse("document_amortissement", args=[obj.pk]),
        )


@admin.register(Rappel)
class RappelAdmin(admin.ModelAdmin):
    date_hierarchy = "date"
    navigation_par = "mois"
    list_display = ["date", "titre", "categorie", "bien", "periodicite", "actif", "fait"]
    list_editable = ["actif", "fait"]
    list_filter = ["fait", "bien__proprietaire"]
    fields = ["titre", "date", "periodicite", "categorie", "bien", "actif", "fait", "notes"]
    list_display_links = ["titre"]
    search_fields = ["titre", "notes"]

    actions = ["marquer_fait", "desactiver", "reactiver"]

    @admin.action(description="Marquer comme fait (les rappels périodiques passent à l'échéance suivante)")
    def marquer_fait(self, request, queryset):
        for rappel in queryset:
            rappel.marquer_fait()

    @admin.action(description="Désactiver les rappels sélectionnés")
    def desactiver(self, request, queryset):
        queryset.update(actif=False)

    @admin.action(description="Réactiver les rappels sélectionnés")
    def reactiver(self, request, queryset):
        queryset.update(actif=True)


@admin.register(RevisionLoyer)
class RevisionLoyerAdmin(admin.ModelAdmin):
    date_hierarchy = "date_effet"
    navigation_par = "annee"
    list_display = ["date_effet", "bail", "ancien_loyer", "ancien_irl", "nouvel_irl", "trimestre", "pourcentage", "nouveau_loyer", "appliquee", "liens"]
    list_filter = ["appliquee", "bail__lot__bien__proprietaire"]

    @admin.display(description="hausse")
    def pourcentage(self, obj):
        return f"+{(obj.nouvel_irl / obj.ancien_irl - 1) * 100:.2f} %".replace(".", ",") if obj.ancien_irl else "–"

    def has_add_permission(self, request):
        return False  # une révision se crée depuis la page « Réviser le loyer » du bail

    @admin.display(description="")
    def liens(self, obj):
        return format_html('<a href="{}">Ouvrir</a> · <a href="{}" target="_blank">Courrier PDF</a>',
                           f"{reverse('revision_loyer', args=[obj.bail_id])}?revision={obj.pk}", reverse("lettre_revision", args=[obj.pk]))


class GarantActifFilter(admin.SimpleListFilter):
    title = "statut"
    parameter_name = "actif"

    def lookups(self, request, model_admin):
        return [("oui", "Actifs"), ("non", "Anciens"), ("tous", "Tous")]

    def choices(self, changelist):
        valeur = self.value() or "oui"
        for code, libelle in self.lookup_choices:
            yield {"selected": valeur == code, "query_string": changelist.get_query_string({self.parameter_name: code}), "display": libelle}

    def queryset(self, request, queryset):
        """Actif = garant d'un bail en cours."""
        valeur = self.value() or "oui"
        if valeur == "oui":
            return queryset.filter(bail__actif=True)
        if valeur == "non":
            return queryset.exclude(bail__actif=True)
        return queryset


@admin.register(Garant)
class GarantAdmin(admin.ModelAdmin):
    list_display = ["__str__", "locataire", "bail", "type_caution", "montant_max", "acte"]
    list_filter = [GarantActifFilter, "bail__lot__bien__proprietaire"]
    search_fields = ["nom", "prenom", "locataire__nom"]

    @admin.display(description="acte")
    def acte(self, obj):
        if obj.bail_id:
            return format_html('<a href="{}" target="_blank">PDF</a>', reverse("acte_cautionnement", args=[obj.pk]))
        return "–"


class GarantCandidatureInline(admin.StackedInline):
    model = GarantCandidature
    extra = 0
    max_num = 2
    verbose_name_plural = "garants proposés par le candidat"
    fields = [("civilite", "prenom", "nom"), ("date_naissance", "lieu_naissance"), "adresse_rue", ("code_postal", "ville"),
              ("email", "telephone"), ("lien", "profession", "revenus_mensuels"), ("piece_identite_numero", "piece_identite_fichier")]


class PieceCandidatureInline(admin.TabularInline):
    model = PieceCandidature
    extra = 0
    can_delete = True
    fields = ["type", "voir", "ajoute_le"]
    readonly_fields = ["type", "voir", "ajoute_le"]
    verbose_name_plural = "justificatifs déposés par le candidat"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="fichier")
    def voir(self, obj):
        return format_html('<a href="{}" target="_blank">{}</a>', obj.fichier.url, obj.fichier.name.rsplit("/", 1)[-1][17:])


@admin.register(Candidature)
class CandidatureAdmin(LotsALouerMixin, admin.ModelAdmin):
    date_hierarchy = "recue_le"
    navigation_par = "mois"
    inlines = [GarantCandidatureInline, PieceCandidatureInline]
    list_display = ["__str__", "statut", "situation", "revenus_mensuels", "taux_effort", "type_garantie", "recue_le"]
    list_filter = ["statut", "lot__bien__proprietaire"]
    readonly_fields = ["lien_a_envoyer", "statut", "recue_le", "taux_effort"]
    actions = ["retenir", "refuser"]

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return [(None, {"fields": ["lot", "type_garantie", "email"],
                            "description": "Choisissez le logement et le type de caution : un lien personnel sera créé pour le candidat. "
                                           "Avec « Garant », le formulaire lui demandera l'identité du ou des garants (adresse, date et lieu "
                                           "de naissance, n° de pièce d'identité et copie)."})]
        anciens = {"adresse_actuelle"} if not obj.adresse_actuelle else set()
        if not obj.garant_nom:
            anciens |= {f.name for f in Candidature._meta.fields if f.name.startswith("garant_")}
        reponses = [f.name for f in Candidature._meta.fields
                    if f.name not in {"id", "lot", "jeton", "statut", "cree_le", "recue_le", "consentement", *anciens}]
        return [
            (None, {"fields": ["lot", "lien_a_envoyer", "statut", "recue_le", "taux_effort"]}),
            ("Réponses du candidat", {"fields": reponses}),
        ]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["lot", *self.readonly_fields]

    def get_inlines(self, request, obj):
        return self.inlines if obj else []

    @admin.display(description="taux d'effort")
    def taux_effort(self, obj):
        taux = obj.taux_effort(obj.lot.loyer_reference)
        if taux is None:
            return "–"
        couleur = "#2e7d32" if taux <= 33 else "#ef6c00" if taux <= 40 else "#c62828"
        return format_html('<strong style="color:{}">{} %</strong>', couleur, taux)

    @admin.display(description="lien à envoyer au candidat")
    def lien_a_envoyer(self, obj):
        url = self.request_courante.build_absolute_uri(reverse("candidature_publique", args=[obj.jeton]))
        from . import mails

        corps = mails.texte_invitation(obj, url)
        mailto = f"mailto:{obj.email}?subject={quote('Votre dossier de location – ' + obj.lot.designation)}&body={quote(corps)}"
        envoi = ""
        if ParametresMail.objects.filter(pk=1).exists():
            envoi = format_html(
                ' <button type="submit" class="button default" formaction="{}" formnovalidate>Envoyer le lien par mail depuis l\'appli</button>',
                reverse("envoyer_lien_candidature", args=[obj.pk]))
        return format_html(
            '<input type="text" value="{}" readonly style="width:100%;max-width:560px" onclick="this.select()"> '
            '<p><a class="button" href="{}">Préparer le mail dans Outlook (mis en forme)</a> '
            '<a class="button" href="{}">Préparer en texte simple</a>{}</p>'
            "<p class='help'>« Mis en forme » télécharge un brouillon : ouvrez-le, il s'affiche dans Outlook prêt à envoyer. "
            "Pour envoyer directement depuis l'appli, renseignez l'e-mail du candidat et le réglage Réglages › Mails.</p>",
            url, reverse("preparer_mail_candidature", args=[obj.pk]), mailto, envoi,
        )

    def get_form(self, request, obj=None, **kwargs):
        self.request_courante = request
        return super().get_form(request, obj, **kwargs)

    def response_add(self, request, obj, post_url_continue=None):
        return redirect("admin:patrimoine_candidature_change", obj.pk)

    @admin.action(description="Retenir : créer le locataire (et son garant)")
    def retenir(self, request, queryset):
        for c in queryset.filter(statut=Candidature.Statut.RECUE):
            locataire = c.creer_locataire()
            self.message_user(request, format_html('Locataire créé : <a href="{}">{}</a>. Créez ensuite le bail.',
                                                   reverse("admin:patrimoine_locataire_change", args=[locataire.pk]), locataire))

    @admin.action(description="Ne pas retenir")
    def refuser(self, request, queryset):
        queryset.update(statut=Candidature.Statut.REFUSEE)


@admin.register(Annonce)
class AnnonceAdmin(LotsALouerMixin, admin.ModelAdmin):
    list_display = ["titre", "lot", "loyer_cc", "disponible_le", "cree_le", "pdf"]
    list_filter = ["lot__bien__proprietaire"]
    readonly_fields = ["a_copier"]

    @admin.display(description="PDF")
    def pdf(self, obj):
        return format_html('<a class="button" href="{}" target="_blank">Générer le PDF</a>', reverse("annonce_pdf", args=[obj.pk]))
    actions = ["regenerer"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["texte"].widget.attrs.update(rows=22, style="width:100%;max-width:720px")
        form.base_fields["titre"].widget.attrs.update(size=60)
        return form
    fieldsets = [
        (None, {"fields": ["lot", "modele", ("loyer_cc", "charges", "charges_forfait", "depot_garantie"), "disponible_le"]}),
        ("Annonce", {"fields": ["titre", "texte", "a_copier"]}),
    ]

    class Media:
        js = ["patrimoine/annonce_modele.js"]

    def get_urls(self):
        return [path("modele-du-lot/", self.admin_site.admin_view(self.modele_du_lot), name="patrimoine_annonce_modele_du_lot")] + super().get_urls()

    def modele_du_lot(self, request):
        """Modèle d'annonce par défaut d'un lot (appelé quand on choisit le lot sur une nouvelle annonce)."""
        from django.http import JsonResponse

        lot = Lot.objects.filter(pk=request.GET.get("lot") or None).first()
        m = ModeleAnnonce.par_defaut(lot.pk if lot else None)
        return JsonResponse({"modele": m.pk if m else None, "nom": str(m) if m else "", "meuble": bool(lot and lot.meuble)})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "modele":
            kwargs["queryset"] = ModeleAnnonce.objects.filter(actif=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        m = ModeleAnnonce.par_defaut(initial.get("lot"))
        if m:
            initial["modele"] = m.pk
        return initial

    @admin.display(description="publier")
    def a_copier(self, obj):
        if not obj.pk:
            return "Enregistrez : le titre et le texte seront générés à partir de la fiche du lot."
        manque = obj.mentions_manquantes()
        alerte = format_html(
            '<p style="color:#c62828"><strong>Mentions obligatoires manquantes :</strong> {} (à compléter dans la fiche du lot puis régénérer le texte).</p>',
            ", ".join(manque),
        ) if manque else ""
        photos = format_html_join("", '<img src="{}" title="{}" style="height:80px;border-radius:8px;margin:0 6px 6px 0">',
                                  ((p.image.url, str(p)) for p in obj.lot.photos.all()))
        actualiser = mark_safe(
            '<p><button type="submit" name="_actualiser" value="1" class="button" title="Enregistre la fiche puis refait le titre et le texte '
            'avec la version actuelle du modèle (et les loyer, dépôt et mentions obligatoires)">Actualiser avec le modèle</button></p>')
        return format_html(
            '{}{}<p><button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById(\'id_titre\').value);this.textContent=\'Titre copié\'">Copier le titre</button> '
            '<button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById(\'id_texte\').value);this.textContent=\'Texte copié\'">Copier le texte</button> '
            '<a class="button" href="https://www.leboncoin.fr/deposer-une-annonce" target="_blank" rel="noopener">Déposer sur Leboncoin</a> '
            '<a class="button" href="{}" target="_blank">Générer le PDF</a> '
            '<a class="button" href="{}">Télécharger les photos (zip)</a></p>'
            '<div>{}</div><p class="help">Leboncoin ne permet pas la publication automatique pour les particuliers : copiez le titre et le texte, puis ajoutez les photos du zip.</p>',
            alerte, actualiser, reverse("annonce_pdf", args=[obj.pk]), reverse("photos_zip", args=[obj.lot_id]), photos or "Aucune photo : ajoutez-en dans la fiche du lot.",
        )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = {**(extra_context or {}), "show_save_and_add_another": False}
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if "_actualiser" in request.POST:
            obj.titre, obj.texte = "", ""  # refaits au save() d'après le modèle actuel
        super().save_model(request, obj, form, change)

    def response_change(self, request, obj):
        if "_actualiser" in request.POST:
            messages.success(request, "Titre et texte actualisés avec la version actuelle du modèle.")
            return redirect(request.path)
        return super().response_change(request, obj)

    @admin.action(description="Actualiser le titre et le texte (modèle actuel ou fiche du lot)")
    def regenerer(self, request, queryset):
        for annonce in queryset:
            annonce.titre = ""
            annonce.texte = ""
            annonce.save()


@admin.register(ModeleAnnonce)
class ModeleAnnonceAdmin(LotsALouerMixin, admin.ModelAdmin):
    list_display = ["nom", "lot", "actif", "nb_annonces", "modifie_le"]
    list_filter = ["actif", "lot__bien__proprietaire"]
    search_fields = ["nom", "texte", "lot__designation", "lot__bien__nom"]
    fields = ["nom", "lot", "titre", "texte", "fichier", "actif", "notes"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["texte"].widget.attrs.update(rows=16, style="width:100%;max-width:720px")
        return form

    @admin.display(description="annonces")
    def nb_annonces(self, obj):
        return obj.annonces.count()


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


@admin.register(ParametresMail)
class ParametresMailAdmin(admin.ModelAdmin):
    fields = ["adresse", "mot_de_passe", "nom_expediteur", "alerte_nouveau_dossier", "accuse_au_candidat", "test"]
    readonly_fields = ["test"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["mot_de_passe"].widget = forms.PasswordInput(render_value=True)
        return form

    def changelist_view(self, request, extra_context=None):
        if ParametresMail.objects.filter(pk=1).exists():
            return redirect("admin:patrimoine_parametresmail_change", 1)
        return redirect("admin:patrimoine_parametresmail_add")

    def has_add_permission(self, request):
        return not ParametresMail.objects.filter(pk=1).exists()

    @admin.display(description="vérifier")
    def test(self, obj):
        if not obj or not obj.pk:
            return "Enregistrez d'abord, puis envoyez-vous un mail de test."
        return format_html('<button type="submit" class="button" formaction="{}" formnovalidate>M\'envoyer un mail de test</button>',
                           reverse("tester_mail"))


@admin.register(ModeleDocument)
class ModeleDocumentAdmin(admin.ModelAdmin):
    list_display = ["nom", "type", "actif", "nb_champs", "telecharger", "modifie_le"]
    list_filter = ["type", "actif"]
    fields = ["nom", "type", "fichier", "actif", "notes", "champs_trouves"]
    readonly_fields = ["champs_trouves"]

    def _champs(self, obj):
        from .champs import CHAMPS
        from .word import champs_du_modele, normaliser

        import inspect
        import re as _re

        from .champs import valeurs as _valeurs
        connus = {normaliser(n) for n in _re.findall(r'"([^"{}]+)":', inspect.getsource(_valeurs))}
        connus |= {normaliser(n) for _, liste in CHAMPS for n, _ in liste} | {normaliser(n) for n in (
            "Adresse_Prop", "Ville_Propr", "Adresse_Appart", "CP_Appart", "Ville_Appart", "IRL de référence", "IRL référence Période",
            "Index_Elec_Entrée", "Index_Elec_Sortie", "Index_Gaz_Sortie", "Index_Eau_froide_Sortie", "Index_Eau_chaude_Sortie",
            "Nationalité_Loc", "Prénom_Garant2", "Civilité_Garant2", "Adresse_Garant2", "Date_naissance_Garant2", "Lieu_naissance_Garant2", "Lien_Garant2")}
        try:
            with obj.fichier.open("rb") as f:
                noms = champs_du_modele(f)
        except Exception:
            return None, None
        connus_loc = {normaliser(n) for n in noms if normaliser(n) in connus or normaliser(n).rstrip("234") in connus}
        return noms, [n for n in noms if normaliser(n) not in connus_loc]

    @admin.display(description="champs")
    def nb_champs(self, obj):
        noms, inconnus = self._champs(obj)
        if noms is None:
            return "fichier illisible"
        if inconnus:
            return format_html('{} <span style="color:#c62828">({} inconnu{})</span>', len(noms), len(inconnus), "s" if len(inconnus) > 1 else "")
        return len(noms)

    @admin.display(description="modèle")
    def telecharger(self, obj):
        return format_html('<a href="{}">Télécharger</a>', obj.fichier.url) if obj.fichier else "–"

    @admin.display(description="champs trouvés dans le document")
    def champs_trouves(self, obj):
        if not obj or not obj.pk:
            return "Enregistrez pour voir les champs reconnus dans le document."
        noms, inconnus = self._champs(obj)
        if noms is None:
            return "Le fichier n'est pas un document Word (.docx) lisible."
        if not noms:
            return format_html("Aucun champ {{…}} trouvé. <a href=\"{}\">Voir la liste des champs</a>", reverse("liste_champs"))
        rendu = format_html_join(" ", '<code style="{}">{{{}}}</code>', (
            ("color:#c62828;font-weight:600" if n in inconnus else "", n) for n in noms))
        aide = format_html(' <p class="help">En rouge : champs inconnus, ils resteront tels quels. <a href="{}">Liste des champs</a></p>',
                           reverse("liste_champs")) if inconnus else ""
        return format_html("{}{}", rendu, aide)


class AnneeFiscaleFilter(admin.SimpleListFilter):
    """Année civile du paiement : année en cours et précédente en boutons, années antérieures en liste déroulante."""
    title = "année"
    parameter_name = "annee"
    affichage_annees = True

    def lookups(self, request, model_admin):
        t = timezone.localdate().year
        anterieures = sorted({p.annee for p in PieceComptable.objects.only("date", "date_paiement")} - {None, t, t - 1}, reverse=True)
        return [(str(t), f"Année en cours ({t})"), (str(t - 1), f"Année précédente ({t - 1})")] + [(str(a), str(a)) for a in anterieures if a < t - 1]

    def queryset(self, request, queryset):
        if self.value():
            a = int(self.value())
            return queryset.filter(Q(date_paiement__year=a) | Q(date_paiement__isnull=True, date__year=a))
        return queryset


class BienPieceFilter(admin.SimpleListFilter):
    """Biens des sociétés ; limités au propriétaire choisi s'il y en a un, sans les biens vendus avant l'année regardée.
    Affiché sous le filtre propriétaire."""
    title = "bien"
    parameter_name = "bien"
    a_droite = True

    def lookups(self, request, model_admin):
        biens = Bien.objects.exclude(proprietaire__type=Proprietaire.Type.NOM_PROPRE).order_by("nom")
        # un bien vendu disparaît de la liste à partir de l'année qui suit sa vente (année filtrée, sinon année en cours)
        annee = request.GET.get("annee", "")
        annee = int(annee) if annee.isdigit() else timezone.localdate().year
        biens = biens.filter(Q(vendu=False) | Q(date_vente__year__gte=annee))
        if request.GET.get("bien__proprietaire__id__exact"):
            biens = biens.filter(proprietaire_id=request.GET["bien__proprietaire__id__exact"])
        return [(str(b.pk), b.nom) for b in biens]

    def queryset(self, request, queryset):
        return queryset.filter(bien_id=self.value()) if self.value() else queryset


class ACodifierFilter(admin.SimpleListFilter):
    title = "codification"
    parameter_name = "a_codifier"

    def lookups(self, request, model_admin):
        return [("oui", "À codifier"), ("non", "Codifiées")]

    MANQUE = Q(preremplie=True) | Q(ligne="") | Q(montant__isnull=True) | Q(date__isnull=True)

    def queryset(self, request, queryset):
        if self.value() == "oui":
            return queryset.filter(self.MANQUE)
        if self.value() == "non":
            return queryset.exclude(self.MANQUE)
        return queryset


class EntreeFichiersMultiples(forms.ClearableFileInput):
    allow_multiple_selected = True


class ChampFichiersMultiples(forms.FileField):
    widget = EntreeFichiersMultiples

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(ChampFichiersMultiples, self).clean(d, initial) for d in data]
        return super().clean(data, initial)


class ImportFacturesForm(forms.Form):
    bien = forms.ModelChoiceField(Bien.objects.none(), label="bien concerné",
                                  help_text="Le bien pourra être changé ensuite pièce par pièce.")
    fichiers = ChampFichiersMultiples(label="factures (PDF ou photos)", required=False,
                               widget=EntreeFichiersMultiples(attrs={"accept": ".pdf,.jpg,.jpeg,.png,.heic"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bien"].queryset = Bien.objects.exclude(proprietaire__type=Proprietaire.Type.NOM_PROPRE).order_by("proprietaire__nom", "nom")
        self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}"


class PieceComptableForm(forms.ModelForm):
    class Meta:
        model = PieceComptable
        fields = "__all__"
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "date_paiement": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "bien" in self.fields:
            # biens des sociétés (l'annexe 2072 concerne les SCI)
            self.fields["bien"].queryset = Bien.objects.exclude(proprietaire__type=Proprietaire.Type.NOM_PROPRE).order_by("proprietaire__nom", "nom")
            self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}"

    def clean_fichier(self):
        f = self.cleaned_data.get("fichier")
        if f and hasattr(f, "content_type"):  # nouveau fichier envoyé
            empreinte = PieceComptable.calculer_empreinte(f)
            deja = PieceComptable.objects.filter(empreinte=empreinte).exclude(pk=self.instance.pk).first()
            if deja:
                raise forms.ValidationError(f"Cette facture est déjà enregistrée : {deja}.")
            self.instance.empreinte = empreinte
        return f


@admin.register(PieceComptable)
class PieceComptableAdmin(admin.ModelAdmin):
    form = PieceComptableForm
    list_display = ["date_affichee", "bien", "fournisseur", "libelle", "ligne_courte", "montant_affiche", "piece"]
    list_display_links = ["date_affichee", "libelle"]
    filtres_en_liste = ("bien__proprietaire",)  # propriétaire en liste déroulante, comme le bien
    list_filter = [ACodifierFilter, AnneeFiscaleFilter, "bien__proprietaire", BienPieceFilter, "ligne"]
    search_fields = ["fournisseur", "libelle", "bien__nom", "notes"]
    # mode « codifier » (liste filtrée sur les pièces à codifier) : tout se complète directement dans le tableau
    LISTE_CODIFIER = ["piece", "etat_lecture", "bien", "date", "date_paiement", "fournisseur", "libelle", "montant", "ligne"]
    fieldsets = [
        (None, {"classes": ["identite-lot"], "fields": ["bien", ("date", "date_paiement"), ("fournisseur", "montant"), "libelle", "ligne", "fichier", "notes"],
                "description": "Chaque facture alimente la ligne choisie de l'annexe 2072-S-A1 (Comptabilité SCI › Annexe 2072), "
                               "pour l'année de son paiement. Les loyers, le forfait de 20 € par local et les intérêts d'emprunt "
                               "sont calculés automatiquement : inutile de les saisir ici."}),
    ]

    def _codifier(self, request):
        return request.GET.get("a_codifier") == "oui"

    def get_list_display(self, request):
        return self.LISTE_CODIFIER if self._codifier(request) else self.list_display

    def get_list_display_links(self, request, list_display):
        return None if self._codifier(request) else self.list_display_links

    def get_changelist_instance(self, request):
        self.list_editable = self.LISTE_CODIFIER[2:] if self._codifier(request) else ()
        return super().get_changelist_instance(request)

    def get_changelist_formset(self, request, **kwargs):
        # tableau de codification : listes simples (sans les icônes d'ajout / modification du bien)
        self.list_editable = self.LISTE_CODIFIER[2:] if self._codifier(request) else ()
        return forms.modelformset_factory(self.model, form=PieceComptableForm, fields=self.list_editable, extra=0)

    def get_urls(self):
        from django.urls import path

        return [path("import/", self.admin_site.admin_view(self.import_view), name="patrimoine_piececomptable_import")] + super().get_urls()

    def import_view(self, request):
        from pathlib import Path

        from django.core.exceptions import ValidationError

        form = ImportFacturesForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid() and not request.FILES.getlist("fichiers"):
            form.add_error("fichiers", "Choisissez au moins une facture.")
        if request.method == "POST" and form.is_valid():
            from .lecture_factures import analyser, texte_pdf

            crees, lues, refuses, doublons, vus = 0, 0, [], [], set()
            for f in request.FILES.getlist("fichiers"):
                empreinte = PieceComptable.calculer_empreinte(f)
                deja = PieceComptable.objects.filter(empreinte=empreinte).select_related("bien").first()
                if deja or empreinte in vus:
                    quand = f" (déjà importée pour {deja.bien.nom}{f', facture du {deja.date:%d/%m/%Y}' if deja.date else ''})" if deja else " (en double dans cet envoi)"
                    doublons.append(f.name + quand)
                    continue
                vus.add(empreinte)
                piece = PieceComptable(empreinte=empreinte, bien=form.cleaned_data["bien"], libelle=Path(f.name).stem.replace("_", " ")[:200], fichier=f)
                if f.name.lower().endswith(".pdf"):
                    propositions = analyser(texte_pdf(f))
                    for champ, valeur in propositions.items():
                        setattr(piece, champ, valeur)
                    if propositions:
                        piece.preremplie = True
                        lues += 1
                try:
                    piece.full_clean()
                except ValidationError:
                    refuses.append(f.name)
                    continue
                piece.save()
                crees += 1
            if crees:
                lu = (f" Pour {lues} d'entre elles, les champs ont été lus dans le PDF : vérifiez-les." if lues else "")
                messages.success(request, f"{crees} facture{'s' if crees > 1 else ''} importée{'s' if crees > 1 else ''} : "
                                          "complétez date, montant et ligne 2072 dans le tableau, puis Enregistrer." + lu)
            if refuses:
                messages.error(request, "Format non accepté (PDF, JPG, PNG ou HEIC) : " + ", ".join(refuses))
            if doublons:
                messages.warning(request, f"{len(doublons)} facture{'s' if len(doublons) > 1 else ''} déjà présente{'s' if len(doublons) > 1 else ''}, "
                                          "non importée" + ("s" if len(doublons) > 1 else "") + " : " + " ; ".join(doublons))
            return redirect(reverse("admin:patrimoine_piececomptable_changelist") + ("?a_codifier=oui" if crees else ""))
        return render(request, "admin/patrimoine/piececomptable/import.html",
                      {**self.admin_site.each_context(request), "form": form, "opts": self.model._meta,
                       "title": "Importer des factures"})

    actions = ["valider_preremplies"]

    @admin.action(description="Valider les champs lus automatiquement (pièces sélectionnées)")
    def valider_preremplies(self, request, queryset):
        n = queryset.filter(preremplie=True).update(preremplie=False)
        messages.success(request, f"{n} pièce{'s' if n > 1 else ''} validée{'s' if n > 1 else ''}.")

    @admin.display(description="")
    def etat_lecture(self, obj):
        if obj.preremplie:
            return format_html('<span class="pill bleu" title="Champs lus dans le PDF : vérifiez-les, puis Enregistrer ou action « Valider »">lu auto</span>')
        return ""

    def save_model(self, request, obj, form, change):
        obj.preremplie = False  # enregistrée par l'utilisateur (fiche ou tableau) : les champs sont considérés vérifiés
        super().save_model(request, obj, form, change)

    @admin.display(description="date", ordering="date")
    def date_affichee(self, obj):
        return obj.date.strftime("%d/%m/%Y") if obj.date else format_html('<span class="pill orange">à codifier</span>')

    @admin.display(description="ligne 2072", ordering="ligne")
    def ligne_courte(self, obj):
        if not obj.ligne:
            return "–"
        return format_html('<span class="pill gris" title="{}">{}</span>', obj.get_ligne_display(),
                           "non déd." if obj.ligne == "non" else ("9 bis" if obj.ligne == "9b" else obj.ligne))

    @admin.display(description="montant", ordering="montant")
    def montant_affiche(self, obj):
        return euros_liste(obj.montant) if obj.montant is not None else "–"

    @admin.display(description="facture")
    def piece(self, obj):
        if not obj.fichier:
            return "–"
        image = obj.fichier.name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png")
        return format_html('<a href="{}" target="_blank" class="apercu-piece" data-image="{}">{}</a>',
                           obj.fichier.url, "1" if image else "", "Photo" if image else "PDF")

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), "lien_2072": reverse("annexe_2072"),
                         "lien_import": reverse("admin:patrimoine_piececomptable_import"),
                         "nb_a_codifier": PieceComptable.objects.filter(ACodifierFilter.MANQUE).count()}
        return super().changelist_view(request, extra_context)


from . import admin_utilisateurs  # noqa: E402,F401  (écran Utilisateurs avec droits par module)
