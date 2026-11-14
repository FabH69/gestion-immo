"""Base de test : une deuxième base SQLite (db-essai.sqlite3), séparée de la vraie, pour essayer l'appli sans risque.

Le bouton « Mode test » (barre du haut) pose un cookie `base=essai` dans ce navigateur. Tant qu'il est là :
- les données de l'appli (patrimoine, balles), l'historique de l'administration et les types de contenu
  sont lus et écrits UNIQUEMENT dans la base de test (routeur ci-dessous) ;
- les comptes et les connexions restent ceux de la vraie base (on garde le même identifiant et mot de passe),
  mais leur modification est bloquée en mode test ;
- aucun mail ne part (mails.envoyer) et les loyers automatiques sont créés dans la base de test.

La base de test démarre vierge, comme une première installation. « Remettre à zéro » la recrée vierge.
Rien ici ne touche à db.sqlite3 : seul le fichier db-essai.sqlite3 est supprimé ou recréé.
"""
import contextvars
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

ESSAI = "essai"
COOKIE = "base"
# applis dont les données suivent la base choisie ; les autres (comptes, sessions) restent dans la vraie base
APPLIS_SUIVIES = {"patrimoine", "commandes", "admin", "contenttypes"}

_base = contextvars.ContextVar("base_active", default="default")
journal = logging.getLogger(__name__)


def active():
    return _base.get()


def en_essai():
    return _base.get() == ESSAI


@contextmanager
def utiliser(alias):
    jeton = _base.set(alias)
    try:
        yield
    finally:
        _base.reset(jeton)


class Routeur:
    """Envoie les données de l'appli vers la base active ; les comptes et sessions vers la vraie base."""

    def _alias(self, model):
        return active() if model._meta.app_label in APPLIS_SUIVIES else "default"

    def db_for_read(self, model, **hints):
        return self._alias(model)

    def db_for_write(self, model, **hints):
        return self._alias(model)

    def allow_relation(self, obj1, obj2, **hints):
        return True  # un enregistrement de test peut citer le compte connecté (copié dans la base de test)

    def allow_migrate(self, db, app_label, **hints):
        return True


class BaseEssaiMiddleware:
    """À placer en tout premier : choisit la base pour toute la requête, d'après le cookie de ce navigateur."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        alias = ESSAI if request.COOKIES.get(COOKIE) == ESSAI and fichier().exists() else "default"
        request.base_essai = alias == ESSAI
        with utiliser(alias):
            return self.get_response(request)


class ComptesProtegesMiddleware:
    """Après les messages : en mode test, les comptes (communs aux deux bases) ne se modifient pas."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        chemin = request.path
        if en_essai() and request.method == "POST" and (chemin.startswith("/admin/auth/") or chemin.startswith("/admin/password_change/")):
            from django.contrib import messages
            from django.shortcuts import redirect

            messages.warning(request, "En mode test, les comptes et mots de passe ne se modifient pas : reviens à tes vraies données pour le faire.")
            return redirect(chemin)
        return self.get_response(request)


def contexte(request):
    return {"mode_test": getattr(request, "base_essai", False)}


# ---------- fichiers ----------

def fichier():
    return Path(settings.DATABASES[ESSAI]["NAME"])


def _modele_vierge():
    return fichier().with_name(fichier().stem + "-vierge.sqlite3")


def _fermer():
    from django.db import connections

    connections[ESSAI].close()


def _migrer():
    from django.core.management import call_command

    with utiliser(ESSAI):
        call_command("migrate", database=ESSAI, interactive=False, verbosity=0)


def preparer():
    """Crée la base de test si elle n'existe pas (vierge), applique les mises à jour du code, copie les comptes."""
    if not fichier().exists():
        _creer_vierge()
    else:
        _migrer()
    copier_comptes()


def _creer_vierge():
    _fermer()
    modele = _modele_vierge()
    if modele.exists():
        shutil.copyfile(modele, fichier())
        _migrer()  # rapide si le code n'a pas changé depuis
    else:
        _migrer()
    _fermer()
    shutil.copyfile(fichier(), modele)  # modèle vierge, à jour, pour la prochaine remise à zéro


def copier_comptes():
    """Recopie les comptes de la vraie base dans la base de test (mêmes numéros), pour les liens vers « qui a fait quoi »."""
    from django.contrib.auth.models import User

    champs = ["id", "username", "password", "first_name", "last_name", "email", "is_staff", "is_active", "is_superuser", "date_joined"]
    with utiliser("default"):
        comptes = list(User.objects.using("default").values(*champs))
    for c in comptes:
        User.objects.using(ESSAI).update_or_create(id=c["id"], defaults={k: v for k, v in c.items() if k != "id"})


def _fichiers_de_la_base():
    """Fichiers (photos, pièces, factures…) enregistrés dans la base de test ; ceux de la vraie base ne sont jamais cités."""
    from django.apps import apps
    from django.db import models

    trouves = []
    with utiliser(ESSAI):
        for modele in apps.get_models():
            if modele._meta.app_label not in APPLIS_SUIVIES:
                continue
            for champ in modele._meta.get_fields():
                if isinstance(champ, models.FileField):
                    for nom in modele.objects.using(ESSAI).exclude(**{champ.name: ""}).values_list(champ.name, flat=True):
                        if nom:
                            trouves.append((champ.storage, nom))
    return trouves


def reinitialiser():
    """Remet la base de test vierge : ses fichiers puis la base elle-même. La vraie base n'est pas ouverte."""
    if fichier().exists():
        try:
            fichiers = _fichiers_de_la_base()
        except Exception:
            journal.exception("Liste des fichiers de la base de test impossible")
            fichiers = []
        vrais = _fichiers_vraie_base()
        for stockage, nom in fichiers:
            if (stockage.location, nom) in vrais:
                continue  # garde-fou : jamais un fichier cité par la vraie base
            try:
                stockage.delete(nom)
            except Exception:
                journal.exception("Fichier de test non supprimé : %s", nom)
        _fermer()
        fichier().unlink()
    _creer_vierge()
    copier_comptes()


def _fichiers_vraie_base():
    from django.apps import apps
    from django.db import models

    vrais = set()
    with utiliser("default"):
        for modele in apps.get_models():
            if modele._meta.app_label not in APPLIS_SUIVIES:
                continue
            for champ in modele._meta.get_fields():
                if isinstance(champ, models.FileField):
                    for nom in modele.objects.using("default").exclude(**{champ.name: ""}).values_list(champ.name, flat=True):
                        vrais.add((champ.storage.location, nom))
    return vrais


def charger_demo():
    from django.core.management import call_command

    with utiliser(ESSAI):
        call_command("donnees_demo", verbosity=0)


# ---------- migrations ----------
# Les reprises de données des migrations (RunPython) passent par le routeur : pendant « migrate --database X »,
# elles doivent lire et écrire dans la base X qu'on met à jour, pas dans la base active.
_avant_migration = []


def _debut_migration(sender, using="default", **kwargs):
    if not _avant_migration:
        _avant_migration.append(_base.set(using))


def _fin_migration(sender, **kwargs):
    if _avant_migration:
        _base.reset(_avant_migration.pop())


def brancher_signaux():
    from django.db.models.signals import post_migrate, pre_migrate

    pre_migrate.connect(_debut_migration, dispatch_uid="base_essai_debut_migration")
    post_migrate.connect(_fin_migration, dispatch_uid="base_essai_fin_migration")
