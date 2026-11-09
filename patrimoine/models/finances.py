"""Prêts et factures (comptabilité)."""
from decimal import Decimal

from django.core.validators import FileExtensionValidator
from django.db import models

from .communs import _ajouter_mois, stockage_prive
from .biens import Bien, Proprietaire


class Pret(models.Model):
    """Emprunt immobilier. L'échéancier est recalculé à partir des conditions du prêt."""

    class Type(models.TextChoices):
        AMORTISSABLE = "amortissable", "Amortissable (mensualités constantes)"
        IN_FINE = "in_fine", "In fine (intérêts seuls, capital à la fin)"

    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.PROTECT, related_name="prets", verbose_name="emprunteur")
    bien = models.ForeignKey(Bien, on_delete=models.SET_NULL, null=True, blank=True, related_name="prets", help_text="Bien financé (facultatif)")
    banque = models.CharField(max_length=100)
    reference = models.CharField("n° de prêt", max_length=50, blank=True)
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.AMORTISSABLE)
    montant = models.DecimalField("capital emprunté (€)", max_digits=12, decimal_places=2)
    taux = models.DecimalField("taux nominal annuel (%)", max_digits=5, decimal_places=3)
    duree_mois = models.PositiveSmallIntegerField("durée (mois)")
    date_premiere_echeance = models.DateField("date de la 1re échéance")
    assurance_mensuelle = models.DecimalField("assurance mensuelle (€)", max_digits=8, decimal_places=2, default=0)
    taux_assurance = models.DecimalField(
        "assurance en % du capital restant dû", max_digits=5, decimal_places=3, null=True, blank=True,
        help_text="Seulement si la banque calcule l'assurance sur le capital restant dû et l'inclut dans une échéance constante "
                  "(ex. Caisse d'Épargne) : l'assurance mensuelle ci-dessus est alors ignorée.",
    )
    capital_constate = models.DecimalField(
        "capital restant dû constaté (€)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Montant lu sur un relevé de la banque, s'il diffère du calcul (échéances reportées, remboursement "
                  "anticipé…). L'échéancier repart de ce montant à cette date, avec la même mensualité.")
    capital_constate_le = models.DateField("constaté le", null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "prêt"
        ordering = ["proprietaire__nom", "date_premiere_echeance"]

    def __str__(self):
        return f"{self.banque} {self.reference or ''} – {self.montant:.0f} €".replace("  ", " ")

    @property
    def recale(self):
        return self.capital_constate is not None and self.capital_constate_le is not None

    @property
    def date_derniere_echeance(self):
        if self.recale:
            lignes = self.echeancier()
            if lignes:
                return lignes[-1]["date"]
        return _ajouter_mois(self.date_premiere_echeance, self.duree_mois - 1)

    @staticmethod
    def _mensualite(capital, taux_mensuel, duree):
        if taux_mensuel == 0:
            return (capital / duree).quantize(Decimal("0.01"))
        return (capital * taux_mensuel / (1 - (1 + taux_mensuel) ** -duree)).quantize(Decimal("0.01"))

    @property
    def mensualite_totale(self):
        """Échéance assurance comprise (constante quand l'assurance est incluse en % du capital restant dû)."""
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            return self._mensualite(self.montant, (self.taux + self.taux_assurance) / Decimal(1200), self.duree_mois)
        return self.mensualite_hors_assurance + self.assurance_mensuelle

    @property
    def mensualite_hors_assurance(self):
        t = self.taux / Decimal(1200)
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            # Première échéance : la part d'assurance diminue ensuite avec le capital restant dû.
            return self.mensualite_totale - (self.montant * self.taux_assurance / Decimal(1200)).quantize(Decimal("0.01"))
        if self.type == self.Type.IN_FINE:
            return (self.montant * t).quantize(Decimal("0.01"))
        if t == 0:
            return (self.montant / self.duree_mois).quantize(Decimal("0.01"))
        return (self.montant * t / (1 - (1 + t) ** -self.duree_mois)).quantize(Decimal("0.01"))

    def echeancier(self):
        """Liste des échéances : (n°, date, mensualité, intérêts, capital remboursé, assurance, capital restant dû).

        Avec un capital restant dû constaté, le capital repart de ce montant après la dernière échéance passée à cette
        date ; la mensualité ne change pas et le prêt s'arrête quand tout est remboursé (durée allongée ou raccourcie)."""
        t = self.taux / Decimal(1200)
        restant = self.montant
        mensualite = self.mensualite_hors_assurance
        assurance_incluse = bool(self.taux_assurance) and self.type == self.Type.AMORTISSABLE
        totale = self.mensualite_totale
        recale = self.recale
        a_recaler = recale
        if recale and self.capital_constate_le < self.date_premiere_echeance:
            restant, a_recaler = self.capital_constate, False
        lignes = []
        n = 0
        while restant > 0 and n < 1200:
            n += 1
            if n > self.duree_mois and not recale:
                break
            date_ = _ajouter_mois(self.date_premiere_echeance, n - 1)
            interets = (restant * t).quantize(Decimal("0.01"))
            assurance = (restant * self.taux_assurance / Decimal(1200)).quantize(Decimal("0.01")) if assurance_incluse else self.assurance_mensuelle
            fin_prevue = n >= self.duree_mois and not recale
            if self.type == self.Type.IN_FINE:
                capital = restant if n >= self.duree_mois else Decimal(0)
            elif assurance_incluse:
                capital = restant if fin_prevue else min(restant, totale - interets - assurance)
            else:
                capital = restant if fin_prevue else min(restant, mensualite - interets)
            if recale and not a_recaler and restant - capital < 1:
                capital = restant  # reliquat d'arrondi de quelques centimes : soldé avec cette échéance
            restant -= capital
            if a_recaler and date_ <= self.capital_constate_le < _ajouter_mois(self.date_premiere_echeance, n):
                restant, a_recaler = self.capital_constate, False
            lignes.append({
                "numero": n,
                "date": date_,
                "mensualite": interets + capital,
                "interets": interets,
                "capital": capital,
                "assurance": assurance,
                "restant": restant,
            })
        return lignes

    def capital_restant_du(self, a_date):
        """Capital restant dû après les échéances passées à cette date (incluse)."""
        restant = self.montant
        for ligne in self.echeancier():
            if ligne["date"] > a_date:
                break
            restant = ligne["restant"]
        return restant

    def cumul_annee(self, annee):
        lignes = [l for l in self.echeancier() if l["date"].year == annee]
        return {
            "interets": sum((l["interets"] for l in lignes), Decimal(0)),
            "capital": sum((l["capital"] for l in lignes), Decimal(0)),
            "assurance": sum((l["assurance"] for l in lignes), Decimal(0)),
        }


def _chemin_facture(instance, nom):
    import secrets

    from django.utils import timezone

    return f"factures/{(instance.date or timezone.localdate()):%Y}/{secrets.token_hex(6)}-{nom}"


class PieceComptable(models.Model):
    """Facture ou recette d'un bien (comptabilité interne) : codée sur une ligne de l'annexe 2072-S-A1-SD."""

    class Ligne(models.TextChoices):
        L2 = "2", "2 · Dépenses du propriétaire mises à la charge des locataires"
        L3 = "3", "3 · Recettes diverses (subventions ANAH, indemnités d'assurance)"
        L6 = "6", "6 · Frais d'administration et de gestion (syndic, comptable, honoraires)"
        L8 = "8", "8 · Primes d'assurance (PNO…)"
        L9 = "9", "9 · Travaux de réparation, d'entretien et d'amélioration"
        L9B = "9b", "9 bis · Travaux de rénovation énergétique (passage E/F/G vers A–D)"
        L10 = "10", "10 · Charges récupérables non récupérées au départ du locataire"
        L11 = "11", "11 · Indemnités d'éviction, frais de relogement"
        L12 = "12", "12 · Impôts (taxe foncière, CRL…)"
        L13 = "13", "13 · Provisions pour charges de copropriété"
        L14 = "14", "14 · Régularisation des provisions de copropriété de l'année précédente"
        L15 = "15", "15 · Déduction spécifique"
        L17 = "17", "17 · Frais d'emprunt (dossier, garantie, assurance hors échéancier)"
        NON = "non", "Non déductible (capital d'emprunt, agrandissement, mobilier…)"

    RECETTES = {"2", "3"}

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="pieces_comptables")
    date = models.DateField("date de la facture", null=True, blank=True)
    date_paiement = models.DateField("payée le", null=True, blank=True,
                                     help_text="Année fiscale = année du paiement (date de la facture si vide)")
    fournisseur = models.CharField(max_length=150, blank=True)
    libelle = models.CharField("objet", max_length=200, blank=True)
    montant = models.DecimalField("montant TTC (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    ligne = models.CharField("ligne de l'annexe 2072", max_length=4, choices=Ligne.choices, blank=True,
                             help_text="Vide = pièce importée pas encore codifiée (ignorée par l'annexe 2072)")
    empreinte = models.CharField(max_length=64, blank=True, db_index=True, editable=False,
                                 help_text="Empreinte SHA-256 du fichier, pour repérer une facture importée deux fois")
    preremplie = models.BooleanField("préremplie automatiquement", default=False, editable=False,
                                     help_text="Champs lus dans le PDF à l'import : à vérifier avant d'être comptés dans l'annexe")
    fichier = models.FileField("facture (PDF ou photo)", upload_to=_chemin_facture, storage=stockage_prive, blank=True,
                               validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "heic"])])
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "facture / pièce comptable"
        verbose_name_plural = "factures et pièces comptables"
        ordering = ["-date"]

    def __str__(self):
        if self.a_codifier:
            return f"À codifier – {self.libelle or self.fichier.name.rsplit('/', 1)[-1]}"
        return f"{self.date:%d/%m/%Y} – {self.fournisseur or self.libelle} – {self.montant} €"

    def save(self, *args, **kwargs):
        if self.fichier and not self.empreinte:
            try:
                self.empreinte = self.calculer_empreinte(self.fichier)
            except Exception:
                pass
        super().save(*args, **kwargs)

    @staticmethod
    def calculer_empreinte(fichier):
        import hashlib

        h = hashlib.sha256()
        fichier.seek(0)
        for bloc in iter(lambda: fichier.read(65536), b""):
            h.update(bloc)
        fichier.seek(0)
        return h.hexdigest()

    @property
    def a_codifier(self):
        return self.preremplie or not self.ligne or self.montant is None or self.date is None

    @property
    def annee(self):
        d = self.date_paiement or self.date
        return d.year if d else None
