from datetime import date, timedelta

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from urllib.parse import quote
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .models import SiteUtile, Syndic, Annonce, ChargeLCD, ParametresLCD, ReservationLCD, Bail, Bien, Candidature, ChangementLocataire, Garant, PhotoLot, Echeance, EtatDesLieux, MouvementDepot, Pret, Rappel, RevisionLoyer, Locataire, Lot, Paiement, Proprietaire, Tache

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
    fields = [("civilite", "prenom", "nom"), ("date_naissance", "lieu_naissance"), "adresse", ("email", "telephone"),
              ("lien", "profession", "revenus_mensuels"), ("type_caution", "montant_max", "duree_mois"), "acte"]
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
    fields = ["nom", "type", "representant", ("siret", "rcs", "date_immatriculation"), "adresse", ("code_postal", "ville"), "iban", "notes"]

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


@admin.register(SiteUtile)
class SiteUtileAdmin(admin.ModelAdmin):
    list_display = ["nom", "url", "ordre"]
    list_editable = ["ordre"]
    fields = ["nom", "url", "ordre"]


@admin.register(Bien)
class BienAdmin(admin.ModelAdmin):
    list_display = ["nom", "ville", "proprietaire", "syndic", "nb_lots"]
    list_filter = ["proprietaire", "ville", "syndic"]
    search_fields = ["nom", "adresse", "ville"]
    inlines = [LotInline]
    fields = ["proprietaire", "nom", "adresse", ("code_postal", "ville"), "syndic", ("date_acquisition", "prix_acquisition"), "notes"]

    @admin.display(description="lots")
    def nb_lots(self, obj):
        return obj.lots.count()


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ["designation", "bien", "type", "surface", "meuble", "locataire_actuel", "loyer_actuel", "vendu", "date_vente"]
    list_filter = ["vendu", "bien__proprietaire", "bien", "type", "meuble"]
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
        (None, {"fields": ["bien", "designation", "type", "surface", "nb_pieces", "meuble", "etage"]}),
        ("Caractéristiques (reprises dans le bail)", {"fields": ["annee_construction", "regime_juridique", "chauffage", "eau_chaude", "classe_dpe", "zone_tendue", "equipements", "annexes"]}),
        ("Chaudière gaz", {"fields": ["chaudiere_gaz", "date_entretien_chaudiere"]}),
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
        if obj.vendu:
            return "–"
        bail = obj.bail_actif
        return ", ".join(str(l) for l in bail.locataires.all()) if bail else "Vacant"

    @admin.display(description="loyer CC (€)")
    def loyer_actuel(self, obj):
        bail = obj.bail_actif
        return bail.loyer_cc if bail else "–"


class PresentFilter(admin.SimpleListFilter):
    title = "statut"
    parameter_name = "present"

    def lookups(self, request, model_admin):
        return [("oui", "Actifs (dans les lieux)"), ("non", "Anciens (partis)"), ("tous", "Tous")]

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


class LocataireForm(forms.ModelForm):
    """Fiche locataire avec rattachement à un lot et déclaration de sortie."""

    lot = forms.ModelChoiceField(
        Lot.objects.filter(vendu=False).select_related("bien"), required=False, label="logement loué",
        help_text="Rattache le locataire au bail en cours de ce lot. S'il n'y en a pas, un bail est créé : complétez-le ensuite.",
    )
    date_entree = forms.DateField(
        label="date d'entrée", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Utilisée si un nouveau bail est créé (aujourd'hui par défaut).",
    )
    sortie = forms.BooleanField(label="Le locataire quitte le logement", required=False)
    date_sortie = forms.DateField(label="date de sortie", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

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


@admin.register(Locataire)
class LocataireAdmin(admin.ModelAdmin):
    form = LocataireForm
    list_display = ["__str__", "present", "logement", "date_sortie", "email", "telephone", "liste_garants"]
    list_filter = [PresentFilter]
    search_fields = ["nom", "prenom", "email"]
    inlines = [GarantInline]

    class Media:
        js = ["patrimoine/sortie_locataire.js"]

    def get_fieldsets(self, request, obj=None):
        identite = (None, {"fields": ["civilite", "nom", "prenom", "email", "telephone", ("date_naissance", "lieu_naissance"), "nationalite", "adresse_actuelle", "notes"]})
        logement = ("Logement", {"fields": ["lot", "date_entree"]})
        if obj and obj.baux.filter(actif=True).exists():
            sortie = ("Sortie du locataire", {"fields": ["sortie", "date_sortie"], "classes": ["sortie-locataire"],
                                              "description": "Cochez pour enregistrer le départ : le bail est clôturé et les rappels de fin de bail sont créés (restitution du dépôt de garantie, fin de la garantie Visale)."})
            return [sortie, identite, logement]
        return [identite, logement]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        donnees = form.cleaned_data
        bail = form.bail_actif()
        if donnees.get("sortie") and bail:
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
        return ", ".join(str(g) for g in obj.garants.all()) or "–"


@admin.register(Bail)
class BailAdmin(admin.ModelAdmin):
    list_display = ["liste_locataires", "lot", "type", "date_debut", "colonne_loyer_initial", "colonne_loyer_actuel", "charges", "loyer_cc", "actif"]
    list_display_links = ["liste_locataires", "lot"]
    list_filter = ["actif", "type", "type_garantie", "lot__bien__proprietaire"]
    search_fields = ["lot__designation", "lot__bien__nom", "locataires__nom"]
    filter_horizontal = ["locataires"]
    date_hierarchy = "date_debut"
    readonly_fields = ["documents", "recu_depot", "historique"]
    fieldsets = [
        (None, {"fields": ["lot", "locataires", "type", "destination", "date_debut", "date_fin", "actif"]}),
        ("Conditions financières", {"fields": [("loyer_initial", "charges"), "loyer_hc", "mode_charges", ("depot_garantie", "recu_depot"), "jour_echeance", "paiement_a_echoir"]}),
        ("Garantie", {"fields": [("type_garantie", "detail_garantie")], "description": "Les garants se saisissent en bas de page."}),
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
        return format_html(
            "{}<p class='help'>Les champs manquants apparaissent « à compléter » : complétez la fiche du lot, du locataire ou du propriétaire. "
            "Vérifiez le bail avec le modèle officiel (décret n° 2015-587) avant signature.</p>",
            format_html(" · ".join('<a href="{}" target="_blank">{}</a>' for _ in liens), *[x for l in liens for x in l]),
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


class DepotEnCoursFilter(admin.SimpleListFilter):
    title = "garanties"
    parameter_name = "en_cours"

    def lookups(self, request, model_admin):
        return [("oui", "En cours (encore détenues)"), ("non", "Soldées (restituées)")]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        en_cours = [b.pk for b in Bail.objects.prefetch_related("mouvements_depot") if b.depot_detenu > 0]
        return queryset.filter(bail__in=en_cours) if self.value() == "oui" else queryset.exclude(bail__in=en_cours)


@admin.register(MouvementDepot)
class MouvementDepotAdmin(admin.ModelAdmin):
    list_display = ["date", "type", "montant_affiche", "logement", "locataires", "proprietaire", "mode", "recu"]
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
    list_filter = [DepotEnCoursFilter, "type", "bail__lot__bien__proprietaire"]
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
    list_display = ["__str__", "proprietaire", "bien", "taux_affiche", "mensualite", "fin", "restant_du", "amortissement"]
    list_filter = ["proprietaire", "banque", "type"]
    readonly_fields = ["calculs"]
    fieldsets = [
        (None, {"fields": ["proprietaire", "bien", "banque", "reference", "type"]}),
        ("Conditions", {"fields": ["montant", "taux", "duree_mois", "date_premiere_echeance", "assurance_mensuelle", "taux_assurance"]}),
        ("Calculs", {"fields": ["calculs"]}),
        ("Notes", {"fields": ["notes"]}),
    ]
    change_list_template = "admin/patrimoine/pret/change_list.html"

    @admin.display(description="taux")
    def taux_affiche(self, obj):
        return f"{obj.taux:.3f} %".replace(".", ",")

    @admin.display(description="mensualité + assur. (€)")
    def mensualite(self, obj):
        return obj.mensualite_totale

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
    list_display = ["date", "titre", "categorie", "bien", "periodicite", "actif", "fait"]
    list_editable = ["actif", "fait"]
    list_filter = ["actif", "fait", "periodicite", "categorie", "bien"]
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
    list_display = ["date_effet", "bail", "ancien_loyer", "ancien_irl", "nouvel_irl", "trimestre", "pourcentage", "nouveau_loyer", "appliquee", "liens"]
    list_filter = ["appliquee", "bail__lot__bien__proprietaire", "bail"]

    @admin.display(description="hausse")
    def pourcentage(self, obj):
        return f"+{(obj.nouvel_irl / obj.ancien_irl - 1) * 100:.2f} %".replace(".", ",") if obj.ancien_irl else "–"

    def has_add_permission(self, request):
        return False  # une révision se crée depuis la page « Réviser le loyer » du bail

    @admin.display(description="")
    def liens(self, obj):
        return format_html('<a href="{}">Ouvrir</a> · <a href="{}" target="_blank">Courrier PDF</a>',
                           f"{reverse('revision_loyer', args=[obj.bail_id])}?revision={obj.pk}", reverse("lettre_revision", args=[obj.pk]))


@admin.register(Garant)
class GarantAdmin(admin.ModelAdmin):
    list_display = ["__str__", "locataire", "bail", "type_caution", "montant_max", "acte"]
    list_filter = ["type_caution"]
    search_fields = ["nom", "prenom", "locataire__nom"]

    @admin.display(description="acte")
    def acte(self, obj):
        if obj.bail_id:
            return format_html('<a href="{}" target="_blank">PDF</a>', reverse("acte_cautionnement", args=[obj.pk]))
        return "–"


@admin.register(Candidature)
class CandidatureAdmin(admin.ModelAdmin):
    list_display = ["__str__", "statut", "situation", "revenus_mensuels", "taux_effort", "type_garantie", "recue_le"]
    list_filter = ["statut", "lot"]
    readonly_fields = ["lien_a_envoyer", "statut", "recue_le", "taux_effort"]
    actions = ["retenir", "refuser"]

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return [(None, {"fields": ["lot"], "description": "Choisissez le logement : un lien personnel sera créé pour le candidat."})]
        reponses = [f.name for f in Candidature._meta.fields if f.name not in ("id", "lot", "jeton", "statut", "cree_le", "recue_le", "consentement")]
        return [
            (None, {"fields": ["lot", "lien_a_envoyer", "statut", "recue_le", "taux_effort"]}),
            ("Réponses du candidat", {"fields": reponses}),
        ]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["lot", *self.readonly_fields]

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
        corps = (f"Bonjour,\n\nSuite à votre intérêt pour le logement {obj.lot.designation} ({obj.lot.bien.adresse}, {obj.lot.bien.ville}), "
                 f"voici le formulaire de candidature à remplir en ligne :\n{url}\n\nCordialement")
        mailto = f"mailto:{obj.email}?subject={quote('Votre dossier de candidature – ' + obj.lot.designation)}&body={quote(corps)}"
        return format_html(
            '<input type="text" value="{}" readonly style="width:100%;max-width:560px" onclick="this.select()"> '
            '<p><a class="button" href="{}">Préparer le mail au candidat</a></p>'
            "<p class='help'>Le lien ne fonctionnera pour le candidat qu'une fois l'application mise en ligne.</p>",
            url, mailto,
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
class AnnonceAdmin(admin.ModelAdmin):
    list_display = ["titre", "lot", "loyer_cc", "disponible_le", "cree_le"]
    list_filter = ["lot__bien"]
    readonly_fields = ["a_copier"]
    actions = ["regenerer"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["texte"].widget.attrs.update(rows=22, style="width:100%;max-width:720px")
        form.base_fields["points_forts"].widget.attrs.update(rows=4)
        form.base_fields["titre"].widget.attrs.update(size=60)
        return form
    fieldsets = [
        (None, {"fields": ["lot", ("loyer_cc", "charges", "charges_forfait"), "depot_garantie", "disponible_le", "points_forts"]}),
        ("Annonce", {"fields": ["titre", "texte", "a_copier"]}),
    ]

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
        return format_html(
            '{}<p><button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById(\'id_titre\').value);this.textContent=\'Titre copié\'">Copier le titre</button> '
            '<button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById(\'id_texte\').value);this.textContent=\'Texte copié\'">Copier le texte</button> '
            '<a class="button" href="{}">Télécharger les photos (zip)</a> '
            '<a class="button" href="https://www.leboncoin.fr/deposer-une-annonce" target="_blank" rel="noopener">Déposer sur Leboncoin</a></p>'
            '<div>{}</div><p class="help">Leboncoin ne permet pas la publication automatique pour les particuliers : copiez le titre et le texte, puis ajoutez les photos du zip.</p>',
            alerte, reverse("photos_zip", args=[obj.lot_id]), photos or "Aucune photo : ajoutez-en dans la fiche du lot.",
        )

    @admin.action(description="Régénérer le titre et le texte depuis la fiche du lot")
    def regenerer(self, request, queryset):
        for annonce in queryset:
            annonce.titre = ""
            annonce.texte = ""
            annonce.save()


@admin.register(ReservationLCD)
class ReservationLCDAdmin(admin.ModelAdmin):
    list_display = ["arrivee", "depart", "nuits", "voyageur", "plateforme", "lot", "montant_brut", "commission", "frais_service", "taxe_sejour", "net", "annulee"]
    list_filter = ["plateforme", "lot", "annulee"]
    search_fields = ["voyageur", "reference"]
    date_hierarchy = "arrivee"

    @admin.display(description="nuits")
    def nuits(self, obj):
        return obj.nuits

    @admin.display(description="net (€)")
    def net(self, obj):
        return obj.net


@admin.register(ChargeLCD)
class ChargeLCDAdmin(admin.ModelAdmin):
    list_display = ["libelle", "lot", "montant_mensuel", "debut", "fin"]
    list_filter = ["lot"]


@admin.register(ParametresLCD)
class ParametresLCDAdmin(admin.ModelAdmin):
    list_display = ["lot", "menage_par_sejour", "consommables_par_sejour", "commission_gestion"]
