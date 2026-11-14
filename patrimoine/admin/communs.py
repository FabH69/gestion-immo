"""Réglages communs de l'administration : titres et libellés en bon français, montants, choix des lots."""
from django.contrib import admin

from ..models import Lot


admin.site.site_header = "Gestion immobilière"
admin.site.enable_nav_sidebar = False  # remplacé par le menu du bandeau
admin.site.site_title = "Gestion immobilière"
admin.site.index_title = "Tableau de bord"

_changelist_view_django = admin.ModelAdmin.changelist_view


def _changelist_view_titre_simple(self, request, extra_context=None):
    """Titre de liste « Loyers » au lieu de « Sélectionnez l'objet loyer à changer »."""
    extra_context = {"title": self.model._meta.verbose_name_plural.capitalize(), **(extra_context or {})}
    return _changelist_view_django(self, request, extra_context)


admin.ModelAdmin.changelist_view = _changelist_view_titre_simple

# --- Vocabulaire commun à toutes les fiches ---------------------------------------------------------------------------
# « Ajouter une annonce » / « Modifier l'annonce » au lieu de « Ajout de annonce » / « Modification de annonce ».
# « Enregistrer et ajouter un nouveau » n'est gardé que là où l'on saisit souvent plusieurs fiches d'affilée.
PLUSIEURS_D_AFFILEE = {"echeance", "paiement", "rappel", "piececomptable", "chargelcd", "chargelmnp", "encaissementhistorique",
                       "siteutile", "tachepardefaut", "mouvementdepot", "reservationlcd", "photolot", "documentlocataire",
                       "modeledocument", "modeleannonce", "tache"}
_changeform_view_django = admin.ModelAdmin.changeform_view


def _changeform_view_libelles(self, request, object_id=None, form_url="", extra_context=None):
    from .. import libelles
    nom = self.model._meta.verbose_name
    contexte = {"title": libelles.ajouter(nom) if object_id is None else libelles.modifier(nom)}
    if self.model._meta.model_name not in PLUSIEURS_D_AFFILEE:
        contexte["show_save_and_add_another"] = False
    return _changeform_view_django(self, request, object_id, form_url, {**contexte, **(extra_context or {})})


admin.ModelAdmin.changeform_view = _changeform_view_libelles

_get_inline_formsets_django = admin.ModelAdmin.get_inline_formsets


def _get_inline_formsets_libelles(self, request, formsets, inline_instances, obj=None):
    """Bouton des sous-listes : « Ajouter un garant » au lieu de « Ajouter un objet Garant supplémentaire »."""
    import json
    from .. import libelles
    resultat = _get_inline_formsets_django(self, request, formsets, inline_instances, obj)
    for inline in resultat:
        donnees = json.loads(inline.inline_formset_data())
        donnees["options"]["addText"] = getattr(inline.opts, "texte_ajout", None) or libelles.ajouter(inline.opts.verbose_name)
        inline.inline_formset_data = (lambda texte: lambda: texte)(json.dumps(donnees))
    return resultat


admin.ModelAdmin.get_inline_formsets = _get_inline_formsets_libelles

_message_user_django = admin.ModelAdmin.message_user


def _message_user_libelles(self, request, message, *args, **kwargs):
    """« Enregistrement effectué : « X ». » au lieu de « L'objet loyer « X » a été ajouté avec succès. »."""
    from .. import libelles
    return _message_user_django(self, request, libelles.message(message), *args, **kwargs)


admin.ModelAdmin.message_user = _message_user_libelles


class LotsALouerMixin:
    """Ne propose que les lots non vendus ; un lot vendu déjà choisi reste affiché sur sa fiche."""

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "lot":
            from django.db.models import Q
            actuel = None
            object_id = request.resolver_match.kwargs.get("object_id") if request.resolver_match else None
            if object_id:
                actuel = self.model.objects.filter(pk=object_id).values_list("lot_id", flat=True).first()
            kwargs["queryset"] = Lot.objects.filter(Q(vendu=False, bien__vendu=False) | Q(pk=actuel)).select_related("bien")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


def euros_liste(montant):
    """Montant pour les listes : « 1 234,50 € »."""
    texte = f"{montant:,.2f}".replace(",", "\u202f").replace(".", ",")
    return f"{texte}\u00a0€"
