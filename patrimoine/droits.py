"""Droits des utilisateurs, module par module : aucun accès, consultation ou modification.

Chaque niveau se traduit en permissions Django sur les tables du module (consultation = « view », modification =
« view/add/change/delete ») : les écrans d'administration les respectent d'eux-mêmes (fiches en lecture seule…).
Le middleware ci-dessous applique les mêmes règles aux pages faites à la main (états, PDF, quittances, Balles…).
Les administrateurs (superutilisateurs) ont tout ; les Réglages (sauvegardes, mails, utilisateurs) leur sont réservés.
"""
from django.core.exceptions import PermissionDenied

AUCUN, CONSULTATION, MODIFICATION = "", "consultation", "modification"
NIVEAUX = [(AUCUN, "Aucun accès"), (CONSULTATION, "Consultation"), (MODIFICATION, "Modification")]

# (code, libellé, tables « app.modele », pages faites à la main (noms d'URL), pages qui modifient même en GET)
MODULES = [
    ("biens", "Biens et lots", ["patrimoine.bien", "patrimoine.lot", "patrimoine.photolot", "patrimoine.proprietaire",
                                "patrimoine.syndic"], [], []),
    ("locataires", "Locataires", ["patrimoine.locataire", "patrimoine.documentlocataire"],
     ["admin:patrimoine_locataire_contacter"], []),
    ("baux", "Baux, états des lieux et garants", ["patrimoine.bail", "patrimoine.etatdeslieux", "patrimoine.garant"],
     ["document_bail", "document_edl", "document_edl_vierge", "document_inventaire", "acte_cautionnement", "remplir_modele"], []),
    ("loyers", "Loyers, quittances, relances et dépôts", ["patrimoine.echeance", "patrimoine.paiement", "patrimoine.revisionloyer",
                                                          "patrimoine.mouvementdepot", "patrimoine.relance"],
     ["document_echeance", "envoyer_quittance", "preparer_mail_quittance", "recu_depot_mouvement", "recu_depot_bail",
      "document_recu_depot", "revision_loyer", "lettre_revision", "admin:patrimoine_echeance_generer",
      "admin:patrimoine_echeance_encaisser", "relances_impayes", "lettre_relance", "preparer_mail_relance", "depot_bail"],
     ["revision_loyer", "admin:patrimoine_echeance_generer", "admin:patrimoine_echeance_encaisser", "lettre_relance",
      "preparer_mail_relance", "depot_bail"]),
    ("relocation", "Relocation (candidatures, annonces)", ["patrimoine.candidature", "patrimoine.garantcandidature",
                                                         "patrimoine.piececandidature", "patrimoine.annonce",
                                                         "patrimoine.changementlocataire", "patrimoine.tache"],
     ["preparer_mail_candidature", "envoyer_lien_candidature", "photos_zip", "admin:patrimoine_annonce_modele_du_lot", "annonce_pdf",
      "tache_faite", "admin:patrimoine_candidature_retenir"],
     ["envoyer_lien_candidature", "tache_faite", "admin:patrimoine_candidature_retenir"]),
    ("lcd", "Courte durée (Airbnb / Booking)", ["patrimoine.reservationlcd", "patrimoine.chargelcd", "patrimoine.parametreslcd",
                                               "patrimoine.encaissementlcd", "patrimoine.comparatifnue", "patrimoine.lignecomparatif"],
     ["suivi_lcd", "comparatif_lcd"], []),
    ("finances", "Encaissements, prêts et endettement", ["patrimoine.pret", "patrimoine.encaissementhistorique"],
     ["suivi_encaissements", "etat_endettement", "document_amortissement"], []),
    ("compta", "Comptabilité (revenus fonciers, LMNP, société)", ["patrimoine.piececomptable"],
     ["annexe_2072", "recap_2044", "compta_lmnp", "compta_societe", "conciergerie_ajouter", "controle_conciergerie",
      "admin:patrimoine_piececomptable_import"], ["admin:patrimoine_piececomptable_import"]),
    ("etats", "États, bilan LMNP et plus-value", ["patrimoine.chargelmnp"],
     ["etat_loyers_encaisses", "etat_loyers_detail", "biens_lmnp", "plus_value"], []),
    ("outils", "Rappels, contrôles, modèles (location, annonces) et sites utiles", ["patrimoine.rappel", "patrimoine.journalfait",
                                                         "patrimoine.modeledocument", "patrimoine.modeleannonce",
                                                         "patrimoine.siteutile", "patrimoine.controleignore"],
     ["liste_champs", "rappel_fait", "ne_plus_rappeler", "controles", "controle_ignorer", "a_faire"],
     ["rappel_fait", "ne_plus_rappeler", "controle_ignorer"]),
    ("balles", "Balles", ["commandes.joueur", "commandes.article", "commandes.commande", "commandes.ligne"], [], []),
]
LIBELLES = {code: libelle for code, libelle, *_ in MODULES}
RESERVE_ADMIN = {"sauvegardes", "telecharger_sauvegarde", "tester_mail"}  # Réglages : administrateurs seulement
# pièces privées : le dossier dit à quel module elles appartiennent
DOSSIERS_PRIVES = {"factures/": ["compta"], "modeles-word/": ["outils"], "modeles-annonces/": ["outils"],
                   "pieces-identite/": ["locataires", "baux", "relocation"]}


def _tables(code):
    return next(m[2] for m in MODULES if m[0] == code)


def niveau(user, code):
    """Niveau de l'utilisateur sur un module (les administrateurs ont tout)."""
    if not user.is_authenticated or not user.is_active:
        return AUCUN
    if user.is_superuser:
        return MODIFICATION
    app, modele = _tables(code)[0].split(".")
    if user.has_perm(f"{app}.change_{modele}"):
        return MODIFICATION
    if user.has_perm(f"{app}.view_{modele}"):
        return CONSULTATION
    return AUCUN


def niveaux(user):
    return {code: niveau(user, code) for code, *_ in MODULES}


def contexte(request):
    """`droits` dans les gabarits : {code: niveau} ; `droits.admin` vrai pour un administrateur."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"droits": {**niveaux(user), "admin": user.is_superuser}}


def appliquer(user, choix):
    """Remplace les permissions de l'utilisateur par celles des niveaux choisis {code: niveau}."""
    from django.contrib.auth.models import Permission

    perms = []
    for code, *_ in MODULES:
        actions = {CONSULTATION: ["view"], MODIFICATION: ["view", "add", "change", "delete"]}.get(choix.get(code), [])
        for table in _tables(code):
            app, modele = table.split(".")
            perms += list(Permission.objects.filter(content_type__app_label=app, content_type__model=modele,
                                                    codename__in=[f"{a}_{modele}" for a in actions]))
    user.user_permissions.set(perms)
    user.groups.clear()


def _module_de_vue(nom):
    for code, _, _, vues, modifient in MODULES:
        if nom in vues:
            return code, nom in modifient
    return None, False


class DroitsMiddleware:
    """Applique les droits aux pages faites à la main et aux envois de formulaires dans l'administration."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, vue, args, kwargs):
        user = getattr(request, "user", None)
        match = request.resolver_match
        if user is None or not user.is_authenticated or user.is_superuser or match is None:
            return None
        nom = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
        ecriture = request.method not in ("GET", "HEAD", "OPTIONS")
        if match.namespace == "balles":
            exige = MODIFICATION if ecriture or match.url_name.endswith(("_creer", "_modifier")) else CONSULTATION
            return self._verifier(user, ["balles"], exige)
        if match.url_name in RESERVE_ADMIN or request.path.startswith(("/admin/auth/", "/admin/patrimoine/parametresmail/", "/admin/patrimoine/tachepardefaut/")):
            raise PermissionDenied("Réservé aux administrateurs.")
        code, modifie = _module_de_vue(nom)
        if code:
            return self._verifier(user, [code], MODIFICATION if ecriture or modifie else CONSULTATION)
        if match.url_name == "fichier_prive":
            chemin = kwargs.get("chemin", "")
            codes = next((c for d, c in DOSSIERS_PRIVES.items() if chemin.startswith(d)), None)
            return self._verifier(user, codes, CONSULTATION) if codes else self._refuser()
        if ecriture and request.path.startswith("/admin/"):
            # listes (actions groupées, saisie dans la liste) : il faut pouvoir modifier la table
            morceaux = request.path.strip("/").split("/")
            if len(morceaux) >= 3 and morceaux[1] in ("patrimoine", "commandes"):
                app, modele = morceaux[1], morceaux[2]
                if not any(user.has_perm(f"{app}.{a}_{modele}") for a in ("add", "change", "delete")):
                    self._refuser()
        return None

    @staticmethod
    def _refuser():
        raise PermissionDenied("Vous n'avez pas accès à cette page.")

    def _verifier(self, user, codes, exige):
        ordre = [AUCUN, CONSULTATION, MODIFICATION]
        if not any(ordre.index(niveau(user, c)) >= ordre.index(exige) for c in codes):
            self._refuser()
        return None
