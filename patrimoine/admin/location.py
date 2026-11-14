"""Écrans des locataires, baux, garants et états des lieux."""
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from ..models import (
    DocumentLocataire,
    ModeleDocument,
    mois_depot_maximum,
    Bail,
    Garant,
    EtatDesLieux,
    MouvementDepot,
    Locataire,
)
from .. import fiches
from .communs import LotsALouerMixin
from .fiche import FicheMixin


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
    """Mouvements du dépôt, en lecture : ils se saisissent par « Encaisser » / « Restituer » (une erreur se supprime)."""
    model = MouvementDepot
    fields = ["date", "type", "montant", "mode", "justification", "recu"]
    readonly_fields = fields
    extra = 0
    verbose_name_plural = "Mouvements du dépôt de garantie (compte séparé)"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="reçu")
    def recu(self, obj):
        if obj.pk and obj.type == MouvementDepot.Type.ENCAISSEMENT:
            return format_html('<a class="bouton-recu" href="{}">Créer le reçu</a>', reverse("recu_depot_mouvement", args=[obj.pk]))
        return "–"


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


class LocataireForm(forms.ModelForm):
    """Fiche locataire : identité et notes. Le bail se crée dans Baux › Nouveau bail (ou depuis une candidature),
    le départ se déclare avec « Déclarer un départ »."""

    class Meta:
        model = Locataire
        exclude = ["garant"]


class DocumentLocataireInline(admin.TabularInline):
    model = DocumentLocataire
    extra = 0
    fields = ["type", "titre", "date", "fichier"]
    verbose_name_plural = "Ranger un document (bail signé, attestation d'assurance…)"


@admin.register(Locataire)
class LocataireAdmin(FicheMixin, admin.ModelAdmin):
    form = LocataireForm
    onglets = [
        ("general", "Général", ["Identité", "Notes"], []),
        ("documents", "Documents", ["Documents"], ["DocumentLocataireInline"]),
        ("garants", "Garants", [], ["GarantInline"]),
    ]

    def contenu_fiche(self, request, obj):
        return fiches.fiche_locataire(obj)
    list_display = ["fiche", "statut_present", "contact", "bouton_contacter"]
    list_display_links = ["fiche"]
    list_filter = [PresentFilter, "baux__lot__bien__proprietaire"]
    search_fields = ["nom", "prenom", "email"]
    inlines = [DocumentLocataireInline, GarantInline]
    readonly_fields = ["encart_documents"]

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
        notes = ("Notes", {"classes": ["fiche-compacte"], "fields": ["notes"]})
        if not obj:
            return [identite, notes]
        documents = ("Documents", {"classes": ["encart-documents"], "fields": ["encart_documents"]})
        return self.avec_onglets([identite, notes, documents], obj)

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

    def get_search_results(self, request, queryset, search_term):
        qs, doublons = super().get_search_results(request, queryset, search_term)
        if request.GET.get("model_name") == "bail" and request.GET.get("field_name") == "locataires":
            qs = qs.exclude(baux__actif=True)  # ajout d'un locataire sur un bail : pas ceux qui ont déjà un bail en cours
        return qs, doublons

    def get_urls(self):
        return [path("<int:pk>/contacter/", self.admin_site.admin_view(self.contacter_view), name="patrimoine_locataire_contacter")] + super().get_urls()

    def contacter_view(self, request, pk):
        from .. import contact, mails

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
                    "motif_choisi": request.POST.get("motif") or request.GET.get("motif", "")}
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
            # seuls les locataires sans bail en cours et ceux déjà sur ce bail sont proposés (recherche par le nom)
            deja = self.instance.locataires.all() if self.instance.pk else Locataire.objects.none()
            self.fields["locataires"].queryset = (Locataire.objects.exclude(baux__actif=True) | Locataire.objects.filter(pk__in=deja)).distinct()
            self.fields["locataires"].help_text = "Tapez le nom pour ajouter un locataire (seuls ceux sans bail en cours sont proposés)."
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
class BailAdmin(FicheMixin, LotsALouerMixin, admin.ModelAdmin):
    form = BailForm
    onglets = [
        ("general", "Général", [None, "Clauses particulières"], []),
        ("argent", "Argent", ["Conditions financières", "Réduction temporaire", "Garantie"], ["MouvementDepotInline"]),
        ("compteurs", "Compteurs", ["Relevés des compteurs"], []),
        ("revisions", "Révisions", ["Zone tendue", "Révision annuelle et rappels", "Historique des révisions"], []),
        ("garants", "Garants et documents", ["Documents pré-remplis"], ["GarantBailInline"]),
        ("historique", "Historique", [], []),
    ]

    def contenu_fiche(self, request, obj):
        return fiches.fiche_bail(obj)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        lot = form.base_fields.get("lot")
        if obj is not None and lot is not None:  # le lot s'ouvre par le lien de l'en-tête : pas d'icônes sous la liste
            lot.widget.can_add_related = lot.widget.can_change_related = lot.widget.can_delete_related = lot.widget.can_view_related = False
        return form

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is not None:
            return [(t, {**o, "description": "Les garants se saisissent dans l'onglet « Garants et documents »."} if t == "Garantie" else o)
                    for t, o in fieldsets]
        return fieldsets

    class Media:
        js = ["patrimoine/depot_garantie.js", "patrimoine/heures_creuses.js"]
    list_display = ["liste_locataires", "lot", "type", "date_debut", "colonne_loyer_initial", "colonne_loyer_actuel", "charges", "loyer_cc", "actif"]
    list_display_links = ["liste_locataires", "lot"]
    list_filter = [BailEnCoursFilter, "lot__bien__proprietaire"]
    search_fields = ["lot__designation", "lot__bien__nom", "locataires__nom"]
    autocomplete_fields = ["locataires"]
    date_hierarchy = "date_debut"
    readonly_fields = ["documents", "etat_depot", "historique"]
    fieldsets = [
        (None, {"fields": [("lot", "type"), "locataires", ("date_debut", "date_fin"), "destination", "actif"]}),
        ("Conditions financières", {"fields": [("loyer_initial", "loyer_hc"), ("charges", "mode_charges"), ("depot_garantie", "etat_depot"), "jour_echeance", "paiement_a_echoir"]}),
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
        ("Révision annuelle et rappels", {"fields": [("revision_annuelle", "rappel_revision"), ("irl_trimestre", "irl_valeur"), "indice_reference", "date_derniere_revision", ("date_attestation_assurance", "rappel_assurance")]}),
        ("Historique des révisions", {"fields": ["historique"]}),
        ("Clauses particulières", {"fields": ["notes"]}),
        ("Documents pré-remplis", {"fields": ["documents"]}),
    ]
    inlines = [GarantBailInline, MouvementDepotInline]

    @admin.display(description="détenu aujourd'hui")
    def etat_depot(self, obj):
        """Solde du dépôt avec « Encaisser », « Restituer » et le reçu : les mouvements se saisissent ici, pas dans la liste."""
        if not obj.pk:
            return "Enregistrez le bail pour encaisser le dépôt."
        detenu = obj.depot_detenu
        boutons = []
        if detenu < obj.depot_garantie:
            boutons.append((reverse("depot_bail", args=[obj.pk, "encaisser"]), "button", "Encaisser"))
        if detenu > 0:
            boutons.append((reverse("depot_bail", args=[obj.pk, "restituer"]), "button annuler", "Restituer"))
        if obj.depot_garantie:
            boutons.append((reverse("recu_depot_bail", args=[obj.pk]), "lien-recu", "Créer le reçu (Word)"))
        return format_html('<span class="depot-detenu">{}</span>{}', fiches.eur(detenu),
                           format_html_join("", ' <a class="{1}" href="{0}">{2}</a>', boutons))

    @admin.display(description="documents")
    def documents(self, obj):
        if not obj.pk:
            return "Enregistrez le bail pour générer les documents."
        edl_tel = format_html(
            "<p style='margin:0 0 10px'><strong>État des lieux sur téléphone :</strong> <a href='{}'>entrée</a> · <a href='{}'>sortie</a>"
            " <span class='help'>(pièce par pièce, photos horodatées, signature à l'écran)</span></p>",
            reverse("edl_commencer", args=[obj.pk, "entree"]), reverse("edl_commencer", args=[obj.pk, "sortie"]))
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
            "{}{}{}<p class='help'>Les champs manquants apparaissent « à compléter » : complétez la fiche du lot, du locataire ou du propriétaire. "
            "Vérifiez le bail avec le modèle officiel (décret n° 2015-587) avant signature. Vos modèles Word se gèrent dans le menu Modèles.</p>",
            edl_tel, format_html(" · ".join('<a href="{}" target="_blank">{}</a>' for _ in liens), *[x for l in liens for x in l]), bloc_word,
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


@admin.register(EtatDesLieux)
class EtatDesLieuxAdmin(admin.ModelAdmin):
    date_hierarchy = "date"
    navigation_par = "annee"
    list_display = ["date", "type", "bail", "statut", "document"]
    list_filter = ["type", "bail__lot__bien__proprietaire"]
    readonly_fields = ["saisie", "document"]
    fields = ["saisie", "bail", "type", "date", ("compteur_electricite", "compteur_gaz"), ("compteur_eau_froide", "compteur_eau_chaude"), "nb_cles", "nouvelle_adresse", "observations", "document"]

    def has_change_permission(self, request, obj=None):
        """Signé : plus rien ne se modifie (la fiche reste consultable), sinon l'empreinte ne correspondrait plus."""
        if obj and obj.signe:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.signe:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description="statut")
    def statut(self, obj):
        if obj.signe:
            return format_html('<a class="pill ok" href="{}">Signé le {}</a>', reverse("edl_resume", args=[obj.pk]), f"{timezone.localtime(obj.signe_le):%d/%m/%Y}")
        if obj.elements.exists():
            return format_html('<a class="pill orange" href="{}">En cours</a>', reverse("edl_saisie", args=[obj.pk]))
        return "Fiche papier"

    @admin.display(description="saisie pièce par pièce")
    def saisie(self, obj):
        if not obj or not obj.pk:
            return "Plus simple : depuis la fiche du bail, « Faire l'état des lieux sur téléphone »."
        if obj.signe:
            return format_html('Signé et verrouillé le {}. <a href="{}">Voir l\'état des lieux signé et vérifier son intégrité</a>',
                               f"{timezone.localtime(obj.signe_le):%d/%m/%Y à %H:%M:%S}", reverse("edl_resume", args=[obj.pk]))
        texte = "Continuer la saisie pièce par pièce" if obj.elements.exists() else "Saisir pièce par pièce sur téléphone (photos, signatures)"
        return format_html('<a class="button" href="{}">{}</a>', reverse("edl_saisie", args=[obj.pk]), texte)

    @admin.display(description="document")
    def document(self, obj):
        if not obj.pk:
            return "Enregistrez pour générer la fiche pré-remplie."
        texte = "PDF avec photos et signatures" if obj.elements.exists() else "Fiche PDF pré-remplie"
        return format_html('<a href="{}" target="_blank">{}</a>', reverse("document_edl", args=[obj.pk]), texte)


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
