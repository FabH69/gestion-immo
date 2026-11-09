"""Location courte durée (Airbnb, Booking) et charges des biens en LMNP."""
from decimal import Decimal

from django.db import models

from .biens import Lot


class EncaissementLCD(models.Model):
    """Encaissements mensuels d'un logement en courte durée, saisis à la main (relevé de la plateforme ou de la conciergerie)."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="encaissements_lcd")
    mois = models.DateField(help_text="Premier jour du mois")
    ca = models.DecimalField("chiffre d'affaires (€)", max_digits=10, decimal_places=2, default=0)
    aircover = models.DecimalField("AirCover / indemnités (€)", max_digits=10, decimal_places=2, default=0)
    menage = models.DecimalField("ménage (€)", max_digits=10, decimal_places=2, default=0)
    commission = models.DecimalField("commission conciergerie (€)", max_digits=10, decimal_places=2, default=0)
    frais = models.DecimalField("frais (€)", max_digits=10, decimal_places=2, default=0)
    regul = models.DecimalField("régularisation (€)", max_digits=10, decimal_places=2, default=0, help_text="Positif si vous recevez, négatif si vous payez")
    notes = models.CharField(max_length=255, blank=True)

    CHAMPS = ["ca", "aircover", "menage", "commission", "frais", "regul"]

    class Meta:
        verbose_name = "encaissement courte durée"
        verbose_name_plural = "encaissements courte durée"
        ordering = ["lot", "-mois"]
        constraints = [models.UniqueConstraint(fields=["lot", "mois"], name="encaissement_lcd_unique_par_mois")]

    def __str__(self):
        return f"{self.lot} – {self.mois:%m/%Y}"

    @property
    def versement(self):
        return self.ca + self.aircover + self.regul - self.menage - self.commission - self.frais


class ChargeLMNP(models.Model):
    """Charge annuelle ou amortissement d'un bien en LMNP (saisie manuelle, pour la déclaration 2031)."""

    class Categorie(models.TextChoices):
        TAXE_FONCIERE = "taxe_fonciere", "Taxe foncière"
        CFE = "cfe", "Impôts et taxes (CFE…)"
        COPROPRIETE = "copropriete", "Charges de copropriété non récupérées"
        ASSURANCE = "assurance", "Assurance PNO"
        ENTRETIEN = "entretien", "Entretien et réparations"
        GESTION = "gestion", "Frais de gestion, comptable, CGA"
        ENERGIE = "energie", "Électricité, eau, internet (non refacturés)"
        AUTRE = "autre", "Autre charge"
        AMORTISSEMENT = "amortissement", "Amortissements (selon le comptable)"

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="charges_lmnp")
    annee = models.PositiveSmallIntegerField("année")
    categorie = models.CharField("catégorie", max_length=20, choices=Categorie.choices)
    libelle = models.CharField("précision", max_length=150, blank=True)
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "charge LMNP"
        verbose_name_plural = "charges LMNP"
        ordering = ["lot", "-annee", "categorie"]

    def __str__(self):
        return f"{self.get_categorie_display()} {self.annee} – {self.lot}"


class ReservationLCD(models.Model):
    """Réservation de location courte durée importée depuis Airbnb, Booking ou saisie (réservation directe)."""

    class Plateforme(models.TextChoices):
        AIRBNB = "airbnb", "Airbnb"
        BOOKING = "booking", "Booking"
        DIRECT = "direct", "Direct"

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="reservations_lcd")
    plateforme = models.CharField(max_length=10, choices=Plateforme.choices)
    reference = models.CharField("n° de réservation", max_length=40)
    voyageur = models.CharField(max_length=150, blank=True)
    arrivee = models.DateField("arrivée")
    depart = models.DateField("départ")
    montant_brut = models.DecimalField("montant brut (€)", max_digits=10, decimal_places=2, default=0)
    commission = models.DecimalField("commission plateforme (€)", max_digits=10, decimal_places=2, default=0)
    frais_service = models.DecimalField("frais de paiement (€)", max_digits=10, decimal_places=2, default=0)
    taxe_sejour = models.DecimalField("taxe de séjour (€)", max_digits=10, decimal_places=2, default=0)
    date_versement = models.DateField("versée le", null=True, blank=True)
    annulee = models.BooleanField("annulée", default=False)

    class Meta:
        verbose_name = "réservation courte durée"
        verbose_name_plural = "réservations courte durée"
        ordering = ["-arrivee"]
        constraints = [models.UniqueConstraint(fields=["plateforme", "reference"], name="reservation_lcd_unique")]

    def __str__(self):
        return f"{self.get_plateforme_display()} {self.reference} – {self.voyageur} ({self.arrivee:%d/%m/%Y})"

    @property
    def nuits(self):
        return (self.depart - self.arrivee).days

    @property
    def net(self):
        """Ce qui revient au propriétaire : brut moins commission, frais et taxe de séjour."""
        return self.montant_brut - self.commission - self.frais_service - self.taxe_sejour


class ParametresLCD(models.Model):
    """Frais de gestion d'un logement en courte durée, appliqués à chaque séjour ou au revenu."""

    lot = models.OneToOneField(Lot, on_delete=models.CASCADE, related_name="parametres_lcd")
    menage_par_sejour = models.DecimalField("ménage par séjour (€)", max_digits=8, decimal_places=2, default=0)
    consommables_par_sejour = models.DecimalField("consommables par séjour (€)", max_digits=8, decimal_places=2, default=0)
    commission_gestion = models.DecimalField(
        "commission de conciergerie (% du revenu net)", max_digits=5, decimal_places=2, default=0,
        help_text="Laisser 0 si vous gérez vous-même",
    )
    commission_hors_menage = models.BooleanField(
        "commission calculée après déduction du ménage", default=True,
        help_text="Comme la facture de la conciergerie : (net versé par la plateforme − ménage) × taux.")

    class Meta:
        verbose_name = "paramètres courte durée"
        verbose_name_plural = "paramètres courte durée"

    def __str__(self):
        return f"Paramètres LCD – {self.lot}"


class ChargeLCD(models.Model):
    """Charge mensuelle d'un logement en courte durée (électricité, eau, internet, assurance PNO, copropriété…)."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="charges_lcd")
    libelle = models.CharField("libellé", max_length=100)
    montant_mensuel = models.DecimalField("montant mensuel (€)", max_digits=8, decimal_places=2,
                                          help_text="Pour une charge annuelle (taxe foncière…), indiquez le montant divisé par 12")
    debut = models.DateField("à partir du", null=True, blank=True)
    fin = models.DateField("jusqu'au", null=True, blank=True)

    class Meta:
        verbose_name = "charge courte durée"
        verbose_name_plural = "charges courte durée"
        ordering = ["lot", "libelle"]

    def __str__(self):
        return f"{self.libelle} – {self.montant_mensuel} €/mois"

    def active_en(self, premier_jour, dernier_jour):
        return (not self.debut or self.debut <= dernier_jour) and (not self.fin or self.fin >= premier_jour)


class ComparatifNue(models.Model):
    """Hypothèses de location nue (revenus fonciers) d'un logement exploité en courte durée, pour les comparer."""

    TRAITEMENTS = [("charge", "À ma charge"), ("recuperable", "Récupérée sur le locataire"), ("partielle", "En partie récupérée"),
                   ("supprimee", "Supprimée en location nue")]
    # charge de la courte durée → ce qu'elle devient en location nue (modifiable logement par logement)
    TRAITEMENTS_PAR_DEFAUT = {"commission": ("supprimee", 0), "menage": ("supprimee", 0), "consommables": ("supprimee", 0),
                              "electricite": ("supprimee", 0), "fibre": ("supprimee", 0), "eau": ("recuperable", 100),
                              "comptable": ("supprimee", 0), "impots_taxes": ("supprimee", 0), "copro": ("partielle", 50),
                              "pno": ("charge", 0), "taxe_fonciere": ("charge", 0), "autre": ("charge", 0)}

    lot = models.OneToOneField(Lot, on_delete=models.CASCADE, related_name="comparatif_nue")
    loyer_hc = models.DecimalField("loyer mensuel hors charges (€)", max_digits=10, decimal_places=2, default=0)
    charges = models.DecimalField("provision mensuelle sur charges (€)", max_digits=10, decimal_places=2, default=0,
                                  help_text="Payée par le locataire : sert au calcul de la GLI")
    vacance_mois = models.DecimalField("vacance locative (mois par an)", max_digits=4, decimal_places=1, default=1)
    gli = models.DecimalField("GLI (% des loyers et charges)", max_digits=5, decimal_places=2, default=3,
                              help_text="Assurance loyers impayés ; 0 si vous n'en prenez pas")
    traitements = models.JSONField(default=dict, blank=True, help_text="{rubrique: [traitement, % récupéré]}")
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "comparatif location nue"
        verbose_name_plural = "comparatifs location nue"

    def __str__(self):
        return f"Comparatif location nue – {self.lot}"

    def traitement(self, rubrique):
        t = self.traitements.get(rubrique) or self.TRAITEMENTS_PAR_DEFAUT.get(rubrique) or ("charge", 0)
        return t[0], Decimal(str(t[1] or 0))


class LigneComparatif(models.Model):
    """Dépense ajoutée à la main dans le comparatif (ex. frais de relocation, travaux, mobilier à renouveler)."""

    comparatif = models.ForeignKey(ComparatifNue, on_delete=models.CASCADE, related_name="lignes")
    cote = models.CharField("côté", max_length=6, choices=[("nue", "Location nue"), ("courte", "Courte durée"), ("deux", "Les deux")], default="nue")
    libelle = models.CharField("libellé", max_length=100)
    montant_annuel = models.DecimalField("montant par an (€)", max_digits=10, decimal_places=2)
    recuperable = models.BooleanField("récupérable sur le locataire", default=False)

    class Meta:
        verbose_name = "dépense du comparatif"
        verbose_name_plural = "dépenses du comparatif"
        ordering = ["cote", "pk"]

    def __str__(self):
        return f"{self.libelle} ({self.montant_annuel} €/an)"
