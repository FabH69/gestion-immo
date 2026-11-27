"""Page « Ma maison » : dépenses et entretiens de la résidence principale (voir patrimoine/maison.py)."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from .. import fiches_maison as fm
from .. import maison as m
from ..models import JournalFait, Rappel


@staff_member_required
def ma_maison(request):
    liste = list(m.maisons())
    choisi = request.GET.get("bien", "")
    bien = next((b for b in liste if str(b.pk) == choisi), liste[0] if liste else None)
    contexte = {"maisons": liste, "bien": bien, "title": "Ma maison"}
    if bien is not None:
        contexte.update(m.synthese(bien))
        contexte["liens"] = m.liens(bien)
    return render(request, "patrimoine/ma_maison.html", {**admin.site.each_context(request), **contexte})


@staff_member_required
@require_POST
def maison_entretien_fait(request, pk):
    """« ✓ Fait » sur un entretien : le rappel passe à l'échéance suivante (ou se ferme), puis la facture de
    l'entretien s'ouvre pré-remplie (catégorie Entretien) ; « Enregistrer » ramène à la page."""
    rappel = get_object_or_404(Rappel, pk=pk, bien__residence_principale=True)
    JournalFait.objects.create(titre=rappel.titre, detail=str(rappel.bien), categorie=rappel.get_categorie_display(),
                               url=reverse("admin:patrimoine_rappel_change", args=[pk]))
    rappel.marquer_fait()
    suite = "prochain le " + f"{rappel.date:%d/%m/%Y}" if not rappel.fait else "rappel terminé"
    messages.success(request, f"« {rappel.titre} » noté fait aujourd'hui ({suite}). Ajoutez sa facture, ou revenez sans l'enregistrer.")
    retour = reverse("ma_maison") + f"?bien={rappel.bien_id}"
    params = {"bien": rappel.bien_id, "rubrique": "entretien", "libelle": rappel.titre,
              "date": timezone.localdate().isoformat(), "next": retour}
    return redirect(reverse("admin:patrimoine_piececomptable_add") + "?" + urlencode(params))


def _maison_choisie(request):
    liste = list(m.maisons())
    choisi = request.GET.get("bien", "")
    return liste, next((b for b in liste if str(b.pk) == choisi), liste[0] if liste else None)


@staff_member_required
def maison_fiches(request):
    """Fiches pratiques : entretien de la maison par thème, conseils de saison, économies, papiers, assurance."""
    liste, bien = _maison_choisie(request)
    contexte = {"maisons": liste, "bien": bien, "title": "Fiches pratiques", "groupes": fm.par_theme(bien),
                "saisons": fm.SAISONS, "conseils": fm.CONSEILS, "suffixe": f"?bien={bien.pk}" if bien else ""}
    return render(request, "patrimoine/maison_fiches.html", {**admin.site.each_context(request), **contexte})


@staff_member_required
def maison_fiche(request, slug):
    """Une fiche pratique : fréquence, obligation, qui s'en charge, coût, gestes, alertes, papiers, et ses rappels
    (déjà suivis pour la maison, ou à créer en un clic)."""
    fiche = fm.PAR_SLUG.get(slug)
    if fiche is None:
        raise Http404("Fiche inconnue")
    liste, bien = _maison_choisie(request)
    i = fm.FICHES.index(fiche)
    contexte = {"maisons": liste, "bien": bien, "title": fiche["titre"], "fiche": fiche,
                "rappels": fm.rappels_de_la_fiche(fiche, bien), "suffixe": f"?bien={bien.pk}" if bien else "",
                "precedente": fm.FICHES[i - 1] if i > 0 else None,
                "suivante": fm.FICHES[i + 1] if i + 1 < len(fm.FICHES) else None}
    return render(request, "patrimoine/maison_fiche.html", {**admin.site.each_context(request), **contexte})
