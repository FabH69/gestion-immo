import csv

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from weasyprint import HTML

from .etats import loyers_encaisses
from .models import Echeance, Proprietaire


@staff_member_required
def document_echeance(request, pk):
    """Quittance si l'échéance est soldée, sinon avis d'échéance (une quittance ne se délivre que pour un loyer payé en entier)."""
    echeance = get_object_or_404(Echeance.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    est_quittance = echeance.statut == Echeance.Statut.PAYE
    dernier_paiement = echeance.paiements.order_by("-date").first()
    html = render_to_string(
        "patrimoine/quittance.html",
        {
            "e": echeance,
            "bail": echeance.bail,
            "lot": echeance.bail.lot,
            "bien": echeance.bail.lot.bien,
            "proprietaire": echeance.bail.lot.bien.proprietaire,
            "locataires": echeance.bail.locataires.all(),
            "est_quittance": est_quittance,
            "date_paiement": dernier_paiement.date if dernier_paiement else None,
            "aujourdhui": timezone.localdate(),
        },
    )
    titre = "quittance" if est_quittance else "avis-echeance"
    nom = f"{titre}-{echeance.periode:%Y-%m}-{echeance.bail.lot.designation}.pdf".replace(" ", "-")
    response = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{nom}"'
    return response


@staff_member_required
def etat_loyers_encaisses(request):
    annee = int(request.GET.get("annee") or timezone.localdate().year)
    proprietaire = None
    if request.GET.get("proprietaire"):
        proprietaire = get_object_or_404(Proprietaire, pk=request.GET["proprietaire"])
    etat = loyers_encaisses(annee, proprietaire)
    suffixe = slugify(proprietaire.nom) if proprietaire else "tous"
    nom_fichier = f"loyers-encaisses-{annee}-{suffixe}"
    format_ = request.GET.get("format")

    if format_ == "csv":
        return _etat_csv(etat, nom_fichier)

    contexte = {
        **etat,
        "proprietaire": proprietaire,
        "proprietaires_choix": Proprietaire.objects.all(),
        "annees": range(timezone.localdate().year + 1, timezone.localdate().year - 6, -1),
        "aujourdhui": timezone.localdate(),
    }
    if format_ == "pdf":
        html = render_to_string("patrimoine/etat_loyers_pdf.html", contexte)
        response = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{nom_fichier}.pdf"'
        return response
    return render(request, "patrimoine/etat_loyers.html", {**admin.site.each_context(request), **contexte, "title": f"Loyers encaissés en {annee}" + (f" – {proprietaire.nom}" if proprietaire else "")})


def _etat_csv(etat, nom_fichier):
    """CSV au format Excel français : séparateur « ; », virgule décimale, BOM UTF-8."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{nom_fichier}.csv"'
    response.write("﻿")
    w = csv.writer(response, delimiter=";")
    fr = lambda d: f"{d:.2f}".replace(".", ",")
    w.writerow(["Propriétaire", "Bien", "Lot", *etat["mois"], "Total", "dont loyers HC", "dont charges"])
    for bp in etat["proprietaires"]:
        for bb in bp["biens"]:
            for l in bb["lots"]:
                w.writerow([bp["proprietaire"].nom, bb["bien"].nom, l["lot"].designation, *map(fr, l["mois"]), fr(l["total"]), fr(l["loyers_hc"]), fr(l["charges"])])
    t = etat["total"]
    w.writerow(["TOTAL", "", "", *map(fr, t["mois"]), fr(t["total"]), fr(t["loyers_hc"]), fr(t["charges"])])
    return response
