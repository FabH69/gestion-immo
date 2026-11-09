"""Échéances de loyer, paiements, dépôts de garantie, relances et loyers repris d'un ancien suivi."""
import calendar
from datetime import date
from django.db import models

from .biens import Lot
from .location import Bail


class Echeance(models.Model):
    """Loyer dû pour un mois donné sur un bail."""

    class Statut(models.TextChoices):
        A_PAYER = "a_payer", "À payer"
        PARTIEL = "partiel", "Payé partiellement"
        PAYE = "paye", "Payé"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="echeances")
    periode = models.DateField("période", help_text="Premier jour du mois concerné")
    loyer_hc = models.DecimalField("loyer HC (€)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("charges (€)", max_digits=10, decimal_places=2, default=0)
    date_exigibilite = models.DateField("date d'exigibilité")
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.A_PAYER, editable=False)
    ajustee = models.BooleanField("montant modifié à la main", default=False, editable=False,
                                  help_text="Le loyer ou les charges ont été corrigés à la main : ils ne sont plus recalculés automatiquement")

    class Meta:
        verbose_name = "loyer"
        verbose_name_plural = "loyers"
        ordering = ["-periode", "bail__lot__bien__nom"]
        constraints = [models.UniqueConstraint(fields=["bail", "periode"], name="echeance_unique_par_mois")]

    def __str__(self):
        return f"{self.bail.lot} – {self.periode:%m/%Y}"

    @property
    def total(self):
        return self.loyer_hc + self.charges

    @property
    def montant_paye(self):
        return self.paiements.aggregate(s=models.Sum("montant"))["s"] or 0

    @property
    def reste_du(self):
        return self.total - self.montant_paye

    @property
    def fin_periode(self):
        return self.periode.replace(day=calendar.monthrange(self.periode.year, self.periode.month)[1])

    def mettre_a_jour_statut(self):
        paye = self.montant_paye
        if paye >= self.total:
            self.statut = self.Statut.PAYE
        elif paye > 0:
            self.statut = self.Statut.PARTIEL
        else:
            self.statut = self.Statut.A_PAYER
        self.save(update_fields=["statut"])

    @classmethod
    def generer_pour_mois(cls, periode):
        """Crée l'échéance du mois pour chaque bail actif ; renvoie le nombre créé."""
        periode = periode.replace(day=1)
        fin = periode.replace(day=calendar.monthrange(periode.year, periode.month)[1])
        crees = 0
        baux = Bail.objects.filter(actif=True, date_debut__lte=fin).exclude(date_fin__lt=periode)
        for bail in baux:
            jour = min(bail.jour_echeance, fin.day)
            loyer, charges = bail.montants_du_mois(periode)
            echeance, cree = cls.objects.get_or_create(
                bail=bail,
                periode=periode,
                defaults={
                    "loyer_hc": loyer,
                    "charges": charges,
                    "date_exigibilite": periode.replace(day=jour),
                },
            )
            crees += cree
            if not cree:
                echeance.actualiser_loyer()
        return crees

    def actualiser_loyer(self):
        """Échéance pas encore réglée ni corrigée à la main : reprend le loyer en vigueur ce mois-là (après une révision)
        et les charges du bail."""
        if self.ajustee or self.paiements.exists():
            return False
        loyer, charges = self.bail.montants_du_mois(self.periode)
        if (self.loyer_hc, self.charges) == (loyer, charges):
            return False
        self.loyer_hc, self.charges = loyer, charges
        self.save(update_fields=["loyer_hc", "charges"])
        return True


class Paiement(models.Model):
    class Mode(models.TextChoices):
        VIREMENT = "virement", "Virement"
        PRELEVEMENT = "prelevement", "Prélèvement"
        CHEQUE = "cheque", "Chèque"
        ESPECES = "especes", "Espèces"
        CAF = "caf", "CAF / APL"

    echeance = models.ForeignKey(Echeance, on_delete=models.CASCADE, related_name="paiements", verbose_name="échéance")
    date = models.DateField()
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.VIREMENT)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.montant} € le {self.date:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.echeance.mettre_a_jour_statut()

    def delete(self, *args, **kwargs):
        echeance = self.echeance
        super().delete(*args, **kwargs)
        echeance.mettre_a_jour_statut()


class MouvementDepot(models.Model):
    """Compte séparé des dépôts de garantie : encaissements, retenues et restitutions, hors loyers."""

    class Type(models.TextChoices):
        ENCAISSEMENT = "encaissement", "Encaissement"
        RETENUE = "retenue", "Retenue (dégradations, charges)"
        RESTITUTION = "restitution", "Restitution au locataire"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="mouvements_depot")
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.ENCAISSEMENT)
    date = models.DateField()
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2, help_text="Toujours positif")
    mode = models.CharField(max_length=20, choices=Paiement.Mode.choices, default=Paiement.Mode.VIREMENT)
    justification = models.CharField("motif", max_length=255, blank=True, help_text="Motif de la retenue (ex. casse d'une lampe) et justificatif : devis ou facture")

    class Meta:
        verbose_name = "mouvement de dépôt de garantie"
        verbose_name_plural = "dépôts de garantie"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.get_type_display()} {self.montant} € – {self.bail.lot}"

    @property
    def montant_signe(self):
        return self.montant if self.type == self.Type.ENCAISSEMENT else -self.montant


class EncaissementHistorique(models.Model):
    """Loyer encaissé repris d'un ancien suivi (Excel) : montant du mois pour un logement, sans détail par échéance."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="encaissements_historiques")
    mois = models.DateField(help_text="Premier jour du mois")
    montant = models.DecimalField("montant encaissé (€)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("dont charges (€)", max_digits=10, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "loyer encaissé (historique)"
        verbose_name_plural = "loyers encaissés (historique)"
        ordering = ["lot", "-mois"]
        constraints = [models.UniqueConstraint(fields=["lot", "mois"], name="encaissement_historique_unique_par_mois")]

    def __str__(self):
        return f"{self.lot} – {self.mois:%m/%Y} : {self.montant} €"


class Relance(models.Model):
    """Relance d'un locataire (ou de sa caution) pour des loyers impayés : historique des courriers et mails préparés."""

    class Niveau(models.TextChoices):
        RAPPEL = "rappel", "1. Rappel amiable"
        RELANCE = "relance", "2. Relance"
        MISE_EN_DEMEURE = "mise_en_demeure", "3. Mise en demeure"
        GARANT = "garant", "Information de la caution"

    class Mode(models.TextChoices):
        MAIL = "mail", "Mail (Outlook)"
        COURRIER = "courrier", "Courrier (PDF)"

    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, related_name="relances")
    niveau = models.CharField(max_length=20, choices=Niveau.choices)
    date = models.DateField(default=date.today)
    montant = models.DecimalField("montant réclamé (€)", max_digits=10, decimal_places=2)
    detail = models.CharField("loyers concernés", max_length=300, blank=True)
    mode = models.CharField(max_length=10, choices=Mode.choices)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.get_niveau_display()} du {self.date:%d/%m/%Y} – {self.bail.lot}"
