"""Échéances de loyer, paiements, dépôts de garantie, relances et loyers repris d'un ancien suivi."""
import calendar
from datetime import date
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

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
    def document(self):
        """(code, libellé) du PDF à remettre : quittance si soldé, reçu si payé en partie (obligatoire), sinon avis d'échéance."""
        if self.statut == self.Statut.PAYE:
            return "quittance", "Quittance"
        if self.statut == self.Statut.PARTIEL:
            return "recu-partiel", "Reçu de paiement partiel"
        return "avis-echeance", "Avis d'échéance"

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
            if cree:
                Versement.imputer_avances(echeance)
            else:
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


class ModePaiement(models.TextChoices):
    VIREMENT = "virement", "Virement"
    PRELEVEMENT = "prelevement", "Prélèvement"
    CHEQUE = "cheque", "Chèque"
    ESPECES = "especes", "Espèces"
    CAF = "caf", "CAF / APL"


class Versement(models.Model):
    """Virement (ou chèque…) reçu d'un locataire, réparti sur un ou plusieurs loyers.

    Chaque part est un paiement rattaché à un loyer ; ce qui n'est affecté à aucun loyer reste en avance
    et s'impute tout seul sur les loyers suivants, dès qu'ils sont créés."""

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="versements")
    date = models.DateField("date de réception")
    montant = models.DecimalField("montant reçu (€)", max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20, choices=ModePaiement.choices, default=ModePaiement.VIREMENT)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "virement reçu"
        verbose_name_plural = "virements reçus"
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.get_mode_display()} de {self.montant} € le {self.date:%d/%m/%Y} – {self.bail.lot}"

    @property
    def montant_reparti(self):
        return self.paiements.aggregate(s=models.Sum("montant"))["s"] or 0

    @property
    def avance(self):
        """Partie du versement pas encore affectée à un loyer (trop-perçu gardé pour les loyers suivants)."""
        return self.montant - self.montant_reparti

    @staticmethod
    def repartition_proposee(bail, montant, exclure_versement=None):
        """[(loyer, part)] : le montant couvre d'abord le loyer non soldé le plus ancien, puis le suivant…"""
        from decimal import Decimal
        reste = Decimal(montant)
        parts = []
        for e in bail.echeances.exclude(statut=Echeance.Statut.PAYE).order_by("periode"):
            du = e.reste_du
            if du <= 0:
                continue
            part = min(du, reste) if reste > 0 else Decimal(0)
            parts.append((e, part))
            reste -= part
        return parts

    def repartir(self, parts):
        """Enregistre les parts choisies [(loyer, montant)] comme paiements liés à ce versement."""
        for echeance, part in parts:
            if part and part > 0:
                Paiement.objects.create(echeance=echeance, versement=self, date=self.date, montant=part, mode=self.mode,
                                        notes=self.notes or f"{self.get_mode_display()} du {self.date:%d/%m/%Y}")

    @classmethod
    def imputer_avances(cls, echeance):
        """Nouveau loyer : utilise les avances (trop-perçus) du bail, de la plus ancienne à la plus récente."""
        for v in cls.objects.filter(bail=echeance.bail).order_by("date", "pk"):
            du = echeance.reste_du
            if du <= 0:
                break
            avance = v.avance
            if avance > 0:
                Paiement.objects.create(echeance=echeance, versement=v, date=v.date, montant=min(avance, du), mode=v.mode,
                                        notes=f"Avance du {v.get_mode_display().lower()} du {v.date:%d/%m/%Y}")


class Paiement(models.Model):
    Mode = ModePaiement

    echeance = models.ForeignKey(Echeance, on_delete=models.CASCADE, related_name="paiements", verbose_name="échéance")
    versement = models.ForeignKey(Versement, on_delete=models.CASCADE, null=True, blank=True, related_name="paiements",
                                  verbose_name="virement", help_text="Virement reçu dont ce paiement est une part")
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



@receiver(post_delete, sender=Paiement)
def _statut_apres_suppression(sender, instance, **kwargs):
    """Paiement supprimé (seul, ou avec le virement dont il est une part) : le statut du loyer est recalculé."""
    echeance = Echeance.objects.filter(pk=instance.echeance_id).first()
    if echeance:
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
