"""Rappels et tâches, recherche, notes, fichiers privés, sauvegardes et test des mails."""
from datetime import datetime
from urllib.parse import quote

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from ..models import Bail, Candidature, JournalFait, Lot, Rappel, Tache


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


@require_POST
def ne_plus_rappeler(request, type_rappel, pk):
    """Lien « Ne plus rappeler » du bloc À faire : la révision ou l'assurance de ce bail quitte la liste pour de bon.
    Le rappel se réactive sur la fiche du bail (cases « Rappel de la révision annuelle » et « Rappel annuel de l'attestation »)."""
    bail = get_object_or_404(Bail, pk=pk)
    champ, libelle = {"revision": ("rappel_revision", "révision du loyer"),
                      "assurance": ("rappel_assurance", "attestation d'assurance")}.get(type_rappel, (None, None))
    if champ:
        Bail.objects.filter(pk=pk).update(**{champ: False})
        JournalFait.objects.create(titre=f"Rappel de {libelle} désactivé", detail=_lieu(bail), categorie="Révision" if type_rappel == "revision" else "Assurance",
                                   url=reverse("admin:patrimoine_bail_change", args=[pk]))
        messages.success(request, f"{_lieu(bail)} : la {libelle} ne sera plus rappelée. Pour la réactiver, cochez la case sur la fiche du bail.")
    return redirect("admin:index")


@require_POST
def tache_faite(request, pk):
    """Case à cocher du bloc « Entrées et sorties de locataires » de l'accueil."""
    tache = get_object_or_404(Tache, pk=pk)
    tache.faite = True
    tache.save()
    messages.success(request, f"Tâche « {tache.libelle} » cochée.")
    return redirect("admin:index")


@xframe_options_sameorigin  # aperçu des factures au survol (cadre sur la même adresse)
@staff_member_required
def fichier_prive(request, chemin):
    """Pièces d'identité des garants : visibles seulement une fois connecté à l'administration."""
    from django.core.exceptions import SuspiciousFileOperation
    from django.http import FileResponse, Http404

    from ..models import stockage_prive

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
    from .. import sauvegarde

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

    from .. import sauvegarde

    fichier = next((f for f in sauvegarde.liste() if f.name == nom), None)
    if fichier is None:
        raise Http404
    return FileResponse(fichier.open("rb"), as_attachment=True, filename=fichier.name)


@staff_member_required
@require_POST
def tester_mail(request):
    from .. import mails

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

    from ..models import Bien, Garant, Locataire, Lot, Pret, Syndic

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

        from .. import droits

        acces = droits.niveaux(request.user)
        module = {"Locataires": "locataires", "Biens": "biens", "Lots": "biens", "Baux": "baux", "Garants": "baux",
                  "Candidatures": "relocation", "Prêts": "finances", "Syndics": "biens"}

        def ajouter(titre, objets, lien, detail=lambda o: ""):
            if not acces.get(module.get(titre, ""), ""):
                return
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


@staff_member_required
@require_POST
def enregistrer_note(request):
    """Bloc-notes du tableau de bord (chaque utilisateur a le sien)."""
    from django.http import JsonResponse

    from ..models import NoteRapide

    note, _ = NoteRapide.objects.get_or_create(utilisateur=request.user)
    note.texte = request.POST.get("texte", "")[:20000]
    note.save()
    return JsonResponse({"ok": True, "heure": timezone.localtime(note.modifie_le).strftime("%H:%M")})
