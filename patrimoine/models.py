import calendar

from django.db import models


class Proprietaire(models.Model):
    """Entité qui détient des biens : nom propre, SCI, SARL de famille..."""

    class Type(models.TextChoices):
        NOM_PROPRE = "nom_propre", "Nom propre"
        SCI_IR = "sci_ir", "SCI à l'IR"
        SCI_IS = "sci_is", "SCI à l'IS"
        SARL_FAMILLE = "sarl_famille", "SARL de famille"
        AUTRE = "autre", "Autre"

    nom = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=Type.choices)
    siret = models.CharField("SIRET", max_length=14, blank=True)
    adresse = models.TextField(blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "propriétaire"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Bien(models.Model):
    """Immeuble ou adresse physique, qui contient un ou plusieurs lots."""

    proprietaire = models.ForeignKey(
        Proprietaire, on_delete=models.PROTECT, related_name="biens", verbose_name="propriétaire"
    )
    nom = models.CharField(max_length=200, help_text="Nom court, ex. « Immeuble rue Victor Hugo »")
    adresse = models.CharField(max_length=255)
    code_postal = models.CharField(max_length=10)
    ville = models.CharField(max_length=100)
    date_acquisition = models.DateField("date d'acquisition", null=True, blank=True)
    prix_acquisition = models.DecimalField(
        "prix d'acquisition (€)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["proprietaire__nom", "nom"]

    def __str__(self):
        return f"{self.nom} ({self.ville})"


class Lot(models.Model):
    """Unité louable : appartement, maison, parking, local..."""

    class Type(models.TextChoices):
        APPARTEMENT = "appartement", "Appartement"
        MAISON = "maison", "Maison"
        STUDIO = "studio", "Studio"
        PARKING = "parking", "Parking / garage"
        CAVE = "cave", "Cave"
        LOCAL_COMMERCIAL = "local_commercial", "Local commercial"
        BUREAU = "bureau", "Bureau"

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="lots")
    designation = models.CharField("désignation", max_length=100, help_text="ex. « Apt 2e gauche »")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.APPARTEMENT)
    surface = models.DecimalField("surface (m²)", max_digits=7, decimal_places=2, null=True, blank=True)
    nb_pieces = models.PositiveSmallIntegerField("nombre de pièces", null=True, blank=True)
    meuble = models.BooleanField("meublé", default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["bien__nom", "designation"]

    def __str__(self):
        return f"{self.bien.nom} – {self.designation}"

    @property
    def bail_actif(self):
        return self.baux.filter(actif=True).first()


class Locataire(models.Model):
    civilite = models.CharField("civilité", max_length=10, blank=True, choices=[("M.", "M."), ("Mme", "Mme")])
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    garant = models.CharField(max_length=200, blank=True, help_text="Nom et coordonnées du garant")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["nom", "prenom"]

    def __str__(self):
        return f"{self.civilite} {self.prenom} {self.nom}".strip()


class Bail(models.Model):
    class Type(models.TextChoices):
        NU = "nu", "Location nue (3 ans)"
        MEUBLE = "meuble", "Location meublée (1 an)"
        ETUDIANT = "etudiant", "Meublé étudiant (9 mois)"
        MOBILITE = "mobilite", "Bail mobilité"
        COMMERCIAL = "commercial", "Bail commercial (3-6-9)"
        PROFESSIONNEL = "professionnel", "Bail professionnel"
        PARKING = "parking", "Location de parking"

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="baux")
    locataires = models.ManyToManyField(Locataire, related_name="baux")
    type = models.CharField(max_length=20, choices=Type.choices)
    date_debut = models.DateField("date de début")
    date_fin = models.DateField("date de fin", null=True, blank=True)
    loyer_hc = models.DecimalField("loyer hors charges (€)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("provision sur charges (€)", max_digits=10, decimal_places=2, default=0)
    depot_garantie = models.DecimalField("dépôt de garantie (€)", max_digits=10, decimal_places=2, default=0)
    jour_echeance = models.PositiveSmallIntegerField("jour d'échéance", default=1)
    indice_reference = models.CharField(
        "indice de référence (IRL/ILC)", max_length=50, blank=True, help_text="ex. « IRL T2 2026 : 146,12 »"
    )
    actif = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "bail"
        verbose_name_plural = "baux"
        ordering = ["-actif", "lot__bien__nom", "-date_debut"]

    def __str__(self):
        return f"{self.lot} – {self.get_type_display()} depuis le {self.date_debut:%d/%m/%Y}"

    @property
    def loyer_cc(self):
        return self.loyer_hc + self.charges


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
            _, cree = cls.objects.get_or_create(
                bail=bail,
                periode=periode,
                defaults={
                    "loyer_hc": bail.loyer_hc,
                    "charges": bail.charges,
                    "date_exigibilite": periode.replace(day=jour),
                },
            )
            crees += cree
        return crees


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
