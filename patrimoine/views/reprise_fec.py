"""Reprise d'un FEC dans un dossier comptable : dépôt du fichier, correspondance des comptes, simulation, enregistrement
et annulation. Le traitement est dans patrimoine/fec.py."""
from datetime import date

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import dossiers, fec
from ..declarations import annee_fiscale
from ..models import Immobilisation, ImportFEC


def _dossier(code):
    d = dossiers.trouver(code)
    if d is None or d["formulaire"] == "2044":
        raise Http404
    return d


def _base(request, dossier, titre):
    annee = annee_fiscale(request)
    onglets = dossiers.onglets(dossier, "fec", annee)
    return {**admin.site.each_context(request), "d": {**dossier, **dossiers.resume_liens(dossier, annee)}, "annee": annee,
            "onglets": onglets, "libelle_regime": dossier["titre"], "title": titre,
            "url_liste": reverse("reprise_fec", args=[dossier["code"]])}


@staff_member_required
def reprise_fec(request, code):
    """Dépôt d'un FEC et liste des imports du dossier."""
    dossier = _dossier(code)
    if request.method == "POST":
        fichier = request.FILES.get("fichier")
        if not fichier:
            messages.error(request, "Choisissez le fichier FEC (.txt ou .csv) à importer.")
        else:
            try:
                imp = fec.preparer(dossier, fichier)
            except fec.ErreurFEC as e:
                messages.error(request, str(e))
            else:
                return redirect("reprise_fec_detail", code, imp.pk)
    c = _base(request, dossier, f"Reprise d'un FEC · {dossier['titre']}")
    c["imports"] = ImportFEC.objects.filter(dossier=code)
    return render(request, "patrimoine/reprise_fec.html", c)


def _lire_formulaire(imp, dossier, post):
    valeurs_cibles = {v for v, _ in fec.cibles(dossier)}
    valeurs_biens = {v for v, _ in fec.choix_biens(dossier)} | {""}
    for compte, c in imp.correspondances.items():
        cible, bien = post.get(f"c_{compte}"), post.get(f"b_{compte}")
        if cible in valeurs_cibles:
            c["cible"] = cible
        if bien in valeurs_biens:
            c["bien"] = bien
    categories = {v for v, _ in Immobilisation.Categorie.choices}
    for compte, r in imp.reglages_immos.items():
        p = f"i_{compte}_"
        if f"{p}designation" not in post:
            continue
        r["inclure"] = f"{p}inclure" in post
        r["designation"] = (post.get(f"{p}designation") or r["designation"]).strip()[:200]
        if post.get(f"{p}categorie") in categories:
            r["categorie"] = post[f"{p}categorie"]
        if post.get(f"{p}bien") in valeurs_biens:
            r["bien"] = post[f"{p}bien"]
        try:
            mes = date.fromisoformat(post.get(f"{p}mise_en_service", ""))
            if mes.isoformat() != r["mise_en_service"]:
                r["mise_en_service"], r["mes_estimee"] = mes.isoformat(), False
        except ValueError:
            pass
        duree = post.get(f"{p}duree", "")
        if duree.isdigit() and int(duree) <= 100:
            r["duree"] = int(duree)
        if r["categorie"] == "terrain":
            r["duree"] = 0
    annee = post.get("annee_reprise", "")
    if annee.isdigit() and 2000 <= int(annee) <= 2100:
        imp.options["annee_reprise"] = int(annee)
    imp.options["charges"] = "charges" in post
    imp.options["recettes"] = "recettes" in post
    if post.get("bien_defaut") in valeurs_biens:
        imp.options["bien_defaut"] = post["bien_defaut"]
    imp.save(update_fields=["correspondances", "reglages_immos", "options"])


@staff_member_required
def reprise_fec_detail(request, code, pk):
    """Correspondance des comptes et simulation ; enregistrement de la reprise."""
    dossier = _dossier(code)
    imp = get_object_or_404(ImportFEC, pk=pk, dossier=code)
    url = reverse("reprise_fec_detail", args=[code, pk])
    if request.method == "POST":
        if imp.etat != ImportFEC.Etat.BROUILLON:
            messages.error(request, "Cet import est déjà enregistré ou annulé : déposez à nouveau le fichier pour le refaire.")
            return redirect(url)
        _lire_formulaire(imp, dossier, request.POST)
        if request.POST.get("action") == "enregistrer":
            try:
                r = fec.enregistrer(imp, dossier)
            except fec.ErreurFEC as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f"Reprise enregistrée : {r['factures']} facture{'s' if r['factures'] > 1 else ''}, "
                                          f"{r['loyers']} mois de loyers, {r['immobilisations']} immobilisation"
                                          f"{'s' if r['immobilisations'] > 1 else ''} et {r['soldes']} solde{'s' if r['soldes'] > 1 else ''} de bilan.")
                return redirect(url)
        else:
            messages.info(request, "Simulation recalculée : rien n'est encore enregistré.")
        return redirect(f"{url}#simulation")
    try:
        s = fec.simuler(imp, dossier)
    except fec.ErreurFEC as e:
        messages.error(request, str(e))
        return redirect("reprise_fec", code)
    a = s["analyse"]
    comptes = []
    for compte, corr in imp.correspondances.items():
        x = a["comptes"].get(compte)
        if x:
            net = x["debit"] - x["credit"] if x["classe"] == "6" else x["credit"] - x["debit"]
            comptes.append({"compte": compte, "libelle": x["libelle"], "nb": x["nb"], "net": net, "classe": x["classe"], **corr})
    c = _base(request, dossier, f"Reprise du FEC {imp.nom_fichier}")
    annee = c["annee"]
    c.update({"imp": imp, "s": s, "a": a, "comptes": comptes, "cibles": fec.cibles(dossier), "choix_biens": fec.choix_biens(dossier),
              "categories": Immobilisation.Categorie.choices, "amortissable": dossiers.amortissable(dossier),
              "brouillon": imp.etat == ImportFEC.Etat.BROUILLON,
              "lien_immos": reverse("immobilisations", args=[code]) + f"?annee={s['annee_reprise']}" if dossiers.amortissable(dossier) else "",
              "lien_reprise_immos": reverse("reprise_immobilisations", args=[code]) if dossier["formulaire"] == "2031" else "",
              "lien_factures": reverse("admin:patrimoine_piececomptable_changelist") + f"?dossier={code}&annee={a['fin'].year}",
              "bloquees": fec.verrous_annulation(imp) if imp.etat == ImportFEC.Etat.IMPORTE else [], "annee_page": annee})
    return render(request, "patrimoine/reprise_fec_detail.html", c)


@staff_member_required
@require_POST
def reprise_fec_annuler(request, code, pk):
    dossier = _dossier(code)
    imp = get_object_or_404(ImportFEC, pk=pk, dossier=dossier["code"])
    if imp.etat == ImportFEC.Etat.BROUILLON:
        imp.fichier.delete(save=False)
        imp.delete()
        messages.success(request, "Import abandonné : rien n'avait été enregistré.")
        return redirect("reprise_fec", code)
    try:
        fec.annuler(imp)
    except fec.ErreurFEC as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Import annulé : les factures, loyers, immobilisations et soldes qu'il avait créés sont supprimés.")
    return redirect("reprise_fec_detail", code, pk)
