"""Rappels, sites utiles, modèles de documents, paramètres des mails, journal et notes rapides."""
from django.core.validators import FileExtensionValidator
from django.db import models

from .communs import _ajouter_mois, stockage_prive
from .biens import Bien


class SiteUtile(models.Model):
    """Lien affiché dans le menu « Sites utiles »."""

    nom = models.CharField(max_length=60, help_text="Texte affiché dans le menu")
    url = models.URLField("adresse du site", max_length=500)
    ordre = models.PositiveSmallIntegerField(default=0, help_text="Les plus petits en premier")

    class Meta:
        verbose_name = "site utile"
        verbose_name_plural = "sites utiles"
        ordering = ["ordre", "nom"]

    def __str__(self):
        return self.nom


class Rappel(models.Model):
    """Rappel saisi à la main (AG de copropriété, taxe foncière, contrôle…), ponctuel ou périodique."""

    class Periodicite(models.TextChoices):
        PONCTUEL = "ponctuel", "Une seule fois"
        MENSUEL = "mensuel", "Tous les mois"
        TRIMESTRIEL = "trimestriel", "Tous les trimestres"
        ANNUEL = "annuel", "Tous les ans"

    MOIS_PAR_PERIODE = {"mensuel": 1, "trimestriel": 3, "annuel": 12}

    class Categorie(models.TextChoices):
        AG = "ag", "Assemblée générale"
        FISCAL = "fiscal", "Impôts et déclarations"
        TRAVAUX = "travaux", "Travaux / entretien"
        ADMINISTRATIF = "administratif", "Administratif"
        AUTRE = "autre", "Autre"

    titre = models.CharField(max_length=200)
    date = models.DateField()
    categorie = models.CharField("catégorie", max_length=20, choices=Categorie.choices, default=Categorie.AUTRE)
    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, null=True, blank=True, related_name="rappels")
    periodicite = models.CharField("périodicité", max_length=12, choices=Periodicite.choices, default=Periodicite.PONCTUEL)
    important = models.BooleanField(
        "★ important", default=False,
        help_text="Paiement ou date limite à ne pas manquer : le rappel passe en tête de l'accueil et vous recevez un mail "
                  "quelques jours avant (Réglages › Mails).")
    prevenir_jours = models.PositiveSmallIntegerField(
        "prévenir … jours avant", null=True, blank=True,
        help_text="Le rappel apparaît dans « À faire » (et le mail part s'il est important) ce nombre de jours avant la date. "
                  "Vide : 30 jours avant, et mail 7 jours avant.")
    actif = models.BooleanField(default=True, help_text="Décocher pour arrêter un rappel périodique sans le supprimer")
    fait = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    dernier_mail = models.DateField("dernier mail envoyé le", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "rappel"
        ordering = ["fait", "-important", "date"]

    MAIL_JOURS_PAR_DEFAUT = 7

    @property
    def date_mail(self):
        """Jour où part le mail d'un rappel important."""
        from datetime import timedelta

        return self.date - timedelta(days=self.MAIL_JOURS_PAR_DEFAUT if self.prevenir_jours is None else self.prevenir_jours)

    def mail_a_envoyer(self, aujourdhui):
        """Un mail quelques jours avant, puis un le jour même si ce n'est toujours pas fait (un seul par étape)."""
        if not (self.important and self.actif and not self.fait):
            return None
        if aujourdhui >= self.date and (self.dernier_mail is None or self.dernier_mail < self.date):
            return "jour"
        if self.date_mail <= aujourdhui < self.date and (self.dernier_mail is None or self.dernier_mail < self.date_mail):
            return "avant"
        return None

    def __str__(self):
        return self.titre

    def marquer_fait(self):
        """Un rappel périodique passe à l'échéance suivante ; un rappel ponctuel est clos."""
        if self.periodicite in self.MOIS_PAR_PERIODE:
            self.date = _ajouter_mois(self.date, self.MOIS_PAR_PERIODE[self.periodicite])
        else:
            self.fait = True
        self.save()


class JournalFait(models.Model):
    """Trace des rappels cochés « Fait » depuis l'accueil, pour le filtre « Déjà fait »."""

    date = models.DateField(auto_now_add=True)
    titre = models.CharField(max_length=200)
    detail = models.CharField("détail", max_length=255, blank=True)
    categorie = models.CharField("catégorie", max_length=50)
    url = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "rappel fait"
        verbose_name_plural = "rappels faits"
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.titre} ({self.date:%d/%m/%Y})"


class ParametresMail(models.Model):
    """Envoi des mails depuis l'application, par votre compte Gmail (un seul réglage)."""

    adresse = models.EmailField("adresse Gmail d'envoi", help_text="Les mails partiront de cette adresse")
    mot_de_passe = models.CharField(
        "mot de passe d'application", max_length=40,
        help_text="Code de 16 lettres créé sur myaccount.google.com/apppasswords (validation en 2 étapes obligatoire). "
                  "Ce n'est pas le mot de passe de votre compte Google.")
    nom_expediteur = models.CharField("nom affiché", max_length=100, blank=True, help_text="ex. « Fabien Hunault »")
    alerte_nouveau_dossier = models.BooleanField("me prévenir à chaque dossier de candidature reçu", default=True)
    alerte_rappels = models.BooleanField(
        "m'envoyer les rappels importants (★) par mail", default=True,
        help_text="Quelques jours avant la date limite, puis le jour même si ce n'est pas fait. Nécessite la tâche quotidienne "
                  "de PythonAnywhere (onglet Tasks).")
    accuse_au_candidat = models.BooleanField("envoyer un accusé de réception au candidat", default=True)

    class Meta:
        verbose_name = "réglage des mails"
        verbose_name_plural = "réglage des mails"

    def __str__(self):
        return f"Envoi par {self.adresse}"

    def save(self, *args, **kwargs):
        self.pk = 1  # un seul réglage
        self.mot_de_passe = self.mot_de_passe.replace(" ", "")
        super().save(*args, **kwargs)


class ModeleDocument(models.Model):
    """Modèle Word de l'utilisateur : les champs {Nom_Loc}, {Loyer}… sont remplis à partir du bail (Modèles › Liste des champs)."""

    class Type(models.TextChoices):
        BAIL_NU = "bail_nu", "Bail de location nue"
        BAIL_MEUBLE = "bail_meuble", "Bail de location meublée"
        CAUTION = "caution", "Acte de cautionnement"
        EDL = "edl", "État des lieux (entrée / sortie)"
        INVENTAIRE = "inventaire", "Inventaire du mobilier"
        RECU_DEPOT = "recu_depot", "Reçu du dépôt de garantie"
        AUTRE = "autre", "Autre document lié au bail"

    nom = models.CharField(max_length=150)
    type = models.CharField("type de document", max_length=15, choices=Type.choices,
                            help_text="Détermine quand le modèle est proposé : un bail meublé n'apparaît que sur les baux meublés, etc.")
    fichier = models.FileField("fichier Word (.docx)", upload_to="modeles-word/", storage=stockage_prive,
                               validators=[FileExtensionValidator(["docx"], "Enregistrez le modèle au format Word .docx.")],
                               help_text="Écrivez les champs entre accolades, ex. {Nom_Loc}. Liste complète : Modèles › Liste des champs.")
    actif = models.BooleanField(default=True, help_text="Décochez pour ne plus le proposer sans le supprimer")
    notes = models.TextField(blank=True)
    modifie_le = models.DateTimeField("modifié le", auto_now=True)

    TYPES_BAIL = {"bail_nu": ["nu"], "bail_meuble": ["meuble", "etudiant", "mobilite"], "inventaire": ["meuble", "etudiant", "mobilite"]}

    class Meta:
        verbose_name = "modèle de location"
        verbose_name_plural = "modèles de location (documents contractuels)"
        ordering = ["type", "nom"]

    def __str__(self):
        return self.nom

    def convient(self, bail):
        types = self.TYPES_BAIL.get(self.type)
        return types is None or bail.type in types


class NoteRapide(models.Model):
    """Bloc-notes du tableau de bord : une note par utilisateur, enregistrée au fil de la frappe."""

    utilisateur = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="note_rapide")
    texte = models.TextField(blank=True)
    modifie_le = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "note rapide"
        verbose_name_plural = "notes rapides"

    def __str__(self):
        return f"Notes de {self.utilisateur}"
