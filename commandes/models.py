from decimal import Decimal

from django.db import models
from django.urls import reverse

MESSAGE_PAR_DEFAUT = (
    "Bonjour {prenom}, ta commande est arrivée : {detail}, soit {montant} €. "
    "Remise : {lieu}. À bientôt !"
)

DEUX_DECIMALES = Decimal("0.01")


def euros(montant):
    """12.5 -> « 12,50 »."""
    return f"{montant:.2f}".replace(".", ",")


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


class Article(models.Model):
    """Produit vendu : tube de balles, carton, raquette, accessoire..."""

    nom = models.CharField(max_length=200, help_text="Ex. « Tube de 4 balles Pulse »")
    reference = models.CharField("référence", max_length=50, blank=True)
    prix_vente = models.DecimalField("prix de vente (€)", max_digits=8, decimal_places=2)
    taux_commission = models.DecimalField(
        "ma commission (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    actif = models.BooleanField(default=True, help_text="Décochez pour ne plus le proposer dans les commandes.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return f"{self.nom} ({euros(self.prix_vente)} €)"

    def get_absolute_url(self):
        return reverse("balles:article_modifier", args=[self.pk])


class Commande(models.Model):
    class Statut(models.TextChoices):
        FERME = "ferme", "Ferme (validée)"
        PASSEE = "passee", "Passée au fournisseur"
        RECUE = "recue", "Reçue, à distribuer"
        TERMINEE = "terminee", "Terminée"

    nom = models.CharField(max_length=200, help_text="Ex. « Commande Pickblitz novembre »")
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.FERME)
    date_creation = models.DateField("créée le", auto_now_add=True)
    date_reception = models.DateField("reçue le", null=True, blank=True)
    lieu_remise = models.CharField(
        "lieu / créneau de remise", max_length=255, blank=True,
        help_text="Ex. « au club samedi 10h-12h »",
    )
    message_arrivee = models.TextField(
        "message « commande arrivée »", default=MESSAGE_PAR_DEFAUT,
        help_text="Variables : {prenom}, {nom}, {detail}, {quantite}, {montant}, {lieu}, {commande}",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_creation", "-pk"]

    def __str__(self):
        return self.nom

    def get_absolute_url(self):
        return reverse("balles:commande_detail", args=[self.pk])

    def _lignes(self):
        return list(self.lignes.all())

    @property
    def total_quantite(self):
        return sum(l.quantite for l in self._lignes())

    @property
    def nb_joueurs(self):
        return len({l.joueur_id for l in self._lignes()})

    @property
    def montant_total(self):
        return sum((l.montant for l in self._lignes()), Decimal("0"))

    @property
    def commission(self):
        return sum((l.commission for l in self._lignes()), Decimal("0"))

    @property
    def montant_encaisse(self):
        return sum((l.montant for l in self._lignes() if l.paye), Decimal("0"))

    @property
    def reste_a_encaisser(self):
        return self.montant_total - self.montant_encaisse

    @property
    def nb_joueurs_a_livrer(self):
        """Joueurs qui ont encore au moins une ligne non remise."""
        return len({l.joueur_id for l in self._lignes() if not l.remis})

    def dupliquer(self):
        """Copie la commande et ses lignes ; paiements, remises et messages repartent de zéro."""
        copie = Commande.objects.create(
            nom=f"{self.nom} (copie)", lieu_remise=self.lieu_remise,
            message_arrivee=self.message_arrivee, notes=self.notes,
        )
        Ligne.objects.bulk_create([
            Ligne(commande=copie, joueur_id=l.joueur_id, article_id=l.article_id, quantite=l.quantite,
                  prix_unitaire=l.prix_unitaire, taux_commission=l.taux_commission, notes=l.notes)
            for l in self.lignes.all()
        ])
        return copie

    def recap_articles(self):
        """Quantité totale par article : ce qu'il faut commander au fournisseur."""
        recap = {}
        for l in self.lignes.select_related("article"):
            r = recap.setdefault(l.article_id, {"article": l.article, "quantite": 0, "montant": Decimal("0")})
            r["quantite"] += l.quantite
            r["montant"] += l.montant
        return sorted(recap.values(), key=lambda r: r["article"].nom)

    def par_joueur(self):
        """Lignes regroupées par joueur, avec le message « commande arrivée » de chacun."""
        groupes = {}
        for l in self.lignes.select_related("joueur", "article"):
            groupes.setdefault(l.joueur_id, {"joueur": l.joueur, "lignes": []})["lignes"].append(l)
        resultat = []
        for g in sorted(groupes.values(), key=lambda g: str(g["joueur"])):
            lignes = g["lignes"]
            g["quantite"] = sum(l.quantite for l in lignes)
            g["montant"] = sum((l.montant for l in lignes), Decimal("0"))
            g["prevenu_le"] = min(l.prevenu_le for l in lignes) if all(l.prevenu_le for l in lignes) else None
            g["detail"] = ", ".join(f"{l.quantite} × {l.article.nom}" for l in lignes)
            g["texte"] = self._message(g)
            resultat.append(g)
        return resultat

    def _message(self, g):
        j = g["joueur"]
        try:
            return self.message_arrivee.format(
                prenom=j.prenom, nom=j.nom, detail=g["detail"], quantite=g["quantite"],
                nb_boites=g["quantite"], montant=euros(g["montant"]),
                lieu=self.lieu_remise or "à convenir", commande=self.nom,
            )
        except (KeyError, IndexError, ValueError):
            return self.message_arrivee


class Ligne(models.Model):
    """Un article pris par un joueur dans une commande."""

    commande = models.ForeignKey(Commande, on_delete=models.CASCADE, related_name="lignes")
    joueur = models.ForeignKey(Joueur, on_delete=models.PROTECT, related_name="lignes")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="lignes")
    quantite = models.PositiveIntegerField("quantité", default=1)
    prix_unitaire = models.DecimalField("prix unitaire (€)", max_digits=8, decimal_places=2)
    taux_commission = models.DecimalField(
        "commission (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    paye = models.BooleanField("payé", default=False)
    remis = models.BooleanField("remis", default=False)
    prevenu_le = models.DateTimeField("prévenu le", null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["joueur__prenom", "joueur__nom", "article__nom"]

    def __str__(self):
        return f"{self.joueur} : {self.quantite} × {self.article.nom}"

    def save(self, *args, **kwargs):
        # Prix et commission repris de l'article s'ils ne sont pas précisés
        if self.prix_unitaire is None:
            self.prix_unitaire = self.article.prix_vente
        if self._state.adding and not self.taux_commission:
            self.taux_commission = self.article.taux_commission
        super().save(*args, **kwargs)

    @property
    def montant(self):
        return self.prix_unitaire * self.quantite

    @property
    def commission(self):
        return (self.montant * self.taux_commission / 100).quantize(DEUX_DECIMALES)
