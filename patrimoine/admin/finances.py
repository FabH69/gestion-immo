"""Écrans des prêts et des factures (comptabilité)."""
from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .. import amortissements, compta
from ..models import AnneeCloturee, AttestationPret, DeclarationFaite, DepenseRecurrente, Immobilisation, PieceComptable, Bien, Pret, PretLot, Proprietaire
from .communs import euros_liste


class AttestationsFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        for f in self.deleted_forms:
            d = f.instance.declaration_qui_verrouille() if f.instance.pk else None
            if d:
                raise forms.ValidationError(f"{d.libelle_court} {d.annee} déclarée le {d.date_declaration:%d/%m/%Y} : l'attestation "
                                            f"{f.instance.annee} ne peut pas être supprimée. Décochez d'abord « Déclaration effectuée ».")


class AttestationPretInline(admin.TabularInline):
    model = AttestationPret
    formset = AttestationsFormSet
    extra = 0
    fields = ["annee", "interets", "assurance"]
    verbose_name = "attestation annuelle de la banque"
    verbose_name_plural = ("Attestations annuelles de la banque (facultatif : les montants saisis pour une année remplacent "
                           "l'échéancier dans la 2044 et l'annexe 2072)")


class LotsFinancesFormSet(forms.BaseInlineFormSet):
    """Lots d'un même bien, celui du prêt ; % tous vides (prorata de la surface) ou tous remplis avec un total de 100."""

    def clean(self):
        super().clean()
        lignes = [f.cleaned_data for f in self.forms
                  if f.cleaned_data and not f.cleaned_data.get("DELETE") and f.cleaned_data.get("lot")]
        if not lignes:
            return
        bien = self.instance.bien
        if bien is None:
            raise forms.ValidationError("Choisissez d'abord le bien financé : les lots financés sont des lots de ce bien.")
        autres = [l["lot"].designation for l in lignes if l["lot"].bien_id != bien.pk]
        if autres:
            raise forms.ValidationError(f"{', '.join(autres)} : pas un lot de {bien.nom}, le bien du prêt.")
        parts = [l.get("part") for l in lignes]
        if any(p is not None for p in parts):
            if any(p is None for p in parts):
                raise forms.ValidationError("Remplissez la part de chaque lot, ou laissez-les toutes vides pour répartir au "
                                            "prorata de la surface.")
            if sum(parts) != 100:
                raise forms.ValidationError(f"Le total des parts fait {sum(parts):g} % au lieu de 100 %.".replace(".", ","))


class PretLotInline(admin.TabularInline):
    model = PretLot
    formset = LotsFinancesFormSet
    extra = 0
    fields = ["lot", "part"]
    verbose_name = "lot financé"
    verbose_name_plural = ("Lots financés (facultatif) : aucun lot = le prêt finance tout le bien, réparti entre ses lots au "
                           "prorata de la surface. Cochez les lots si le prêt ne finance qu'une partie du bien (ex. travaux "
                           "d'un appartement) ; parts vides = prorata de la surface de ces lots.")

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        champ = formset.form.base_fields.get("lot")
        if champ is not None:
            from ..models import Lot

            champ.queryset = Lot.objects.filter(bien=obj.bien) if obj and obj.bien_id else Lot.objects.select_related("bien")
        return formset


class PretACompleterFilter(admin.SimpleListFilter):
    title = "à compléter"
    parameter_name = "a_completer"

    def lookups(self, request, model_admin):
        return [("oui", "Objet financé à compléter")]

    def queryset(self, request, queryset):
        return queryset.filter(objet="") if self.value() == "oui" else queryset


@admin.register(Pret)
class PretAdmin(admin.ModelAdmin):
    inlines = [PretLotInline, AttestationPretInline]
    list_display = ["fiche_pret", "proprietaire", "bien", "objet_finance", "taux_affiche", "mensualite", "fin", "restant_du", "amortissement"]
    list_display_links = ["fiche_pret"]
    list_filter = ["proprietaire", PretACompleterFilter]
    readonly_fields = ["calculs"]
    fieldsets = [
        (None, {"fields": ["proprietaire", "bien", "banque", ("reference", "type"), "objet"]}),
        ("Conditions", {"fields": [("montant", "taux", "duree_mois"), "date_premiere_echeance", "assurance_mensuelle", "taux_assurance"]}),
        ("Recalage sur la banque", {"fields": [("capital_constate", "capital_constate_le")],
                                     "description": "Facultatif : capital restant dû indiqué par la banque à une date, si le calcul s'en écarte."}),
        ("Calculs", {"fields": ["calculs"]}),
        ("Notes", {"fields": ["notes"]}),
    ]
    change_list_template = "admin/patrimoine/pret/change_list.html"

    @admin.display(description="objet financé", ordering="objet")
    def objet_finance(self, obj):
        if obj.a_completer:
            return format_html('<span class="pill orange">À compléter</span>')
        return format_html("{}<br><small>{}</small>", obj.get_objet_display(), obj.lots_finances_libelle)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("proprietaire", "bien").prefetch_related("lots_finances__lot", "bien__lots")

    def changelist_view(self, request, extra_context=None):
        nb = Pret.objects.filter(objet="").count()
        if nb and request.GET.get("a_completer") != "oui":
            messages.warning(request, format_html(
                '{} prêt{} à compléter : indiquez l\'objet financé (achat, travaux…) et, si le prêt ne finance qu\'une partie '
                'du bien, les lots financés. <a href="?a_completer=oui">Voir les prêts à compléter</a>',
                nb, "s" if nb > 1 else ""))
        return super().changelist_view(request, extra_context)

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
            "Capital restant dû aujourd'hui : <strong>{} €</strong><br>Assurance appliquée : {}<br>"
            '<a href="{}" target="_blank">Tableau d\'amortissement PDF</a> · <a href="{}">Importer le tableau de la banque (PDF)</a>',
            source, e(obj.mensualite_totale), e(obj.mensualite_hors_assurance), obj.date_derniere_echeance.strftime("%d/%m/%Y"),
            e(obj.capital_restant_du(timezone.localdate())), obj.assurance_appliquee, reverse("document_amortissement", args=[obj.pk]),
            reverse("admin:patrimoine_pret_tableau", args=[obj.pk]),
        )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        champ = form.base_fields.get("taux_assurance")
        if champ:
            champ.help_text = ("Seulement si la banque calcule l'assurance sur le capital restant dû et l'inclut dans une échéance "
                               "constante (ex. Caisse d'Épargne) : l'assurance mensuelle ci-dessus est alors remise à 0. "
                               "Vider ce champ recalcule le prêt sans assurance.")
        return form

    def save_model(self, request, obj, form, change):
        """Une seule assurance à la fois : en % du capital restant dû, l'assurance mensuelle fixe est remise à 0 (elle était
        ignorée et revenait dès que le % était effacé) ; vider le % recalcule le prêt sans assurance."""
        modifies = set(form.changed_data)
        if obj.taux_assurance and obj.assurance_mensuelle:
            obj.assurance_mensuelle = 0
        elif change and "taux_assurance" in modifies and not obj.taux_assurance and "assurance_mensuelle" not in modifies:
            obj.assurance_mensuelle = 0
        super().save_model(request, obj, form, change)
        if change and modifies & {"taux_assurance", "assurance_mensuelle", "montant", "taux", "duree_mois", "date_premiere_echeance"} \
                and obj.lignes.exists():
            messages.warning(request, format_html(
                "Le tableau de la banque fait foi : l'échéancier et l'assurance ne changent pas avec les conditions. "
                '<a href="{}">Retirer le tableau de la banque</a> pour recalculer à partir des conditions.',
                reverse("admin:patrimoine_pret_tableau", args=[obj.pk])))

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
        from .communs import _page_d_origine

        # ouvert depuis une déclaration (?next=) : retour à la déclaration après l'import ou « Annuler »
        retour = _page_d_origine(request) or reverse("admin:patrimoine_pret_change", args=[pk])
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
                return redirect(request.get_full_path())
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
            **self.admin_site.each_context(request), "pret": pret, "retour": retour, "fiche_pret": reverse("admin:patrimoine_pret_change", args=[pk]), "nb_lignes": pret.lignes.count(),
            "title": f"Tableau d'amortissement de la banque – {pret}", "opts": self.model._meta})


def filtre_verrouillees(qs, fermees=None):
    """Pièces du queryset comptées dans une année clôturée ou dans un cerfa déclaré."""
    fermees = AnneeCloturee.annees() if fermees is None else fermees
    q = Q(date_paiement__year__in=fermees) | Q(date_paiement__isnull=True, date__year__in=fermees)
    for annee, biens in DeclarationFaite.verrous().items():
        q |= Q(bien_id__in=list(biens)) & (Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee))
    return qs.filter(q)


def lien_annee(piece):
    """Liste des factures de l'année (de paiement) où se trouve la pièce, dans son entrée de comptabilité."""
    regime = piece.bien.comptabilite if piece.bien_id else ""
    return reverse("admin:patrimoine_piececomptable_changelist") + "?" + "&".join(
        x for x in (f"compta={regime}" if regime else "", f"annee={piece.annee}" if piece.annee else "") if x)


def message_deja_enregistree(deja, nom=""):
    """« Cette facture est enregistrée en 2024 … » avec l'année où la retrouver et un lien direct (doublon à l'import)."""
    details = [f"pour {deja.bien.nom}" if deja.bien_id else ""]
    if deja.date:
        details.append(f"facture du {deja.date:%d/%m/%Y}")
    if deja.date_paiement:
        details.append(f"payée le {deja.date_paiement:%d/%m/%Y}")
    annee = f"en {deja.annee}" if deja.annee else "(année pas encore renseignée)"
    fiche = reverse("admin:patrimoine_piececomptable_change", args=[deja.pk])
    return format_html('{}Cette facture est enregistrée {} ({}) : <a href="{}">voir la facture</a>{}', f"« {nom} » : " if nom else "",
                       annee, ", ".join(d for d in details if d), fiche,
                       format_html(' · <a href="{}">voir les factures de {}</a>', lien_annee(deja), deja.annee) if deja.annee else "")


class AnneeFiscaleFilter(admin.SimpleListFilter):
    """Année civile du paiement : année en cours et précédente en boutons, années antérieures en liste déroulante."""
    title = "année"
    parameter_name = "annee"
    affichage_annees = True

    def lookups(self, request, model_admin):
        t = timezone.localdate().year
        # années antérieures : toujours les 10 dernières, plus les plus anciennes qui ont des factures
        avec_factures = {p.annee for p in PieceComptable.objects.only("date", "date_paiement")} - {None}
        anterieures = sorted(set(range(t - 10, t - 1)) | {a for a in avec_factures if a < t - 1}, reverse=True)
        return [(str(t), f"{t} (en cours)"), (str(t - 1), str(t - 1))] + [(str(a), str(a)) for a in anterieures]

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


def _dossier(request):
    """Dossier comptable choisi (?dossier=…, depuis l'accueil Comptabilité), aussi retrouvé depuis une fiche ouverte de sa liste."""
    from django.http import QueryDict

    from .. import dossiers

    code = request.GET.get("dossier") or request.POST.get("dossier") or QueryDict(request.GET.get("_changelist_filters", "")).get("dossier", "")
    return dossiers.trouver(code) if code else None


def _saisie_retenue(request):
    """Dernier propriétaire (et bien) choisis dans la liste des factures, gardés en session pour l'ajout de factures.
    ?proprietaire=tous sur la page d'ajout les ignore une fois (tous les biens)."""
    if request.GET.get("proprietaire") == "tous":
        return None, None
    return Proprietaire.objects.filter(pk=request.session.get("saisie_proprietaire")).first(), request.session.get("saisie_bien")


def _maisons():
    """Résidences principales hors comptabilité locative : leurs factures vont dans « Ma maison »."""
    return Bien.objects.filter(residence_principale=True, comptabilite="").select_related("proprietaire")


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


class DossierFilter(admin.SimpleListFilter):
    """Dossier comptable (2044, une SCI, LMNP, une société) : choisi depuis l'accueil Comptabilité, pas affiché dans la liste."""
    title = "dossier"
    parameter_name = "dossier"

    def lookups(self, request, model_admin):
        from .. import dossiers

        return [(d["code"], d["titre"]) for d in dossiers.tous()]

    def queryset(self, request, queryset):
        from .. import dossiers

        d = dossiers.trouver(self.value()) if self.value() else None
        return queryset.filter(bien__in=dossiers.biens(d)) if d else queryset


class BienPieceFilter(admin.SimpleListFilter):
    """Biens de l'entrée de comptabilité choisie ; limités au propriétaire choisi s'il y en a un, sans les biens vendus
    avant l'année regardée. Affiché sous le filtre propriétaire."""
    title = "bien"
    parameter_name = "bien"
    a_droite = True

    def lookups(self, request, model_admin):
        biens = _biens_comptables(_regime(request))
        dossier = _dossier(request)
        if dossier:
            from .. import dossiers

            biens = dossiers.biens(dossier)
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
        bien = request.GET.get("bien", "")
        if bien.isdigit() and _maisons().filter(pk=bien).exists():  # factures de la maison : ses catégories
            return list(PieceComptable.RUBRIQUES_MAISON)
        return list(PieceComptable.LIBELLES_RUBRIQUES.items())

    def queryset(self, request, queryset):
        return queryset.filter(rubrique=self.value()) if self.value() else queryset


class ACodifierFilter(admin.SimpleListFilter):
    title = "codification"
    parameter_name = "a_codifier"
    MANQUE = compta.MANQUE  # ligne 2072 pour les revenus fonciers, rubrique pour le LMNP et la société

    def lookups(self, request, model_admin):
        nombre = compta.a_codifier(_regime(request) or None).count()
        choix = [("oui", f"À classer ({nombre})" if nombre else "À classer"), ("non", "Classées")]
        recurrentes = DepenseRecurrente.objects.filter(active=True).count()
        if recurrentes:
            choix.append(("recurrentes", f"Récurrentes ({recurrentes})"))
        return choix

    def queryset(self, request, queryset):
        if self.value() == "oui":
            return queryset.filter(self.MANQUE)
        if self.value() == "non":
            return queryset.exclude(self.MANQUE)
        if self.value() == "recurrentes":
            return queryset.filter(recurrence__isnull=False)
        return queryset


class EntreeFichiersMultiples(forms.ClearableFileInput):
    allow_multiple_selected = True


class ChampFichiersMultiples(forms.FileField):
    widget = EntreeFichiersMultiples

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(ChampFichiersMultiples, self).clean(d, initial) for d in data]
        return super().clean(data, initial)


def _facture_electronique_existante(donnees):
    """Pièce déjà enregistrée avec le même numéro chez le même fournisseur (même facture reçue en PDF et en XML, par ex.)."""
    if not donnees["numero"]:
        return None
    pieces = PieceComptable.objects.filter(numero_facture=donnees["numero"]).select_related("bien")
    if donnees["siren"]:
        return pieces.filter(siren_fournisseur=donnees["siren"]).first()
    return pieces.filter(fournisseur__iexact=donnees["fournisseur"]).first() if donnees["fournisseur"] else None


def _piece_electronique(bien, fichier, empreinte, fmt, donnees, propositions):
    """Pièce « à codifier » créée depuis une facture électronique : les données sûres viennent du XML, la ligne 2072 ou
    la rubrique est seulement proposée (mots-clés), d'où preremplie=True : elle reste à vérifier dans « À codifier »."""
    from pathlib import Path

    objet = donnees["objet"] or Path(fichier.name).stem.replace("_", " ")
    if donnees["avoir"]:
        objet = f"Avoir – {objet}"
    notes = []
    if donnees["echeance"]:
        notes.append(f"Échéance de paiement : {donnees['echeance']:%d/%m/%Y}")
    if donnees["devise"] and donnees["devise"] != "EUR":
        notes.append(f"Devise de la facture : {donnees['devise']} (montants non convertis)")
    piece = PieceComptable(
        empreinte=empreinte, bien=bien, fichier=fichier, format_electronique=fmt, preremplie=True,
        libelle=objet[:200], fournisseur=donnees["fournisseur"], numero_facture=donnees["numero"],
        siren_fournisseur=donnees["siren"], date=donnees["date"], montant=donnees["montant_ttc"],
        montant_ht=donnees["montant_ht"], montant_tva=donnees["montant_tva"], notes="\n".join(notes))
    for champ in ("ligne", "rubrique"):
        if propositions.get(champ):
            setattr(piece, champ, propositions[champ])
    return piece


class ImportFacturesForm(forms.Form):
    bien = forms.ModelChoiceField(Bien.objects.none(), label="bien concerné",
                                  help_text="Le bien pourra être changé ensuite pièce par pièce.")
    fichiers = ChampFichiersMultiples(label="factures (PDF, photos ou factures électroniques)", required=False,
                               help_text="Factures électroniques acceptées dans les trois formats : Factur-X (PDF), UBL et CII (XML). "
                                         "Leurs données sont lues directement, sans ressaisie.",
                               widget=EntreeFichiersMultiples(attrs={"accept": ".pdf,.jpg,.jpeg,.png,.heic,.xml"}))

    def __init__(self, *args, regime="", proprietaire=None, bien_choisi=None, biens=None, **kwargs):
        super().__init__(*args, **kwargs)
        biens = _biens_comptables(regime) if biens is None else biens
        # dernier propriétaire choisi dans la liste des factures : seulement ses biens (tous s'il n'en a aucun ici)
        if proprietaire and biens.filter(proprietaire=proprietaire).exists():
            biens = biens.filter(proprietaire=proprietaire)
        self.fields["bien"].queryset = biens
        self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}"
        ids = list(biens.values_list("pk", flat=True)[:2])
        if bien_choisi in set(biens.values_list("pk", flat=True)):
            self.fields["bien"].initial = bien_choisi
        elif len(ids) == 1:
            self.fields["bien"].initial = ids[0]


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
            self.fields["bien"].queryset = (_biens_comptables() | _maisons()).distinct()
            self.fields["bien"].label_from_instance = lambda b: f"{b.proprietaire.nom} – {b.nom}" + (" (ma maison)" if b.est_maison else "")
        if "rubrique" in self.fields:
            bien = self.instance.bien if self.instance.bien_id else None
            choisi = self.data.get(self.add_prefix("bien")) if self.is_bound else self.initial.get("bien")
            if choisi and str(getattr(choisi, "pk", choisi)).isdigit():
                bien = Bien.objects.filter(pk=getattr(choisi, "pk", choisi)).first() or bien
            if bien is not None and bien.est_maison:
                self.fields["rubrique"].choices = [("", "---------")] + PieceComptable.RUBRIQUES_MAISON
                self.fields["rubrique"].label = "catégorie"
            elif bien is not None:
                self.fields["rubrique"].choices = [("", "---------")] + [(g, list(r)) for g, r in PieceComptable.RUBRIQUES]
        immo = self.instance.immobilisations.first() if self.instance.pk and self.instance.rubrique == "immobilisation" else None
        if immo and "immo_categorie" in self.fields:
            self.initial.setdefault("immo_categorie", immo.categorie)
            self.initial.setdefault("immo_duree", immo.duree)
            self.initial.setdefault("immo_mise_en_service", immo.mise_en_service)
        elif "immo_duree" in self.fields:
            self.initial.setdefault("immo_categorie", "mobilier")
            self.initial.setdefault("immo_duree", amortissements.duree_proposee("mobilier"))
        # tableau de codification : chaque ligne ne propose que la codification de son bien (ligne 2072 ou rubrique)
        if self.instance.pk and self.instance.bien_id and "ligne" in self.fields and "rubrique" in self.fields:
            inutile = "ligne" if self.instance.par_rubrique else "rubrique"
            self.fields[inutile].widget = forms.HiddenInput()
        # bien en location nue (nom propre) : les lignes portent les numéros de la déclaration 2044
        if self.instance.bien_id and "ligne" in self.fields and self.instance.bien.regime_fiscal == "nu":
            from .. import compta

            self.fields["ligne"].choices = [("", "---------")] + compta.choix_lignes_2044()
            self.fields["ligne"].label = "ligne de la déclaration 2044"

    # « Immobilisation (à amortir) » : de quoi créer la fiche immobilisation de la facture
    immo_categorie = forms.ChoiceField(label="catégorie de l'immobilisation", required=False,
                                       choices=[(c, l) for c, l in Immobilisation.Categorie.choices if c != "terrain"])
    immo_duree = forms.IntegerField(label="durée (ans)", required=False, min_value=1, max_value=100)
    immo_mise_en_service = forms.DateField(label="mise en service le", required=False,
                                           widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                                           help_text="Vide : la date de la facture")

    OBLIGATOIRES = {"bien": "le bien", "date": "la date de la facture", "montant": "le montant"}

    def clean(self):
        """Montant, date et bien obligatoires dès qu'on enregistre une saisie (une ligne non modifiée du tableau
        « À codifier » reste en attente sans bloquer les autres)."""
        donnees = super().clean()
        if self.instance.pk and not self.has_changed():
            return donnees
        for champ, nom in self.OBLIGATOIRES.items():
            if champ in self.fields and not isinstance(self.fields[champ].widget, forms.HiddenInput) \
                    and donnees.get(champ) in (None, "") and champ not in self.errors:
                self.add_error(champ, f"Obligatoire : saisissez {nom}.")
        return donnees

    def clean_fichier(self):
        f = self.cleaned_data.get("fichier")
        if f and hasattr(f, "content_type"):  # nouveau fichier envoyé
            empreinte = PieceComptable.calculer_empreinte(f)
            deja = PieceComptable.objects.filter(empreinte=empreinte).exclude(pk=self.instance.pk).first()
            if deja:
                raise forms.ValidationError(message_deja_enregistree(deja))
            self.instance.empreinte = empreinte
        return f


@admin.register(DepenseRecurrente)
class DepenseRecurrenteAdmin(admin.ModelAdmin):
    """Dépenses qui reviennent : chaque échéance passée devient une facture déjà classée dans la saisie."""
    list_display = ["fournisseur", "libelle", "bien", "montant_affiche", "frequence", "prochaine_echeance", "nb_factures", "active"]
    list_filter = ["active", "frequence", "bien__proprietaire", "bien"]
    search_fields = ["fournisseur", "libelle", "bien__nom"]
    fieldsets = [(None, {"fields": ["bien", ("fournisseur", "montant"), "libelle", ("ligne", "rubrique"), "frequence",
                                    ("premiere_echeance", "fin"), "active"],
                         "description": "La facture de chaque échéance passée est créée toute seule dans la saisie, déjà classée "
                                        "(ligne 2072 pour les revenus fonciers, rubrique en LMNP ou en société). Rien n'est ajouté "
                                        "à une année déclarée. Une facture supprimée à la main n'est pas recréée."})]

    @admin.display(description="montant", ordering="montant")
    def montant_affiche(self, obj):
        return euros_liste(obj.montant)

    @admin.display(description="prochaine échéance")
    def prochaine_echeance(self, obj):
        d = obj.prochaine(timezone.localdate()) if obj.active else None
        return f"{d:%d/%m/%Y}" if d else "–"

    @admin.display(description="factures créées")
    def nb_factures(self, obj):
        n = obj.pieces.count()
        if not n:
            return "0"
        url = reverse("admin:patrimoine_piececomptable_changelist") + f"?a_codifier=recurrentes&q={obj.fournisseur}"
        return format_html('<a href="{}">{}</a>', url, n)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        bien = request.GET.get("bien") or request.session.get("saisie_bien")
        if bien and str(bien).isdigit() and "bien" not in initial:
            initial["bien"] = bien
        initial.setdefault("premiere_echeance", timezone.localdate())
        return initial

    def save_model(self, request, obj, form, change):
        if change and {"premiere_echeance", "frequence"} & set(form.changed_data):
            obj.generee_jusqua = None  # recalcul des échéances ; celles déjà créées ne sont pas doublées
        super().save_model(request, obj, form, change)
        n = obj.generer()
        if n:
            messages.info(request, f"{n} facture{'s' if n > 1 else ''} créée{'s' if n > 1 else ''} pour les échéances passées.")

    def changelist_view(self, request, extra_context=None):
        DepenseRecurrente.generer_toutes()
        return super().changelist_view(request, extra_context)


@admin.register(PieceComptable)
class PieceComptableAdmin(admin.ModelAdmin):
    """Factures des trois comptabilités : la liste s'ouvre depuis le menu sur une entrée (?compta=foncier, lmnp ou societe)
    et ne montre que sa codification (ligne 2072 ou rubrique)."""
    form = PieceComptableForm
    list_display_links = ["date_affichee", "libelle"]
    filtres_en_liste = ("bien__proprietaire",)  # propriétaire en liste déroulante, comme le bien
    filtres_caches = ("compta", "dossier")  # choisis par le menu ou l'accueil Comptabilité
    search_fields = ["fournisseur", "libelle", "bien__nom", "notes", "numero_facture", "siren_fournisseur"]
    fieldsets = [
        (None, {"classes": ["identite-lot"], "fields": ["bien", ("date", "date_paiement"), ("fournisseur", "montant"), ("numero_facture", "siren_fournisseur"), ("montant_ht", "montant_tva"), "libelle", "ligne",
                                                        ("rubrique", "mois_concerne"), "charge_confirmee", "fichier", "notes"],
                "description": "Revenus fonciers : la facture alimente la ligne choisie de l'annexe 2072 (ou du récapitulatif 2044), "
                               "pour l'année de son paiement ; loyers, forfait de 20 € par local et intérêts d'emprunt sont calculés "
                               "automatiquement. LMNP et société : la facture alimente sa rubrique dans le récapitulatif."}),
        ("Immobilisation (à amortir)", {"classes": ["immo-facture"], "fields": [("immo_categorie", "immo_duree", "immo_mise_en_service")],
                                        "description": "La facture devient une fiche immobilisation : seule la dotation de chaque année "
                                                       "compte en charge (linéaire, au prorata la première année)."}),
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
        # dans un dossier, le propriétaire est déjà fixé : pas de filtre propriétaire
        proprietaire = [] if _dossier(request) else ["bien__proprietaire"]
        return [ComptabiliteFilter, DossierFilter, ACodifierFilter, AnneeFiscaleFilter] + proprietaire + [BienPieceFilter] + codif

    def get_list_display(self, request):
        if self._codifier(request):
            liste = self._liste_codifier(request)
            return liste[:1] + ["scinder"] + liste[1:]  # près du PDF : les dernières colonnes sortent de l'écran
        codif = {"foncier": "ligne_courte", "lmnp": "rubrique_courte", "societe": "rubrique_courte"}.get(_regime(request), "codification")
        # toutes les factures (hors dossier) : la déclaration où chacune compte
        declaration = [] if _dossier(request) else ["declaration"]
        return ["date_affichee", "payee_le", "bien"] + declaration + ["fournisseur", "libelle", codif, "montant_affiche", "piece", "scinder"]

    @admin.display(description="déclaration")
    def declaration(self, obj):
        from .. import dossiers

        if getattr(self, "_declarations", None) is None:
            self._declarations = dossiers.declaration_des_biens()
        d = self._declarations.get(obj.bien_id)
        if not d and obj.est_maison:
            return format_html('<a href="{}?bien={}" class="pill-decl info">Ma maison</a>', reverse("ma_maison"), obj.bien_id)
        if not d:
            return format_html('<span class="pill-decl ko" title="Régime fiscal du bien à renseigner">Hors dossier</span>')
        return format_html('<a href="{}" class="pill-decl info">{}</a>', reverse("dossier_compta", args=[d["code"]]), dossiers.libelle_court(d))

    def get_ordering(self, request):
        # rangées par date de paiement (l'année fiscale), la date de la facture si le paiement n'est pas renseigné
        from django.db.models.functions import Coalesce

        return [Coalesce("date_paiement", "date").desc(nulls_first=True), "-pk"]

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
        retirer = {"foncier": {("rubrique", "mois_concerne"), "charge_confirmee"}, "lmnp": {"ligne"}, "societe": {"ligne"}}.get(regime, set())
        fieldsets = [(t, {**o, "fields": [c for c in o["fields"] if c not in retirer]}) for t, o in self.fieldsets]
        # revenus fonciers et maison : pas d'immobilisation
        if regime == "foncier" or (obj and obj.est_maison):
            fieldsets = [(t, o) for t, o in fieldsets if t != "Immobilisation (à amortir)"]
        return fieldsets

    def get_urls(self):
        from django.urls import path

        return [path("import/", self.admin_site.admin_view(self.import_view), name="patrimoine_piececomptable_import"),
                path("<int:pk>/scinder/", self.admin_site.admin_view(self.scinder_view), name="patrimoine_piececomptable_scinder"),
                path("cloture/<int:annee>/", self.admin_site.admin_view(self.cloture_view), name="patrimoine_piececomptable_cloture"),
                ] + super().get_urls()

    def scinder_view(self, request, pk):
        """Scinde une facture en plusieurs lignes (montant, codification et bien chacune), dont la somme égale le total :
        ex. appel de fonds de copropriété (ligne 13) + régularisation de l'exercice précédent (ligne 14). La première ligne
        reste sur la pièce qui porte le PDF, les autres deviennent ses parts."""
        from decimal import Decimal, InvalidOperation

        from django.db import transaction
        from django.shortcuts import get_object_or_404

        piece = get_object_or_404(PieceComptable.objects.select_related("bien", "piece_mere"), pk=pk)
        mere = piece.piece_mere or piece
        if not self.has_change_permission(request, mere):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if mere.est_verrouillee():
            messages.error(request, f"{mere.motif_verrou()} : cette facture ne peut plus être répartie. {mere.comment_deverrouiller()}")
            return redirect(lien_annee(mere))
        existantes = mere.facture_complete
        total = sum((p.montant or Decimal(0) for p in existantes), Decimal(0))
        regime = (mere.bien.comptabilite if mere.bien_id else "") or "foncier"
        par_rubrique = regime in ("lmnp", "societe")
        champ = "rubrique" if par_rubrique else "ligne"
        biens = list(_biens_comptables(regime))
        if mere.bien_id and mere.bien not in biens:
            biens.insert(0, mere.bien)
        codes = ([(g, list(r)) for g, r in PieceComptable.RUBRIQUES] if par_rubrique
                 else [("", list(PieceComptable.Ligne.choices))])
        valides = {c for _, choix in codes for c, _ in choix}
        lignes, erreurs = [], []
        if request.method == "POST":
            for i in range(int(request.POST.get("nb", 0) or 0)):
                montant = (request.POST.get(f"montant_{i}") or "").replace(" ", "").replace("\u202f", "").replace(",", ".")
                bien_id, code = request.POST.get(f"bien_{i}", ""), request.POST.get(f"code_{i}", "")
                if not montant:
                    continue
                try:
                    montant = Decimal(montant).quantize(Decimal("0.01"))
                except InvalidOperation:
                    erreurs.append(f"Montant illisible : « {montant} ».")
                    continue
                bien = next((b for b in biens if str(b.pk) == bien_id), None)
                if not bien or code not in valides:
                    erreurs.append("Chaque ligne doit avoir un bien et une " + ("rubrique." if par_rubrique else "ligne de l'annexe 2072."))
                lignes.append({"bien": bien, "montant": montant, "code": code})
            if not lignes:
                erreurs.append("Saisissez au moins une ligne.")
            somme = sum((l["montant"] for l in lignes), Decimal(0))
            if somme != total:
                erreurs.append(f"La somme des lignes ({somme} €) doit être égale au total de la facture ({total} €). "
                               f"Écart : {somme - total} €.")
            if not erreurs:
                with transaction.atomic():
                    parts = existantes[1:]
                    commun = {c: getattr(mere, c) for c in ("date", "date_paiement", "fournisseur", "libelle", "mois_concerne")}
                    for n, l in enumerate(lignes):
                        p = mere if n == 0 else (parts[n - 1] if n - 1 < len(parts) else PieceComptable(piece_mere=mere, **commun))
                        p.bien, p.montant, p.preremplie = l["bien"], l["montant"], False
                        setattr(p, champ, l["code"])
                        p.save()
                    for p in parts[len(lignes) - 1:]:
                        p.delete()
                messages.success(request, f"Facture répartie sur {len(lignes)} ligne{'s' if len(lignes) > 1 else ''}.")
                return redirect("admin:patrimoine_piececomptable_change", mere.pk)
        else:
            lignes = [{"bien": p.bien, "montant": p.montant, "code": getattr(p, champ)} for p in existantes]
        nb_vides = max(2, 4 - len(lignes))
        lignes += [{"bien": mere.bien, "montant": None, "code": ""} for _ in range(nb_vides)]
        return render(request, "admin/patrimoine/piececomptable/scinder.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "mere": mere, "lignes": lignes, "total": total,
            "biens": biens, "codes": codes, "par_rubrique": par_rubrique, "erreurs": erreurs, "nb": len(lignes),
            "libelle_regime": compta.libelle(regime), "title": "Scinder la facture",
            "lien_factures": reverse("admin:patrimoine_piececomptable_changelist") + f"?compta={regime}"})

    def import_view(self, request):
        from pathlib import Path

        from django.core.exceptions import ValidationError

        from .. import conciergerie

        from .. import dossiers

        regime = _regime(request) or request.POST.get("compta", "")
        dossier = _dossier(request)
        proprietaire, bien_choisi = _saisie_retenue(request)
        if request.method == "POST" or dossier:
            proprietaire = None  # dossier : ses biens seulement ; envoi : le bien est contrôlé parmi tous les biens
        form = ImportFacturesForm(request.POST or None, request.FILES or None, regime=regime, proprietaire=proprietaire,
                                  bien_choisi=bien_choisi, biens=dossiers.biens(dossier, timezone.localdate().year - 1) if dossier else None)
        if request.method == "POST" and form.is_valid() and not request.FILES.getlist("fichiers"):
            form.add_error("fichiers", "Choisissez au moins une facture.")
        if request.method == "POST" and form.is_valid():
            from .. import factures_electroniques as fe
            from ..lecture_factures import analyser, texte_pdf

            bien = form.cleaned_data["bien"]
            crees, lues, refuses, doublons, vus, controles, nouvelles = 0, 0, [], [], set(), [], []
            electroniques, illisibles, numeros_vus = 0, [], set()
            for f in request.FILES.getlist("fichiers"):
                empreinte = PieceComptable.calculer_empreinte(f)
                deja = PieceComptable.objects.filter(empreinte=empreinte).select_related("bien").first()
                if deja or empreinte in vus:
                    doublons.append(message_deja_enregistree(deja, f.name) if deja else format_html("« {} » : en double dans cet envoi", f.name))
                    continue
                vus.add(empreinte)
                try:
                    electronique = fe.lire(f, f.name)
                except fe.FactureIllisible as e:
                    illisibles.append(f"{f.name} : {e}")
                    continue
                if electronique:
                    fmt, donnees = electronique
                    cle = (donnees["siren"] or donnees["fournisseur"].lower(), donnees["numero"])
                    deja = _facture_electronique_existante(donnees)
                    if deja or (donnees["numero"] and cle in numeros_vus):
                        doublons.append(f"{f.name} (facture n° {donnees['numero']} "
                                        + (f"déjà importée pour {deja.bien.nom})" if deja else "en double dans cet envoi)"))
                        continue
                    numeros_vus.add(cle)
                    par_rubrique = bien.comptabilite in compta.PAR_RUBRIQUE
                    propositions = {**analyser(fe.texte_pour_codifier(donnees), par_rubrique=par_rubrique),
                                    **compta.code_du_fournisseur(donnees["fournisseur"], par_rubrique)}  # règle par fournisseur d'abord
                    piece = _piece_electronique(bien, f, empreinte, fmt, donnees, propositions)
                    try:
                        piece.full_clean()
                    except ValidationError:
                        refuses.append(f.name)
                        continue
                    piece.save()
                    crees += 1
                    electroniques += 1
                    continue
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
                    par_rubrique = bien.comptabilite in compta.PAR_RUBRIQUE
                    propositions = analyser(texte, par_rubrique=par_rubrique)
                    # règle par fournisseur : la codification de ses factures déjà classées l'emporte sur les mots-clés
                    propositions.update(compta.code_du_fournisseur(propositions.get("fournisseur"), par_rubrique))
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
                nouvelles.append(piece.pk)
                crees += 1
            codif = "rubrique" if bien.comptabilite in compta.PAR_RUBRIQUE else "ligne 2072"
            if crees - len(controles):
                lu = (f" Pour {lues} d'entre elles, les champs ont été lus dans le PDF : vérifiez-les." if lues else "")
                if electroniques:
                    lu += (f" {electroniques} facture{'s' if electroniques > 1 else ''} électronique{'s' if electroniques > 1 else ''} "
                           "(Factur-X, UBL ou CII) : numéro, fournisseur, date et montants lus dans le fichier, "
                           f"il reste à vérifier {codif} proposée et bien.")
                n = crees - len(controles)
                messages.success(request, f"{n} facture{'s' if n > 1 else ''} importée{'s' if n > 1 else ''} : "
                                          f"complétez date, montant et {codif} dans le tableau, puis Enregistrer." + lu)
            if refuses:
                messages.error(request, "Format non accepté (PDF, JPG, PNG, HEIC ou XML) : " + ", ".join(refuses))
            if illisibles:
                messages.error(request, "Fichier XML non reconnu comme facture électronique (UBL ou CII), non importé : "
                                        + " ; ".join(illisibles))
            for d in doublons:
                messages.warning(request, format_html("Facture déjà présente, non importée. {}", d))
            fermees = AnneeCloturee.annees()
            if fermees or DeclarationFaite.objects.filter(active=True).exists():
                bloquees = filtre_verrouillees(PieceComptable.objects.filter(pk__in=nouvelles), fermees)
                for p in bloquees:
                    messages.warning(request, f"« {p.libelle} » est datée du {p.date:%d/%m/%Y}, dans une saisie verrouillée ({p.annee}, année clôturée ou déclaration effectuée) : "
                                              "indiquez sa date de paiement si elle a été payée plus tard, sinon elle ne pourra pas "
                                              f"être enregistrée tant que {p.annee} reste verrouillée.")
            anciennes = PieceComptable.objects.filter(pk__in=nouvelles, date_paiement__isnull=True,
                                                      date__year__lt=timezone.localdate().year).exclude(date__year__in=fermees)
            if anciennes.exists() and bien.comptabilite == "foncier":
                n = anciennes.count()
                messages.info(request, f"{n} facture{'s' if n > 1 else ''} datée{'s' if n > 1 else ''} d'une année précédente. En revenus "
                                       "fonciers, une dépense compte dans l'année où elle a été payée (pas l'année de la facture) : "
                                       "si elle a été payée cette année, indiquez la date de paiement dans la colonne « payée le ».")
            if len(controles) == 1 and crees == 1:
                return redirect("controle_conciergerie", pk=controles[0].pk)
            for p in controles:
                messages.info(request, f"Facture de conciergerie contrôlée et répartie en rubriques : {p.libelle}.")
            suite = (f"?dossier={dossier['code']}" if dossier else f"?compta={bien.comptabilite}") + ("&a_codifier=oui" if crees - len(controles) else "")
            return redirect(reverse("admin:patrimoine_piececomptable_changelist") + suite)
        return render(request, "admin/patrimoine/piececomptable/import.html",
                      {**self.admin_site.each_context(request), "form": form, "opts": self.model._meta, "regime": regime,
                       "libelle_regime": compta.libelle(regime) if regime else "", "title": "Ajouter des factures",
                       "par_rubrique": regime in compta.PAR_RUBRIQUE, "proprietaire_retenu": proprietaire, "dossier": dossier,
                       "lien_tous_les_biens": reverse("admin:patrimoine_piececomptable_import") + "?" + "&".join(
                           x for x in (f"compta={regime}" if regime else "", "proprietaire=tous") if x),
                       "lien_factures": reverse("admin:patrimoine_piececomptable_changelist") + (
                           f"?dossier={dossier['code']}" if dossier else f"?compta={regime}" if regime else "")})

    actions = ["valider_preremplies"]

    def response_action(self, request, queryset):
        """Tableau « À codifier » : les saisies faites dans le tableau sont enregistrées avant de lancer l'action
        (au lieu du message du navigateur « modifications non sauvegardées » qui les faisait perdre)."""
        if self._codifier(request) and request.POST.get("form-TOTAL_FORMS"):
            FormSet = self.get_changelist_formset(request)
            objets = self._get_list_editable_queryset(request, FormSet.get_default_prefix())
            formset = FormSet(request.POST, request.FILES, queryset=objets)
            enregistrees, refusees = 0, 0
            for form in formset.initial_forms:
                if not form.has_changed():
                    continue
                if form.is_valid():
                    obj = self.save_form(request, form, change=True)
                    self.save_model(request, obj, form, change=True)
                    enregistrees += 1
                else:
                    refusees += 1
            if enregistrees:
                messages.info(request, f"{enregistrees} saisie{'s' if enregistrees > 1 else ''} du tableau enregistrée{'s' if enregistrees > 1 else ''}.")
            if refusees:
                messages.warning(request, f"{refusees} ligne{'s' if refusees > 1 else ''} non enregistrée{'s' if refusees > 1 else ''} : "
                                          "montant, date et bien sont obligatoires. Complétez-les puis « Enregistrer ma saisie ».")
        return super().response_action(request, queryset)

    @admin.action(description="Valider les champs lus automatiquement (pièces sélectionnées)")
    def valider_preremplies(self, request, queryset):
        fermees = AnneeCloturee.annees()
        a_valider = queryset.filter(preremplie=True)
        if fermees or DeclarationFaite.objects.filter(active=True).exists():
            bloquees = filtre_verrouillees(a_valider, fermees)
            if bloquees.exists():
                messages.warning(request, f"{bloquees.count()} pièce(s) d'une année clôturée non validée(s) : indiquez leur date de "
                                          "paiement si elles ont été payées plus tard, ou déverrouillez l'année.")
                a_valider = a_valider.exclude(pk__in=bloquees)
        n = a_valider.update(preremplie=False)
        non_lues = queryset.filter(preremplie=False).filter(Q(montant__isnull=True) | Q(date__isnull=True)).count()
        if n:
            messages.success(request, f"{n} pièce{'s' if n > 1 else ''} validée{'s' if n > 1 else ''}.")
        if non_lues:
            messages.warning(request, f"{non_lues} pièce{'s' if non_lues > 1 else ''} n'{'ont' if non_lues > 1 else 'a'} pas pu être "
                                      f"lue{'s' if non_lues > 1 else ''} automatiquement : rien à valider. Saisissez montant et date "
                                      "à la main, puis « Enregistrer ma saisie ».")
        elif not n:
            messages.info(request, "Aucune des pièces sélectionnées n'avait de champs lus automatiquement.")

    @admin.display(description="")
    def etat_lecture(self, obj):
        if obj.format_electronique:
            return format_html('<span class="pill ok" title="Facture électronique : données lues dans le fichier ({}). '
                               'Vérifiez la codification proposée, puis Enregistrer ou action « Valider »">{}</span>',
                               obj.get_format_electronique_display(), obj.get_format_electronique_display())
        if obj.preremplie:
            return format_html('<span class="pill bleu lu-auto" title="Champs lus dans le PDF : vérifiez-les, puis Enregistrer ou action « Valider »">lu auto</span>')
        if obj.montant is None or obj.date is None:
            return format_html('<span class="pill orange a-saisir" title="La pièce n\'a pas pu être lue : saisissez montant, date et bien">À saisir à la main</span>')
        return ""

    def has_change_permission(self, request, obj=None):
        # facture comptée dans une année clôturée : la fiche s'ouvre en lecture seule
        if obj is not None and obj.est_verrouillee():
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.est_verrouillee():
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        fermees, declares = AnneeCloturee.annees(), DeclarationFaite.verrous()
        bloquees = [p.pk for p in queryset.select_related("bien") if p.est_verrouillee(fermees, declares)]
        if bloquees:
            messages.warning(request, f"{len(bloquees)} facture(s) verrouillée(s) conservée(s) (année clôturée ou déclaration "
                                      "effectuée) : déverrouillez-les pour les supprimer.")
        super().delete_queryset(request, queryset.exclude(pk__in=bloquees))

    def cloture_view(self, request, annee):
        """Interrupteur de clôture d'une année (glisser vers la droite : verrouiller ; vers la gauche : déverrouiller)."""
        from django.http import HttpResponseNotAllowed

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not super().has_change_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        retour = request.POST.get("retour") or reverse("admin:patrimoine_piececomptable_changelist") + f"?annee={annee}"
        if not retour.startswith("/"):
            retour = reverse("admin:patrimoine_piececomptable_changelist")
        if request.POST.get("verrouiller") == "1":
            AnneeCloturee.objects.get_or_create(annee=annee)
            reste = compta.a_codifier().filter(Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee)).count()
            messages.success(request, f"Année {annee} clôturée : plus aucune facture ne peut y être ajoutée, modifiée ni supprimée."
                                      + (f" Attention : {reste} facture(s) de {annee} restent à codifier." if reste else ""))
        else:
            AnneeCloturee.objects.filter(annee=annee).delete()
            messages.success(request, f"Année {annee} déverrouillée : ses factures peuvent de nouveau être modifiées.")
        return redirect(retour)

    def save_model(self, request, obj, form, change):
        obj.preremplie = False  # enregistrée par l'utilisateur (fiche ou tableau) : les champs sont considérés vérifiés
        super().save_model(request, obj, form, change)
        self._suivre_immobilisation(request, obj, form)

    def _suivre_immobilisation(self, request, obj, form):
        """Classée « Immobilisation (à amortir) » : crée ou met à jour sa fiche immobilisation ; plus de 500 € en charge : alerte."""
        if not obj.par_rubrique or obj.est_maison:
            return
        d = getattr(form, "cleaned_data", {}) or {}
        immo, cree = amortissements.depuis_facture(obj, d.get("immo_categorie") or None, d.get("immo_duree"), d.get("immo_mise_en_service"))
        if immo:
            lien = reverse("admin:patrimoine_immobilisation_change", args=[immo.pk])
            messages.info(request, format_html("{} <a href=\"{}\">{}</a> · {} ans · dotation {} : {}.",
                                               "Fiche immobilisation créée :" if cree else "Fiche immobilisation mise à jour :", lien,
                                               immo.designation, immo.duree, immo.mise_en_service.year,
                                               euros_liste(amortissements.dotation(immo, immo.mise_en_service.year))))
        elif obj.rubrique in amortissements.RUBRIQUES_A_VERIFIER and obj.montant and obj.montant > amortissements.SEUIL \
                and not obj.charge_confirmee:
            messages.warning(request, f"{euros_liste(obj.montant)} classés en charge : au-delà de {amortissements.SEUIL:.0f} €, un meuble, "
                                      "un équipement ou des travaux d'amélioration s'amortissent. Choisissez « Immobilisation "
                                      "(à amortir) », ou cochez « c'est bien une charge » s'il s'agit d'une réparation.")

    @admin.display(description="facture du", ordering="date")
    def date_affichee(self, obj):
        return obj.date.strftime("%d/%m/%Y") if obj.date else format_html('<span class="pill orange">à classer</span>')

    @admin.display(description="payée le", ordering="date_paiement")
    def payee_le(self, obj):
        if obj.date_paiement:
            return obj.date_paiement.strftime("%d/%m/%Y")
        if obj.date:
            return format_html('<span style="color:#888" title="Date de paiement non renseignée : la facture est rangée dans '
                               "l'année de sa date ({})\">= date facture</span>", obj.date.year)
        return "–"

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
        return format_html('<span class="pill gris">{}</span>', obj.rubrique_libelle)

    @admin.display(description="codification")
    def codification(self, obj):
        return self.rubrique_courte(obj) if obj.par_rubrique else self.ligne_courte(obj)

    @admin.display(description="montant", ordering="montant")
    def montant_affiche(self, obj):
        return euros_liste(obj.montant) if obj.montant is not None else "–"

    @admin.display(description="répartir")
    def scinder(self, obj):
        """Lien vers l'écran de scission (aussi depuis l'onglet « À codifier », où les lignes ne s'ouvrent pas)."""
        motif = obj.motif_verrou(getattr(self, "_fermees", None), getattr(self, "_declares", None))
        if motif:
            return format_html('<span title="{}">🔒</span>', motif)
        scindee = obj.piece_mere_id or obj.parts.exists()
        return format_html('<a href="{}" class="button" style="white-space:nowrap">{}</a>',
                           reverse("admin:patrimoine_piececomptable_scinder", args=[obj.pk]),
                           "Répartie" if scindee else "Scinder")

    @admin.display(description="facture")
    def piece(self, obj):
        if not obj.fichier and obj.piece_mere_id:  # part d'une facture scindée : le PDF est sur la pièce mère
            obj = obj.piece_mere
        if not obj.fichier:
            return "–"
        extension = obj.fichier.name.lower().rsplit(".", 1)[-1]
        image = extension in ("jpg", "jpeg", "png")
        return format_html('<a href="{}" target="_blank" class="apercu-piece" data-image="{}">{}</a>',
                           obj.fichier.url, "1" if image else "", "Photo" if image else "XML" if extension == "xml" else "PDF")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("bien", "piece_mere")
        # listes de la comptabilité : sans les factures de la maison, qui ont leur page (sauf la liste filtrée sur la maison)
        vue = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
        if vue.endswith("_changelist") and not _maisons().filter(pk=request.GET.get("bien") if str(request.GET.get("bien", "")).isdigit() else None).exists():
            qs = qs.exclude(compta.MAISON)
        return qs

    def get_changeform_initial_data(self, request):
        """Nouvelle facture : le bien choisi dans la liste, ou le seul bien du dernier propriétaire choisi."""
        initial = super().get_changeform_initial_data(request)
        if "bien" not in initial:
            proprietaire, bien_choisi = _saisie_retenue(request)
            biens = list(_biens_comptables().filter(proprietaire=proprietaire).values_list("pk", flat=True)) if proprietaire else []
            if bien_choisi in biens:
                initial["bien"] = bien_choisi
            elif len(biens) == 1:
                initial["bien"] = biens[0]
        return initial

    def changelist_view(self, request, extra_context=None):
        DepenseRecurrente.generer_toutes()  # factures des échéances passées des dépenses récurrentes
        self._declarations = None  # colonne « Déclaration » : calculée une fois par affichage
        self._fermees = AnneeCloturee.annees()  # une seule requête pour toutes les lignes
        self._declares = DeclarationFaite.verrous()
        regime = _regime(request)
        # propriétaire et bien choisis dans les filtres : repris par « Ajouter des factures »
        choisi = request.GET.get("bien__proprietaire__id__exact", "")
        if choisi.isdigit():
            if request.session.get("saisie_proprietaire") != int(choisi):
                request.session.pop("saisie_bien", None)
            request.session["saisie_proprietaire"] = int(choisi)
        if request.GET.get("bien", "").isdigit():
            request.session["saisie_bien"] = int(request.GET["bien"])
        dossier = _dossier(request)
        lien_import = reverse("admin:patrimoine_piececomptable_import") + (
            f"?dossier={dossier['code']}" if dossier else f"?compta={regime}" if regime else "")
        extra_context = {**(extra_context or {}), "lien_2072": reverse("annexe_2072"), "lien_import": lien_import,
                         "regime": regime, "libelle_regime": compta.libelle(regime) if regime else "",
                         "nb_a_codifier": compta.a_codifier(regime or None).count(),
                         "nb_a_saisir": compta.a_saisir(regime or None).count() if self._codifier(request) else 0}
        annee = request.GET.get("annee", "")
        if annee.isdigit():
            extra_context.update({"annee_choisie": int(annee), "annee_cloturee": AnneeCloturee.est_cloturee(int(annee)),
                                  "lien_cloture": reverse("admin:patrimoine_piececomptable_cloture", args=[int(annee)])})
        bien_liste = request.GET.get("bien", "")
        maison = _maisons().filter(pk=bien_liste).first() if bien_liste.isdigit() else None
        if maison:
            extra_context.update({"title": f"Factures · Ma maison ({maison.nom})",
                                  "lien_import": reverse("admin:patrimoine_piececomptable_add") + f"?bien={maison.pk}"})
        if regime:
            from ..views.compta import onglets

            extra_context["title"] = f"Factures · {compta.libelle(regime)}"
            extra_context["onglets"] = onglets(regime, "codifier" if self._codifier(request) else "factures")
            extra_context["lien_regime"] = reverse(f"compta_{regime}") if regime in compta.PAR_RUBRIQUE else ""
        if dossier:
            from .. import declarations, dossiers

            annee_d = int(annee) if annee.isdigit() else declarations.annee_fiscale(request)
            extra_context.update({"title": f"Factures · {dossier['titre']}", "dossier": dossier, "libelle_regime": dossier["titre"],
                                  "lien_regime": reverse("dossier_compta", args=[dossier["code"]]),
                                  "nb_a_codifier": dossiers.a_saisir(dossier).count(),
                                  "onglets": dossiers.onglets(dossier, "a_saisir" if self._codifier(request) else "factures", annee_d)})
        return super().changelist_view(request, extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        biens = {str(b.pk): "maison" if b.est_maison else b.comptabilite
                 for b in Bien.objects.only("pk", "comptabilite", "residence_principale")}
        regime = _regime(request)
        if object_id and not regime:
            regime = PieceComptable.objects.filter(pk=object_id).values_list("bien__comptabilite", flat=True).first() or ""
        extra_context = {**(extra_context or {}), "regimes_des_biens": biens, "regime": regime,
                         "seuil_immobilisation": int(amortissements.SEUIL), "durees_immo": amortissements.DUREES,
                         "libelle_regime": compta.libelle(regime) if regime else "",
                         "lien_factures": reverse("admin:patrimoine_piececomptable_changelist") + (f"?compta={regime}" if regime else "")}
        piece = PieceComptable.objects.filter(pk=object_id).select_related("bien").first() if object_id and str(object_id).isdigit() else None
        motif = piece.motif_verrou() if piece else None
        # facture d'origine (PDF ou photo) affichée à côté de la fiche ; une part scindée montre celle de la pièce mère
        origine = piece.piece_mere if piece and not piece.fichier and piece.piece_mere_id else piece
        if origine and origine.fichier:
            extension = origine.fichier.name.lower().rsplit(".", 1)[-1]
            extra_context["fichier_origine"] = {"url": origine.fichier.url, "nom": origine.fichier.name.rsplit("/", 1)[-1],
                                                "genre": "image" if extension in ("jpg", "jpeg", "png") else
                                                "pdf" if extension == "pdf" else "autre"}
        if motif:
            d = piece.declaration_qui_verrouille()
            extra_context.update({"annee_verrouillee": piece.annee, "motif_verrou": motif,
                                  "lien_annee": reverse("declarations") + f"?annee={piece.annee}" if d else lien_annee(piece),
                                  "par_declaration": bool(d)})
        return super().changeform_view(request, object_id, form_url, extra_context)
