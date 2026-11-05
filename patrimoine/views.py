import csv
import io
import zipfile
from datetime import date, datetime
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
from .forms import CandidatureForm
from .models import PieceCandidature, Bail, Candidature, Echeance, EtatDesLieux, Garant, JournalFait, Lot, MouvementDepot, Pret, Proprietaire, Rappel, RevisionLoyer, Tache


def annees_disponibles():
    """Années proposées dans les états : de la plus ancienne donnée (ancien suivi compris) à l'année prochaine."""
    from .models import EncaissementHistorique
    courante = timezone.localdate().year
    premier = EncaissementHistorique.objects.order_by("mois").values_list("mois", flat=True).first()
    debut = min(courante - 6, premier.year) if premier else courante - 6
    return range(courante + 1, debut - 1, -1)


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
        "annees": annees_disponibles(),
        "aujourdhui": timezone.localdate(),
    }
    if format_ == "pdf":
        html = render_to_string("patrimoine/etat_loyers_pdf.html", contexte)
        response = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{nom_fichier}.pdf"'
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
        "garants": list(bail.garants.select_related("locataire")),
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
    edl = EtatDesLieux(bail=bail, type=type_edl, date=None)
    edl.reprendre_index_du_bail()
    return _rendu_edl(bail, edl)


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


CIVILITES_LONGUES = {"M.": "Monsieur", "Mme": "Madame"}


def _montant_fr(montant):
    texte = f"{montant:,.2f}".replace(",", " ").replace(".", ",")
    return texte[:-3] if texte.endswith(",00") else texte


def _recu_depot_word(bail, montant):
    from . import champs
    from .models import ModeleDocument
    from .word import remplir_docx

    locataires = list(bail.locataires.all())
    valeurs = champs.valeurs(bail)
    if len(locataires) > 1:
        valeurs.update({"Civilité_Loc": "Madame, Monsieur", "Prénom_Loc": "",
                        "Nom_Loc": " et ".join(f"{l.prenom} {l.nom}".strip() for l in locataires)})
    valeurs.update({"Dépôt_Gar": _montant_fr(montant), "Dépôt_Gar_lettres": champs.euros_en_lettres(montant)})
    modele = ModeleDocument.objects.filter(type="recu_depot", actif=True).order_by("-modifie_le").first()
    contenu = remplir_docx(modele.fichier.open("rb") if modele else "recu_depot_garantie.docx", valeurs)
    return _reponse_docx(contenu, f"Reçu dépôt de garantie {bail.lot} {valeurs['Nom_Loc']}")


def _reponse_docx(contenu, nom):
    reponse = HttpResponse(contenu, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    nom_fichier = f"{nom}.docx".replace("/", "-")
    reponse["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(nom_fichier)}"
    return reponse


@staff_member_required
def remplir_modele(request, pk, bail_pk):
    """Modèle Word de l'utilisateur rempli avec les données du bail (et du type d'état des lieux demandé)."""
    from . import champs
    from .models import ModeleDocument
    from .word import remplir_docx

    modele = get_object_or_404(ModeleDocument, pk=pk)
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=bail_pk)
    type_edl = request.GET.get("edl") if request.GET.get("edl") in ("entree", "sortie") else None
    with modele.fichier.open("rb") as f:
        contenu = remplir_docx(f, champs.valeurs(bail, type_edl=type_edl))
    noms = " ".join(l.nom for l in bail.locataires.all())
    suffixe = {"entree": " entrée", "sortie": " sortie"}.get(type_edl, "")
    return _reponse_docx(contenu, f"{modele.nom}{suffixe} {bail.lot} {noms}".strip())


@staff_member_required
def liste_champs(request):
    """Modèles › Liste des champs : nom à écrire entre accolades, description, et valeur pour un bail choisi."""
    from . import champs
    from .word import normaliser

    baux = Bail.objects.select_related("lot__bien").prefetch_related("locataires").order_by("-actif", "lot__bien__nom")
    bail = baux.filter(pk=request.GET.get("bail")).first() if request.GET.get("bail") else baux.filter(actif=True).first()
    exemple = {normaliser(k): v for k, v in champs.valeurs(bail).items()} if bail else {}
    groupes = [(titre, [(nom, desc, exemple.get(normaliser(nom), "")) for nom, desc in liste]) for titre, liste in champs.CHAMPS]
    return render(request, "patrimoine/liste_champs.html", {
        **admin.site.each_context(request), "title": "Champs des modèles Word", "groupes": groupes, "baux": baux, "bail": bail})


@staff_member_required
def recu_depot_mouvement(request, pk):
    m = get_object_or_404(MouvementDepot.objects.select_related("bail__lot__bien__proprietaire"), pk=pk, type=MouvementDepot.Type.ENCAISSEMENT)
    return _recu_depot_word(m.bail, m.montant)


@staff_member_required
def recu_depot_bail(request, pk):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    return _recu_depot_word(bail, bail.depot_garantie)


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


def _lieu(bail):
    noms = ", ".join(str(l) for l in bail.locataires.all())
    return f"{bail.lot} – {noms}" if noms else str(bail.lot)


@staff_member_required
@require_POST
def rappel_fait(request, type_rappel, pk):
    """Bouton « Fait » du bloc À faire de la page d'accueil."""
    aujourdhui = timezone.localdate()
    if type_rappel == "assurance":
        bail = get_object_or_404(Bail, pk=pk)
        Bail.objects.filter(pk=pk).update(date_attestation_assurance=aujourdhui)
        JournalFait.objects.create(titre="Attestation d'assurance habitation reçue", detail=_lieu(bail), categorie="Assurance",
                                   url=reverse("admin:patrimoine_bail_change", args=[pk]))
        messages.success(request, "Attestation d'assurance notée comme reçue aujourd'hui.")
    elif type_rappel == "chaudiere":
        lot = get_object_or_404(Lot, pk=pk)
        Lot.objects.filter(pk=pk).update(date_entretien_chaudiere=aujourdhui)
        JournalFait.objects.create(titre="Attestation d'entretien de la chaudière reçue", detail=str(lot), categorie="Chaudière",
                                   url=reverse("admin:patrimoine_lot_change", args=[pk]))
        messages.success(request, "Entretien de la chaudière noté à la date d'aujourd'hui.")
    elif type_rappel == "revision":
        # Révision traitée hors de l'appli, ou volontairement non appliquée cette année.
        bail = get_object_or_404(Bail, pk=pk)
        echeance = bail.prochaine_revision(aujourdhui)
        if echeance:
            Bail.objects.filter(pk=pk).update(date_derniere_revision=echeance)
            JournalFait.objects.create(titre=f"Révision du loyer du {echeance:%d/%m/%Y} traitée", detail=_lieu(bail), categorie="Révision",
                                       url=reverse("admin:patrimoine_bail_change", args=[pk]))
        messages.success(request, "Révision notée comme traitée.")
    elif type_rappel == "rappel":
        rappel = get_object_or_404(Rappel, pk=pk)
        JournalFait.objects.create(titre=rappel.titre, detail=str(rappel.bien or ""), categorie=rappel.get_categorie_display(),
                                   url=reverse("admin:patrimoine_rappel_change", args=[pk]))
        rappel.marquer_fait()
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


def _verifier_pieces(request):
    """Justificatifs : 10 Mo maximum par fichier, PDF ou photo ; renvoie les erreurs par type de pièce."""
    from .forms import EXTENSIONS_PIECE, TAILLE_MAX_PIECE

    erreurs = {}
    for type_piece, _ in PieceCandidature.Type.choices:
        for f in request.FILES.getlist(f"piece-{type_piece}"):
            if f.size > TAILLE_MAX_PIECE:
                erreurs[type_piece] = f"« {f.name} » est trop lourd : 10 Mo maximum par fichier."
            elif not f.name.lower().endswith(EXTENSIONS_PIECE):
                erreurs[type_piece] = f"« {f.name} » : formats acceptés PDF, JPG, PNG, HEIC."
    return erreurs


def candidature_publique(request, jeton):
    """Formulaire envoyé au futur locataire : accessible sans compte, via un lien personnel."""
    from .forms import GarantsFormSet

    candidature = get_object_or_404(Candidature.objects.select_related("lot__bien"), jeton=jeton)
    lot = candidature.lot
    ferme = candidature.statut in (Candidature.Statut.RETENUE, Candidature.Statut.REFUSEE)
    if request.method == "POST" and not ferme:
        form = CandidatureForm(request.POST, instance=candidature)
        garants = GarantsFormSet(request.POST, request.FILES, instance=candidature, prefix="garants")
        avec_garant = (candidature.type_garantie or request.POST.get("type_garantie")) == "garant"
        if avec_garant:
            garants.forms[0].empty_permitted = False
        erreurs_pieces = _verifier_pieces(request)
        if form.is_valid() and (not avec_garant or garants.is_valid()) and not erreurs_pieces:
            candidature = form.save(commit=False)
            candidature.statut = Candidature.Statut.RECUE
            candidature.recue_le = timezone.now()
            candidature.save()
            if avec_garant:
                garants.instance = candidature
                garants.save()
            for type_piece, _ in PieceCandidature.Type.choices:
                for f in request.FILES.getlist(f"piece-{type_piece}"):
                    PieceCandidature.objects.create(candidature=candidature, type=type_piece, fichier=f)
            from . import mails

            mails.dossier_recu(candidature, request)
            return render(request, "patrimoine/candidature_merci.html", {"candidature": candidature, "lot": lot})
    else:
        form = CandidatureForm(instance=candidature)
        garants = GarantsFormSet(instance=candidature, prefix="garants")
        erreurs_pieces = {}
    types_pieces = [{"code": c, "libelle": l, "erreur": erreurs_pieces.get(c), "deja": candidature.pieces.filter(type=c).count()}
                    for c, l in PieceCandidature.Type.choices]
    return render(request, "patrimoine/candidature_formulaire.html", {
        "form": form, "garants": garants, "types_pieces": types_pieces, "candidature": candidature, "lot": lot, "bien": lot.bien, "ferme": ferme,
        "loyer_indicatif": lot.loyer_reference,
    })


@staff_member_required
def acte_cautionnement(request, pk):
    garant = get_object_or_404(Garant.objects.select_related("locataire", "bail__lot__bien__proprietaire"), pk=pk)
    if not garant.bail:
        messages.error(request, "Rattachez d'abord le garant à un bail pour générer l'acte de cautionnement.")
        return redirect("admin:patrimoine_garant_change", garant.pk)
    contexte = _contexte_bail(garant.bail)
    contexte.update(g=garant, montant=garant.montant_garanti(), duree_texte=duree_texte(garant.bail))
    return _pdf(render_to_string("patrimoine/acte_cautionnement.html", contexte), f"acte cautionnement {garant}")


@staff_member_required
def photos_zip(request, pk):
    """Toutes les photos du lot dans un fichier zip, numérotées dans l'ordre, prêtes à déposer sur l'annonce."""
    lot = get_object_or_404(Lot, pk=pk)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for i, photo in enumerate(lot.photos.all(), start=1):
            extension = photo.image.name.rsplit(".", 1)[-1].lower()
            nom = slugify(photo.legende) or "photo"
            with photo.image.open("rb") as f:
                archive.writestr(f"{i:02d}-{nom}.{extension}", f.read())
    response = HttpResponse(tampon.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="photos-{slugify(str(lot))}.zip"'
    return response


def _decimal_saisi(valeur):
    valeur = (valeur or "").strip().replace(" ", "").replace("\u202f", "").replace(",", ".")
    return Decimal(valeur) if valeur else None


@staff_member_required
def plus_value(request):
    """Simulation de la plus-value immobilière à la revente (particuliers et SCI à l'IR)."""
    from .models import Bien
    from .plus_value import TAUX_PS, Simulation

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
    from . import lcd

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


@staff_member_required
def suivi_encaissements(request):
    """Finances › Suivi des encaissements : loyers dus selon les baux et bloc courte durée complété à la main."""
    from . import encaissements

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
    from . import lmnp

    aujourdhui = timezone.localdate()
    annee = int(request.GET.get("annee") or aujourdhui.year)
    contexte = {**admin.site.each_context(request), "title": f"Biens en LMNP – {annee}", "annee": annee,
                "annees": annees_disponibles(), **lmnp.bilan(annee)}
    return render(request, "patrimoine/lmnp.html", contexte)


@staff_member_required
def fichier_prive(request, chemin):
    """Pièces d'identité des garants : visibles seulement une fois connecté à l'administration."""
    from django.core.exceptions import SuspiciousFileOperation
    from django.http import FileResponse, Http404

    from .models import stockage_prive

    stockage = stockage_prive()
    try:
        if not stockage.exists(chemin):
            raise Http404
    except SuspiciousFileOperation:
        raise Http404
    return FileResponse(stockage.open(chemin, "rb"), filename=chemin.rsplit("/", 1)[-1])


@staff_member_required
def sauvegardes(request):
    """Réglages › Sauvegardes : liste des sauvegardes du serveur, création et téléchargement."""
    from . import sauvegarde

    if request.method == "POST":
        fichier = sauvegarde.creer()
        messages.success(request, f"Sauvegarde créée : {fichier.name}. Elle se télécharge, gardez-la sur votre ordinateur.")
        return redirect(f"{reverse('sauvegardes')}?telecharger={quote(fichier.name)}")
    fichiers = [{"nom": f.name, "date": datetime.fromtimestamp(f.stat().st_mtime), "taille": f.stat().st_size}
                for f in sauvegarde.liste()]
    return render(request, "patrimoine/sauvegardes.html", {
        **admin.site.each_context(request), "title": "Sauvegardes", "fichiers": fichiers, "a_garder": sauvegarde.A_GARDER,
        "telecharger": request.GET.get("telecharger", ""),
    })


@staff_member_required
def telecharger_sauvegarde(request, nom):
    from django.http import FileResponse, Http404

    from . import sauvegarde

    fichier = next((f for f in sauvegarde.liste() if f.name == nom), None)
    if fichier is None:
        raise Http404
    return FileResponse(fichier.open("rb"), as_attachment=True, filename=fichier.name)


@staff_member_required
@require_POST
def envoyer_lien_candidature(request, pk):
    from . import mails

    c = get_object_or_404(Candidature.objects.select_related("lot__bien"), pk=pk)
    email = request.POST.get("email", "").strip() or c.email
    if email and email != c.email:
        c.email = email
        c.save(update_fields=["email"])
    if not email:
        messages.error(request, "Renseignez d'abord l'e-mail du candidat.")
    elif mails.envoyer([email], f"Votre dossier de candidature – {c.lot.designation}", mails.texte_invitation(c, mails.lien_candidature(c, request))):
        messages.success(request, f"Lien envoyé à {email}.")
    else:
        messages.error(request, "Le mail n'est pas parti : vérifiez le réglage Réglages › Mails (adresse et mot de passe d'application).")
    return redirect("admin:patrimoine_candidature_change", pk)


@staff_member_required
@require_POST
def tester_mail(request):
    from . import mails

    r = mails.reglage()
    try:
        mails.envoyer([r.adresse], "Test – Gestion immobilière", "Ce mail confirme que l'application peut envoyer des mails.", r, lever=True)
        messages.success(request, f"Mail de test envoyé à {r.adresse} : vérifiez votre boîte.")
    except Exception as erreur:
        messages.error(request, f"Échec de l'envoi ({erreur.__class__.__name__}). Vérifiez l'adresse et le mot de passe d'application (16 lettres).")
    return redirect("admin:patrimoine_parametresmail_change", 1)


@staff_member_required
def recherche(request):
    """Recherche globale (barre du haut) : locataires, biens et lots, baux, garants, candidatures, prêts, syndics."""
    from django.db.models import Q

    from .models import Bien, Garant, Locataire, Lot, Pret, Syndic

    q = (request.GET.get("q") or "").strip()
    groupes = []
    if len(q) >= 2:
        mots = q.split()

        def filtre(champs):
            condition = Q()
            for mot in mots:
                sous = Q()
                for c in champs:
                    sous |= Q(**{f"{c}__icontains": mot})
                condition &= sous
            return condition

        def ajouter(titre, objets, lien, detail=lambda o: ""):
            lignes = [{"titre": str(o), "detail": detail(o), "url": reverse(lien, args=[o.pk])} for o in objets[:15]]
            if lignes:
                groupes.append({"titre": titre, "lignes": lignes})

        ajouter("Locataires", Locataire.objects.filter(filtre(["nom", "prenom", "email", "telephone"])).prefetch_related("baux__lot__bien"),
                "admin:patrimoine_locataire_change",
                lambda o: " · ".join(dict.fromkeys(str(b.lot) for b in o.baux.all())) or o.email)
        ajouter("Biens", Bien.objects.filter(filtre(["nom", "adresse", "ville", "code_postal"])).select_related("proprietaire"),
                "admin:patrimoine_bien_change", lambda o: f"{o.adresse}, {o.ville} · {o.proprietaire}{' · vendu' if o.vendu else ''}")
        ajouter("Lots", Lot.objects.filter(filtre(["designation", "bien__nom", "bien__ville"])).select_related("bien"),
                "admin:patrimoine_lot_change", lambda o: f"{o.get_type_display()}{' · vendu' if o.vendu else ''}")
        ajouter("Baux", Bail.objects.filter(filtre(["locataires__nom", "locataires__prenom", "lot__designation", "lot__bien__nom"]))
                .distinct().select_related("lot__bien").prefetch_related("locataires"),
                "admin:patrimoine_bail_change", lambda o: ", ".join(str(l) for l in o.locataires.all()) + ("" if o.actif else " · terminé"))
        ajouter("Garants", Garant.objects.filter(filtre(["nom", "prenom", "email"])).select_related("locataire"),
                "admin:patrimoine_garant_change", lambda o: f"garant de {o.locataire}")
        ajouter("Candidatures", Candidature.objects.filter(filtre(["nom", "prenom", "email"])).select_related("lot__bien"),
                "admin:patrimoine_candidature_change", lambda o: f"{o.lot} · {o.get_statut_display()}")
        ajouter("Prêts", Pret.objects.filter(filtre(["banque", "reference", "bien__nom"])).select_related("proprietaire"),
                "admin:patrimoine_pret_change", lambda o: str(o.proprietaire))
        ajouter("Syndics", Syndic.objects.filter(filtre(["nom", "ville"])), "admin:patrimoine_syndic_change")
    return render(request, "patrimoine/recherche.html", {
        **admin.site.each_context(request), "title": f"Recherche : {q}" if q else "Recherche", "q": q, "groupes": groupes,
        "nb": sum(len(g["lignes"]) for g in groupes),
    })
