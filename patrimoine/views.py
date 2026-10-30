import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from weasyprint import HTML

from .documents import ELEMENTS_PIECE, INVENTAIRE_MEUBLE, duree_texte
from .etats import endettement, loyers_encaisses
from .models import Bail, Echeance, EtatDesLieux, Lot, MouvementDepot, Pret, Proprietaire, Rappel, RevisionLoyer, Tache


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


@staff_member_required
@require_POST
def rappel_fait(request, type_rappel, pk):
    """Bouton « Fait » du bloc À faire de la page d'accueil."""
    aujourdhui = timezone.localdate()
    if type_rappel == "assurance":
        Bail.objects.filter(pk=pk).update(date_attestation_assurance=aujourdhui)
        messages.success(request, "Attestation d'assurance notée comme reçue aujourd'hui.")
    elif type_rappel == "chaudiere":
        Lot.objects.filter(pk=pk).update(date_entretien_chaudiere=aujourdhui)
        messages.success(request, "Entretien de la chaudière noté à la date d'aujourd'hui.")
    elif type_rappel == "rappel":
        get_object_or_404(Rappel, pk=pk).marquer_fait()
        messages.success(request, "Rappel marqué comme fait.")
    elif type_rappel == "tache":
        tache = get_object_or_404(Tache, pk=pk)
        tache.faite = True
        tache.save()
        messages.success(request, "Tâche cochée.")
    return redirect("admin:index")


def _mail_revision(revision):
    bail = revision.bail
    locataires = list(bail.locataires.all())
    proprietaire = bail.lot.bien.proprietaire
    destinataires = ",".join(l.email for l in locataires if l.email)
    objet = f"Révision annuelle de votre loyer – {bail.lot.bien.adresse}"
    corps = (
        f"Bonjour {' et '.join(str(l) for l in locataires)},\n\n"
        f"Conformément à votre bail, votre loyer est révisé chaque année selon l'indice de référence des loyers (IRL) publié par l'INSEE.\n\n"
        f"À compter du {revision.date_effet:%d/%m/%Y}, votre loyer hors charges passe de {revision.ancien_loyer:.2f} € à {revision.nouveau_loyer:.2f} € "
        f"(IRL {revision.trimestre} : {revision.nouvel_irl} / IRL précédent : {revision.ancien_irl}).\n"
        f"Avec les charges de {bail.charges:.2f} €, votre nouveau loyer mensuel est de {revision.nouveau_loyer + bail.charges:.2f} €.\n\n"
        f"Merci d'ajuster votre virement à partir de cette échéance.\n\n"
        f"Cordialement,\n{proprietaire.representant or proprietaire.nom}"
    )
    return f"mailto:{destinataires}?subject={quote(objet)}&body={quote(corps)}"


def _dernier_irl_publie(trimestre, a_date):
    """Libellé du dernier IRL publié pour ce trimestre à cette date (l'INSEE publie vers le 15 du mois suivant le trimestre)."""
    if not trimestre:
        return ""
    mois_publication = {"T1": 4, "T2": 7, "T3": 10, "T4": 1}[trimestre]
    annee = a_date.year
    while True:
        publication = date(annee + (1 if trimestre == "T4" else 0), mois_publication, 15)
        if publication <= a_date:
            return f"{trimestre} {annee}"
        annee -= 1


@staff_member_required
def revision_loyer(request, pk):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    revision = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "appliquer":
            revision = get_object_or_404(RevisionLoyer, pk=request.POST["revision"], bail=bail)
            revision.appliquer()
            messages.success(request, f"Nouveau loyer de {revision.nouveau_loyer} € appliqué au bail. Les prochains loyers générés l'utiliseront.")
            return redirect(f"{reverse('revision_loyer', args=[bail.pk])}?revision={revision.pk}")
        try:
            ancien_irl = Decimal(request.POST["ancien_irl"].replace(",", "."))
            nouvel_irl = Decimal(request.POST["nouvel_irl"].replace(",", "."))
            date_effet = date.fromisoformat(request.POST["date_effet"])
        except (KeyError, ValueError, InvalidOperation):
            messages.error(request, "Indiquez l'ancien et le nouvel IRL (ex. 145,47) et la date d'effet.")
        else:
            revision = RevisionLoyer.objects.create(
                bail=bail, date_effet=date_effet, ancien_loyer=bail.loyer_hc, ancien_irl=ancien_irl, nouvel_irl=nouvel_irl,
                trimestre=request.POST.get("trimestre", ""), nouveau_loyer=RevisionLoyer.calculer(bail.loyer_hc, ancien_irl, nouvel_irl),
            )
            return redirect(f"{reverse('revision_loyer', args=[bail.pk])}?revision={revision.pk}")
    elif request.GET.get("revision"):
        revision = get_object_or_404(RevisionLoyer, pk=request.GET["revision"], bail=bail)

    prochaine = bail.prochaine_revision() or timezone.localdate()
    contexte = {
        **admin.site.each_context(request),
        "title": f"Révision du loyer – {bail.lot}",
        "bail": bail,
        "locataires": bail.locataires.all(),
        "revision": revision,
        "mailto": _mail_revision(revision) if revision else None,
        "date_effet": prochaine,
        "trimestre_propose": _dernier_irl_publie(bail.irl_trimestre, prochaine),
        "dpe_gele": bail.lot.classe_dpe in ("F", "G"),
        "historique": bail.revisions.all(),
    }
    return render(request, "patrimoine/revision_loyer.html", contexte)


@staff_member_required
def lettre_revision(request, pk):
    revision = get_object_or_404(RevisionLoyer.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    contexte = _contexte_bail(revision.bail)
    contexte.update(r=revision, aujourdhui=timezone.localdate())
    return _pdf(render_to_string("patrimoine/lettre_revision.html", contexte), f"revision loyer {revision.bail.lot} {revision.date_effet:%Y}")
