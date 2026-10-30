from django import forms

from .models import Candidature


class CandidatureForm(forms.ModelForm):
    consentement = forms.BooleanField(
        required=True,
        label="J'accepte que ces informations soient utilisées uniquement pour étudier ma candidature.",
    )

    class Meta:
        model = Candidature
        fields = [
            "civilite", "prenom", "nom", "date_naissance", "lieu_naissance", "email", "telephone", "adresse_actuelle",
            "situation", "employeur", "revenus_mensuels", "nb_occupants", "date_entree_souhaitee",
            "type_garantie", "garant_nom", "garant_lien", "garant_email", "garant_telephone", "garant_adresse",
            "garant_profession", "garant_revenus", "message", "consentement",
        ]
        widgets = {
            "date_naissance": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_entree_souhaitee": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "adresse_actuelle": forms.Textarea(attrs={"rows": 2}),
            "garant_adresse": forms.Textarea(attrs={"rows": 2}),
            "message": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nom in ("nom", "prenom", "email", "telephone", "situation", "revenus_mensuels", "type_garantie"):
            self.fields[nom].required = True
        self.fields["message"].label = "Un mot pour le propriétaire (facultatif)"

    def clean(self):
        donnees = super().clean()
        if donnees.get("type_garantie") == "garant" and not donnees.get("garant_nom"):
            self.add_error("garant_nom", "Indiquez le nom de la personne qui se porte caution.")
        return donnees
