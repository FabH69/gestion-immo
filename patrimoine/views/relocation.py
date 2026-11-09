"""Candidatures (formulaire public et mails), photos et annonces."""
import io
import zipfile

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from ..forms import CandidatureForm
from ..models import PieceCandidature, Candidature, Lot
from .communs import _pdf


def _verifier_pieces(request):
    """Justificatifs : 10 Mo maximum par fichier, PDF ou photo ; renvoie les erreurs par type de pièce."""
    from ..forms import EXTENSIONS_PIECE, TAILLE_MAX_PIECE

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
    from ..forms import GarantsFormSet

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
            from .. import mails

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


@staff_member_required
def preparer_mail_candidature(request, pk):
    """Brouillon .eml mis en forme : Outlook l'ouvre comme un nouveau mail prêt à envoyer (en-tête X-Unsent)."""
    from email.message import EmailMessage as Brouillon
    from email.policy import SMTP

    from .. import mails

    c = get_object_or_404(Candidature.objects.select_related("lot__bien"), pk=pk)
    lien = mails.lien_candidature(c, request)
    brouillon = Brouillon(policy=SMTP)
    brouillon["Subject"] = f"Votre dossier de location – {c.lot.designation}"
    if c.email:
        brouillon["To"] = c.email
    brouillon["X-Unsent"] = "1"
    brouillon.set_content(mails.texte_invitation(c, lien))
    brouillon.add_alternative(mails.html_invitation(c, lien), subtype="html")
    reponse = HttpResponse(brouillon.as_bytes(), content_type="message/rfc822")
    reponse["Content-Disposition"] = f'attachment; filename="invitation-candidature-{c.pk}.eml"'
    return reponse


@staff_member_required
@require_POST
def envoyer_lien_candidature(request, pk):
    from .. import mails

    c = get_object_or_404(Candidature.objects.select_related("lot__bien"), pk=pk)
    email = request.POST.get("email", "").strip() or c.email
    if email and email != c.email:
        c.email = email
        c.save(update_fields=["email"])
    if not email:
        messages.error(request, "Renseignez d'abord l'e-mail du candidat.")
    elif mails.envoyer([email], f"Votre dossier de location – {c.lot.designation}",
                       mails.texte_invitation(c, lien := mails.lien_candidature(c, request)),
                       html=mails.html_invitation(c, lien)):
        messages.success(request, f"Lien envoyé à {email}.")
    else:
        messages.error(request, "Le mail n'est pas parti : vérifiez le réglage Réglages › Mails (adresse et mot de passe d'application).")
    return redirect("admin:patrimoine_candidature_change", pk)


@staff_member_required
def annonce_pdf(request, pk):
    """Annonce en PDF : titre, chiffres clés, photos du lot et texte (pour l'imprimer ou l'envoyer)."""
    from pathlib import Path

    from ..models import Annonce

    a = get_object_or_404(Annonce.objects.select_related("lot__bien"), pk=pk)
    photos = [Path(p.image.path).as_uri() for p in a.lot.photos.all()[:7] if p.image and Path(p.image.path).exists()]
    html = render_to_string("patrimoine/annonce_pdf.html", {"a": a, "lot": a.lot, "bien": a.lot.bien, "photos": photos})
    return _pdf(html, f"annonce {a.lot.bien.ville or a.lot}")
