"""Écrans des prêts et des factures (comptabilité)."""
from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .. import compta
from ..models import PieceComptable, Bien, Pret
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
        lignes = obj.lignes_banque()
        if lignes:
            source = format_html("Échéancier : <strong>tableau de la banque</strong> ({} échéances, du {} au {}).",
                                 len(lignes), lignes[0]["date"].strftime("%d/%m/%Y"), lignes[-1]["date"].strftime("%d/%m/%Y"))
        else:
            source = "Échéancier : calculé à partir des conditions ci-dessus (aucun tableau de la banque importé)."
        def e(v):
            return f"{v:,.2f}".replace(",", "\u202f").replace(".", ",")

        return format_html(
            "{}<br>Échéance habituelle : <strong>{} €</strong> dont {} € hors assurance · Dernière échéance : {} · "
            "Capital restant dû aujourd'hui : <strong>{} €</strong><br>"
            '<a href="{}" target="_blank">Tableau d\'amortissement PDF</a> · <a href="{}">Importer le tableau de la banque (PDF)</a>',
            source, e(obj.mensualite_totale), e(obj.mensualite_hors_assurance), obj.date_derniere_echeance.strftime("%d/%m/%Y"),
            e(obj.capital_restant_du(timezone.localdate())), reverse("document_amortissement", args=[obj.pk]),
            reverse("admin:patrimoine_pret_tableau", args=[obj.pk]),
        )

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj and obj.pk and obj.lignes.exists():
            # le tableau de la banque fait foi : plus besoin de recalage, les conditions sont indicatives
            fieldsets = [(nom, {**options, "description": "Le tableau de la banque fait foi : ces conditions sont indicatives "
                                                          "et ne changent plus l'échéancier."} if nom == "Conditions" else options)
                         for nom, options in fieldsets if nom != "Recalage sur la banque"]
        return fieldsets

    def get_urls(self):
        from django.urls import path

        return [path("<int:pk>/tableau/", self.admin_site.admin_view(self.tableau_view), name="patrimoine_pret_tableau")] + super().get_urls()

    def tableau_view(self, request, pk):
        """Import du tableau d'amortissement remis par la banque (PDF texte du Crédit Mutuel ou de la Caisse d'Épargne)."""
        from .. import tableaux_amortissement as ta

        pret = Pret.objects.select_related("bien").filter(pk=pk).first()
        if not pret:
            return redirect("admin:patrimoine_pret_changelist")
        retour = reverse("admin:patrimoine_pret_change", args=[pk])
        if request.method == "POST":
            if request.POST.get("action") == "calcul":
                n = pret.lignes.count()
                pret.lignes.all().delete()
                messages.success(request, f"Tableau de la banque retiré ({n} échéances) : l'échéancier est de nouveau calculé.")
                return redirect(retour)
            fichier = request.FILES.get("fichier")
            lignes = ta.lire(ta.texte_tableau(fichier)) if fichier else []
            if not lignes:
                messages.error(request, "Aucune échéance n'a pu être lue dans ce fichier. S'il s'agit d'un scan (photo ou PDF "
                                        "numérisé), il ne contient pas de texte : transmettez-le pour une saisie manuelle.")
                return redirect(request.path)
            erreurs = ta.anomalies(lignes)
            if request.POST.get("action") == "remplacer_debut" and pret.lignes.exists():
                # avenant : on garde les échéances passées avant le nouveau tableau
                anciennes = [l for l in pret.lignes_banque() if l["date"] < lignes[0]["date"]]
                lignes = [{"rang": l["numero"], "date": l["date"], "capital": l["capital"], "interets": l["interets"],
                           "assurance": l["assurance"], "restant": l["restant"]} for l in anciennes] + lignes
                for i, l in enumerate(lignes, 1):
                    l["rang"] = i
            n = ta.enregistrer(pret, lignes)
            Pret.objects.filter(pk=pk).update(capital_constate=None, capital_constate_le=None)
            message = (f"{n} échéances importées, du {lignes[0]['date']:%d/%m/%Y} au {lignes[-1]['date']:%d/%m/%Y}. "
                       f"Capital restant dû aujourd'hui : {pret.capital_restant_du(timezone.localdate())} €.")
            if erreurs:
                messages.warning(request, message + " À vérifier : " + " ; ".join(erreurs[:3]))
            else:
                messages.success(request, message)
            return redirect(retour)
        return render(request, "admin/patrimoine/pret/tableau.html", {
            **self.admin_site.each_context(request), "pret": pret, "retour": retour, "nb_lignes": pret.lignes.count(),
            "title": f"Tableau d'amortissement de la banque – {pret}", "opts": self.model._meta})


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


def _regime(request):
    """Régime de comptabilité choisi dans le menu (?compta=…), aussi retrouvé depuis une fiche ouverte depuis la liste."""
    from django.http import QueryDict

    valeur = request.GET.get("compta") or QueryDict(request.GET.get("_changelist_filters", "")).get("compta", "")
    return valeur if valeur in compta.REGIMES else ""


def _biens_comptables(regime=""):
    biens = Bien.objects.exclude(comptabilite="").select_related("proprietaire").order_by("proprietaire__nom", "nom")
    return biens.filter(comptabilite=regime) if regime else biens


class ComptabiliteFilter(admin.SimpleListFilter):
    """Entrée du menu Comptabilité (revenus fonciers, LMNP, société) : choisie par le menu, pas affichée dans la liste."""
    title = "comptabilité"
    parameter_name = "compta"

    def lookups(self, request, model_admin):
        return [(r, compta.libelle(r)) for r in compta.REGIMES]

    def queryset(self, request, queryset):
        return queryset.filter(bien__comptabilite=self.value()) if self.value() else queryset


class BienPieceFilter(admin.SimpleListFilter):
    """Biens de l'entrée de comptabilité choisie ; limités au propriétaire choisi s'il y en a un, sans les biens vendus
    avant l'année regardée. Affiché sous le filtre propriétaire."""
    title = "bien"
    parameter_name = "bien"
    a_droite = True

    def lookups(self, request, model_admin):
        biens = _biens_comptables(_regime(request))
        # un bien vendu disparaît de la liste à partir de l'année qui suit sa vente (année filtrée, sinon année en cours)
        annee = request.GET.get("annee", "")
        annee = int(annee) if annee.isdigit() else timezone.localdate().year
        biens = biens.filter(Q(vendu=False) | Q(date_vente__year__gte=annee))
        if request.GET.get("bien__proprietaire__id__exact"):
            biens = biens.filter(proprietaire_id=request.GET["bien__proprietaire__id__exact"])
        return [(str(b.pk), b.nom) for b in biens]

    def queryset(self, request, queryset):
        return queryset.filter(bien_id=self.value()) if self.value() else queryset


class RubriqueFilter(admin.SimpleListFilter):
    title = "rubrique"
    parameter_name = "rubrique"

    def lookups(self, request, model_admin):
        return list(PieceComptable.LIBELLES_RUBRIQUES.items())

    def queryset(self, request, queryset):
        return queryset.filter(rubrique=self.value()) if self.value() else queryset


class ACodifierFilter(admin.SimpleListFilter):
    title = "codification"
    parameter_name = "a_codifier"
    MANQUE = compta.MANQUE  # ligne 2072 pour les revenus fonciers, rubrique pour le LMNP et la société

    def lookups(self, request, model_admin):
        nombre = compta.a_codifier(_regime(request) or None).count()
        return [("oui", f"À codifier ({nombre})" if nombre else "À codifier"), ("non", "Codifiées")]

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

    def __init__(self, *args, regime="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bien"].queryset = _biens_comptables(regime)
        self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}"


class PieceComptableForm(forms.ModelForm):
    class Meta:
        model = PieceComptable
        fields = "__all__"
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "date_paiement": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "mois_concerne": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "bien" in self.fields:
            self.fields["bien"].queryset = _biens_comptables()
            self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}"
        # tableau de codification : chaque ligne ne propose que la codification de son bien (ligne 2072 ou rubrique)
        if self.instance.pk and self.instance.bien_id and "ligne" in self.fields and "rubrique" in self.fields:
            inutile = "ligne" if self.instance.par_rubrique else "rubrique"
            self.fields[inutile].widget = forms.HiddenInput()

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
    """Factures des trois comptabilités : la liste s'ouvre depuis le menu sur une entrée (?compta=foncier, lmnp ou societe)
    et ne montre que sa codification (ligne 2072 ou rubrique)."""
    form = PieceComptableForm
    list_display_links = ["date_affichee", "libelle"]
    filtres_en_liste = ("bien__proprietaire",)  # propriétaire en liste déroulante, comme le bien
    filtres_caches = ("compta",)  # choisie par le menu
    search_fields = ["fournisseur", "libelle", "bien__nom", "notes"]
    fieldsets = [
        (None, {"classes": ["identite-lot"], "fields": ["bien", ("date", "date_paiement"), ("fournisseur", "montant"), "libelle", "ligne",
                                                        ("rubrique", "mois_concerne"), "fichier", "notes"],
                "description": "Revenus fonciers : la facture alimente la ligne choisie de l'annexe 2072 (ou du récapitulatif 2044), "
                               "pour l'année de son paiement ; loyers, forfait de 20 € par local et intérêts d'emprunt sont calculés "
                               "automatiquement. LMNP et société : la facture alimente sa rubrique dans le récapitulatif."}),
    ]

    def _codifier(self, request):
        return request.GET.get("a_codifier") == "oui"

    def _champs_codification(self, request):
        regime = _regime(request)
        return {"foncier": ["ligne"], "lmnp": ["rubrique"], "societe": ["rubrique"]}.get(regime, ["ligne", "rubrique"])

    def _liste_codifier(self, request):
        return ["piece", "etat_lecture", "bien", "date", "date_paiement", "fournisseur", "libelle", "montant"] + self._champs_codification(request)

    def get_list_filter(self, request):
        regime = _regime(request)
        codif = {"foncier": ["ligne"], "lmnp": [RubriqueFilter], "societe": [RubriqueFilter]}.get(regime, ["ligne", RubriqueFilter])
        return [ComptabiliteFilter, ACodifierFilter, AnneeFiscaleFilter, "bien__proprietaire", BienPieceFilter] + codif

    def get_list_display(self, request):
        if self._codifier(request):
            return self._liste_codifier(request)
        codif = {"foncier": "ligne_courte", "lmnp": "rubrique_courte", "societe": "rubrique_courte"}.get(_regime(request), "codification")
        return ["date_affichee", "bien", "fournisseur", "libelle", codif, "montant_affiche", "piece"]

    def get_list_display_links(self, request, list_display):
        return None if self._codifier(request) else self.list_display_links

    def get_changelist_instance(self, request):
        self.list_editable = self._liste_codifier(request)[2:] if self._codifier(request) else ()
        return super().get_changelist_instance(request)

    def get_changelist_formset(self, request, **kwargs):
        # tableau de codification : listes simples (sans les icônes d'ajout / modification du bien)
        self.list_editable = self._liste_codifier(request)[2:] if self._codifier(request) else ()
        return forms.modelformset_factory(self.model, form=PieceComptableForm, fields=self.list_editable, extra=0)

    def get_fieldsets(self, request, obj=None):
        # la fiche ne montre que la codification du bien (les deux pour une nouvelle pièce, le choix du bien les départage)
        regime = (obj.bien.comptabilite if obj and obj.bien_id else "") or _regime(request)
        retirer = {"foncier": {("rubrique", "mois_concerne")}, "lmnp": {"ligne"}, "societe": {"ligne"}}.get(regime, set())
        return [(t, {**o, "fields": [c for c in o["fields"] if c not in retirer]}) for t, o in self.fieldsets]

    def get_urls(self):
        from django.urls import path

        return [path("import/", self.admin_site.admin_view(self.import_view), name="patrimoine_piececomptable_import")] + super().get_urls()

    def import_view(self, request):
        from pathlib import Path

        from django.core.exceptions import ValidationError

        from .. import conciergerie

        regime = _regime(request) or request.POST.get("compta", "")
        form = ImportFacturesForm(request.POST or None, request.FILES or None, regime=regime)
        if request.method == "POST" and form.is_valid() and not request.FILES.getlist("fichiers"):
            form.add_error("fichiers", "Choisissez au moins une facture.")
        if request.method == "POST" and form.is_valid():
            from ..lecture_factures import analyser, texte_pdf

            bien = form.cleaned_data["bien"]
            crees, lues, refuses, doublons, vus, controles = 0, 0, [], [], set(), []
            for f in request.FILES.getlist("fichiers"):
                empreinte = PieceComptable.calculer_empreinte(f)
                deja = PieceComptable.objects.filter(empreinte=empreinte).select_related("bien").first()
                if deja or empreinte in vus:
                    quand = f" (déjà importée pour {deja.bien.nom}{f', facture du {deja.date:%d/%m/%Y}' if deja.date else ''})" if deja else " (en double dans cet envoi)"
                    doublons.append(f.name + quand)
                    continue
                vus.add(empreinte)
                texte = texte_pdf(f) if f.name.lower().endswith(".pdf") else ""
                # facture de conciergerie d'un logement en courte durée : contrôlée et répartie en rubriques
                lot_courte = next((l for l in bien.lots.all() if l.exploitation == "courte"), None) if bien.comptabilite in compta.PAR_RUBRIQUE else None
                if lot_courte and conciergerie.est_une_facture(texte):
                    principale = conciergerie.enregistrer(lot_courte, f, texte)
                    controles.append(principale)
                    crees += 1
                    continue
                piece = PieceComptable(empreinte=empreinte, bien=bien, libelle=Path(f.name).stem.replace("_", " ")[:200], fichier=f)
                if texte:
                    propositions = analyser(texte, par_rubrique=bien.comptabilite in compta.PAR_RUBRIQUE)
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
            codif = "rubrique" if bien.comptabilite in compta.PAR_RUBRIQUE else "ligne 2072"
            if crees - len(controles):
                lu = (f" Pour {lues} d'entre elles, les champs ont été lus dans le PDF : vérifiez-les." if lues else "")
                n = crees - len(controles)
                messages.success(request, f"{n} facture{'s' if n > 1 else ''} importée{'s' if n > 1 else ''} : "
                                          f"complétez date, montant et {codif} dans le tableau, puis Enregistrer." + lu)
            if refuses:
                messages.error(request, "Format non accepté (PDF, JPG, PNG ou HEIC) : " + ", ".join(refuses))
            if doublons:
                messages.warning(request, f"{len(doublons)} facture{'s' if len(doublons) > 1 else ''} déjà présente{'s' if len(doublons) > 1 else ''}, "
                                          "non importée" + ("s" if len(doublons) > 1 else "") + " : " + " ; ".join(doublons))
            if len(controles) == 1 and crees == 1:
                return redirect("controle_conciergerie", pk=controles[0].pk)
            for p in controles:
                messages.info(request, f"Facture de conciergerie contrôlée et répartie en rubriques : {p.libelle}.")
            suite = f"?compta={bien.comptabilite}" + ("&a_codifier=oui" if crees - len(controles) else "")
            return redirect(reverse("admin:patrimoine_piececomptable_changelist") + suite)
        return render(request, "admin/patrimoine/piececomptable/import.html",
                      {**self.admin_site.each_context(request), "form": form, "opts": self.model._meta, "regime": regime,
                       "libelle_regime": compta.libelle(regime) if regime else "", "title": "Ajouter des factures",
                       "par_rubrique": regime in compta.PAR_RUBRIQUE,
                       "lien_factures": reverse("admin:patrimoine_piececomptable_changelist") + (f"?compta={regime}" if regime else "")})

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

    @admin.display(description="rubrique", ordering="rubrique")
    def rubrique_courte(self, obj):
        if not obj.rubrique:
            return "–"
        return format_html('<span class="pill gris">{}</span>', obj.get_rubrique_display())

    @admin.display(description="codification")
    def codification(self, obj):
        return self.rubrique_courte(obj) if obj.par_rubrique else self.ligne_courte(obj)

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

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("bien")

    def changelist_view(self, request, extra_context=None):
        regime = _regime(request)
        lien_import = reverse("admin:patrimoine_piececomptable_import") + (f"?compta={regime}" if regime else "")
        extra_context = {**(extra_context or {}), "lien_2072": reverse("annexe_2072"), "lien_import": lien_import,
                         "regime": regime, "libelle_regime": compta.libelle(regime) if regime else "",
                         "nb_a_codifier": compta.a_codifier(regime or None).count()}
        if regime:
            from ..views.compta import onglets

            extra_context["title"] = f"Factures · {compta.libelle(regime)}"
            extra_context["onglets"] = onglets(regime, "codifier" if self._codifier(request) else "factures")
            extra_context["lien_regime"] = reverse(f"compta_{regime}") if regime in compta.PAR_RUBRIQUE else ""
        return super().changelist_view(request, extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        biens = {str(b.pk): b.comptabilite for b in Bien.objects.only("pk", "comptabilite")}
        regime = _regime(request)
        if object_id and not regime:
            regime = PieceComptable.objects.filter(pk=object_id).values_list("bien__comptabilite", flat=True).first() or ""
        extra_context = {**(extra_context or {}), "regimes_des_biens": biens, "regime": regime,
                         "libelle_regime": compta.libelle(regime) if regime else "",
                         "lien_factures": reverse("admin:patrimoine_piececomptable_changelist") + (f"?compta={regime}" if regime else "")}
        return super().changeform_view(request, object_id, form_url, extra_context)
