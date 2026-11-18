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
