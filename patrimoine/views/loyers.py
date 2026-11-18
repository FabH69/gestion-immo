"""Avis d'échéance, quittances, révisions de loyer et relances d'impayés."""
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
from weasyprint import HTML
from ..models import Bail, Echeance, RevisionLoyer
from .communs import _contexte_bail, _pdf


def pdf_echeance(echeance):
    """(contenu PDF, nom du fichier, est_quittance) : quittance si l'échéance est soldée, sinon avis d'échéance."""
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
    return HTML(string=html).write_pdf(), nom, est_quittance


@staff_member_required
def document_echeance(request, pk):
    """Quittance si l'échéance est soldée, sinon avis d'échéance (une quittance ne se délivre que pour un loyer payé en entier)."""
    echeance = get_object_or_404(Echeance.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    contenu, nom, _ = pdf_echeance(echeance)
    response = HttpResponse(contenu, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{nom}"'
    return response


MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _noms_locataires(bail):
    return ", ".join(" ".join(x for x in [l.nom.upper(), l.prenom] if x) for l in bail.locataires.all()) or "sans locataire"


@staff_member_required
def envoyer_quittance(request):
    """Depuis le tableau de bord : choix du bail (lot + locataires) puis du mois ; PDF ou mail préparé pour Outlook."""
    baux = list(Bail.objects.select_related("lot__bien").prefetch_related("locataires")
                .order_by("-actif", "lot__bien__nom", "lot__designation", "-date_debut"))
    choix_baux = [{"pk": b.pk, "libelle": f"{b.lot.bien.nom} – {b.lot.designation} : {_noms_locataires(b)}"
                   + ("" if b.actif else " (terminé)"), "actif": b.actif} for b in baux]
    bail = next((b for b in baux if str(b.pk) == request.GET.get("bail")), None)
    echeances, echeance = [], None
    if bail:
        echeances = list(bail.echeances.order_by("-periode"))
        echeance = next((e for e in echeances if str(e.pk) == request.GET.get("mois")), None)
        if echeance is None:  # par défaut : le dernier mois payé
            echeance = next((e for e in echeances if e.statut == Echeance.Statut.PAYE), echeances[0] if echeances else None)
    choix_mois = [{"pk": e.pk, "libelle": f"{MOIS_FR[e.periode.month - 1].capitalize()} {e.periode.year}"
                   + ("" if e.statut == Echeance.Statut.PAYE else f" – {e.get_statut_display().lower()}")} for e in echeances]
    emails = [l.email for l in bail.locataires.all() if l.email] if bail else []
    return render(request, "patrimoine/envoyer_quittance.html", {
        **admin.site.each_context(request), "title": "Créer une quittance", "baux": choix_baux, "bail": bail,
        "mois": choix_mois, "echeance": echeance, "emails": emails,
        "est_quittance": echeance is not None and echeance.statut == Echeance.Statut.PAYE})


@staff_member_required
def preparer_mail_quittance(request, pk):
    """Brouillon .eml (quittance en pièce jointe, texte d'accompagnement) qui s'ouvre dans Outlook prêt à envoyer."""
    from email.message import EmailMessage as Brouillon
    from email.policy import SMTP

    from .. import mails

    e = get_object_or_404(Echeance.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    contenu, nom, est_quittance = pdf_echeance(e)
    locataires = list(e.bail.locataires.all())
    mois = f"{MOIS_FR[e.periode.month - 1]} {e.periode.year}"
    brouillon = Brouillon(policy=SMTP)
    brouillon["Subject"] = f"{'Quittance de loyer' if est_quittance else 'Avis d’échéance'} – {mois}"
    destinataires = [l.email for l in locataires if l.email]
    if destinataires:
        brouillon["To"] = ", ".join(destinataires)
    brouillon["X-Unsent"] = "1"
    brouillon.set_content(mails.texte_quittance(e, locataires, mois, est_quittance))
    brouillon.add_alternative(mails.html_quittance(e, locataires, mois, est_quittance), subtype="html")
    joint = f"{'quittance' if est_quittance else 'avis-echeance'}-{e.periode:%Y-%m}-{slugify(e.bail.lot.bien.ville or 'loyer')}.pdf"
    brouillon.add_attachment(contenu, maintype="application", subtype="pdf", filename=joint)
    reponse = HttpResponse(brouillon.as_bytes(), content_type="message/rfc822")
    reponse["Content-Disposition"] = f'attachment; filename="{nom[:-4]}.eml"'
    return reponse  # PDF joint nommé d'après la ville, pas d'après le nom interne du logement


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


def _niveau_relance(request, bail, lignes):
    from .. import relances
    from ..models import Relance

    niveau = request.GET.get("niveau")
    return niveau if niveau in Relance.Niveau.values else relances.niveau_suggere(bail, lignes)


@staff_member_required
def relances_impayes(request):
    """Liste des baux en impayé, puis pour un bail : loyers dus, niveau de relance proposé, mail ou lettre, historique."""
    from .. import droits, relances
    from ..models import Relance

    contexte = {**admin.site.each_context(request), "title": "Relancer un impayé"}
    bail = Bail.objects.filter(pk=request.GET.get("bail") or 0).select_related("lot__bien__proprietaire").first()
    if bail:
        lignes = relances.impayes(bail)
        contexte.update(detail={"bail": bail, "lignes": lignes, "total": sum((r for _, r in lignes), Decimal(0))},
                        niveau=_niveau_relance(request, bail, lignes), niveaux=Relance.Niveau.choices,
                        historique=bail.relances.all()[:30],
                        peut_modifier=bool(lignes) and droits.niveau(request.user, "loyers") == droits.MODIFICATION)
    else:
        baux = relances.baux_en_impaye()
        for b in baux:
            b["suggere_libelle"] = Relance.Niveau(b["suggere"]).label.split(". ", 1)[-1]
        contexte["baux"] = baux
    return render(request, "patrimoine/relances.html", contexte)


def _pdf_relance(bail, niveau, lignes):
    from .. import relances
    from ..models import Relance

    personnes = relances.destinataires(bail, niveau)
    garant = niveau == Relance.Niveau.GARANT
    html = render_to_string("patrimoine/relance_pdf.html", {
        "c": relances.courrier(bail, niveau, lignes), "lignes": lignes, "personnes": personnes, "bien": bail.lot.bien,
        "proprietaire": bail.lot.bien.proprietaire, "aujourdhui": timezone.localdate(), "garant": garant,
        "adresse_garant": personnes[0].adresse if garant and personnes else "",
        "appel": relances._bonjour(personnes), "recommande": niveau == Relance.Niveau.MISE_EN_DEMEURE})
    nom = f"{Relance.Niveau(niveau).label.split('. ', 1)[-1]} {bail.lot.bien.ville} {timezone.localdate():%Y-%m-%d}"
    return HTML(string=html).write_pdf(), slugify(nom) + ".pdf"


@staff_member_required
def lettre_relance(request, pk):
    from .. import relances
    from ..models import Relance

    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    lignes = relances.impayes(bail)
    if not lignes:
        messages.info(request, "Plus aucun loyer impayé sur ce bail.")
        return redirect(f"{reverse('relances_impayes')}?bail={pk}")
    niveau = _niveau_relance(request, bail, lignes)
    contenu, nom = _pdf_relance(bail, niveau, lignes)
    relances.enregistrer(bail, niveau, lignes, Relance.Mode.COURRIER)
    reponse = HttpResponse(contenu, content_type="application/pdf")
    reponse["Content-Disposition"] = f'inline; filename="{nom}"'
    return reponse


@staff_member_required
def preparer_mail_relance(request, pk):
    """Brouillon .eml de relance (texte + lettre PDF jointe) qui s'ouvre dans Outlook, prêt à envoyer."""
    from email.message import EmailMessage as Brouillon
    from email.policy import SMTP

    from .. import relances
    from ..models import Relance

    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    lignes = relances.impayes(bail)
    if not lignes:
        messages.info(request, "Plus aucun loyer impayé sur ce bail.")
        return redirect(f"{reverse('relances_impayes')}?bail={pk}")
    niveau = _niveau_relance(request, bail, lignes)
    contenu, nom = _pdf_relance(bail, niveau, lignes)
    brouillon = Brouillon(policy=SMTP)
    brouillon["Subject"] = relances.OBJETS[niveau]
    adresses = [p.email for p in relances.destinataires(bail, niveau) if p.email]
    if adresses:
        brouillon["To"] = ", ".join(adresses)
    brouillon["X-Unsent"] = "1"
    brouillon.set_content(relances.texte_mail(bail, niveau, lignes))
    brouillon.add_attachment(contenu, maintype="application", subtype="pdf", filename=nom)
    relances.enregistrer(bail, niveau, lignes, Relance.Mode.MAIL)
    reponse = HttpResponse(brouillon.as_bytes(), content_type="message/rfc822")
    reponse["Content-Disposition"] = f'attachment; filename="{nom[:-4]}.eml"'
    return reponse
