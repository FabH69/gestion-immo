from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from .models import Echeance


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
