"""Suivi de la location courte durée (Airbnb, Booking)."""
import csv
from decimal import Decimal

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from ..models import Lot


def _comparaison_lcd(periodes):
    lignes = [("Chiffre d'affaires", "brut", "€"), ("Revenu net", "net", "€"), ("Nuits louées", "nuits", ""),
              ("Taux de remplissage", "remplissage", "%"), ("Prix moyen / nuit", "prix_nuit", "€"), ("Cash-flow", "cash_flow", "€")]
    resultat = []
    for titre, n, n1 in periodes:
        bloc = {"titre": titre, "lignes": []}
        for libelle, cle, unite in lignes:
            a, b = n1[cle], n[cle]
            ecart = (Decimal(b) - Decimal(a)) / abs(Decimal(a)) * 100 if a else None
            bloc["lignes"].append({"libelle": libelle, "n1": a, "n": b, "ecart": ecart, "unite": unite})
        resultat.append(bloc)
    return resultat


@staff_member_required
def suivi_lcd(request):
    """Suivi mensuel d'un logement en location courte durée, avec import des exports Airbnb et Booking."""
    from .. import lcd

    lots = Lot.objects.filter(vendu=False, bien__vendu=False).select_related("bien").order_by("bien__nom", "designation")
    lots_lcd = [l for l in lots if l.exploitation == "courte" or l.reservations_lcd.exists() or hasattr(l, "parametres_lcd")]
    lot = lots.filter(pk=request.GET.get("lot") or request.POST.get("lot")).first() if (request.GET.get("lot") or request.POST.get("lot")) else (lots_lcd[0] if lots_lcd else None)
    aujourdhui = timezone.localdate()
    annee = int(request.GET.get("annee") or aujourdhui.year)

    if request.method == "POST" and lot:
        fichiers = request.FILES.getlist("fichiers")
        if not fichiers:
            messages.error(request, "Choisissez un ou plusieurs fichiers exportés depuis Airbnb ou Booking.")
        for f in fichiers:
            try:
                plateforme, creees, maj, ignorees = lcd.importer(lot, f, f.name, request.POST.get("plateforme") or None)
            except (ValueError, KeyError, csv.Error) as erreur:
                messages.error(request, f"{f.name} : {erreur}")
            else:
                messages.success(request, f"{f.name} ({plateforme.label if hasattr(plateforme, 'label') else plateforme}) : "
                                          f"{creees} réservation(s) ajoutée(s), {maj} mise(s) à jour, {ignorees} ligne(s) ignorée(s).")
        return redirect(f"{reverse('suivi_lcd')}?lot={lot.pk}&annee={annee}")

    contexte = {**admin.site.each_context(request), "title": "Suivi location courte durée", "lots": lots_lcd or lots, "lot": lot, "annee": annee,
                "annees": range(aujourdhui.year - 3, aujourdhui.year + 2)}
    if lot:
        mois = lcd.suivi_annuel(lot, annee)
        precedente = lcd.suivi_annuel(lot, annee - 1)
        mois_ref = int(request.GET.get("mois") or (aujourdhui.month if annee == aujourdhui.year else 12))
        contexte.update(
            mois=mois, total=lcd.totaliser(mois),
            plateformes=lcd.PLATEFORMES.choices,
            mois_ref=mois_ref,
            comparaison=_comparaison_lcd([
                (f"{mois_ref:02d}/{annee}", mois[mois_ref - 1], precedente[mois_ref - 1]),
                (f"cumul à fin {mois_ref:02d}/{annee}", lcd.totaliser(mois, mois_ref), lcd.totaliser(precedente, mois_ref)),
            ]),
            parametres=getattr(lot, "parametres_lcd", None),
            nb_reservations=lot.reservations_lcd.filter(arrivee__year=annee).count(),
        )
    return render(request, "patrimoine/suivi_lcd.html", contexte)
