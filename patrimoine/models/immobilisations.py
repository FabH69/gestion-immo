"""Immobilisations des biens en LMNP ou en société : une fiche par immobilisation (meuble, travaux, composant du bien),
avec son plan d'amortissement linéaire. Le calcul des dotations est dans patrimoine/amortissements.py."""
from decimal import Decimal

from django.db import models

from .biens import Bien, Lot


class Immobilisation(models.Model):
    class Categorie(models.TextChoices):
        TERRAIN = "terrain", "Terrain (non amorti)"
        GROS_OEUVRE = "gros_oeuvre", "Gros œuvre"
        FACADE = "facade", "Façade, toiture, étanchéité"
        INSTALLATIONS = "installations", "Installations techniques (électricité, plomberie, chauffage)"
        AGENCEMENTS = "agencements", "Agencements intérieurs, cuisine"
        TRAVAUX = "travaux", "Travaux d'amélioration"
        MOBILIER = "mobilier", "Mobilier"
        ELECTROMENAGER = "electromenager", "Électroménager"
        INFORMATIQUE = "informatique", "Informatique, multimédia"
        FRAIS = "frais", "Frais d'acquisition (notaire, agence)"
        AUTRE = "autre", "Autre"

    class Origine(models.TextChoices):
        FACTURE = "facture", "Facture saisie"
        REPRISE = "reprise", "Reprise (tableau du comptable)"
        DECOMPOSITION = "decomposition", "Décomposition du prix du bien"
        FEC = "fec", "Reprise d'un FEC"
        SAISIE = "saisie", "Saisie directe"

    class MotifSortie(models.TextChoices):
        VENTE = "vente", "Vente"
        REBUT = "rebut", "Mise au rebut"
        VENTE_BIEN = "vente_bien", "Vente du bien"

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="immobilisations")
    lot = models.ForeignKey(Lot, on_delete=models.SET_NULL, null=True, blank=True, related_name="immobilisations",
                            help_text="Vide : le bien entier (rattachée au premier lot en LMNP pour le bilan)")
    designation = models.CharField("désignation", max_length=200)
    categorie = models.CharField("catégorie", max_length=20, choices=Categorie.choices, default=Categorie.MOBILIER)
    numero = models.CharField("n° d'inventaire", max_length=20, blank=True, help_text="Donné automatiquement s'il est vide")
    origine = models.CharField(max_length=15, choices=Origine.choices, default=Origine.SAISIE)
    piece = models.ForeignKey("PieceComptable", on_delete=models.SET_NULL, null=True, blank=True, related_name="immobilisations",
                              verbose_name="facture")
    fournisseur = models.CharField(max_length=150, blank=True)
    date_achat = models.DateField("date d'achat", null=True, blank=True)
    mise_en_service = models.DateField("mise en service le", help_text="Point de départ de l'amortissement (prorata la 1re année)")
    valeur = models.DecimalField("valeur d'origine (€)", max_digits=12, decimal_places=2)
    duree = models.PositiveSmallIntegerField("durée (ans)", default=7, help_text="0 : non amortie (terrain)")
    # reprise : ce que le comptable a déjà amorti avant la première année tenue dans l'appli
    annee_reprise = models.PositiveSmallIntegerField("reprise à partir de", null=True, blank=True,
                                                     help_text="Première année calculée par l'appli (ex. 2026). Vide : tout est calculé "
                                                               "depuis la mise en service")
    amortissements_anterieurs = models.DecimalField("amortissements cumulés avant la reprise (€)", max_digits=12, decimal_places=2,
                                                    default=Decimal(0),
                                                    help_text="Cumul du tableau du comptable au 31/12 de l'année précédant la reprise")
    garantie_jusqua = models.DateField("garantie jusqu'au", null=True, blank=True)
    date_sortie = models.DateField("date de sortie", null=True, blank=True)
    motif_sortie = models.CharField("motif de sortie", max_length=12, choices=MotifSortie.choices, blank=True)
    prix_cession = models.DecimalField("prix de cession (€)", max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    import_fec = models.ForeignKey("ImportFEC", null=True, blank=True, on_delete=models.SET_NULL, related_name="immobilisations",
                                   editable=False, verbose_name="reprise FEC", help_text="Créée par l'import de ce FEC")

    class Meta:
        verbose_name = "immobilisation"
        verbose_name_plural = "immobilisations"
        ordering = ["bien__nom", "mise_en_service", "pk"]

    def __str__(self):
        return f"{self.designation} – {self.bien.nom}" if self.bien_id else self.designation

    def save(self, *args, **kwargs):
        if self.categorie == self.Categorie.TERRAIN:
            self.duree = 0
        super().save(*args, **kwargs)
        if not self.numero:
            self.numero = f"IMM-{self.mise_en_service:%Y}-{self.pk:03d}"
            super().save(update_fields=["numero"])

    @property
    def amortie(self):
        return self.duree > 0


class ReportAmortissements(models.Model):
    """LMNP : amortissements non déduits (l'amortissement ne peut pas créer de déficit) que le comptable reportait
    à la fin de l'année précédant la reprise ; l'appli calcule ensuite les reports année par année."""
    dossier = models.CharField(max_length=30, unique=True, default="2031")
    annee = models.PositiveSmallIntegerField("première année tenue dans l'appli")
    montant = models.DecimalField("amortissements à reporter au 31/12 de l'année précédente (€)", max_digits=12, decimal_places=2,
                                  default=Decimal(0))

    class Meta:
        verbose_name = "report d'amortissements"
        verbose_name_plural = "reports d'amortissements"

    def __str__(self):
        return f"Report {self.dossier} au 01/01/{self.annee} : {self.montant} €"
