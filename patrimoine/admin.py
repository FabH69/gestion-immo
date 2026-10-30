from datetime import date, timedelta

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import Bail, Bien, ChangementLocataire, Echeance, EtatDesLieux, MouvementDepot, Pret, Rappel, RevisionLoyer, Locataire, Lot, Paiement, Proprietaire, Tache

admin.site.site_header = "Gestion immobilière"
admin.site.site_title = "Gestion immobilière"
admin.site.index_title = "Tableau de bord"

_changelist_view_django = admin.ModelAdmin.changelist_view


def _changelist_view_titre_simple(self, request, extra_context=None):
    """Titre de liste « Loyers » au lieu de « Sélectionnez l'objet loyer à changer »."""
    extra_context = {"title": self.model._meta.verbose_name_plural.capitalize(), **(extra_context or {})}
    return _changelist_view_django(self, request, extra_context)


admin.ModelAdmin.changelist_view = _changelist_view_titre_simple


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


class MouvementDepotInline(admin.TabularInline):
    model = MouvementDepot
    fields = ["date", "type", "montant", "mode", "justification", "recu"]
    readonly_fields = ["recu"]
    extra = 0
    verbose_name_plural = "Dépôt de garantie (compte séparé)"

    @admin.display(description="reçu")
    def recu(self, obj):
        if obj.pk and obj.type == MouvementDepot.Type.ENCAISSEMENT:
            return format_html('<a href="{}" target="_blank">Reçu PDF</a>', reverse("document_recu_depot", args=[obj.pk]))
        return "–"


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
    fieldsets = [
        (None, {"fields": ["bien", "designation", "type", "surface", "nb_pieces", "meuble", "etage"]}),
        ("Caractéristiques (reprises dans le bail)", {"fields": ["annee_construction", "regime_juridique", "chauffage", "eau_chaude", "classe_dpe", "zone_tendue", "equipements", "annexes"]}),
        ("Chaudière gaz", {"fields": ["chaudiere_gaz", "date_entretien_chaudiere"]}),
        ("État des lieux", {"fields": ["pieces"]}),
        ("Notes", {"fields": ["notes"]}),
    ]

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
    readonly_fields = ["documents"]
    fieldsets = [
        (None, {"fields": ["lot", "locataires", "type", "destination", "date_debut", "date_fin", "actif"]}),
        ("Conditions financières", {"fields": ["loyer_hc", "charges", "mode_charges", "depot_garantie", "jour_echeance", "paiement_a_echoir"]}),
        ("Révision annuelle et rappels", {"fields": ["revision_annuelle", ("irl_trimestre", "irl_valeur"), "indice_reference", "date_derniere_revision", "date_attestation_assurance"]}),
        ("Clauses particulières", {"fields": ["notes"]}),
        ("Documents pré-remplis", {"fields": ["documents"]}),
    ]
    inlines = [MouvementDepotInline]

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
        return format_html(
            "{}<p class='help'>Les champs manquants apparaissent « à compléter » : complétez la fiche du lot, du locataire ou du propriétaire. "
            "Vérifiez le bail avec le modèle officiel (décret n° 2015-587) avant signature.</p>",
            format_html(" · ".join('<a href="{}" target="_blank">{}</a>' for _ in liens), *[x for l in liens for x in l]),
        )

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
class ChangementLocataireAdmin(admin.ModelAdmin):
    list_display = ["lot", "locataire_sortant", "date_conge", "date_depart", "date_arrivee", "avancement_affiche", "prochaine_tache", "termine"]
    list_filter = ["termine", "lot__bien__proprietaire", "lot__bien"]
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
    list_display = ["echeance", "libelle", "changement", "phase", "faite"]
    list_editable = ["faite"]
    list_filter = [TacheEnRetardFilter, "faite", "phase", "changement__lot__bien__proprietaire"]
    list_display_links = ["libelle"]
    search_fields = ["libelle", "changement__lot__designation", "changement__lot__bien__nom"]
    ordering = ["faite", "echeance"]
    fields = ["changement", "phase", "libelle", "aide", "echeance", "faite", "date_faite", "commentaire"]
    readonly_fields = ["date_faite"]

    def get_model_perms(self, request):
        # Accessible depuis l'accueil et les changements de locataire, sans encombrer le menu.
        return {}


@admin.register(EtatDesLieux)
class EtatDesLieuxAdmin(admin.ModelAdmin):
    list_display = ["date", "type", "bail", "document"]
    list_filter = ["type", "bail__lot__bien__proprietaire"]
    readonly_fields = ["document"]
    fields = ["bail", "type", "date", ("compteur_electricite", "compteur_gaz"), ("compteur_eau_froide", "compteur_eau_chaude"), "nb_cles", "nouvelle_adresse", "observations", "document"]

    @admin.display(description="document")
    def document(self, obj):
        if not obj.pk:
            return "Enregistrez pour générer la fiche pré-remplie."
        return format_html('<a href="{}" target="_blank">Fiche PDF pré-remplie</a>', reverse("document_edl", args=[obj.pk]))


@admin.register(MouvementDepot)
class MouvementDepotAdmin(admin.ModelAdmin):
    list_display = ["date", "type", "montant_affiche", "bail", "mode", "recu"]
    list_filter = ["type", "bail__lot__bien__proprietaire"]
    search_fields = ["bail__lot__designation", "bail__locataires__nom"]
    date_hierarchy = "date"
    change_list_template = "admin/patrimoine/mouvementdepot/change_list.html"

    @admin.display(description="montant (€)", ordering="montant")
    def montant_affiche(self, obj):
        return obj.montant_signe

    @admin.display(description="reçu")
    def recu(self, obj):
        if obj.type == MouvementDepot.Type.ENCAISSEMENT:
            return format_html('<a href="{}" target="_blank">Reçu PDF</a>', reverse("document_recu_depot", args=[obj.pk]))
        return "–"

    def changelist_view(self, request, extra_context=None):
        soldes = []
        for proprio in Proprietaire.objects.all():
            mouvements = MouvementDepot.objects.filter(bail__lot__bien__proprietaire=proprio)
            solde = sum((m.montant_signe for m in mouvements), 0)
            if mouvements.exists():
                soldes.append((proprio, solde))
        extra_context = {**(extra_context or {}), "soldes": soldes, "total_detenu": sum(s for _, s in soldes)}
        return super().changelist_view(request, extra_context)


@admin.register(Pret)
class PretAdmin(admin.ModelAdmin):
    list_display = ["__str__", "proprietaire", "bien", "taux_affiche", "mensualite", "fin", "restant_du", "amortissement"]
    list_filter = ["proprietaire", "banque", "type"]
    readonly_fields = ["calculs"]
    fieldsets = [
        (None, {"fields": ["proprietaire", "bien", "banque", "reference", "type"]}),
        ("Conditions", {"fields": ["montant", "taux", "duree_mois", "date_premiere_echeance", "assurance_mensuelle"]}),
        ("Calculs", {"fields": ["calculs"]}),
        ("Notes", {"fields": ["notes"]}),
    ]
    change_list_template = "admin/patrimoine/pret/change_list.html"

    @admin.display(description="taux")
    def taux_affiche(self, obj):
        return f"{obj.taux:.3f} %".replace(".", ",")

    @admin.display(description="mensualité + assur. (€)")
    def mensualite(self, obj):
        return obj.mensualite_hors_assurance + obj.assurance_mensuelle

    @admin.display(description="fin")
    def fin(self, obj):
        return obj.date_derniere_echeance.strftime("%m/%Y")

    @admin.display(description="restant dû aujourd'hui (€)")
    def restant_du(self, obj):
        return obj.capital_restant_du(timezone.localdate())

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
    list_display = ["date", "titre", "categorie", "bien", "annuel", "fait"]
    list_editable = ["fait"]
    list_filter = ["fait", "categorie", "annuel", "bien"]
    list_display_links = ["titre"]
    search_fields = ["titre", "notes"]
    actions = ["marquer_fait"]

    @admin.action(description="Marquer comme fait (les rappels annuels passent à l'année suivante)")
    def marquer_fait(self, request, queryset):
        for rappel in queryset:
            rappel.marquer_fait()


@admin.register(RevisionLoyer)
class RevisionLoyerAdmin(admin.ModelAdmin):
    list_display = ["date_effet", "bail", "ancien_loyer", "nouveau_loyer", "trimestre", "appliquee", "liens"]
    list_filter = ["appliquee", "bail__lot__bien__proprietaire"]

    def has_add_permission(self, request):
        return False  # une révision se crée depuis la page « Réviser le loyer » du bail

    @admin.display(description="")
    def liens(self, obj):
        return format_html('<a href="{}">Ouvrir</a> · <a href="{}" target="_blank">Courrier PDF</a>',
                           f"{reverse('revision_loyer', args=[obj.bail_id])}?revision={obj.pk}", reverse("lettre_revision", args=[obj.pk]))
