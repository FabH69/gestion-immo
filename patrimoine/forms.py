from django import forms

from .models import Candidature, GarantCandidature

TAILLE_MAX_PIECE = 10 * 1024 * 1024
EXTENSIONS_PIECE = (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp")


class CandidatureForm(forms.ModelForm):
    consentement = forms.BooleanField(
        required=True,
        label="J'accepte que ces informations soient utilisées uniquement pour étudier ma candidature.",
    )

    class Meta:
        model = Candidature
        fields = [
            "civilite", "prenom", "nom", "date_naissance", "lieu_naissance", "email", "telephone",
            "adresse_rue", "adresse_code_postal", "adresse_ville",
            "situation", "employeur", "revenus_mensuels", "nb_occupants", "date_entree_souhaitee",
            "type_garantie", "message", "consentement",
        ]
        widgets = {
            "date_naissance": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_entree_souhaitee": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "message": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nom in ("nom", "prenom", "email", "telephone", "adresse_rue", "adresse_code_postal", "adresse_ville",
                    "situation", "revenus_mensuels", "type_garantie"):
            self.fields[nom].required = True
        self.fields["adresse_rue"].label = "Adresse actuelle : n° et rue"
        self.fields["type_garantie"].help_text = ""
        self.fields["message"].label = "Un mot pour le propriétaire (facultatif)"
        # type de caution choisi par le propriétaire avant l'envoi : affiché, non modifiable
        if self.instance.pk and self.instance.type_garantie:
            self.fields["type_garantie"].disabled = True


class GarantCandidatureForm(forms.ModelForm):
    class Meta:
        model = GarantCandidature
        fields = ["civilite", "prenom", "nom", "date_naissance", "lieu_naissance", "adresse_rue", "code_postal", "ville",
                  "email", "telephone", "lien", "profession", "revenus_mensuels", "piece_identite_numero", "piece_identite_fichier"]
        widgets = {"date_naissance": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "piece_identite_fichier": forms.ClearableFileInput(attrs={"accept": ",".join(EXTENSIONS_PIECE) + ",image/*"})}

    def clean_piece_identite_fichier(self):
        f = self.cleaned_data.get("piece_identite_fichier")
        if f and hasattr(f, "size"):
            if f.size > TAILLE_MAX_PIECE:
                raise forms.ValidationError("Fichier trop lourd : 10 Mo maximum.")
            if not f.name.lower().endswith(EXTENSIONS_PIECE):
                raise forms.ValidationError("Formats acceptés : PDF, JPG, PNG, HEIC.")
        return f


GarantsFormSet = forms.inlineformset_factory(
    Candidature, GarantCandidature, form=GarantCandidatureForm, extra=2, max_num=2, can_delete=False)
