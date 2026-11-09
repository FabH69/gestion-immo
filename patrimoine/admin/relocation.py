"""Écrans des changements de locataire (tâches), candidatures et annonces."""
from datetime import timedelta
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from urllib.parse import quote
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.html import format_html, format_html_join

from ..models import (
    ModeleAnnonce,
    ParametresMail,
    PieceCandidature,
    GarantCandidature,
    Annonce,
    Candidature,
    ChangementLocataire,
    Lot,
    Tache,
    TacheParDefaut,
)
from .communs import LotsALouerMixin


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
    readonly_fields = ["lien_a_envoyer", "statut", "recue_le", "taux_effort", "suite"]
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
            (None, {"fields": ["lot", "lien_a_envoyer", "statut", "recue_le", "taux_effort", "suite"]}),
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
        from .. import mails

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

    @admin.action(description="Retenir et préparer le bail")
    def retenir(self, request, queryset):
        recues = list(queryset.filter(statut=Candidature.Statut.RECUE))
        if not recues:
            self.message_user(request, "Seuls les dossiers reçus peuvent être retenus.", messages.WARNING)
            return None
        if len(recues) == 1:
            return self._retenir(request, recues[0])
        for c in recues:
            locataire = c.creer_locataire()
            self.message_user(request, format_html('Locataire créé : <a href="{}">{}</a>. Créez ensuite le bail.',
                                                   reverse("admin:patrimoine_locataire_change", args=[locataire.pk]), locataire))
        return None

    def _retenir(self, request, candidature):
        from urllib.parse import urlencode

        locataire = candidature.retenir()
        self.message_user(request, format_html(
            "Dossier retenu : locataire <a href=\"{}\">{}</a> créé avec ses garants et ses pièces ; les autres candidatures du logement "
            "sont passées en « non retenue ». Vérifiez le bail pré-rempli ci-dessous puis enregistrez.",
            reverse("admin:patrimoine_locataire_change", args=[locataire.pk]), locataire))
        return redirect(reverse("admin:patrimoine_bail_add") + "?" + urlencode(candidature.valeurs_bail(locataire)))

    def get_urls(self):
        return [path("<int:pk>/retenir/", self.admin_site.admin_view(self.retenir_vue), name="patrimoine_candidature_retenir")] + super().get_urls()

    def retenir_vue(self, request, pk):
        from django.core.exceptions import PermissionDenied

        candidature = get_object_or_404(Candidature, pk=pk)
        if request.method != "POST" or not self.has_change_permission(request, candidature):
            raise PermissionDenied
        if candidature.statut != Candidature.Statut.RECUE:
            messages.warning(request, "Ce dossier n'est pas (ou plus) en attente : rien n'a été fait.")
            return redirect("admin:patrimoine_candidature_change", pk)
        return self._retenir(request, candidature)

    @admin.display(description="suite")
    def suite(self, obj):
        if obj.statut != Candidature.Statut.RECUE:
            return "–"
        return format_html(
            '<button type="submit" class="button default" formaction="{}" formnovalidate '
            'onclick="return confirm(\'Retenir ce dossier ? Le locataire sera créé et les autres candidatures du logement passeront en « non retenue ».\')">'
            'Retenir et préparer le bail</button>'
            '<p class="help">Crée le locataire, ses garants et ses pièces, passe les autres candidatures du logement en « non retenue », '
            'puis ouvre un bail pré-rempli (loyer, charges et dépôt de l\'annonce, date d\'entrée souhaitée) à vérifier.</p>',
            reverse("admin:patrimoine_candidature_retenir", args=[obj.pk]))

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
