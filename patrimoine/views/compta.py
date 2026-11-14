"""Comptabilité par régime : récapitulatifs par rubrique (LMNP, société), récapitulatif 2044 et contrôle des factures
de la conciergerie."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .. import compta, conciergerie
from ..models import Lot, PieceComptable
from .communs import _pdf


def _annees():
    t = timezone.localdate().year
    return range(t - 10, t + 1)


def onglets(regime, actif, request=None):
    """Onglets en haut des pages d'une entrée de comptabilité (récapitulatif, factures, à codifier, bilan…)."""
    factures = reverse("admin:patrimoine_piececomptable_changelist") + f"?compta={regime}"
    liste = []
    if regime in compta.PAR_RUBRIQUE:
        liste.append(("recap", "Récapitulatif", reverse(f"compta_{regime}"), None))
    if regime == "foncier":
        liste.append(("2072", "Annexe 2072 (SCI)", reverse("annexe_2072"), None))
        if compta.biens_2044().exists():
            liste.append(("2044", "Récapitulatif 2044", reverse("recap_2044"), None))
    liste += [("factures", "Factures", factures, None),
              ("codifier", "À codifier", factures + "&a_codifier=oui", compta.a_codifier(regime).count() or None)]
    if regime in compta.PAR_RUBRIQUE and lots_conciergerie(regime).exists():
        liste.append(("conciergerie", "Contrôle conciergerie", reverse("conciergerie_ajouter") + f"?compta={regime}", None))
    if regime == "lmnp":
        liste += [("2031", "Bilan LMNP (2031)", reverse("biens_lmnp"), None),
                  ("amortissements", "Charges et amortissements", reverse("admin:patrimoine_chargelmnp_changelist"), None)]
    return [{"code": c, "libelle": l, "url": u, "compte": n, "actif": c == actif} for c, l, u, n in liste]


def lots_conciergerie(regime=None):
    """Logements en courte durée dont la comptabilité est tenue par rubrique (factures de conciergerie)."""
    qs = Lot.objects.filter(exploitation="courte", bien__comptabilite__in=compta.PAR_RUBRIQUE).select_related("bien")
    return qs.filter(bien__comptabilite=regime) if regime else qs


@staff_member_required
def compta_recap(request, regime):
    """Récapitulatif par rubrique (LMNP ou société), par bien ou mois par mois, comme le tableau de suivi."""
    if regime not in compta.PAR_RUBRIQUE:
        raise Http404
    aujourdhui = timezone.localdate()
    annee = int(request.GET.get("annee") or aujourdhui.year)
    biens = list(compta.biens(regime, annee))
    bien = next((b for b in biens if str(b.pk) == request.GET.get("bien")), None)
    par_mois = request.GET.get("vue") == "mois"
    recap = compta.recapitulatif(regime, annee, bien, par_mois)
    libelle = compta.libelle(regime)
    contexte = {"regime": regime, "libelle_regime": libelle, "annee": annee, "annees": _annees(), "biens": biens, "bien": bien,
                "par_mois": par_mois, "onglets": onglets(regime, "recap"), "aujourdhui": aujourdhui, **recap,
                "lien_import": reverse("admin:patrimoine_piececomptable_import") + f"?compta={regime}"}
    if request.GET.get("format") == "pdf":
        return _pdf(render_to_string("patrimoine/compta_recap_pdf.html", contexte), f"Comptabilite {libelle} {annee}")
    return render(request, "patrimoine/compta_recap.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Comptabilité {libelle}"})


@staff_member_required
def recap_2044(request):
    """Récapitulatif 2044 des biens en nom propre déclarés en revenus fonciers (Pollionnay)."""
    aujourdhui = timezone.localdate()
    annee = int(request.GET.get("annee") or aujourdhui.year - (1 if aujourdhui.month < 6 else 0))
    contexte = {"annee": annee, "annees": _annees(), "onglets": onglets("foncier", "2044"), **compta.recap_2044(annee)}
    return render(request, "patrimoine/recap_2044.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Récapitulatif 2044 – revenus {annee}"})


def _lot_de_la_piece(piece):
    return next((l for l in piece.bien.lots.all() if l.exploitation == "courte"), None) if piece.bien_id else None


def _pieces_liees(principale):
    """Pièces Ménage et Consommables créées avec la facture (elles renvoient à la pièce qui porte le PDF)."""
    return list(PieceComptable.objects.filter(bien=principale.bien, notes__contains=f"pièce n° {principale.pk} (PDF").order_by("rubrique"))


def factures_conciergerie(regime=None):
    """Factures de conciergerie enregistrées (pièce Commission qui porte le PDF), les plus récentes d'abord."""
    qs = PieceComptable.objects.filter(rubrique="commission", fournisseur__istartswith="concierg", bien__lots__exploitation="courte") \
        .exclude(fichier="").select_related("bien").distinct()
    if regime:
        qs = qs.filter(bien__comptabilite=regime)
    return qs.order_by("-mois_concerne", "-date", "-pk")


@staff_member_required
def conciergerie_ajouter(request):
    """Dépôt d'une facture de conciergerie : elle est lue, contrôlée avec les réservations du mois et enregistrée."""
    from ..lecture_factures import texte_pdf

    regime = request.GET.get("compta") or request.POST.get("compta") or ""
    lots = list(lots_conciergerie(regime if regime in compta.PAR_RUBRIQUE else None))
    if request.method == "POST":
        lot = next((l for l in lots if str(l.pk) == request.POST.get("lot")), lots[0] if len(lots) == 1 else None)
        fichier = request.FILES.get("fichier")
        if not lot or not fichier:
            messages.error(request, "Choisissez le logement et la facture (PDF).")
            return redirect(request.get_full_path())
        empreinte = PieceComptable.calculer_empreinte(fichier)
        deja = PieceComptable.objects.filter(empreinte=empreinte).first()
        if deja:
            messages.warning(request, "Cette facture est déjà enregistrée : voici son contrôle.")
            return redirect("controle_conciergerie", pk=deja.pk)
        texte = texte_pdf(fichier) if fichier.name.lower().endswith(".pdf") else ""
        if not conciergerie.est_une_facture(texte):
            messages.error(request, "Ce fichier ne ressemble pas à une facture de conciergerie (aucun séjour avec son honoraire "
                                    "n'a pu être lu). Ajoutez-la avec « Ajouter des factures » pour la codifier à la main.")
            return redirect(request.get_full_path())
        principale = conciergerie.enregistrer(lot, fichier, texte)
        return redirect("controle_conciergerie", pk=principale.pk)
    regime_affiche = regime if regime in compta.PAR_RUBRIQUE else (lots[0].bien.comptabilite if lots else "lmnp")
    historique = []
    for p in factures_conciergerie(regime if regime in compta.PAR_RUBRIQUE else None)[:12]:
        historique.append({"piece": p, "lot": _lot_de_la_piece(p)})
    return render(request, "patrimoine/conciergerie_ajouter.html", {
        **admin.site.each_context(request), "lots": lots, "regime": regime_affiche, "libelle_regime": compta.libelle(regime_affiche),
        "onglets": onglets(regime_affiche, "conciergerie"), "historique": historique, "title": "Contrôle de la facture de conciergerie"})


@staff_member_required
def controle_conciergerie(request, pk):
    """Rapport de contrôle d'une facture de conciergerie, recalculé à chaque ouverture : après un import Booking ou l'ajout
    d'une réservation directe, les écarts corrigés disparaissent."""
    from ..lecture_factures import texte_pdf

    piece = get_object_or_404(PieceComptable.objects.select_related("bien"), pk=pk)
    lot = _lot_de_la_piece(piece)
    texte = ""
    if piece.fichier:
        try:
            with piece.fichier.open("rb") as f:
                texte = texte_pdf(f)
        except (FileNotFoundError, OSError):
            texte = ""
    if not lot or not conciergerie.est_une_facture(texte):
        messages.warning(request, "Cette pièce n'est pas une facture de conciergerie lisible : elle s'ouvre comme une facture ordinaire.")
        return redirect("admin:patrimoine_piececomptable_change", pk)
    facture = conciergerie.lire(texte)
    resultat = conciergerie.controler(lot, facture)
    regime = piece.bien.comptabilite or "lmnp"
    plateformes_absentes = sorted({l["plateforme"] for l in resultat["lignes"] if not l["reservation"] and l["plateforme"]})
    return render(request, "patrimoine/controle_conciergerie.html", {
        **admin.site.each_context(request), "piece": piece, "lot": lot, "facture": facture, **resultat,
        "pieces_liees": _pieces_liees(piece), "regime": regime, "libelle_regime": compta.libelle(regime),
        "onglets": onglets(regime, "conciergerie"), "plateformes_absentes": plateformes_absentes,
        "lien_suivi": reverse("suivi_lcd") + f"?lot={lot.pk}" + (f"&annee={resultat['mois'].year}" if resultat["mois"] else ""),
        "lien_reservation": reverse("admin:patrimoine_reservationlcd_add") + f"?lot={lot.pk}",
        "title": f"Contrôle de la facture {facture['numero'] or ''}".strip()})
