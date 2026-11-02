from django import forms

from .models import Commande, Joueur, Ligne


class JoueurForm(forms.ModelForm):
    class Meta:
        model = Joueur
        fields = ["prenom", "nom", "telephone", "email", "club", "actif", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class CommandeForm(forms.ModelForm):
    class Meta:
        model = Commande
        fields = [
            "nom", "produit", "prix_boite", "taux_commission", "statut",
            "date_limite", "date_reception", "lieu_remise", "message_arrivee", "notes",
        ]
        widgets = {
            "date_limite": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_reception": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "message_arrivee": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class LigneForm(forms.ModelForm):
    class Meta:
        model = Ligne
        fields = ["joueur", "nb_boites", "notes"]

    def __init__(self, *args, commande=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Joueur.objects.filter(actif=True)
        if commande is not None:
            qs = qs.exclude(lignes__commande=commande)
        self.fields["joueur"].queryset = qs
        self.fields["nb_boites"].widget.attrs["min"] = 1
