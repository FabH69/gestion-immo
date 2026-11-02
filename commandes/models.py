from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.urls import reverse

MESSAGE_PAR_DEFAUT = (
    "Bonjour {prenom}, les balles sont arrivées ! "
    "Tu as {nb_boites} boîte(s), soit {montant} €. "
    "Remise : {lieu}. À bientôt !"
)


class Joueur(models.Model):
    prenom = models.CharField("prénom", max_length=100)
    nom = models.CharField(max_length=100, blank=True)
    telephone = models.CharField(
        "téléphone", max_length=30, blank=True, help_text="Ex. 06 12 34 56 78 ou +33612345678"
    )
    email = models.EmailField(blank=True)
    club = models.CharField(max_length=100, blank=True)
    actif = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["prenom", "nom"]

    def __str__(self):
        return f"{self.prenom} {self.nom}".strip()

    def get_absolute_url(self):
        return reverse("balles:joueur_modifier", args=[self.pk])

    @property
    def telephone_international(self):
        """Numéro au format 33612345678, utilisé pour les liens WhatsApp."""
        chiffres = "".join(c for c in self.telephone if c.isdigit())
        if self.telephone.strip().startswith("+"):
            return chiffres
        if chiffres.startswith("00"):
            return chiffres[2:]
        if chiffres.startswith("0") and len(chiffres) == 10:
            return "33" + chiffres[1:]
        return chiffres


class Commande(models.Model):
    class Statut(models.TextChoices):
        OUVERTE = "ouverte", "Ouverte (on prend les réservations)"
        PASSEE = "passee", "Passée au fournisseur"
        RECUE = "recue", "Reçue, à distribuer"
        TERMINEE = "terminee", "Terminée"

    nom = models.CharField(max_length=200, help_text="Ex. « Commande Pickblitz novembre »")
    produit = models.CharField(max_length=200, default="Boîte de balles de tennis")
    prix_boite = models.DecimalField("prix de vente d'une boîte (€)", max_digits=8, decimal_places=2)
    taux_commission = models.DecimalField(
        "ma commission (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.OUVERTE)
    date_creation = models.DateField("créée le", auto_now_add=True)
    date_limite = models.DateField("réservations jusqu'au", null=True, blank=True)
    date_reception = models.DateField("reçue le", null=True, blank=True)
    lieu_remise = models.CharField(
        "lieu / créneau de remise", max_length=255, blank=True,
        help_text="Ex. « au club samedi 10h-12h »",
    )
    message_arrivee = models.TextField(
        "message « balles arrivées »", default=MESSAGE_PAR_DEFAUT,
        help_text="Variables : {prenom}, {nom}, {nb_boites}, {montant}, {lieu}, {commande}",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_creation", "-pk"]

    def __str__(self):
        return self.nom

    def get_absolute_url(self):
        return reverse("balles:commande_detail", args=[self.pk])

    @property
    def total_boites(self):
        return self.lignes.aggregate(t=Sum("nb_boites"))["t"] or 0

    @property
    def montant_total(self):
        return self.prix_boite * self.total_boites

    @property
    def commission(self):
        return (self.montant_total * self.taux_commission / 100).quantize(Decimal("0.01"))

    @property
    def montant_encaisse(self):
        return sum((l.montant for l in self.lignes.all() if l.paye), Decimal("0"))


class Ligne(models.Model):
    """Ce qu'un joueur prend dans une commande."""

    commande = models.ForeignKey(Commande, on_delete=models.CASCADE, related_name="lignes")
    joueur = models.ForeignKey(Joueur, on_delete=models.PROTECT, related_name="lignes")
    nb_boites = models.PositiveIntegerField("nombre de boîtes", default=1)
    paye = models.BooleanField("payé", default=False)
    remis = models.BooleanField("remis", default=False)
    prevenu_le = models.DateTimeField("prévenu le", null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["joueur__prenom", "joueur__nom"]
        constraints = [
            models.UniqueConstraint(fields=["commande", "joueur"], name="une_ligne_par_joueur")
        ]

    def __str__(self):
        return f"{self.joueur} : {self.nb_boites} boîte(s)"

    @property
    def montant(self):
        return self.commande.prix_boite * self.nb_boites

    def message(self):
        c = self.commande
        try:
            return c.message_arrivee.format(
                prenom=self.joueur.prenom,
                nom=self.joueur.nom,
                nb_boites=self.nb_boites,
                montant=f"{self.montant:.2f}".replace(".", ","),
                lieu=c.lieu_remise or "à convenir",
                commande=c.nom,
            )
        except (KeyError, IndexError, ValueError):
            return c.message_arrivee
