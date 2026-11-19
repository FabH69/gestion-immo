"""Simulations d'investissement enregistrées (espace connecté). Les simulateurs publics n'enregistrent rien."""
from django.db import models
from django.urls import reverse


class SimulationInvestissement(models.Model):
    nom = models.CharField(max_length=200)
    adresse = models.CharField(max_length=255, blank=True)
    donnees = models.JSONField("paramètres", default=dict, help_text="Lots et paramètres (voir calculs/investissement.py)")
    bien = models.ForeignKey("patrimoine.Bien", on_delete=models.SET_NULL, null=True, blank=True, related_name="simulations",
                             help_text="Bien créé à partir de cette simulation")
    cree_le = models.DateTimeField("créée le", auto_now_add=True)
    modifie_le = models.DateTimeField("modifiée le", auto_now=True)
    dossier = models.JSONField("dossier banque", default=dict, blank=True,
                               help_text="Réglages du dossier de financement (voir dossier.py)")

    class Meta:
        verbose_name = "simulation d'investissement"
        verbose_name_plural = "simulations d'investissement"
        ordering = ["-modifie_le"]

    def __str__(self):
        return self.nom

    def get_absolute_url(self):
        return reverse("simulateurs:simulation", args=[self.pk])

    @property
    def parametres(self):
        from .calculs.investissement import Parametres

        return Parametres.depuis_dict(self.donnees)


def _chemin_devis(instance, nom):
    import secrets

    return f"devis-travaux/{secrets.token_hex(8)}-{nom}"


def _stockage():
    from patrimoine.models import stockage_prive

    return stockage_prive()


class DevisTravaux(models.Model):
    """Devis (PDF ou photo) joint à une ligne du chiffrage des travaux d'une simulation enregistrée."""

    simulation = models.ForeignKey(SimulationInvestissement, on_delete=models.CASCADE, related_name="devis")
    fichier = models.FileField(upload_to=_chemin_devis, storage=_stockage)
    nom = models.CharField(max_length=200)
    ajoute_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "devis de travaux"
        verbose_name_plural = "devis de travaux"

    def __str__(self):
        return self.nom

    @property
    def url(self):
        from django.urls import reverse

        return reverse("fichier_prive", args=[self.fichier.name])


def _chemin_photo(instance, nom):
    import secrets

    return f"photos-dossier/{secrets.token_hex(8)}-{nom}"


class PhotoDossier(models.Model):
    """Photo du bien, montrée dans le dossier de financement (banque) d'une simulation enregistrée."""

    simulation = models.ForeignKey(SimulationInvestissement, on_delete=models.CASCADE, related_name="photos")
    fichier = models.FileField(upload_to=_chemin_photo, storage=_stockage)
    legende = models.CharField("légende", max_length=120, blank=True)
    ordre = models.PositiveSmallIntegerField(default=0)
    ajoute_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "photo du dossier banque"
        verbose_name_plural = "photos du dossier banque"
        ordering = ["ordre", "pk"]

    def __str__(self):
        return self.legende or self.fichier.name

    @property
    def url(self):
        return reverse("fichier_prive", args=[self.fichier.name])
