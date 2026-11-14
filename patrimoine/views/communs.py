"""Outils partagés par les pages : années proposées, PDF, fichiers Word et CSV."""
import csv
from urllib.parse import quote
from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify
from weasyprint import HTML


def annees_disponibles():
    """Années proposées dans les états : de la plus ancienne donnée (ancien suivi compris) à l'année prochaine."""
    from ..models import EncaissementHistorique
    courante = timezone.localdate().year
    premier = EncaissementHistorique.objects.order_by("mois").values_list("mois", flat=True).first()
    debut = min(courante - 6, premier.year) if premier else courante - 6
    return range(courante + 1, debut - 1, -1)


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
        "garants": list(bail.garants.select_related("locataire")),
    }


def _reponse_docx(contenu, nom):
    reponse = HttpResponse(contenu, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    nom_fichier = f"{nom}.docx".replace("/", "-")
    reponse["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(nom_fichier)}"
    return reponse
