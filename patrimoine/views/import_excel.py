"""Outils › Import Excel : modèle vierge à télécharger, puis import vérifié ligne par ligne (tout ou rien)."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render

from .. import droits, import_excel

TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@staff_member_required
def modele_import_excel(request):
    reponse = HttpResponse(import_excel.generer_modele(), content_type=TYPE_XLSX)
    reponse["Content-Disposition"] = 'attachment; filename="modele-import-gestion-locative.xlsx"'
    return reponse


@staff_member_required
def import_excel_page(request):
    resultat, nom_fichier = None, ""
    if request.method == "POST":
        # l'import crée des biens, des locataires et des baux : il faut pouvoir modifier les trois
        if not all(droits.niveau(request.user, m) == droits.MODIFICATION for m in ("biens", "locataires", "baux")):
            raise PermissionDenied("L'import demande le droit de modifier les biens, les locataires et les baux.")
        fichier = request.FILES.get("fichier")
        if fichier is None:
            messages.error(request, "Choisissez d'abord votre fichier Excel rempli.")
            return redirect("import_excel")
        nom_fichier = fichier.name
        if fichier.size > import_excel.TAILLE_MAX:
            resultat = import_excel.Resultat(erreurs=[import_excel.Erreur("", None, "", "fichier trop lourd (5 Mo au maximum)")])
        else:
            resultat = import_excel.importer(fichier)
        if resultat.ok:
            mots = {"Biens": ("bien", "biens"), "Lots": ("lot", "lots"), "Locataires": ("locataire", "locataires"), "Baux": ("bail", "baux")}
            detail = ", ".join(f"{n} {mots[o][n > 1]}" for o, n in resultat.crees.items() if n)
            messages.success(request, f"Import réussi : {detail or 'rien de nouveau'}."
                             + (f" Propriétaire(s) ajouté(s) : {', '.join(resultat.proprietaires_crees)}." if resultat.proprietaires_crees else ""))
            return redirect("import_excel")  # un rafraîchissement de la page ne renvoie pas le fichier
    return render(request, "patrimoine/import_excel.html", {
        **admin.site.each_context(request), "title": "Importer des données depuis Excel",
        "onglets": import_excel.onglets(), "resultat": resultat, "nom_fichier": nom_fichier,
    })


@staff_member_required
def impayes_excel(request):
    """Fichier Excel des loyers encore dus, à compléter avec les règlements reçus."""
    from django.utils import timezone

    from .. import impayes_excel as ie

    reponse = HttpResponse(ie.generer(), content_type=TYPE_XLSX)
    reponse["Content-Disposition"] = f'attachment; filename="loyers-a-completer-{timezone.localdate():%Y-%m-%d}.xlsx"'
    return reponse


@staff_member_required
def completer_impayes(request):
    """Compléter les impayés : télécharger le fichier, le renvoyer rempli, aperçu, puis validation."""
    from .. import impayes_excel as ie

    cle = "impayes_excel"
    lecture, nom_fichier = None, ""
    if request.method == "POST":
        if droits.niveau(request.user, "loyers") != droits.MODIFICATION:
            raise PermissionDenied("Il faut le droit de modifier les loyers.")
        if request.POST.get("etape") == "valider":
            lecture = ie.depuis_session(request.session.get(cle, []))
            if lecture.erreurs or not lecture.a_enregistrer:
                messages.error(request, "Le fichier a changé ou la base a bougé entre-temps : importez-le à nouveau.")
                return redirect("completer_impayes")
            n = ie.enregistrer(lecture)
            request.session.pop(cle, None)
            messages.success(request, f"{n} paiement(s) reçu(s) enregistré(s), {str(lecture.total).replace('.', ',')} € au total.")
            return redirect("completer_impayes")
        fichier = request.FILES.get("fichier")
        if fichier is None:
            messages.error(request, "Choisissez d'abord votre fichier Excel complété.")
            return redirect("completer_impayes")
        nom_fichier = fichier.name
        lecture = ie.lire(fichier)
        if not lecture.erreurs:
            request.session[cle] = [l.en_session() for l in lecture.a_enregistrer]
    return render(request, "patrimoine/completer_impayes.html", {
        **admin.site.each_context(request), "title": "Compléter les impayés avec Excel",
        "lecture": lecture, "nom_fichier": nom_fichier, "nb_a_completer": len(ie.lignes_a_completer()) if lecture is None else None,
    })
