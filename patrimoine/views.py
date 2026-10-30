import csv

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from weasyprint import HTML

from .documents import ELEMENTS_PIECE, INVENTAIRE_MEUBLE, duree_texte
from .etats import loyers_encaisses
from .models import Bail, Echeance, EtatDesLieux, MouvementDepot, Proprietaire


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


def _pdf(html, nom):
    response = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{slugify(nom)}.pdf"'
    return response


def _contexte_bail(bail):
    lot = bail.lot
    return {
        "bail": bail,
        "lot": lot,
        "bien": lot.bien,
        "proprietaire": lot.bien.proprietaire,
        "locataires": list(bail.locataires.all()),
    }


@staff_member_required
def document_bail(request, pk):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    contexte = _contexte_bail(bail)
    contexte.update(duree_texte=duree_texte(bail), nb_exemplaires=1 + len(contexte["locataires"]))
    return _pdf(render_to_string("patrimoine/bail.html", contexte), f"bail {bail.lot} {bail.date_debut:%Y-%m-%d}")


def _rendu_edl(bail, edl):
    contexte = _contexte_bail(bail)
    contexte.update(
        edl=edl,
        pieces=bail.lot.liste_pieces(),
        elements=ELEMENTS_PIECE,
        meuble=bail.lot.meuble or bail.type in (Bail.Type.MEUBLE, Bail.Type.ETUDIANT, Bail.Type.MOBILITE),
        inventaire=INVENTAIRE_MEUBLE,
    )
    return _pdf(render_to_string("patrimoine/etat_des_lieux.html", contexte), f"etat des lieux {edl.get_type_display()} {bail.lot}")


@staff_member_required
def document_etat_des_lieux(request, pk):
    edl = get_object_or_404(EtatDesLieux.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    return _rendu_edl(edl.bail, edl)


@staff_member_required
def document_etat_des_lieux_vierge(request, pk, type_edl):
    """Fiche pré-remplie depuis le bail, sans état des lieux enregistré (à imprimer avant le rendez-vous)."""
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    return _rendu_edl(bail, EtatDesLieux(bail=bail, type=type_edl, date=None))


@staff_member_required
def document_inventaire(request, pk, type_edl):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    contexte = _contexte_bail(bail)
    contexte.update(type_edl=type_edl, pieces=bail.lot.liste_pieces(), inventaire=INVENTAIRE_MEUBLE, lignes_libres=range(5))
    return _pdf(render_to_string("patrimoine/inventaire.html", contexte), f"inventaire {type_edl} {bail.lot}")


@staff_member_required
def document_recu_depot(request, pk):
    m = get_object_or_404(MouvementDepot.objects.select_related("bail__lot__bien__proprietaire"), pk=pk, type=MouvementDepot.Type.ENCAISSEMENT)
    contexte = _contexte_bail(m.bail)
    contexte.update(m=m, aujourdhui=timezone.localdate())
    return _pdf(render_to_string("patrimoine/recu_depot.html", contexte), f"recu depot garantie {m.bail.lot}")
