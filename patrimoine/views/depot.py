"""Dépôt de garantie depuis la fiche du bail : « Encaisser » ou « Restituer » (avec retenues), en un petit formulaire."""
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from ..fiches import eur
from ..models import Bail, MouvementDepot, Paiement


class DepotForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    montant = forms.DecimalField(label="montant (€)", min_value=0, max_digits=10, decimal_places=2)
    mode = forms.ChoiceField(choices=Paiement.Mode.choices, initial=Paiement.Mode.VIREMENT)
    retenue = forms.DecimalField(label="retenue (€)", min_value=0, max_digits=10, decimal_places=2, required=False,
                                 help_text="Dégradations ou charges, sur devis ou facture")
    motif = forms.CharField(label="motif de la retenue", max_length=255, required=False)

    def __init__(self, *args, sens, detenu, **kwargs):
        super().__init__(*args, **kwargs)
        self.sens, self.detenu = sens, detenu
        if sens == "encaisser":
            del self.fields["retenue"], self.fields["motif"]
        else:
            self.fields["montant"].label = "montant restitué (€)"

    def clean(self):
        donnees = super().clean()
        montant, retenue = donnees.get("montant") or Decimal(0), donnees.get("retenue") or Decimal(0)
        if self.sens == "encaisser" and not montant:
            self.add_error("montant", "Indiquez le montant encaissé.")
        if self.sens == "restituer":
            if not (montant or retenue):
                self.add_error("montant", "Indiquez le montant restitué ou retenu.")
            if montant + retenue > self.detenu:
                self.add_error("montant", f"Restitution et retenue dépassent le dépôt détenu ({eur(self.detenu)}).")
            if retenue and not donnees.get("motif"):
                self.add_error("motif", "Indiquez le motif de la retenue (obligatoire pour la justifier).")
        return donnees


@staff_member_required
def depot_bail(request, pk, sens):
    if sens not in ("encaisser", "restituer"):
        raise Http404
    bail = get_object_or_404(Bail.objects.select_related("lot__bien"), pk=pk)
    detenu = bail.depot_detenu
    retour = reverse("admin:patrimoine_bail_change", args=[bail.pk]) + "#argent"
    if request.method == "POST":
        form = DepotForm(request.POST, sens=sens, detenu=detenu)
        if form.is_valid():
            d = form.cleaned_data
            if sens == "encaisser":
                m = MouvementDepot.objects.create(bail=bail, type=MouvementDepot.Type.ENCAISSEMENT, date=d["date"], montant=d["montant"],
                                                  mode=d["mode"])
                messages.success(request, format_html('Dépôt de garantie de {} encaissé le {}. <a href="{}">Créer le reçu (Word)</a>',
                                                      eur(m.montant), f"{m.date:%d/%m/%Y}", reverse("recu_depot_mouvement", args=[m.pk])))
            else:
                if d.get("retenue"):
                    MouvementDepot.objects.create(bail=bail, type=MouvementDepot.Type.RETENUE, date=d["date"], montant=d["retenue"],
                                                  mode=d["mode"], justification=d.get("motif", ""))
                if d["montant"]:
                    MouvementDepot.objects.create(bail=bail, type=MouvementDepot.Type.RESTITUTION, date=d["date"], montant=d["montant"],
                                                  mode=d["mode"])
                texte = f"Dépôt de garantie : {eur(d['montant'])} restitué le {d['date']:%d/%m/%Y}"
                if d.get("retenue"):
                    texte += f", {eur(d['retenue'])} retenu ({d['motif']})"
                messages.success(request, texte + f". Reste détenu : {eur(bail.depot_detenu)}.")
            return redirect(retour)
    else:
        montant = max(bail.depot_garantie - detenu, Decimal(0)) if sens == "encaisser" else detenu
        form = DepotForm(initial={"date": timezone.localdate(), "montant": montant}, sens=sens, detenu=detenu)
    titre = "Encaisser le dépôt de garantie" if sens == "encaisser" else "Restituer le dépôt de garantie"
    return render(request, "patrimoine/depot_bail.html", {
        **admin.site.each_context(request), "title": titre, "bail": bail, "form": form, "sens": sens, "detenu": detenu,
        "retour": retour, "mouvements": bail.mouvements_depot.order_by("date")})
