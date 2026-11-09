"""Fiches (bail, locataire, lot) : en-tête avec les liens vers les fiches liées, barre d'actions, onglets et encart « En bref ».

Chaque écran déclare ses onglets et renvoie une `patrimoine.fiches.Fiche` ; le gabarit admin/fiche_change_form.html
les affiche et patrimoine/fiche.js montre un onglet à la fois."""
from django.contrib.admin.utils import quote
from django.urls import reverse


class FicheMixin:
    """À placer avant admin.ModelAdmin.

    `onglets` = [(code, libellé, [titres des blocs], [noms des classes de sous-listes])] : le premier onglet reçoit les
    blocs non cités. `contenu_fiche(request, obj)` renvoie une Fiche (ou None). À la création, le formulaire reste d'un seul tenant."""

    change_form_template = "admin/fiche_change_form.html"
    onglets = []

    def contenu_fiche(self, request, obj):
        return None

    def _onglet_du_bloc(self, titre):
        return next((code for code, _, blocs, *_ in self.onglets if titre in blocs), self.onglets[0][0])

    def _onglet_de_sous_liste(self, nom):
        return next((code for code, _, _, *sous in self.onglets if sous and nom in sous[0]), self.onglets[0][0])

    def avec_onglets(self, fieldsets, obj):
        """Range chaque bloc dans son onglet (classes onglet-<code>) ; libellés au-dessus des champs."""
        if not self.onglets or obj is None:
            return fieldsets
        return [(titre, {**options, "classes": [*options.get("classes", []), "fiche-compacte", "onglet",
                                                 f"onglet-{self._onglet_du_bloc(titre)}"]})
                for titre, options in fieldsets]

    def get_fieldsets(self, request, obj=None):
        return self.avec_onglets(super().get_fieldsets(request, obj), obj)

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj is not None and not context.get("is_popup"):
            fiche = self.contenu_fiche(request, obj)
            context["fiche"] = fiche
            if self.onglets and fiche:
                inlines = {f.formset.prefix: self._onglet_de_sous_liste(type(f.opts).__name__)
                           for f in context.get("inline_admin_formsets", [])}
                # un onglet vide (bloc absent pour cette fiche) n'est pas affiché
                remplis = {c[7:] for _, o in context["adminform"].fieldsets for c in o.get("classes", []) if c.startswith("onglet-")}
                remplis |= set(inlines.values()) | {c["code"] for c in fiche.contenus}
                context["onglets"] = [{"code": code, "libelle": libelle, "pastille": fiche.pastilles.get(code)}
                                      for code, libelle, *_ in self.onglets if code in remplis]
                context["onglets_inlines"] = inlines
            opts = self.model._meta
            context["url_historique"] = reverse(f"admin:{opts.app_label}_{opts.model_name}_history", args=[quote(obj.pk)])
            context["url_suppression"] = reverse(f"admin:{opts.app_label}_{opts.model_name}_delete", args=[quote(obj.pk)])
        return super().render_change_form(request, context, add, change, form_url, obj)
