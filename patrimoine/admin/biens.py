"""Écrans des propriétaires, syndics, biens et lots."""
import json
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from ..models import AgenceGestion, Syndic, Bail, Bien, PhotoLot, Lot, Proprietaire
from .communs import euros_liste
from .fiche import FicheMixin
from ..modele_edl import CODES as CODES_PIECES, TYPES_PIECES, nettoyer as nettoyer_config


class BienInline(admin.TabularInline):
    """Biens du propriétaire : les biens vendus passent en bas, avec la date de vente et le prix s'il est connu."""
    model = Bien
    fields = ["nom", "ville", "vente"]
    readonly_fields = ["vente"]
    extra = 0
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("immobilisations").order_by("vendu", "-date_vente", "nom")

    @admin.display(description="vente")
    def vente(self, obj):
        if not obj.pk or not obj.vendu:
            return "–"
        from ..vente import prix_connu

        # prix connu : prix de vente de la fiche, sinon prix de cession des immobilisations sorties pour « Vente du bien »
        prix = prix_connu(obj)
        texte = "Vendu" + (f" le {obj.date_vente:%d/%m/%Y}" if obj.date_vente else "")
        if prix is not None:
            texte += f" – {euros_liste(prix)}"
        return format_html('<span class="pill gris">{}</span>', texte)


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
    """Baux du lot, en lecture : un bail se modifie sur sa propre fiche (« Modifier » ouvre le bail)."""
    model = Bail
    fields = ["locataires_du_bail", "type", "date_debut", "date_fin", "loyer", "actif"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    verbose_name_plural = "Baux du logement"

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("locataires").order_by("-actif", "-date_debut")

    @admin.display(description="locataires")
    def locataires_du_bail(self, obj):
        return ", ".join(str(l) for l in obj.locataires.all()) or "–"

    @admin.display(description="loyer CC")
    def loyer(self, obj):
        return euros_liste(obj.loyer_cc)


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
        biens = list(obj.biens.all())
        vendus = sum(1 for b in biens if b.vendu)
        en_cours = len(biens) - vendus
        return f"{en_cours} (+ {vendus} vendu{'s' if vendus > 1 else ''})" if vendus else en_cours


@admin.register(AgenceGestion)
class AgenceGestionAdmin(admin.ModelAdmin):
    """Agences de gestion : leurs biens et le récap annuel à saisir, propriétaire par propriétaire."""
    list_display = ["nom", "ville", "telephone", "contact_nom", "liste_biens", "recaps"]
    search_fields = ["nom", "ville", "contact_nom"]
    fieldsets = [
        (None, {"fields": ["nom", "mot_releve", "adresse", ("code_postal", "ville"), ("email", "telephone"), "contact_nom"]}),
        (None, {"fields": ["notes"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("biens__proprietaire")

    @admin.display(description="biens gérés")
    def liste_biens(self, obj):
        return ", ".join(b.nom for b in obj.biens.all()) or "–"

    @admin.display(description="récap annuel")
    def recaps(self, obj):
        from django.utils import timezone

        aujourdhui = timezone.localdate()
        annee = aujourdhui.year - (1 if aujourdhui.month <= 6 else 0)
        proprietaires = sorted({b.proprietaire for b in obj.biens.all()}, key=lambda p: p.nom)
        if not proprietaires:
            return "–"
        return format_html_join(" · ", '<a class="button" href="{}">Récap {} – {}</a>',
                                ((reverse("recap_agence", args=[obj.pk, p.pk, annee]), annee, p.nom) for p in proprietaires))


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


def _champ_config(libelle):
    return forms.IntegerField(label=libelle, required=False, min_value=0, max_value=20,
                              widget=forms.NumberInput(attrs={"class": "nombre-piece", "inputmode": "numeric", "min": 0, "max": 20, "placeholder": "0"}))


class _LotFormBase(forms.ModelForm):
    """La configuration du logement (nombre de chambres, de salles de bains, de WC, garage, cave…) est rangée dans un seul champ."""

    class Meta:
        model = Lot
        exclude = ["configuration"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configuration = self.instance.configuration or {}
        for code in CODES_PIECES:
            if f"config_{code}" in self.fields:
                self.fields[f"config_{code}"].initial = configuration.get(code)

    def clean(self):
        donnees = super().clean()
        if any(f"config_{code}" in self.fields for code in CODES_PIECES):
            self.instance.configuration = nettoyer_config({code: donnees.get(f"config_{code}") for code in CODES_PIECES})
        return donnees


LotForm = type("LotForm", (_LotFormBase,), {f"config_{code}": _champ_config(libelle) for code, _, libelle, _, _ in TYPES_PIECES})


class BienForm(forms.ModelForm):
    class Meta:
        model = Bien
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "regime_fiscal" in self.fields:
            self.fields["regime_fiscal"].required = True
        if "proprietaire" in self.fields:
            # pour la fiche : les SCI dont le régime s'impose aux biens (le script verrouille régime et option)
            imposes = {str(p.pk): Bien.REGIME_DU_PROPRIETAIRE[p.type]
                       for p in Proprietaire.objects.filter(type__in=list(Bien.REGIME_DU_PROPRIETAIRE))}
            widget = self.fields["proprietaire"].widget
            getattr(widget, "widget", widget).attrs["data-regimes-imposes"] = json.dumps(imposes)  # sous l'enveloppe « + »

    def clean(self):
        donnees = super().clean()
        regime, proprio = donnees.get("regime_fiscal"), donnees.get("proprietaire")
        impose = Bien.REGIME_DU_PROPRIETAIRE.get(proprio.type) if proprio else None
        if impose:
            # SCI à l'IR (ou à l'IS) : le bien prend le régime de la SCI, au réel, quel que soit le choix affiché
            regime = donnees["regime_fiscal"] = impose
            donnees["option_fiscale"] = Bien.OptionFiscale.REEL
            for champ in ("regime_fiscal", "option_fiscale"):
                self.errors.pop(champ, None)
        if regime in Bien.REGIMES_MICRO_POSSIBLE:
            pass
        elif donnees.get("option_fiscale") == Bien.OptionFiscale.MICRO and regime:
            self.add_error("option_fiscale", "Les SCI et les sociétés sont toujours au réel.")
        if proprio and regime:
            nom_propre = proprio.type == Proprietaire.Type.NOM_PROPRE
            if regime in ("nu", "lmnp", "lmp") and not nom_propre:
                self.add_error("regime_fiscal", f"« {proprio} » est une société : choisissez SCI à l'IR, société à l'IR ou à l'IS.")
            if regime in ("sci_ir", "societe_ir", "societe_is") and nom_propre:
                self.add_error("regime_fiscal", f"« {proprio} » est en nom propre : choisissez location nue, LMNP ou LMP.")
        return donnees


@admin.register(Bien)
class BienAdmin(admin.ModelAdmin):
    form = BienForm
    list_display = ["nom_bien", "ville", "proprietaire", "regime", "syndic", "nb_lots", "loyers_mois", "rendement"]
    list_filter = [VenduFilter, "proprietaire", "regime_fiscal"]

    class Media:
        js = ["patrimoine/regime_impose.js", "patrimoine/vente.js"]

    @admin.display(description="régime fiscal", ordering="regime_fiscal")
    def regime(self, obj):
        court = obj.regime_court
        if obj.regime_fiscal in Bien.REGIMES_MICRO_POSSIBLE:
            court += " micro" if obj.micro else " réel"
        return f"{court} (à vérifier)" if obj.regime_a_verifier else court

    def get_list_display(self, request):
        """Colonnes de vente seulement quand on regarde les biens vendus (sinon des croix rouges partout)."""
        vente = request.GET.get("vente") or "non"
        if vente == "oui":
            return ["nom_bien", "ville", "proprietaire", "nb_lots", "date_vente"]
        if vente == "tous":
            return self.list_display + ["vendu", "date_vente"]
        return self.list_display

    def get_queryset(self, request):
        from django.db.models import Prefetch
        return super().get_queryset(request).select_related("proprietaire", "syndic").prefetch_related(
            "lots", "lots__photos", Prefetch("lots__baux", queryset=Bail.objects.filter(actif=True), to_attr="baux_actifs"))

    def _loyers_hc(self, obj):
        return sum((b.loyer_hc or 0 for lot in obj.lots.all() for b in lot.baux_actifs), Decimal(0))

    def _loyers_cc(self, obj):
        """Loyers charges comprises des baux actifs : ce que les locataires versent chaque mois."""
        return sum(((b.loyer_hc or 0) + (b.charges or 0) for lot in obj.lots.all() for b in lot.baux_actifs), Decimal(0))

    @staticmethod
    def _nb_courte_duree(obj):
        return sum(1 for lot in obj.lots.all() if lot.exploitation == "courte" and not lot.vendu)

    @admin.display(description="nom", ordering="nom")
    def nom_bien(self, obj):
        if self._nb_courte_duree(obj):
            return format_html('{} <span class="pill bleu">Courte durée</span>', obj.nom)
        return obj.nom

    @admin.display(description="loyers CC / mois")
    def loyers_mois(self, obj):
        total = self._loyers_cc(obj)
        return f"{total:,.0f} €".replace(",", " ") if total else "–"

    @admin.display(description="rendement brut (HC)")
    def rendement(self, obj):
        total = self._loyers_hc(obj)
        if not total or not obj.prix_acquisition:
            return "–"
        return f"{total * 12 / obj.prix_acquisition * 100:.1f} %".replace(".", ",")
    search_fields = ["nom", "adresse", "ville"]
    inlines = [LotInline]
    readonly_fields = ["apercu_photo"]

    @admin.display(description="aperçu")
    def apercu_photo(self, obj):
        if obj and obj.photo:
            return format_html('<img src="{}" alt="" style="max-height:140px;max-width:100%;border-radius:8px">', obj.photo.url)
        return "Aucune photo"

    VUES = ("liste", "galerie")

    def get_urls(self):
        from django.urls import path

        return [path("ordre-galerie/", self.admin_site.admin_view(self.ordre_galerie_view), name="patrimoine_bien_ordre_galerie")] + super().get_urls()

    def ordre_galerie_view(self, request):
        """Enregistre l'ordre de la galerie réglé par glisser-déposer : colonnes propriétaires (POST quoi=proprietaire&ids=3,1,2)
        ou biens d'une colonne (quoi=bien)."""
        from django.http import HttpResponseNotAllowed, JsonResponse

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_view_or_change_permission(request):
            return JsonResponse({"ok": False}, status=403)
        ids = [int(i) for i in request.POST.get("ids", "").split(",") if i.strip().isdigit()]
        modele = Bien if request.POST.get("quoi") == "bien" else Proprietaire
        for rang, pk in enumerate(ids, start=1):
            modele.objects.filter(pk=pk).update(ordre_galerie=rang)
        return JsonResponse({"ok": True})

    def changelist_view(self, request, extra_context=None):
        """Bascule liste / galerie : ?vue=galerie est retenue pour la session, puis retirée des paramètres de la liste
        (l'admin refuse les paramètres qu'il ne connaît pas)."""
        if "vue" in request.GET:
            if request.GET["vue"] in self.VUES:
                request.session["biens_vue"] = request.GET["vue"]
            get = request.GET.copy()
            del get["vue"]
            request.GET = get
            request.META["QUERY_STRING"] = get.urlencode()
        vue = request.session.get("biens_vue", "liste")
        liens = {}
        for v in self.VUES:
            get = request.GET.copy()
            get["vue"] = v
            liens[f"url_{v}"] = "?" + get.urlencode()
        reponse = super().changelist_view(request, {**(extra_context or {}), "vue": vue, **liens})
        if vue == "galerie" and hasattr(reponse, "context_data") and "cl" in reponse.context_data:
            reponse.context_data["galerie"] = self._galerie(reponse.context_data["cl"].result_list)
        return reponse

    def _galerie(self, biens):
        """Une colonne par propriétaire (ordre réglé par glisser-déposer, sinon alphabétique), chacune avec les cartes de ses biens."""
        blocs = {}
        for bien in sorted(biens, key=lambda b: (b.proprietaire.ordre_galerie or 10**6, b.proprietaire.nom.lower(),
                                            b.ordre_galerie or 10**6, b.nom.lower())):
            lots = [lot for lot in bien.lots.all()]
            courte = self._nb_courte_duree(bien)
            # un logement en courte durée n'a pas de bail mais n'est pas vacant
            loues = sum(1 for lot in lots if lot.baux_actifs or (lot.exploitation == "courte" and not lot.vendu))
            loyers = self._loyers_cc(bien)
            bloc = blocs.setdefault(bien.proprietaire_id, {"proprietaire": bien.proprietaire, "cartes": [], "loyers": Decimal(0)})
            bloc["loyers"] += loyers
            bloc["cartes"].append({
                "bien": bien, "photo": bien.photo_carte, "nb_lots": len(lots), "loues": loues,
                "courte": courte, "que_courte": bool(lots) and courte == len(lots),
                "occupation": "vide" if not loues else ("partiel" if loues < len(lots) else "plein"),
                "loyers": loyers, "rendement": self.rendement(bien),
                "regime": self.regime(bien),
            })
        for bloc in blocs.values():
            mots = bloc["proprietaire"].nom.replace("-", " ").split() or ["?"]
            bloc["initiales"] = (mots[0][0] + (mots[-1][0] if len(mots) > 1 else "")).upper()
            bloc["teinte"] = bloc["proprietaire"].pk % 6  # couleur stable quand on déplace les colonnes
        return list(blocs.values())
    fieldsets = [
        (None, {"fields": ["proprietaire", "nom", "adresse", ("code_postal", "ville"), "syndic", ("date_acquisition", "prix_acquisition")]}),
        ("Photo", {"fields": [("apercu_photo", "photo")]}),
        ("Régime fiscal", {"fields": [("regime_fiscal", "option_fiscale")],
                           "description": format_html(
                               "Choisit la comptabilité du bien, ses écrans et le formulaire à remplir : <b>2044</b> (location nue), "
                               "<b>2072</b> (SCI à l'IR), <b>2031 / 2042-C-PRO</b> (meublé). Récapitulatif et alertes : "
                               "Comptabilité › Régimes fiscaux.")}),
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
        ("Gestion confiée à une agence", {"fields": [("agence", "agence_depuis")],
                                          "description": format_html(
                                              "L'agence encaisse les loyers et vous vire un <b>acompte</b> chaque mois, puis régularise "
                                              "en fin d'année. Les loyers du bien sont marqués <b>encaissés par l'agence</b> à leur "
                                              "échéance (jamais à relancer) ; en fin d'année, saisissez son récap depuis "
                                              "Comptabilité › Banque › Agences de gestion.")}),
        ("Vente", {"classes": ["cases-a-cocher"], "fields": [("vendu", "date_vente", "prix_vente")],
                   "description": "Un bien vendu (et tous ses lots) n'est plus proposé pour les baux, candidatures et annonces, "
                                  "mais reste dans les états des loyers. La date de vente est facultative."}),
        ("Remboursements de l'acquéreur", {
            "classes": ["remboursements-vente"],
            "fields": [("remb_taxe_fonciere", "remb_copro"), ("remb_autres", "remb_autres_libelle")],
            "description": format_html(
                "Recopiez le décompte du notaire : en plus du prix, l'acquéreur vous rembourse sa part de taxe foncière et de "
                "charges de copropriété. Ces montants sont <b>déduits des charges de l'année de la vente</b> (taxe foncière, "
                "copropriété) dans la 2044, la 2072 ou le récapitulatif LMNP / société. Ce ne sont ni des loyers ni une partie "
                "du prix. Dans le relevé de banque, le virement global du notaire se répartit tout seul entre prix et "
                "remboursements.")}),
        ("Notes", {"fields": ["notes"]}),
    ]

    def save_model(self, request, obj, form, change):
        obj.regime_a_verifier = False  # régime confirmé en enregistrant la fiche
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
        if obj.agence_id:
            from ..agence import encaisser_loyers_agence

            n = encaisser_loyers_agence()
            if n:
                messages.info(request, f"{n} loyer(s) échu(s) de ce bien marqué(s) « encaissé par l'agence » {obj.agence}.")
        from .. import vente

        for niveau, texte in vente.synchroniser(obj):
            (messages.warning if niveau == "warning" else messages.info)(request, texte)
        from ..rappels import rappel_import_lcd

        for lot in obj.lots.all():
            rappel_import_lcd(lot)  # bien vendu : arrête le rappel mensuel d'import courte durée
        if not obj.vendu:
            from ..rappels import rappel_cfe

            if rappel_cfe(obj):
                messages.info(request, "Rappel annuel « Payer la CFE » ajouté pour ce bien (15 décembre, ★ important, 10 jours avant).")

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
class LotAdmin(FicheMixin, admin.ModelAdmin):
    onglets = [
        ("general", "Général", [None, "Notes"], []),
        ("caracteristiques", "Caractéristiques", ["Caractéristiques (reprises dans le bail)", "Chaudière gaz", "Compteurs"], []),
        ("configuration", "Pièces et état des lieux", ["Configuration du logement (état des lieux)"], []),
        ("relocation", "Location et photos", ["Location et annonce"], ["PhotoInline"]),
        ("baux", "Baux", [], ["BailInline"]),
        ("vente", "Vente", ["Vente"], []),
    ]

    def contenu_fiche(self, request, obj):
        from .. import fiches

        return fiches.fiche_lot(obj)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        bien = form.base_fields.get("bien")
        if obj is not None and bien is not None:  # le bien s'ouvre par le lien de l'en-tête
            bien.widget.can_add_related = bien.widget.can_change_related = bien.widget.can_delete_related = bien.widget.can_view_related = False
        return form

    list_display = ["designation", "bien_nom", "type_lot", "surface", "exploitation_pill", "locataire_actuel", "loyer_actuel"]

    def get_queryset(self, request):
        from django.db.models import Prefetch
        return super().get_queryset(request).select_related("bien").prefetch_related(
            Prefetch("baux", queryset=Bail.objects.filter(actif=True).prefetch_related("locataires"), to_attr="baux_actifs_charges"))
    list_filter = [VenduFilter, "bien__proprietaire"]
    search_fields = ["designation", "bien__nom"]
    inlines = [BailInline, PhotoInline]
    form = LotForm
    readonly_fields = ["outils_relocation", "verification_dpe", "modele_edl"]

    @admin.display(description="modèle d'état des lieux")
    def modele_edl(self, obj):
        from ..modele_edl import modele

        if not obj or not obj.pk:
            return "Enregistrez le logement pour voir le modèle."
        lignes = format_html_join("", "<li><strong>{}</strong> <span class='help'>({})</span></li>",
                                  ((p, ", ".join(els)) for p, els in modele(obj)))
        return format_html("<ol class='modele-edl'>{}</ol><p class='help'>Modèle repris par chaque nouvel état des lieux d'entrée "
                           "(une sortie reprend les pièces de son entrée). Pendant la saisie, on peut encore ajouter ou retirer une pièce ou un élément.</p>", lignes)

    @admin.display(description="vérification")
    def verification_dpe(self, obj):
        from .. import dpe

        if not obj.pk:
            return "Enregistrez le lot, puis revenez ici pour vérifier le DPE sur la base de l'ADEME."
        alertes = format_html_join("", '<div class="pill {}" style="display:block;margin:4px 0;white-space:normal">{}</div>',
                                   (({"rouge": "rouge", "orange": "orange"}.get(a["niveau"], "bleu"), a["texte"]) for a in obj.dpe_alertes()))
        fin = obj.dpe_fin_validite
        etat = format_html("Valable jusqu'au <strong>{}</strong>.", f"{fin:%d/%m/%Y}") if fin else "Fin de validité inconnue : saisissez la date du DPE."
        lien = format_html(' <a href="{}" target="_blank" rel="noopener">Voir sur l\'observatoire de l\'ADEME</a>', dpe.lien_observatoire(obj.numero_dpe)) if obj.numero_dpe else ""
        bouton = format_html('<button type="submit" class="button" formaction="{}" formnovalidate>Compléter depuis l\'ADEME</button>',
                             reverse("lot_dpe_ademe", args=[obj.pk]))
        return format_html('{} {}{}<div style="margin-top:6px">{} <span class="help">Remplit classe énergie, GES, date et fin de validité '
                           'à partir du n° saisi (les autres modifications non enregistrées sont perdues).</span></div>', etat, alertes, lien, bouton)

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
        (None, {"classes": ["identite-lot"], "fields": ["bien", "designation", ("type", "surface", "nb_pieces", "meuble"), "etage", ("exploitation", "tourisme_classe", "lmnp")]}),
        ("Caractéristiques (reprises dans le bail)", {"fields": ["annee_construction", ("mode_chauffage", "chauffage"), ("mode_eau_chaude", "eau_chaude"),
                                                              ("code_porte", "cave", "parking"), ("classe_dpe", "numero_dpe"), ("date_dpe", "fin_validite_dpe"), "verification_dpe",
                                                              "equipements", "annexes", "clause_particuliere"],
                                                   "description": "Le régime de copropriété, les équipements communs et la zone tendue se règlent sur la fiche du bien."}),
        ("Chaudière gaz", {"fields": [("chaudiere_gaz", "date_entretien_chaudiere")]}),
        ("Location et annonce", {"fields": ["loyer_propose", ("classe_ges", "energie_annee"), ("energie_min", "energie_max"), "outils_relocation"]}),
        ("Configuration du logement (état des lieux)", {
            "fields": [tuple(f"config_{c}" for c in CODES_PIECES[i:i + 3]) for i in range(0, len(CODES_PIECES), 3)] + ["pieces", "modele_edl"],
            "description": "Indiquez le nombre de chaque pièce : l'état des lieux (sur téléphone et en PDF) reprend ces pièces, "
                           "avec pour chacune les éléments à contrôler (évier et hotte en cuisine, cuvette et chasse d'eau aux WC…)."}),
        ("Compteurs", {"fields": ["pdl"]}),
        ("Vente", {"fields": [("vendu", "date_vente")]}),
        ("Notes", {"fields": ["notes"]}),
    ]

    def save_model(self, request, obj, form, change):
        from .. import dpe

        obj.numero_dpe = dpe.normaliser_numero(obj.numero_dpe)
        if obj.date_vente:
            obj.vendu = True
        super().save_model(request, obj, form, change)
        from ..rappels import rappel_import_lcd

        if rappel_import_lcd(obj):
            messages.info(request, "Rappel mensuel ajouté : importer les réservations du mois précédent le 5 de chaque mois.")

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
