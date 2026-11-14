"""Page « Contrôles » : informations manquantes ou à vérifier, avec « Ne pas contrôler » pour les cas voulus."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import controles as c
from ..models import ControleIgnore


@staff_member_required
def controles(request):
    resultats = c.executer(request.user)
    c.memoriser(request.user, resultats)  # la pastille du menu suit la page
    par_niveau = []
    for niveau, titre in c.NIVEAUX:
        cartes = [r for r in resultats if r["niveau"] == niveau and r["nombre"]]
        par_niveau.append({"code": niveau, "titre": titre, "cartes": cartes})
    bons = [r for r in resultats if not r["nombre"]]
    premiere = next((r["code"] for n in par_niveau for r in n["cartes"]), None)
    return render(request, "patrimoine/controles.html", {
        **admin.site.each_context(request), "title": "Contrôles", "niveaux": par_niveau, "bons": bons, "premiere": premiere,
        "nb_bloquant": len(par_niveau[0]["cartes"]), "nb_completer": len(par_niveau[1]["cartes"]), "nb_bons": len(bons),
        "nb_ecartes": ControleIgnore.objects.count()})


@staff_member_required
@require_POST
def controle_ignorer(request):
    """Écarte une ligne (« Ne pas contrôler »), ou rétablit toutes les lignes écartées."""
    if request.POST.get("action") == "retablir":
        n = ControleIgnore.objects.all().delete()[0]
        messages.success(request, f"{n} ligne(s) de nouveau contrôlée(s).")
    else:
        code, cle = request.POST.get("code", ""), request.POST.get("cle", "")
        if code in {x[0] for x in c.CONTROLES} and cle:
            ControleIgnore.objects.get_or_create(code=code, cle=cle[:60], defaults={"libelle": request.POST.get("libelle", "")[:200]})
            messages.success(request, f"« {request.POST.get('libelle', cle)} » ne sera plus contrôlé pour ce point.")
    c.oublier(request.user)
    return redirect(reverse("controles") + (f"#{request.POST.get('code')}" if request.POST.get("code") else ""))
