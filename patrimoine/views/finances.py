"""États des loyers, endettement, encaissements, LMNP, plus-value et annexe 2072."""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from weasyprint import HTML
from ..etats import endettement, loyers_encaisses
from ..models import Echeance, Lot, Pret, Proprietaire
from .communs import _etat_csv, _pdf, annees_disponibles


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
        "annees": annees_disponibles(),
        "aujourdhui": timezone.localdate(),
    }
    if format_ == "pdf":
        html = render_to_string("patrimoine/etat_loyers_pdf.html", contexte)
        response = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{nom_fichier}.pdf"'
        return response
    return render(request, "patrimoine/etat_loyers.html", {**admin.site.each_context(request), **contexte, "liens": True, "title": f"Loyers encaissés en {annee}" + (f" – {proprietaire.nom}" if proprietaire else "")})


@staff_member_required
def etat_loyers_detail(request):
    """Clic sur un montant de l'état encaissé : ouvre le loyer réglé ce mois-là pour ce lot (ou la liste s'il y en a plusieurs)."""
    from urllib.parse import urlencode

    from django.urls import reverse

    from ..models import EncaissementHistorique

    lot, annee, mois = int(request.GET["lot"]), int(request.GET["annee"]), int(request.GET["mois"])
    ids = list(Echeance.objects.filter(bail__lot_id=lot, paiements__date__year=annee, paiements__date__month=mois)
               .distinct().order_by("periode").values_list("pk", flat=True))
    if len(ids) == 1:
        return redirect("admin:patrimoine_echeance_change", ids[0])
    if ids:
        return redirect(reverse("admin:patrimoine_echeance_changelist") + "?" + urlencode({"id__in": ",".join(map(str, ids)), "reglement": "tous"}))
    h = EncaissementHistorique.objects.filter(lot_id=lot, mois__year=annee, mois__month=mois).first()
    if h:
        return redirect("admin:patrimoine_encaissementhistorique_change", h.pk)
    messages.info(request, "Aucun loyer trouvé pour ce montant.")
    return redirect("etat_loyers_encaisses")


@staff_member_required
def etat_endettement(request):
    try:
        a_date = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        a_date = timezone.localdate()
    proprietaire = None
    if request.GET.get("proprietaire"):
        proprietaire = get_object_or_404(Proprietaire, pk=request.GET["proprietaire"])
    etat = endettement(a_date, proprietaire)
    nom_fichier = f"endettement-{a_date:%Y-%m-%d}-{slugify(proprietaire.nom) if proprietaire else 'tous'}"
    contexte = {**etat, "proprietaire": proprietaire, "proprietaires_choix": Proprietaire.objects.all(), "aujourdhui": timezone.localdate()}

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{nom_fichier}.csv"'
        response.write("\ufeff")
        w = csv.writer(response, delimiter=";")
        fr = lambda d: f"{d:.2f}".replace(".", ",")
        w.writerow(["Emprunteur", "Banque", "N° de prêt", "Bien", "Capital emprunté", "Taux (%)", "Fin", "Mensualité + assurance", f"Intérêts {a_date.year}", f"Capital restant dû au {a_date:%d/%m/%Y}"])
        for bloc in etat["blocs"]:
            for l in bloc["lignes"]:
                p = l["pret"]
                w.writerow([p.proprietaire.nom, p.banque, p.reference, p.bien.nom if p.bien else "", fr(p.montant), fr(p.taux), f"{p.date_derniere_echeance:%m/%Y}", fr(l["mensualite"]), fr(l["interets"]), fr(l["restant"])])
        t = etat["total"]
        w.writerow(["TOTAL", "", "", "", fr(t["montant"]), "", "", fr(t["mensualite"]), fr(t["interets"]), fr(t["restant"])])
        return response
    if request.GET.get("format") == "pdf":
        return _pdf(render_to_string("patrimoine/endettement_pdf.html", contexte), nom_fichier)
    titre = f"Endettement au {a_date:%d/%m/%Y}" + (f" – {proprietaire.nom}" if proprietaire else "")
    return render(request, "patrimoine/endettement.html", {**admin.site.each_context(request), **contexte, "title": titre})


@staff_member_required
def document_amortissement(request, pk):
    pret = get_object_or_404(Pret.objects.select_related("proprietaire", "bien"), pk=pk)
    lignes = pret.echeancier()
    for i, l in enumerate(lignes):
        if i == len(lignes) - 1 or lignes[i + 1]["date"].year != l["date"].year:
            annee = [x for x in lignes if x["date"].year == l["date"].year]
            l["fin_annee"] = {c: sum(x[c] for x in annee) for c in ("mensualite", "interets", "capital", "assurance")}
    return _pdf(render_to_string("patrimoine/amortissement_pdf.html", {"pret": pret, "lignes": lignes}), f"amortissement {pret.banque} {pret.reference}")


def _decimal_saisi(valeur):
    valeur = (valeur or "").strip().replace(" ", "").replace("\u202f", "").replace(",", ".")
    return Decimal(valeur) if valeur else None


@staff_member_required
def plus_value(request):
    """Simulation de la plus-value immobilière à la revente (particuliers et SCI à l'IR)."""
    from ..models import Bien
    from ..plus_value import TAUX_PS, Simulation

    biens = Bien.objects.select_related("proprietaire").order_by("nom")
    donnees = request.GET if "prix_cession" in request.GET else {}
    bien = None
    if request.GET.get("bien"):
        bien = biens.filter(pk=request.GET["bien"]).first()
    valeurs = {
        "bien": bien.pk if bien else "",
        "date_acquisition": donnees.get("date_acquisition") or (bien.date_acquisition.isoformat() if bien and bien.date_acquisition else ""),
        "prix_acquisition": donnees.get("prix_acquisition") or (f"{bien.prix_acquisition:.0f}" if bien and bien.prix_acquisition else ""),
        "date_cession": donnees.get("date_cession") or timezone.localdate().isoformat(),
        "prix_cession": donnees.get("prix_cession", ""),
        "frais_cession": donnees.get("frais_cession", ""),
        "frais_acquisition": donnees.get("frais_acquisition", ""),
        "travaux": donnees.get("travaux", ""),
        "amortissements": donnees.get("amortissements", ""),
        "quote_part": donnees.get("quote_part", "100"),
        "taux_ps": donnees.get("taux_ps", str(TAUX_PS).replace(".", ",")),
    }
    resultat = None
    if donnees:
        try:
            simulation = Simulation(
                date_acquisition=date.fromisoformat(valeurs["date_acquisition"]),
                prix_acquisition=_decimal_saisi(valeurs["prix_acquisition"]),
                date_cession=date.fromisoformat(valeurs["date_cession"]),
                prix_cession=_decimal_saisi(valeurs["prix_cession"]),
                frais_cession=_decimal_saisi(valeurs["frais_cession"]) or Decimal(0),
                frais_acquisition_reels=_decimal_saisi(valeurs["frais_acquisition"]),
                travaux_reels=_decimal_saisi(valeurs["travaux"]),
                amortissements_lmnp=_decimal_saisi(valeurs["amortissements"]) or Decimal(0),
                quote_part=_decimal_saisi(valeurs["quote_part"]) or Decimal(100),
                taux_ps=_decimal_saisi(valeurs["taux_ps"]) or TAUX_PS,
            )
            if simulation.prix_acquisition is None or simulation.prix_cession is None:
                raise ValueError
            resultat = simulation.calculer()
        except (ValueError, InvalidOperation):
            messages.error(request, "Indiquez au moins les dates et les prix d'acquisition et de cession.")
    contexte = {
        **admin.site.each_context(request),
        "title": "Simulation de plus-value immobilière",
        "biens": biens, "bien": bien, "v": valeurs, "r": resultat,
        "regime_is": bien and bien.proprietaire.type in ("sci_is",),
    }
    return render(request, "patrimoine/plus_value.html", contexte)


@staff_member_required
def suivi_encaissements(request):
    """Finances › Suivi des encaissements : loyers dus selon les baux et bloc courte durée complété à la main."""
    from .. import encaissements

    aujourdhui = timezone.localdate()
    proprietaires = list(Proprietaire.objects.all())
    choix = request.POST.get("proprietaire") or request.GET.get("proprietaire")
    proprietaire = next((p for p in proprietaires if str(p.pk) == choix), None)
    if not proprietaire:
        avec_courte = Lot.objects.filter(exploitation="courte", vendu=False).values_list("bien__proprietaire", flat=True).first()
        proprietaire = next((p for p in proprietaires if p.pk == avec_courte), proprietaires[0] if proprietaires else None)
    annee = int(request.POST.get("annee") or request.GET.get("annee") or aujourdhui.year)

    if request.method == "POST" and proprietaire:
        n = encaissements.enregistrer(proprietaire, annee, request.POST)
        messages.success(request, f"Courte durée {annee} enregistrée ({n} mois modifié{'s' if n > 1 else ''}).")
        return redirect(f"{reverse('suivi_encaissements')}?proprietaire={proprietaire.pk}&annee={annee}")

    contexte = {**admin.site.each_context(request), "title": "Suivi des encaissements", "proprietaires": proprietaires,
                "proprietaire": proprietaire, "annee": annee, "annees": range(aujourdhui.year - 3, aujourdhui.year + 2),
                "champs_lcd": [("ca", "CA"), ("aircover", "AirCover"), ("menage", "Ménage"), ("commission", "Commission"), ("frais", "Frais"), ("regul", "Régul.")]}
    if proprietaire:
        contexte.update(encaissements.suivi(proprietaire, annee))
    return render(request, "patrimoine/suivi_encaissements.html", contexte)


@staff_member_required
def biens_lmnp(request):
    """Courte durée › Biens en LMNP : recettes, charges et résultat de l'année par bien."""
    from .. import lmnp

    aujourdhui = timezone.localdate()
    annee = int(request.GET.get("annee") or aujourdhui.year)
    contexte = {**admin.site.each_context(request), "title": f"Biens en LMNP – {annee}", "annee": annee,
                "annees": annees_disponibles(), **lmnp.bilan(annee)}
    return render(request, "patrimoine/lmnp.html", contexte)


@staff_member_required
def annexe_2072(request):
    """Annexe 2072-S-A1-SD pré-remplie : SCI, immeubles et année au choix ; détail des sources sous le tableau."""
    from django.db.models import Q

    from .. import annexe2072

    aujourdhui = timezone.localdate()
    scis = list(Proprietaire.objects.filter(type=Proprietaire.Type.SCI_IR).order_by("nom")) or \
        list(Proprietaire.objects.exclude(type=Proprietaire.Type.NOM_PROPRE).order_by("nom"))
    sci = next((p for p in scis if str(p.pk) == request.GET.get("sci")), scis[0] if scis else None)
    annee = int(request.GET.get("annee") or aujourdhui.year)
    biens_sci = list(sci.biens.filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).order_by("nom")) if sci else []
    choisis = request.GET.getlist("bien")
    biens = [b for b in biens_sci if str(b.pk) in choisis] if choisis else biens_sci
    contexte = {"sci": sci, "scis": scis, "annee": annee, "annees": range(aujourdhui.year - 10, aujourdhui.year + 1),
                "biens_sci": biens_sci, "biens": biens, "choisis": [b.pk for b in biens], **annexe2072.annexe(biens, annee)}
    contexte["cadre_i"] = annexe2072.part_personnelle(contexte["resultat"], sci.pourcentage_detention if sci else 100)
    contexte["tous_les_biens"] = len(biens) == len(biens_sci)
    if request.GET.get("format") == "pdf":
        return _pdf(render_to_string("patrimoine/annexe_2072_pdf.html", contexte), f"2072-S-A1 {annee} {sci.nom if sci else ''}")
    return render(request, "patrimoine/annexe_2072.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Annexe 2072-S-A1 – revenus {annee}"})
