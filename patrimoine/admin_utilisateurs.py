"""Écran « Utilisateurs » : identité, mot de passe et droits module par module (aucun / consultation / modification)."""
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group, User
from django.utils.html import format_html_join

from . import droits

CHAMPS_DROITS = [f"droit_{code}" for code, *_ in droits.MODULES]


def _champ_droit(libelle):
    return forms.ChoiceField(label=libelle, choices=droits.NIVEAUX, required=False, initial=droits.AUCUN,
                             widget=forms.RadioSelect(attrs={"class": "choix-droit"}))


class _AvecDroits(forms.Form):
    """Un choix de niveau par module (champs déclarés plus bas), prérempli d'après les permissions actuelles."""

    def ajouter_droits(self):
        if self.instance and self.instance.pk and not self.instance.is_superuser:
            for code, niveau in droits.niveaux(self.instance).items():
                self.fields[f"droit_{code}"].initial = niveau

    def choix_droits(self):
        return {code: self.cleaned_data.get(f"droit_{code}") or droits.AUCUN for code, *_ in droits.MODULES}


for _code, _libelle, *_ in droits.MODULES:
    _AvecDroits.base_fields[f"droit_{_code}"] = _AvecDroits.declared_fields[f"droit_{_code}"] = _champ_droit(_libelle)


class UtilisateurCreationForm(_AvecDroits, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "is_superuser")
        labels = {"is_superuser": "Administrateur"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("usable_password", None)  # toujours un mot de passe
        self.ajouter_droits()


class UtilisateurForm(_AvecDroits, UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ajouter_droits()


class UtilisateurAdmin(UserAdmin):
    form = UtilisateurForm
    add_form = UtilisateurCreationForm
    list_display = ["username", "nom_complet", "email", "role", "resume_droits", "is_active"]
    list_filter = ["is_active", "is_superuser"]
    TEXTE_DROITS = ("Consultation : voir les pages et les documents sans rien pouvoir modifier. "
                    "Modification : ajouter, modifier et supprimer. Les Réglages (sauvegardes, mails, utilisateurs) "
                    "sont réservés aux administrateurs. Un administrateur a tous les droits : les choix ci-dessous ne comptent pas pour lui.")
    fieldsets = [
        (None, {"fields": ["username", "password"]}),
        ("Identité", {"fields": ["first_name", "last_name", "email"]}),
        ("Accès", {"fields": ["is_active", "is_superuser", "last_login"]}),
        ("Droits par module", {"fields": CHAMPS_DROITS, "description": TEXTE_DROITS, "classes": ["droits-modules"]}),
    ]
    add_fieldsets = [
        (None, {"fields": ["username", "password1", "password2"]}),
        ("Identité", {"fields": ["first_name", "last_name", "email"]}),
        ("Accès", {"fields": ["is_superuser"]}),
        ("Droits par module", {"fields": CHAMPS_DROITS, "description": TEXTE_DROITS, "classes": ["droits-modules"]}),
    ]
    readonly_fields = ["last_login"]

    class Media:
        css = {"all": ["patrimoine/droits.css"]}

    def get_form(self, request, obj=None, **kwargs):
        kwargs["labels"] = {"is_superuser": "Administrateur", "is_active": "Compte actif", "last_login": "Dernière connexion"}
        kwargs["help_texts"] = {"is_superuser": "Tous les droits, y compris les Réglages et les utilisateurs.",
                                "is_active": "Décochez pour bloquer la connexion sans supprimer le compte."}
        return super().get_form(request, obj, **kwargs)

    @admin.display(description="nom")
    def nom_complet(self, obj):
        return obj.get_full_name() or "–"

    @admin.display(description="rôle")
    def role(self, obj):
        return "Administrateur" if obj.is_superuser else "Utilisateur"

    @admin.display(description="droits")
    def resume_droits(self, obj):
        if obj.is_superuser:
            return "Tout"
        n = droits.niveaux(obj)
        lignes = [(droits.LIBELLES[c], "modification" if v == droits.MODIFICATION else "consultation") for c, v in n.items() if v]
        return format_html_join(", ", "{} ({})", lignes) if lignes else "Aucun accès"

    def save_model(self, request, obj, form, change):
        obj.is_staff = True  # indispensable pour se connecter à l'application
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        droits.appliquer(form.instance, {} if form.instance.is_superuser else form.choix_droits())

    def response_add(self, request, obj, post_url_continue=None):
        # pas de passage obligé par la fiche après création : retour à la liste
        from django.contrib.admin.options import ModelAdmin
        return ModelAdmin.response_add(self, request, obj, post_url_continue)


admin.site.unregister(User)
admin.site.register(User, UtilisateurAdmin)
admin.site.unregister(Group)  # les droits se règlent sur chaque utilisateur
