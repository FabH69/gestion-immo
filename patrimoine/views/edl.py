"""État des lieux sur téléphone : une étape par pièce, photos horodatées, compteurs, puis signature à l'écran du bailleur et du locataire."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .. import edl as logique
from ..documents import ELEMENTS_PIECE
from ..models import Bail, ElementEDL, EtatDesLieux, PhotoEDL

CHAMPS_COMPTEURS = ["compteur_electricite", "compteur_gaz", "compteur_eau_froide", "compteur_eau_chaude", "nb_cles", "observations"]


def _rendu(request, gabarit, contexte):
    return render(request, gabarit, {**admin.site.each_context(request), **contexte})


def _peut_modifier(request):
    if not request.user.has_perm("patrimoine.change_etatdeslieux"):
        raise PermissionDenied


@staff_member_required
def edl_commencer(request, pk, type_edl):
    """Depuis la fiche du bail : reprend l'état des lieux en cours de ce type, ou en commence un."""
    _peut_modifier(request)
    if type_edl not in EtatDesLieux.Type.values:
        raise Http404
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    en_cours = bail.etats_des_lieux.filter(type=type_edl, signe_le__isnull=True).order_by("-pk").first()
    if en_cours and request.method != "POST":
        return redirect("edl_saisie", en_cours.pk)
    if request.method == "POST":
        edl = EtatDesLieux(bail=bail, type=type_edl, date=timezone.localdate(),
                           signataire_bailleur=request.POST.get("signataire_bailleur", "")[:150],
                           signataire_locataire=request.POST.get("signataire_locataire", "")[:200])
        edl.save()
        logique.preparer(edl)
        return redirect(reverse("edl_saisie", args=[edl.pk]) + "?etape=1")
    proprietaire = bail.lot.bien.proprietaire
    entree = EtatDesLieux(bail=bail, type=type_edl).edl_entree()
    return _rendu(request, "patrimoine/edl_commencer.html", {
        "title": f"État des lieux {'d’entrée' if type_edl == 'entree' else 'de sortie'}", "bail": bail, "type_edl": type_edl,
        "signataire_bailleur": getattr(proprietaire, "representant", "") or proprietaire.nom,
        "signataire_locataire": _locataires_texte(bail), "entree": entree,
        "entree_detaillee": bool(entree and entree.elements.exists()),
    })


def _locataires_texte(bail):
    from .communs import _contexte_bail

    return ", ".join(str(l) for l in _contexte_bail(bail)["locataires"])


def _etapes(edl, liste_pieces):
    return ["Informations"] + [p for p, _ in liste_pieces] + ["Compteurs et clés", "Signatures"]


def _enregistrer_piece(request, edl, elements):
    for e in elements:
        etat = request.POST.get(f"etat-{e.pk}", e.etat)
        e.etat = etat if etat in ElementEDL.Etat.values or etat == "" else e.etat
        e.commentaire = request.POST.get(f"commentaire-{e.pk}", e.commentaire)[:2000]
        quantite = request.POST.get(f"quantite-{e.pk}")
        if quantite is not None:
            e.quantite = int(quantite) if quantite.strip().isdigit() else None
        e.save()
        for fichier in request.FILES.getlist(f"photos-{e.pk}")[:10]:
            try:
                logique.enregistrer_photo(e, fichier)
            except Exception:
                messages.error(request, f"La photo « {fichier.name} » n'a pas pu être lue (format non reconnu).")


@staff_member_required
def edl_saisie(request, pk):
    edl = get_object_or_404(EtatDesLieux.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    if edl.signe:
        return redirect("edl_resume", edl.pk)
    _peut_modifier(request)
    logique.preparer(edl)
    liste_pieces = logique.pieces(edl)
    etapes = _etapes(edl, liste_pieces)
    try:
        etape = max(0, min(int(request.GET.get("etape", 0)), len(etapes) - 1))
    except ValueError:
        etape = 0
    rang_compteurs, rang_signature = len(etapes) - 2, len(etapes) - 1
    url = reverse("edl_saisie", args=[edl.pk])

    if request.method == "POST":
        action = request.POST.get("action", "")
        if etape == 0:
            if request.POST.get("date"):
                try:
                    edl.date = timezone.datetime.strptime(request.POST["date"], "%Y-%m-%d").date()
                except ValueError:
                    pass
            edl.signataire_bailleur = request.POST.get("signataire_bailleur", edl.signataire_bailleur)[:150]
            edl.signataire_locataire = request.POST.get("signataire_locataire", edl.signataire_locataire)[:200]
            if edl.type == EtatDesLieux.Type.SORTIE:
                edl.nouvelle_adresse = request.POST.get("nouvelle_adresse", edl.nouvelle_adresse)
            edl.save()
            nouvelle = request.POST.get("nouvelle_piece", "").strip()[:100]
            if action == "ajouter_piece" and nouvelle:
                if edl.elements.filter(piece=nouvelle).exists():
                    messages.warning(request, f"La pièce « {nouvelle} » existe déjà.")
                else:
                    for nom in ELEMENTS_PIECE:
                        logique.ajouter_element(edl, nouvelle, nom)
                    messages.success(request, f"Pièce « {nouvelle} » ajoutée à la fin.")
                return redirect(f"{url}?etape=0")
            if action.startswith("retirer_piece:"):
                piece = action.split(":", 1)[1]
                if PhotoEDL.objects.filter(edl=edl, element__piece=piece).exists():
                    messages.error(request, f"La pièce « {piece} » a des photos : supprimez-les d'abord.")
                else:
                    edl.elements.filter(piece=piece).delete()
                    messages.success(request, f"Pièce « {piece} » retirée.")
                return redirect(f"{url}?etape=0")
        elif etape == rang_compteurs:
            for champ in CHAMPS_COMPTEURS:
                if champ in request.POST:
                    setattr(edl, champ, request.POST[champ][:2000 if champ == "observations" else 100])
            edl.save()
        elif etape == rang_signature:
            qui, donnees = request.POST.get("qui"), request.POST.get("signature", "")
            if qui == "locataire" and not edl.signature_bailleur:
                qui = None
            if qui in ("bailleur", "locataire") and logique.signature_valide(donnees):
                logique.signer(edl, qui, donnees)
                if qui == "locataire":
                    messages.success(request, "État des lieux signé, horodaté et verrouillé.")
                    return redirect("edl_resume", edl.pk)
            else:
                messages.error(request, "Signez dans le cadre avant de valider.")
            return redirect(f"{url}?etape={etape}")
        else:
            piece, elements = liste_pieces[etape - 1]
            _enregistrer_piece(request, edl, elements)
            if action == "ajouter" and request.POST.get("nouvel_element", "").strip():
                logique.ajouter_element(edl, piece, request.POST["nouvel_element"].strip()[:150])
                return redirect(f"{url}?etape={etape}#bas")
            if action.startswith("retirer:"):
                ElementEDL.objects.filter(edl=edl, pk=action.split(":")[1], photos__isnull=True).delete()
                return redirect(f"{url}?etape={etape}")
            if action.startswith("photo_supprimer:"):
                photo = PhotoEDL.objects.filter(edl=edl, pk=action.split(":")[1]).first()
                if photo:
                    photo.image.delete(save=False)
                    photo.delete()
                return redirect(f"{url}?etape={etape}")
            if action == "photos":
                return redirect(f"{url}?etape={etape}#element-{request.POST.get('ancre', '')}")
        aller = request.POST.get("aller", "")
        cible = int(aller) if aller.isdigit() else etape + 1
        return redirect(f"{url}?etape={min(cible, len(etapes) - 1)}")

    faits, total = logique.avancement(edl)
    contexte = {
        "title": str(edl), "edl": edl, "bail": edl.bail, "etapes": etapes, "etape": etape, "nom_etape": etapes[etape],
        "rang_compteurs": rang_compteurs, "rang_signature": rang_signature, "faits": faits, "total": total,
        "pourcentage": round(100 * etape / (len(etapes) - 1)), "etats": ElementEDL.Etat.choices,
        "sortie": edl.type == EtatDesLieux.Type.SORTIE, "inventaire": logique.PIECE_INVENTAIRE,
    }
    if 0 < etape < rang_compteurs:
        contexte["piece"], contexte["elements"] = liste_pieces[etape - 1]
    elif etape == 0:
        contexte["pieces"] = [(p, len(els), sum(1 for e in els if e.etat)) for p, els in liste_pieces]
    elif etape == rang_signature:
        contexte["differences"] = logique.differences(edl) if contexte["sortie"] else []
        contexte["nb_photos"] = edl.photos.count()
        contexte["manquants"] = total - faits
    return _rendu(request, "patrimoine/edl_saisie.html", contexte)


@staff_member_required
def edl_resume(request, pk):
    """État des lieux signé : lecture seule, avec contrôle d'intégrité et PDF."""
    edl = get_object_or_404(EtatDesLieux.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    if not request.user.has_perm("patrimoine.view_etatdeslieux"):
        raise PermissionDenied
    if not edl.signe:
        return redirect("edl_saisie", edl.pk)
    return _rendu(request, "patrimoine/edl_resume.html", {
        "title": str(edl), "edl": edl, "bail": edl.bail, "pieces": logique.pieces(edl), "integre": logique.integre(edl),
        "differences": logique.differences(edl) if edl.type == EtatDesLieux.Type.SORTIE else [],
        "nb_photos": edl.photos.count(),
    })
