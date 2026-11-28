"""Biens confiés à une agence : récapitulatif annuel de l'agence (loyers encaissés, charges payées, honoraires, acomptes
versés, solde). Le calcul et l'application du récap sont dans patrimoine/agence.py."""
from django.db import models

from .biens import AgenceGestion, Bien, Proprietaire
from .communs import stockage_prive
from .finances import PieceComptable
from .loyers import Echeance


def _chemin_recap(instance, nom):
    return f"agences/recaps/{nom}"


class RecapAgence(models.Model):
    """Récapitulatif de fin d'année d'une agence pour un propriétaire : tous les biens que l'agence gère pour lui."""

    agence = models.ForeignKey(AgenceGestion, on_delete=models.CASCADE, related_name="recaps")
    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.CASCADE, related_name="recaps_agence", verbose_name="propriétaire")
    annee = models.PositiveSmallIntegerField("année")
    acomptes = models.DecimalField(
        "total des acomptes versés selon l'agence (€)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Somme des virements mensuels indiquée sur le récap")
    solde_annonce = models.DecimalField(
        "solde annoncé par l'agence (€)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Positif : l'agence vous doit ce montant (virement de régularisation). Négatif : vous devez le lui rendre.")
    date_solde = models.DateField("date du virement de régularisation", null=True, blank=True)
    ligne_solde = models.ForeignKey("LigneReleve", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                    verbose_name="ligne du relevé du solde")
    fichier = models.FileField("récap de l'agence (PDF)", upload_to=_chemin_recap, storage=stockage_prive, blank=True)
    applique_le = models.DateTimeField("appliqué le", null=True, blank=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "récap annuel d'agence"
        verbose_name_plural = "récaps annuels d'agence"
        ordering = ["-annee", "agence__nom"]
        constraints = [models.UniqueConstraint(fields=["agence", "proprietaire", "annee"], name="recap_agence_unique")]

    def __str__(self):
        return f"Récap {self.annee} – {self.agence} – {self.proprietaire}"


class LigneRecapAgence(models.Model):
    """Loyer d'un mois selon le récap : ce que l'agence a réellement encaissé (0 = non encaissé, le loyer redevient impayé)."""

    recap = models.ForeignKey(RecapAgence, on_delete=models.CASCADE, related_name="lignes")
    echeance = models.OneToOneField(Echeance, on_delete=models.CASCADE, related_name="ligne_recap_agence", verbose_name="loyer")
    montant = models.DecimalField("encaissé par l'agence (€)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "loyer du récap d'agence"
        verbose_name_plural = "loyers du récap d'agence"


class ChargeRecapAgence(models.Model):
    """Charge payée par l'agence (taxe foncière, syndic, travaux…) ou sa facture de gestion, retenue sur les loyers."""

    class Nature(models.TextChoices):
        HONORAIRES = "honoraires", "Honoraires de gestion de l'agence"
        CHARGE = "charge", "Charge payée par l'agence"

    recap = models.ForeignKey(RecapAgence, on_delete=models.CASCADE, related_name="charges")
    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name="charges_agence")
    nature = models.CharField(max_length=12, choices=Nature.choices, default=Nature.CHARGE)
    libelle = models.CharField("objet", max_length=200, blank=True)
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2)
    code = models.CharField("ligne ou rubrique", max_length=15, blank=True,
                            help_text="Ligne de la 2072 / 2044 (revenus fonciers) ou rubrique (LMNP, société)")
    piece = models.ForeignKey(PieceComptable, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", editable=False,
                              verbose_name="dépense créée")

    class Meta:
        verbose_name = "charge du récap d'agence"
        verbose_name_plural = "charges du récap d'agence"
