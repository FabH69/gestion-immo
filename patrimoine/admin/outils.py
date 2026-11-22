"""Écrans des rappels, sites utiles, mails et modèles de documents ; écran Utilisateurs."""
from django import forms
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from ..models import ModeleDocument, ParametresMail, SiteUtile, Rappel


@admin.register(SiteUtile)
class SiteUtileAdmin(admin.ModelAdmin):
    list_display = ["nom", "url", "ordre"]
    list_editable = ["ordre"]
    fields = ["nom", "url", "ordre"]


# aide pas à pas sous les boutons « Ajouter à mon agenda » de la fiche d'un rappel
AIDE_AGENDA = mark_safe(
    "<p>Le bouton <b>📅 Ajouter à mon agenda</b> télécharge un petit fichier agenda (.ics) de ce rappel : "
    "avec l'heure, un rendez-vous d'une heure et une alerte 1 heure avant ; sans heure, un événement sur la journée ; "
    "un rappel périodique se répète dans l'agenda. On le retrouve aussi dans la page <b>À faire</b> : icône 📅 au bout de la ligne.</p>"
    "<ul>"
    "<li><b>iPhone</b> (Safari) : touchez « 📅 Ajouter à mon agenda », l'aperçu s'ouvre, touchez <b>Ajouter au calendrier</b>.</li>"
    "<li><b>Android</b> : touchez <b>Google Agenda ↗</b>, l'événement s'ouvre déjà rempli, touchez <b>Enregistrer</b>. "
    "(Le fichier .ics marche aussi : ouvrez-le depuis la notification de téléchargement, puis choisissez Agenda.)</li>"
    "<li><b>Outlook</b> (ordinateur) : cliquez « 📅 Ajouter à mon agenda », ouvrez le fichier <i>rappel-….ics</i> téléchargé, "
    "puis <b>Enregistrer et fermer</b>.</li>"
    "<li><b>Google Agenda sur l'ordinateur</b> : cliquez <b>Google Agenda ↗</b>, puis <b>Enregistrer</b>.</li>"
    "</ul>"
    "<p>L'agenda garde une copie : si vous changez la date ou l'heure ici, ajoutez le rappel à nouveau "
    "(iPhone et Outlook mettent l'événement à jour ; dans Google Agenda, supprimez l'ancien).</p>")


@admin.register(Rappel)
class RappelAdmin(admin.ModelAdmin):
    date_hierarchy = "date"
    navigation_par = "mois"
    list_display = ["important", "date_heure", "titre", "categorie", "bien", "periodicite", "prevenir", "actif", "dans_agenda", "fait", "agenda_liste"]
    list_editable = ["important", "actif", "dans_agenda", "fait"]
    list_filter = ["important", "dans_agenda", "fait", "bien__proprietaire"]
    # l'heure (facultative) sur la même ligne que la date, pour noter un rendez-vous
    fields = ["titre", ("date", "heure"), "periodicite", "categorie", "bien", ("important", "prevenir_jours"), ("actif", "dans_agenda"), "fait", "notes",
              "agenda"]
    readonly_fields = ["agenda"]

    def get_fields(self, request, obj=None):
        champs = super().get_fields(request, obj)
        return champs if obj else [c for c in champs if c != "agenda"]

    @admin.display(description="agenda")
    def agenda_liste(self, obj):
        return format_html('<a href="{}" title="Ajouter à mon agenda" aria-label="Ajouter « {} » à mon agenda">📅</a>',
                           reverse("rappel_agenda", args=[obj.pk]), obj.titre)

    def save_model(self, request, obj, form, change):
        """Après l'enregistrement : quand le rappel apparaîtra sur l'accueil, et le lien pour l'ajouter à l'agenda."""
        from datetime import timedelta

        from django.contrib import messages
        from django.utils import timezone

        super().save_model(request, obj, form, change)
        if obj.fait or not obj.actif:
            return
        from .. import mails

        aujourdhui = timezone.localdate()
        debut = obj.date - timedelta(days=max(30, obj.prevenir_jours or 0))
        quand = (f"Il apparaîtra sur l'accueil et dans « À faire » à partir du {debut:%d/%m/%Y}. " if debut > aujourdhui
                 else "Il est affiché sur l'accueil et dans « À faire ». ")
        if obj.date_mail is not None and obj.date_mail <= obj.date and obj.date >= aujourdhui:
            reglage = mails.reglage()
            if reglage and reglage.alerte_rappels:
                quand += (f"Mail de rappel le {obj.date_mail:%d/%m/%Y}. " if obj.date_mail > aujourdhui
                          else "Mail de rappel au prochain envoi du matin. ")
            else:
                quand += "Mail de rappel prévu, mais les mails sont désactivés (Paramètres › Mails). "
        messages.info(request, format_html(
            "{}<a href=\"{}\">📅 Ajouter « {} » à mon agenda</a> (téléphone ou ordinateur).",
            quand, reverse("rappel_agenda", args=[obj.pk]), obj.titre))

    def response_change(self, request, obj):
        """« Enregistrer » sur un rappel modifié : retour au tableau de bord (sauf « Enregistrer et continuer » ou lien ?next=)."""
        from .communs import _page_d_origine

        reponse = super().response_change(request, obj)
        boutons = {"_continue", "_addanother", "_saveasnew", "_popup"}
        if reponse.status_code == 302 and not _page_d_origine(request) and not boutons & set(request.POST) and "_popup" not in request.GET:
            return redirect("admin:index")
        return reponse

    @admin.display(description="agenda")
    def agenda(self, obj):
        """Boutons « Ajouter à mon agenda » (.ics, iPhone et Android) et Google Agenda, une fois le rappel enregistré."""
        from ..agenda import lien_google

        return format_html(
            '<div class="boutons-agenda"><a class="button" href="{}">📅 Ajouter à mon agenda</a> '
            '<a class="button" href="{}" target="_blank" rel="noopener">Google Agenda ↗</a></div>'
            '<details class="aide-agenda"><summary>Comment l\'ajouter au calendrier du téléphone ?</summary>{}</details>'
            '<p class="help">Pour ne plus le faire rappel par rappel : cochez « 📅 dans mon agenda » et abonnez une fois votre téléphone '
            'depuis <a href="{}">📅 Mon agenda</a>.</p>',
            reverse("rappel_agenda", args=[obj.pk]), lien_google(obj), AIDE_AGENDA, reverse("agenda_abonnement"))

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "heure":
            kwargs["widget"] = forms.TimeInput(attrs={"type": "time", "class": "champ-heure"}, format="%H:%M")
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="date", ordering="date")
    def date_heure(self, obj):
        date = f"{obj.date:%d/%m/%Y}"
        return f"{date} à {obj.heure_texte}" if obj.heure else date

    @admin.display(description="mail")
    def prevenir(self, obj):
        jours = obj.prevenir_jours
        if jours is None:
            return "7 j avant" if obj.important else "–"
        return "le jour même" if jours == 0 else f"{jours} j avant"
    list_display_links = ["titre"]
    search_fields = ["titre", "notes"]

    actions = ["marquer_fait", "desactiver", "reactiver", "mettre_dans_agenda", "retirer_de_agenda"]

    @admin.action(description="📅 Mettre dans mon agenda (abonnement automatique)")
    def mettre_dans_agenda(self, request, queryset):
        n = queryset.update(dans_agenda=True)
        from django.contrib import messages

        messages.success(request, format_html("{} rappel(s) ajouté(s) à l'agenda abonné. <a href=\"{}\">Abonner mon téléphone</a>",
                                              n, reverse("agenda_abonnement")))

    @admin.action(description="Retirer de mon agenda")
    def retirer_de_agenda(self, request, queryset):
        queryset.update(dans_agenda=False)

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
    # L'adresse d'envoi et son mot de passe d'application sont côte à côte (classe « mail-envoi » dans theme.css).
    fieldsets = [(None, {"classes": ["mail-envoi"], "fields": [
        ("adresse", "mot_de_passe"), "nom_expediteur", "alerte_nouveau_dossier", "accuse_au_candidat", "alerte_rappels",
        "tache_quotidienne", "test"]})]
    readonly_fields = ["tache_quotidienne", "test"]

    @admin.display(description="tâche quotidienne")
    def tache_quotidienne(self, obj):
        from django.utils.safestring import mark_safe

        return mark_safe(
            "Pour recevoir les rappels ★ par mail, créez une fois une tâche dans PythonAnywhere › onglet <b>Tasks</b> › "
            "« Daily », à 07:00, avec cette commande :<br><code>bash ~/gestion-immo/deploiement/tache_quotidienne.sh</code>")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["mot_de_passe"].widget = forms.PasswordInput(render_value=True)
        # Libellés courts : chaque case tient sur une seule ligne, même sur téléphone.
        for champ, libelle in (("alerte_nouveau_dossier", "Alerte à chaque candidature reçue"),
                               ("accuse_au_candidat", "Accusé de réception au candidat"),
                               ("alerte_rappels", "Rappels par mail")):
            form.base_fields[champ].label = libelle
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
