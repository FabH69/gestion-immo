from django import forms

from .models import Article, Commande, Joueur, Ligne


class JoueurForm(forms.ModelForm):
    class Meta:
        model = Joueur
        fields = ["prenom", "nom", "telephone", "email", "club", "actif", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["nom", "reference", "prix_vente", "taux_commission", "actif", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class CommandeForm(forms.ModelForm):
    class Meta:
        model = Commande
        fields = ["nom", "statut", "date_reception", "lieu_remise", "message_arrivee", "notes"]
        widgets = {
            "date_reception": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "message_arrivee": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class LigneForm(forms.ModelForm):
    class Meta:
        model = Ligne
        fields = ["joueur", "article", "quantite", "prix_unitaire", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["joueur"].queryset = Joueur.objects.filter(actif=True)
        self.fields["article"].queryset = Article.objects.filter(actif=True)
        self.fields["quantite"].widget.attrs["min"] = 1
        self.fields["prix_unitaire"].required = False
        self.fields["prix_unitaire"].widget.attrs["placeholder"] = "auto"
        self.fields["prix_unitaire"].help_text = "Laisser vide pour reprendre le prix de l'article."
