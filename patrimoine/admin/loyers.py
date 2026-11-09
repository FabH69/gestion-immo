"""Écrans des loyers (échéances et paiements), dépôts de garantie, révisions et loyers repris."""
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from ..models import EncaissementHistorique, Bail, Echeance, MouvementDepot, RevisionLoyer, Paiement, Proprietaire
from .communs import euros_liste


@admin.register(EncaissementHistorique)
class EncaissementHistoriqueAdmin(admin.ModelAdmin):
    list_display = ["mois", "lot", "montant", "charges", "note"]
    list_filter = ["lot__bien__proprietaire"]
    date_hierarchy = "mois"
    navigation_par = "mois"


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

    @admin.action(description="Tout encaisser (virement, aujourd'hui)")
    def marquer_paye(self, request, queryset):
        n = 0
        for e in queryset.exclude(statut=Echeance.Statut.PAYE):
            Paiement.objects.create(echeance=e, date=timezone.localdate(), montant=e.reste_du)
            n += 1
        self.message_user(request, f"{n} échéance(s) marquée(s) comme payée(s).", messages.SUCCESS)


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
