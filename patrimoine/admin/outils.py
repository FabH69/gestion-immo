"""Écrans des rappels, sites utiles, mails et modèles de documents ; écran Utilisateurs."""
from django import forms
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from ..models import ModeleDocument, ParametresMail, SiteUtile, Rappel


@admin.register(SiteUtile)
class SiteUtileAdmin(admin.ModelAdmin):
    list_display = ["nom", "url", "ordre"]
    list_editable = ["ordre"]
    fields = ["nom", "url", "ordre"]


@admin.register(Rappel)
class RappelAdmin(admin.ModelAdmin):
    date_hierarchy = "date"
    navigation_par = "mois"
    list_display = ["date", "titre", "categorie", "bien", "periodicite", "actif", "fait"]
    list_editable = ["actif", "fait"]
    list_filter = ["fait", "bien__proprietaire"]
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


@admin.register(ParametresMail)
class ParametresMailAdmin(admin.ModelAdmin):
    fields = ["adresse", "mot_de_passe", "nom_expediteur", "alerte_nouveau_dossier", "accuse_au_candidat", "test"]
    readonly_fields = ["test"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["mot_de_passe"].widget = forms.PasswordInput(render_value=True)
        return form

    def changelist_view(self, request, extra_context=None):
        if ParametresMail.objects.filter(pk=1).exists():
            return redirect("admin:patrimoine_parametresmail_change", 1)
        return redirect("admin:patrimoine_parametresmail_add")

    def has_add_permission(self, request):
        return not ParametresMail.objects.filter(pk=1).exists()

    @admin.display(description="vérifier")
    def test(self, obj):
        if not obj or not obj.pk:
            return "Enregistrez d'abord, puis envoyez-vous un mail de test."
        return format_html('<button type="submit" class="button" formaction="{}" formnovalidate>M\'envoyer un mail de test</button>',
                           reverse("tester_mail"))


@admin.register(ModeleDocument)
class ModeleDocumentAdmin(admin.ModelAdmin):
    list_display = ["nom", "type", "actif", "nb_champs", "telecharger", "modifie_le"]
    list_filter = ["type", "actif"]
    fields = ["nom", "type", "fichier", "actif", "notes", "champs_trouves"]
    readonly_fields = ["champs_trouves"]

    def _champs(self, obj):
        from ..champs import CHAMPS
        from ..word import champs_du_modele, normaliser

        import inspect
        import re as _re

        from ..champs import valeurs as _valeurs
        connus = {normaliser(n) for n in _re.findall(r'"([^"{}]+)":', inspect.getsource(_valeurs))}
        connus |= {normaliser(n) for _, liste in CHAMPS for n, _ in liste} | {normaliser(n) for n in (
            "Adresse_Prop", "Ville_Propr", "Adresse_Appart", "CP_Appart", "Ville_Appart", "IRL de référence", "IRL référence Période",
            "Index_Elec_Entrée", "Index_Elec_Sortie", "Index_Gaz_Sortie", "Index_Eau_froide_Sortie", "Index_Eau_chaude_Sortie",
            "Nationalité_Loc", "Prénom_Garant2", "Civilité_Garant2", "Adresse_Garant2", "Date_naissance_Garant2", "Lieu_naissance_Garant2", "Lien_Garant2")}
        try:
            with obj.fichier.open("rb") as f:
                noms = champs_du_modele(f)
        except Exception:
            return None, None
        connus_loc = {normaliser(n) for n in noms if normaliser(n) in connus or normaliser(n).rstrip("234") in connus}
        return noms, [n for n in noms if normaliser(n) not in connus_loc]

    @admin.display(description="champs")
    def nb_champs(self, obj):
        noms, inconnus = self._champs(obj)
        if noms is None:
            return "fichier illisible"
        if inconnus:
            return format_html('{} <span style="color:#c62828">({} inconnu{})</span>', len(noms), len(inconnus), "s" if len(inconnus) > 1 else "")
        return len(noms)

    @admin.display(description="modèle")
    def telecharger(self, obj):
        return format_html('<a href="{}">Télécharger</a>', obj.fichier.url) if obj.fichier else "–"

    @admin.display(description="champs trouvés dans le document")
    def champs_trouves(self, obj):
        if not obj or not obj.pk:
            return "Enregistrez pour voir les champs reconnus dans le document."
        noms, inconnus = self._champs(obj)
        if noms is None:
            return "Le fichier n'est pas un document Word (.docx) lisible."
        if not noms:
            return format_html("Aucun champ {{…}} trouvé. <a href=\"{}\">Voir la liste des champs</a>", reverse("liste_champs"))
        rendu = format_html_join(" ", '<code style="{}">{{{}}}</code>', (
            ("color:#c62828;font-weight:600" if n in inconnus else "", n) for n in noms))
        aide = format_html(' <p class="help">En rouge : champs inconnus, ils resteront tels quels. <a href="{}">Liste des champs</a></p>',
                           reverse("liste_champs")) if inconnus else ""
        return format_html("{}{}", rendu, aide)


from .. import admin_utilisateurs  # noqa: E402,F401  (écran Utilisateurs avec droits par module)
