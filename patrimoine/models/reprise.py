"""Reprise des données d'un ancien logiciel ou d'un expert-comptable : import d'un FEC (fichier des écritures comptables,
article A47 A-1 du livre des procédures fiscales). Le traitement est dans patrimoine/fec.py."""
from django.db import models

from .communs import stockage_prive


def _chemin_fec(instance, nom):
    import secrets

    return f"fec/{secrets.token_hex(6)}-{nom}"


class ImportFEC(models.Model):
    """Un FEC déposé pour un dossier comptable : correspondance des comptes, simulation, puis enregistrement.
    Ce qui a été créé garde un lien vers l'import, pour pouvoir l'annuler."""

    class Etat(models.TextChoices):
        BROUILLON = "brouillon", "En préparation"
        IMPORTE = "importe", "Importé"
        ANNULE = "annule", "Annulé"

    dossier = models.CharField(max_length=30, help_text="Code du dossier comptable : 2031, 2072-<id>, societe-<id>")
    fichier = models.FileField(upload_to=_chemin_fec, storage=stockage_prive)
    nom_fichier = models.CharField("fichier", max_length=200)
    empreinte = models.CharField(max_length=64, db_index=True)
    siren = models.CharField("SIREN", max_length=9, blank=True)
    debut = models.DateField("première écriture", null=True, blank=True)
    fin = models.DateField("dernière écriture", null=True, blank=True)
    date_cloture = models.DateField("clôture de l'exercice", null=True, blank=True)
    nb_lignes = models.PositiveIntegerField(default=0)
    etat = models.CharField(max_length=10, choices=Etat.choices, default=Etat.BROUILLON)
    correspondances = models.JSONField(default=dict, blank=True,
                                       help_text="{compte: {cible, bien}} pour les charges et les recettes")
    reglages_immos = models.JSONField(default=dict, blank=True, help_text="{compte: réglages de la fiche à créer}")
    options = models.JSONField(default=dict, blank=True)
    resume = models.JSONField(default=dict, blank=True, help_text="Ce qui a été créé à l'enregistrement")
    cree_le = models.DateTimeField(auto_now_add=True)
    importe_le = models.DateTimeField(null=True, blank=True)
    annule_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "import FEC"
        verbose_name_plural = "imports FEC"
        ordering = ["-cree_le"]

    def __str__(self):
        return f"FEC {self.nom_fichier} ({self.get_etat_display().lower()})"


class SoldeFEC(models.Model):
    """Solde d'un compte de bilan (classes 1 à 5) à la fin de l'exercice importé : capital, emprunts, comptes courants
    d'associés, fournisseurs, banque. L'appli ne tient pas de bilan : ces soldes sont gardés pour mémoire et contrôlés."""

    import_fec = models.ForeignKey(ImportFEC, on_delete=models.CASCADE, related_name="soldes")
    compte = models.CharField(max_length=20)
    libelle = models.CharField("libellé", max_length=200, blank=True)
    debit = models.DecimalField("total débit (€)", max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField("total crédit (€)", max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "solde repris du FEC"
        verbose_name_plural = "soldes repris du FEC"
        ordering = ["import_fec", "compte"]

    def __str__(self):
        return f"{self.compte} {self.libelle} : {self.solde} €"

    @property
    def solde(self):
        return self.debit - self.credit
