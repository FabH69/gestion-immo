"""Écrans des loyers (échéances et paiements), dépôts de garantie, révisions et loyers repris."""
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from ..models import EncaissementHistorique, Bail, Echeance, MouvementDepot, RevisionLoyer, Paiement, Proprietaire, Versement
from .communs import euros_liste


def url_encaisser(echeance, retour=""):
    """Adresse du formulaire « Encaisser » prérempli pour ce loyer : son reste dû, aujourd'hui, par virement."""
    from urllib.parse import urlencode

    params = {"bail": echeance.bail_id, "echeance": echeance.pk, "montant": f"{max(echeance.reste_du, Decimal(0)):.2f}"}
    if retour:
        params["next"] = retour
    return reverse("admin:patrimoine_echeance_virement") + "?" + urlencode(params)


def libelle_echeance(e):
    return f"régularisation des charges {e.periode:%m/%Y}" if e.est_regularisation else f"{e.periode:%m/%Y}"


@admin.register(EncaissementHistorique)
class EncaissementHistoriqueAdmin(admin.ModelAdmin):
    """Loyers repris d'un ancien suivi. Enregistrer une ligne la transforme en paiement reçu du locataire de ce mois-là
    (réparti sur les loyers qu'elle couvre) : il n'y a ainsi qu'une seule liste des encaissements, « Paiements reçus ».
    Les lignes qui restent ici sont celles qu'on n'a pas pu rattacher (aucun bail ce mois-là, ou paiement déjà saisi)."""
    list_display = ["mois", "lot", "montant", "charges", "note", "pas_repris"]
    list_filter = ["lot__bien__proprietaire"]
    date_hierarchy = "mois"
    navigation_par = "mois"
    actions = ["reprendre", "reprendre_bail_suivant"]

    @admin.display(description="pourquoi elle est encore ici")
    def pas_repris(self, obj):
        from ..reprise_historique import raison

        return raison(obj) or "peut être reprise"

    def _reprendre(self, request, queryset, bail_suivant):
        from ..reprise_historique import convertir

        n, laissees = convertir(queryset, bail_suivant=bail_suivant)
        if n:
            self.message_user(request, f"{n} ligne(s) reprise(s) dans « Paiements reçus ».", messages.SUCCESS)
        for h, raison in laissees:
            self.message_user(request, f"{h.lot} – {h.mois:%m/%Y} gardée : {raison}.", messages.WARNING)

    @admin.action(description="Reprendre dans « Paiements reçus »")
    def reprendre(self, request, queryset):
        self._reprendre(request, queryset, False)

    @admin.action(description="Rattacher au locataire suivant du lot et reprendre (mois avant la date d'entrée saisie)")
    def reprendre_bail_suivant(self, request, queryset):
        self._reprendre(request, queryset, True)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from ..reprise_historique import convertir

        n, laissees = convertir(EncaissementHistorique.objects.filter(pk=obj.pk))
        if n:
            self.message_user(request, f"{obj.montant} € de {obj.mois:%m/%Y} enregistrés comme paiement reçu du locataire "
                                       "(voir « Paiements reçus »).", messages.SUCCESS)
        for _, raison in laissees:
            self.message_user(request, f"Gardé dans l'historique : {raison}.", messages.WARNING)

    def response_add(self, request, obj, post_url_continue=None):
        if not EncaissementHistorique.objects.filter(pk=obj.pk).exists():
            return redirect("admin:patrimoine_versement_changelist")
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if not EncaissementHistorique.objects.filter(pk=obj.pk).exists():
            return redirect("admin:patrimoine_versement_changelist")
        return super().response_change(request, obj)


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


class PaiementInline(admin.TabularInline):
    """Paiements du loyer, en lecture : on encaisse par le bouton « Encaisser » ; pour corriger, on ouvre le paiement reçu."""
    model = Paiement
    fields = ["date", "montant", "mode", "notes", "part_de_virement"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    verbose_name = "paiement"
    verbose_name_plural = "paiements de ce loyer"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="paiement reçu")
    def part_de_virement(self, obj):
        if not obj or not obj.versement_id:
            return "–"
        v = obj.versement
        return format_html('<a href="{}">{} € du {}</a>', reverse("admin:patrimoine_versement_change", args=[v.pk]),
                           euros_liste(v.montant), v.date.strftime("%d/%m/%Y"))


class VirementForm(forms.Form):
    """Étape 1 de « Encaisser » : qui a payé, quand, combien, comment."""
    bail = forms.ModelChoiceField(label="Locataire et logement", queryset=Bail.objects.none())
    date = forms.DateField(label="Date de réception", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    montant = forms.DecimalField(label="Montant reçu (€)", max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    mode = forms.ChoiceField(label="Mode", choices=Paiement.Mode.choices, initial=Paiement.Mode.VIREMENT)
    part_depot = forms.DecimalField(label="Dont dépôt de garantie (€)", required=False, max_digits=10, decimal_places=2,
                                    min_value=0, help_text="Seulement si ce paiement comprend le dépôt de garantie")
    notes = forms.CharField(label="Note (facultatif)", required=False, max_length=255,
                            widget=forms.TextInput(attrs={"placeholder": "ex. part CAF de novembre"}))

    def clean(self):
        d = super().clean()
        if d.get("montant") and (d.get("part_depot") or 0) > d["montant"]:
            self.add_error("part_depot", "Le dépôt ne peut pas dépasser le montant reçu.")
        return d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        baux = Bail.objects.select_related("lot__bien").prefetch_related("locataires").order_by("-actif", "lot__bien__nom", "lot__designation", "-date_debut")
        self.fields["bail"].queryset = baux
        self.fields["bail"].label_from_instance = lambda b: (
            f"{', '.join(str(l) for l in b.locataires.all()) or 'sans locataire'} – {b.lot}" + ("" if b.actif else " (terminé)"))


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
    search_fields = ["bail__lot__designation", "bail__lot__bien__nom", "bail__locataires__nom", "bail__locataires__prenom"]
    date_hierarchy = "periode"
    navigation_par = "mois"
    readonly_fields = ["statut", "document", "encaisser"]
    fields = ["bail", "nature", "periode", ("loyer_hc", "charges"), "date_exigibilite", ("remise", "motif_remise"), "statut",
              "document", "encaisser"]
    inlines = [PaiementInline]
    actions = ["modifier", "marquer_paye"]
    change_list_template = "admin/patrimoine/echeance/change_list.html"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("bail__lot__bien").prefetch_related("bail__locataires", "paiements")

    def get_urls(self):
        return [
            path("generer/", self.admin_site.admin_view(self.generer_view), name="patrimoine_echeance_generer"),
            path("<int:pk>/encaisser/", self.admin_site.admin_view(self.encaisser_view), name="patrimoine_echeance_encaisser"),
            path("virement/", self.admin_site.admin_view(self.virement_view), name="patrimoine_echeance_virement"),
        ] + super().get_urls()

    def encaisser_view(self, request, pk):
        """Ancienne adresse du bouton « Encaisser » : ouvre le formulaire d'encaissement prérempli pour ce loyer."""
        e = Echeance.objects.get(pk=pk)
        return redirect(url_encaisser(e, request.GET.get("next") or request.META.get("HTTP_REFERER") or ""))

    @staticmethod
    def _retour_sur(request, retour):
        from django.utils.http import url_has_allowed_host_and_scheme

        return retour if retour and url_has_allowed_host_and_scheme(retour, allowed_hosts={request.get_host()}) else ""

    def virement_view(self, request):
        """« Encaisser » : la seule façon d'enregistrer un paiement. Un montant reçu est réparti sur un ou plusieurs loyers
        (le loyer choisi d'abord, puis du plus ancien au plus récent) ; la répartition proposée peut être corrigée, une part
        peut aller au dépôt de garantie, et ce qui dépasse reste en avance pour les loyers suivants.
        Depuis le bouton « Encaisser » d'un loyer, le formulaire arrive prérempli et la répartition est déjà affichée :
        un clic sur « Valider » suffit."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        prerempli = request.method == "GET" and request.GET.get("bail") and request.GET.get("montant")
        if request.method == "POST":
            etape, donnees = request.POST.get("etape"), request.POST
        elif prerempli:
            etape = "repartir"
            donnees = {"bail": request.GET["bail"], "montant": request.GET["montant"], "mode": request.GET.get("mode", "virement"),
                       "date": request.GET.get("date") or timezone.localdate().isoformat(), "notes": "", "part_depot": ""}
        else:
            etape, donnees = None, None
        initial = {"date": timezone.localdate(), "bail": request.GET.get("bail")}
        form = VirementForm(donnees if etape else None, initial=initial)
        source = request.POST if request.method == "POST" else request.GET
        priorite = Echeance.objects.filter(pk=source.get("echeance")).first() if (source.get("echeance") or "").isdigit() else None
        retour = self._retour_sur(request, source.get("next", ""))
        contexte = {**self.admin_site.each_context(request), "title": "Encaisser un paiement", "opts": self.model._meta, "form": form,
                    "echeance_choisie": priorite, "retour": retour}
        if not etape or not form.is_valid():
            return render(request, "admin/patrimoine/echeance/virement.html", contexte)
        d = form.cleaned_data
        depot = d.get("part_depot") or Decimal(0)
        if priorite is not None and priorite.bail_id != d["bail"].pk:
            priorite = None
        proposee = Versement.repartition_proposee(d["bail"], d["montant"] - depot, priorite=priorite)
        lignes, erreurs = [], []
        for e, part in proposee:
            if etape == "valider":
                brut = (request.POST.get(f"part-{e.pk}") or "").strip().replace(" ", "").replace(",", ".")
                try:
                    part = Decimal(brut) if brut else Decimal(0)
                except ArithmeticError:
                    erreurs.append(f"{e.periode:%m/%Y} : montant illisible « {brut} ».")
                    part = Decimal(0)
                if part < 0 or part > e.reste_du:
                    erreurs.append(f"{e.periode:%m/%Y} : la part doit être entre 0 et le reste dû ({e.reste_du} €).")
            lignes.append({"echeance": e, "part": part})
        total = sum((l["part"] for l in lignes), Decimal(0))
        if total + depot > d["montant"]:
            erreurs.append(f"Les parts ({total + depot} €) dépassent le montant reçu ({d['montant']} €).")
        if etape == "valider" and not erreurs:
            v = Versement.objects.create(bail=d["bail"], date=d["date"], montant=d["montant"], mode=d["mode"], notes=d["notes"],
                                         part_depot=depot)
            v.repartir([(l["echeance"], l["part"]) for l in lignes])
            v.enregistrer_depot()
            mois = ", ".join(libelle_echeance(l["echeance"]) for l in lignes if l["part"] > 0) or "aucun loyer"
            texte = f"{d['montant']} € encaissés le {d['date']:%d/%m/%Y}, répartis sur : {mois}."
            if depot:
                texte += f" Dépôt de garantie : {depot} €."
            if v.avance > 0:
                texte += f" Avance gardée pour les prochains loyers : {v.avance} €."
            self.message_user(request, texte, messages.SUCCESS)
            return redirect(retour or reverse("admin:patrimoine_echeance_changelist") + f"?bail={d['bail'].pk}&reglement=tous")
        contexte.update({"lignes": lignes, "total": total, "avance": d["montant"] - total - depot, "erreurs": erreurs, "repartir": True,
                         "depot": depot,
                         "avance_existante": sum((v.avance for v in d["bail"].versements.all()), Decimal(0))})
        return render(request, "admin/patrimoine/echeance/virement.html", contexte)

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
            remises = sum((e.remise for e in lignes), Decimal(0))
            jour = timezone.localdate()
            retard = [e for e in lignes if e.statut != Echeance.Statut.PAYE and e.date_exigibilite < jour]
            reponse.context_data["resume"] = {
                "du": du, "paye": paye, "reste": du - paye - remises, "nb": len(lignes),
                "retard": sum((e.total - e.remise - sum((p.montant for p in e.paiements.all()), Decimal(0)) for e in retard), Decimal(0)),
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
        if not change:
            Versement.imputer_avances(form.instance)  # loyer créé à la main : utilise l'avance éventuelle du locataire
        form.instance.mettre_a_jour_statut()

    @admin.display(description="mois", ordering="periode")
    def mois(self, obj):
        return obj.periode.strftime("%m/%Y")

    @admin.display(description="locataire et logement", ordering="bail__lot__bien__nom")
    def locataire_et_logement(self, obj):
        noms = [str(l) for l in obj.bail.locataires.all()]
        if obj.est_regularisation:
            return format_html('<span class="cellule-personne"><span><b>{}</b><small>{} · <span class="pill">Régularisation des charges</span></small></span></span>',
                               ", ".join(noms) or "–", obj.bail.lot)
        return format_html('<span class="cellule-personne"><span><b>{}</b><small>{}</small></span></span>', ", ".join(noms) or "–", obj.bail.lot)

    @admin.display(description="encaisser")
    def encaisser(self, obj):
        if not obj.pk:
            return "–"
        if obj.reste_du <= 0:
            return "Rien à encaisser : ce loyer est soldé."
        return format_html('<a class="button" href="{}">Encaisser</a> <span class="help">Ouvre le formulaire prérempli avec le reste dû ({} €).</span>',
                           url_encaisser(obj, reverse("admin:patrimoine_echeance_change", args=[obj.pk])), obj.reste_du)

    @admin.display(description="échéance", ordering="date_exigibilite")
    def echeance_le(self, obj):
        return obj.date_exigibilite.strftime("%d/%m/%Y")

    @admin.display(description="")
    def actions_ligne(self, obj):
        # libellé d'après le document réel : un loyer soldé par une remise sans être payé en entier n'a pas de quittance
        paye = self._paye(obj)
        libelle = ("Quittance" if obj.statut == "paye" and paye >= obj.total else
                   "Reçu PDF" if obj.statut == "partiel" or (obj.statut == "paye" and paye > 0) else "Avis PDF")
        pdf = format_html('<a class="lien-action" href="{}" target="_blank">{}</a>', reverse("document_echeance", args=[obj.pk]), libelle)
        if obj.statut == "paye":
            return pdf
        if obj.date_exigibilite < timezone.localdate():
            pdf = format_html('{} <a class="lien-action" href="{}?bail={}">Relancer</a>', pdf, reverse("relances_impayes"), obj.bail_id)
        # un simple lien vers le formulaire d'encaissement prérempli (reste dû, aujourd'hui, virement) : un clic pour valider
        retour = reverse("admin:patrimoine_echeance_changelist") + (f"?{self._requete}" if getattr(self, "_requete", "") else "")
        return format_html('<a class="bouton-encaisser" href="{}" title="Encaisser le reste dû ({} €) : date, montant et mode modifiables">'
                           'Encaisser</a>{}', url_encaisser(obj, retour), obj.reste_du, pdf)

    def get_changelist_instance(self, request):
        from django.middleware.csrf import get_token
        self._csrf = get_token(request)
        self._requete = request.GET.urlencode()
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
        reste = obj.total - self._paye(obj) - obj.remise
        return format_html("<b>{}</b>", euros_liste(reste)) if reste else "–"

    @admin.display(description="statut", ordering="statut")
    def statut_colore(self, obj):
        if obj.statut == "paye":
            if obj.remise:
                return format_html('<span class="pill ok" title="Soldé avec une remise de {} €">✓ Soldé (remise)</span>', obj.remise)
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
        libelle = f"{obj.document[1]} PDF"
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

    @admin.action(description="Tout encaisser (virement, aujourd'hui)")
    def marquer_paye(self, request, queryset):
        """Un paiement reçu par loyer coché, du reste dû, daté d'aujourd'hui."""
        n = 0
        for e in queryset.exclude(statut=Echeance.Statut.PAYE):
            if e.reste_du <= 0:
                continue
            v = Versement.objects.create(bail=e.bail, date=timezone.localdate(), montant=e.reste_du)
            v.repartir([(e, e.reste_du)])
            n += 1
        self.message_user(request, f"{n} échéance(s) marquée(s) comme payée(s).", messages.SUCCESS)


class VersementPaiementInline(admin.TabularInline):
    model = Paiement
    fields = ["loyer", "montant", "date"]
    readonly_fields = ["loyer", "montant", "date"]
    extra = 0
    can_delete = False
    verbose_name = "part affectée à un loyer"
    verbose_name_plural = "répartition sur les loyers"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="loyer")
    def loyer(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:patrimoine_echeance_change", args=[obj.echeance_id]),
                           f"{obj.echeance.periode:%m/%Y}")


class VersementForm(forms.ModelForm):
    """Corriger un paiement reçu : l'annuler (erreur, chèque impayé, prélèvement rejeté) ou rembourser l'avance au locataire."""

    class Meta:
        model = Versement
        fields = ["notes", "rembourse", "date_remboursement", "annule", "motif_annulation"]
        widgets = {"date_remboursement": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "rembourse" in self.fields:
            self.fields["rembourse"].required = False

    def clean(self):
        d = super().clean()
        v = self.instance
        if "rembourse" in d and d["rembourse"] is None:
            d["rembourse"] = Decimal(0)
        rembourse = d.get("rembourse") or Decimal(0)
        if rembourse < 0:
            self.add_error("rembourse", "Le montant remboursé doit être positif.")
        disponible = v.montant - v.montant_reparti - (v.part_depot or 0)
        if not d.get("annule") and rembourse > disponible:
            self.add_error(None, f"Vous ne pouvez rembourser que l'avance non utilisée : {disponible} € au plus.")
        if rembourse and not d.get("date_remboursement"):
            d["date_remboursement"] = timezone.localdate()
        if d.get("annule") and not v.annule and not (d.get("motif_annulation") or "").strip():
            self.add_error(None, "Indiquez pourquoi ce paiement est annulé (ex. chèque sans provision).")
        return d


@admin.register(Versement)
class VersementAdmin(admin.ModelAdmin):
    """Paiements reçus : chacun montre sa répartition sur les loyers, sa part de dépôt et l'avance qui reste.
    On le corrige ici : annulation (les loyers redeviennent à payer) ou remboursement de l'avance au locataire."""
    form = VersementForm
    list_display = ["date", "locataire_et_logement", "montant_recu", "reparti", "avance_restante", "mode", "etat"]
    list_filter = [BailEcheanceFilter]
    filtres_en_liste = ("bail",)
    date_hierarchy = "date"
    search_fields = ["bail__lot__designation", "bail__lot__bien__nom", "bail__locataires__nom", "bail__locataires__prenom", "notes"]
    fieldsets = [
        (None, {"fields": ["bail", "date", "montant", "mode", "part_depot", "notes", "avance_restante"]}),
        ("Rembourser l'avance au locataire", {"fields": [("rembourse", "date_remboursement")],
                                              "description": "Si le locataire a trop payé et que vous lui rendez la différence, "
                                                             "au lieu de la garder pour ses prochains loyers."}),
        ("Annuler ce paiement", {"fields": [("annule", "motif_annulation")],
                                 "description": "Erreur de saisie, chèque sans provision, prélèvement rejeté : le paiement reste "
                                                "dans l'historique, mais les loyers qu'il réglait redeviennent à payer."}),
    ]
    inlines = [VersementPaiementInline]

    def get_readonly_fields(self, request, obj=None):
        lecture = ["bail", "date", "montant", "mode", "part_depot", "avance_restante"]
        if obj and obj.annule:
            lecture += ["rembourse", "date_remboursement", "annule", "motif_annulation"]
        return lecture

    def save_model(self, request, obj, form, change):
        annuler = change and "annule" in form.changed_data and obj.annule
        if annuler:
            obj.annule = False  # annuler() retire les parts puis enregistre l'annulation
        super().save_model(request, obj, form, change)
        if annuler:
            obj.annuler(obj.motif_annulation)
            self.message_user(request, "Paiement annulé : les loyers qu'il réglait sont de nouveau à payer.", messages.WARNING)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("bail__lot__bien").prefetch_related("bail__locataires", "paiements")

    def add_view(self, request, form_url="", extra_context=None):
        return redirect("admin:patrimoine_echeance_virement")

    @admin.display(description="locataire et logement", ordering="bail__lot__bien__nom")
    def locataire_et_logement(self, obj):
        noms = [str(l) for l in obj.bail.locataires.all()]
        return format_html('<span class="cellule-personne"><span><b>{}</b><small>{}</small></span></span>', ", ".join(noms) or "–", obj.bail.lot)

    @admin.display(description="reçu", ordering="montant")
    def montant_recu(self, obj):
        return euros_liste(obj.montant)

    @admin.display(description="affecté aux loyers")
    def reparti(self, obj):
        return euros_liste(sum((p.montant for p in obj.paiements.all()), Decimal(0)))

    @admin.display(description="avance restante")
    def avance_restante(self, obj):
        if obj.annule:
            return "–"
        avance = obj.montant - sum((p.montant for p in obj.paiements.all()), Decimal(0)) - obj.part_depot - obj.rembourse
        return format_html("<b>{}</b>", euros_liste(avance)) if avance else "–"

    @admin.display(description="")
    def etat(self, obj):
        if obj.annule:
            return format_html('<span class="pill rouge" title="{}">Annulé</span>', obj.motif_annulation)
        morceaux = []
        if obj.part_depot:
            morceaux.append(format_html('<span class="pill">Dépôt {}</span>', euros_liste(obj.part_depot)))
        if obj.rembourse:
            morceaux.append(format_html('<span class="pill orange">Remboursé {}</span>', euros_liste(obj.rembourse)))
        return format_html(" ".join(["{}"] * len(morceaux)), *morceaux) if morceaux else ""


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
    """Vue d'ensemble en lecture : un dépôt s'encaisse et se restitue sur la fiche du bail (onglet Argent)."""
    navigation_par = "annee"
    list_display = ["date", "type", "montant_affiche", "logement", "locataires", "proprietaire", "mode", "justification", "recu"]
    list_display_links = None
    list_select_related = ["bail__lot__bien__proprietaire"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="bail", ordering="bail__lot__bien__nom")
    def logement(self, obj):
        return format_html('<a href="{}#argent">{}</a>', reverse("admin:patrimoine_bail_change", args=[obj.bail_id]), obj.bail.lot)

    @admin.display(description="locataire")
    def locataires(self, obj):
        return ", ".join(str(l) for l in obj.bail.locataires.all()) or "–"

    @admin.display(description="propriétaire", ordering="bail__lot__bien__proprietaire__nom")
    def proprietaire(self, obj):
        return obj.bail.lot.bien.proprietaire
    list_filter = [DepotEnCoursFilter, "bail__lot__bien__proprietaire"]
    search_fields = ["bail__lot__designation", "bail__locataires__nom", "bail__locataires__prenom"]
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
