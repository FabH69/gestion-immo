"""Écrans des prêts et des factures (comptabilité)."""
from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from ..models import PieceComptable, Bien, Pret, Proprietaire
from .communs import euros_liste


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
        nombre = PieceComptable.objects.filter(self.MANQUE).count()
        return [("oui", f"À codifier ({nombre})" if nombre else "À codifier"), ("non", "Codifiées")]

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
            from ..lecture_factures import analyser, texte_pdf

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
