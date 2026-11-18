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
