"""Comparatif location nue / courte durée, logement par logement (avant impôts)."""
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.forms import inlineformset_factory
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .. import comparatif as cmp
from ..models import ComparatifNue, LigneComparatif


class HypothesesForm(forms.ModelForm):
    class Meta:
        model = ComparatifNue
        fields = ["loyer_hc", "charges", "vacance_mois", "gli", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _sans_zeros(self)


class LigneForm(forms.ModelForm):
    class Meta:
        model = LigneComparatif
        fields = ["cote", "libelle", "montant_annuel", "recuperable"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _sans_zeros(self)


def _sans_zeros(form):
    """780 plutôt que 780.00 dans les champs."""
    for nom, champ in form.fields.items():
        valeur = form.initial.get(nom)
        if isinstance(champ, forms.DecimalField) and isinstance(valeur, Decimal):
            form.initial[nom] = f"{valeur.normalize():f}"


LignesFormSet = inlineformset_factory(ComparatifNue, LigneComparatif, form=LigneForm, extra=1, can_delete=True)


@staff_member_required
def comparatif_lcd(request):
    lots = list(cmp.lots_courte_duree())
    lot = next((l for l in lots if str(l.pk) == (request.POST.get("lot") or request.GET.get("lot"))), lots[0] if lots else None)
    aujourdhui = timezone.localdate()
    annee = int(request.GET.get("annee") or aujourdhui.year)
    contexte = {**admin.site.each_context(request), "title": "Comparatif location nue / courte durée", "lots": lots, "lot": lot,
                "annee": annee, "annees": range(aujourdhui.year - 3, aujourdhui.year + 1)}
    if lot is None:
        return render(request, "patrimoine/comparatif_lcd.html", contexte)
    hypotheses = cmp.comparatif_du_lot(lot)
    adresse = f"{reverse('comparatif_lcd')}?lot={lot.pk}&annee={annee}"
    if request.method == "POST":
        form = HypothesesForm(request.POST, instance=hypotheses)
        lignes = LignesFormSet(request.POST, instance=hypotheses)
        if form.is_valid() and lignes.is_valid():
            c = form.save(commit=False)
            traitements = {}
            for code in cmp.EXPLOITATION:
                t = request.POST.get(f"t_{code}")
                if t in dict(ComparatifNue.TRAITEMENTS):
                    try:
                        pct = min(max(Decimal((request.POST.get(f"p_{code}") or "0").replace(",", ".")), Decimal(0)), Decimal(100))
                    except InvalidOperation:
                        pct = Decimal(0)
                    traitements[code] = [t, float(pct)]
            c.traitements = {**c.traitements, **traitements}
            c.save()
            lignes.save()
            messages.success(request, "Hypothèses de location nue enregistrées.")
            return redirect(adresse)
    else:
        form, lignes = HypothesesForm(instance=hypotheses), LignesFormSet(instance=hypotheses)
    hypotheses, periodes = cmp.comparer(lot, annee)
    codes = [code for code in cmp.EXPLOITATION if any(l.get("code") == code for p in periodes for l in p["lignes"])]
    traitements = [{"code": code, "libelle": cmp.LIBELLES.get(code, code), "traitement": hypotheses.traitement(code)[0],
                    "pct": hypotheses.traitement(code)[1]} for code in codes]
    contexte.update(hypotheses=hypotheses, periodes=periodes, form=form, lignes=lignes, traitements=traitements,
                    choix_traitements=ComparatifNue.TRAITEMENTS, adresse=adresse)
    return render(request, "patrimoine/comparatif_lcd.html", contexte)
