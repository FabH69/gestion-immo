from decimal import Decimal, InvalidOperation

from django import forms

from .calculs import notaire, pret


class MontantField(forms.DecimalField):
    """Nombre saisi à la française : « 250 000 », « 3,45 », « 1 200,50 € »."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(" ", "").replace(" ", "").replace(" ", "").replace("€", "").replace("%", "")
            value = value.replace(",", ".")
        return super().to_python(value)


def montant(label, aide="", requis=True, mini=0, maxi=None, decimales=2, **kw):
    attrs = {"inputmode": "decimal", "autocomplete": "off"}
    attrs.update(kw.pop("attrs", {}))
    return MontantField(label=label, help_text=aide, required=requis, min_value=Decimal(mini),
                        max_value=Decimal(maxi) if maxi is not None else None, decimal_places=decimales,
                        widget=forms.TextInput(attrs=attrs), **kw)


class FraisNotaireForm(forms.Form):
    prix = montant("Prix d'achat (€)", "Prix net vendeur. Dans le neuf : prix TVA comprise.", mini=1000, maxi=100_000_000,
                   attrs={"placeholder": "250 000"})
    type_bien = forms.ChoiceField(label="Type de bien", initial=notaire.ANCIEN, widget=forms.RadioSelect, choices=[
        (notaire.ANCIEN, "Ancien"), (notaire.NEUF, "Neuf (VEFA ou moins de 5 ans, vendu avec TVA)")])
    departement = forms.ChoiceField(label="Département du bien", initial="69",
                                    choices=[(c, f"{c} – {n}") for c, n in notaire.DEPARTEMENTS])
    primo_accedant = forms.BooleanField(label="Premier achat de ma résidence principale (primo-accédant)", required=False,
                                        help_text="Ancien uniquement : la hausse 2025 des droits ne s'applique pas.")
    mobilier = montant("Mobilier inclus dans le prix (€)", "Meubles listés dans l'acte : ils ne sont pas taxés.",
                       requis=False, attrs={"placeholder": "0"})
    formalites = montant("Formalités et débours (€)", "Estimation : environ 1 000 à 1 500 €.", requis=False,
                         initial=notaire.FORMALITES_PAR_DEFAUT, maxi=100_000)

    def clean(self):
        donnees = super().clean()
        prix, mobilier = donnees.get("prix"), donnees.get("mobilier")
        if prix and mobilier and mobilier >= prix:
            self.add_error("mobilier", "Le mobilier doit être inférieur au prix d'achat.")
        if donnees.get("formalites") is None:
            donnees["formalites"] = notaire.FORMALITES_PAR_DEFAUT
        return donnees


class PretForm(forms.Form):
    capital = montant("Montant emprunté (€)", mini=1000, maxi=100_000_000, attrs={"placeholder": "200 000"})
    taux = montant("Taux nominal annuel (%)", "Hors assurance.", mini=0, maxi=20, decimales=3, attrs={"placeholder": "3,40"})
    duree_annees = forms.IntegerField(label="Durée (années)", min_value=1, max_value=35, initial=20,
                                      widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    taux_assurance = montant("Taux d'assurance annuel (%)", "Par exemple 0,30 %. Laissez vide sans assurance.", requis=False,
                             maxi=5, decimales=3, attrs={"placeholder": "0,30"})
    mode_assurance = forms.ChoiceField(label="Assurance calculée sur", initial=pret.ASSURANCE_INITIAL, widget=forms.RadioSelect,
                                       choices=[(pret.ASSURANCE_INITIAL, "le capital initial (montant fixe)"),
                                                (pret.ASSURANCE_RESTANT, "le capital restant dû (dégressive)")])
    frais = montant("Frais de dossier et de garantie (€)", "Caution ou hypothèque, frais de la banque.", requis=False,
                    attrs={"placeholder": "0"})
    revenus = montant("Revenus nets mensuels du foyer (€)", "Facultatif : pour calculer votre taux d'endettement.",
                      requis=False, attrs={"placeholder": "4 000"})
    autres_credits = montant("Autres crédits en cours (€ / mois)", requis=False, attrs={"placeholder": "0"})

    def clean(self):
        donnees = super().clean()
        frais, capital = donnees.get("frais"), donnees.get("capital")
        if frais and capital and frais >= capital:
            self.add_error("frais", "Les frais doivent être inférieurs au montant emprunté.")
        return donnees
