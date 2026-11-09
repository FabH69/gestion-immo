"""Rappels et tâches, recherche, notes, fichiers privés, sauvegardes et test des mails."""
from datetime import datetime
from urllib.parse import quote

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from ..models import Bail, Candidature, JournalFait, Lot, Rappel, Tache


def _retour(request):
    """Retour à la page d'où vient le clic (page À faire ou accueil)."""
    suite = request.POST.get("next") or request.GET.get("next")
    if suite and url_has_allowed_host_and_scheme(suite, allowed_hosts={request.get_host()}):
        return redirect(suite)
    return redirect("admin:index")


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
        messages.success(request, tache.message or "Tâche cochée.")
    return _retour(request)


@staff_member_required
def a_faire(request):
    """Page « À faire » : loyers, révisions, assurances, entrées et sorties et rappels saisis, en une seule liste."""
    from collections import Counter

    from .. import droits
    from ..rappels import GENRES, HISTORIQUE_JOURS, a_faire as calculer, deja_fait

    aujourdhui = timezone.localdate()
    jours = int(request.GET["jours"]) if request.GET.get("jours") in ("30", "90", "365") else 30
    vue = "fait" if request.GET.get("vue") == "fait" else ""
    genre = request.GET.get("type", "")
    relocation = droits.niveau(request.user, "relocation")
    items = calculer(aujourdhui, jours, avec_taches=bool(relocation))
    comptes = Counter(i["genre"] for i in items)
    filtres = [{"code": code, "libelle": libelle, "nombre": comptes[code]} for code, libelle in GENRES
               if comptes[code] or code in ("loyers", "revisions", "assurances", "rappels") or (code == "entrees" and relocation)]
    if genre not in comptes and genre not in {f["code"] for f in filtres}:
        genre = ""
    modifiable = {"outils": droits.niveau(request.user, "outils") == droits.MODIFICATION,
                  "entrees": relocation == droits.MODIFICATION}
    lignes = deja_fait(aujourdhui) if vue == "fait" else [i for i in items if not genre or i["genre"] == genre]
    for i in lignes:
        i["cochable"] = bool(i.get("action")) and modifiable["entrees" if i.get("genre") == "entrees" else "outils"]
    return render(request, "patrimoine/a_faire.html", {
        **admin.site.each_context(request), "title": "À faire", "lignes": lignes, "filtres": filtres, "genre": genre, "vue": vue,
        "jours": jours, "total": len(items), "retard": sum(i["en_retard"] for i in items), "historique_jours": HISTORIQUE_JOURS,
        "peut_ajouter": modifiable["outils"], "ici": request.get_full_path(),
    })


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
    return _retour(request)


@require_POST
def tache_faite(request, pk):
    """Case à cocher du bloc « Entrées et sorties de locataires » de l'accueil."""
    tache = get_object_or_404(Tache, pk=pk)
    tache.faite = True
    tache.save()
    messages.success(request, f"Tâche « {tache.libelle} » cochée." + (f" {tache.message}" if tache.message else ""))
    return _retour(request)


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


def resultats_recherche(q, user, par_groupe=15):
    """Recherche globale : locataires, baux, loyers, biens et lots, garants, candidatures, prêts, syndics.
    Les éléments en cours (locataire présent, bail actif, lot non vendu) passent devant."""
    from django.db.models import Q

    from .. import droits
    from ..fiches import MOIS
    from ..models import Bien, Echeance, Garant, Locataire, Lot, Pret, Syndic

    q = (q or "").strip()
    if len(q) < 2:
        return []
    mots = q.split()

    def filtre(champs):
        condition = Q()
        for mot in mots:
            sous = Q()
            for c in champs:
                sous |= Q(**{f"{c}__icontains": mot})
            condition &= sous
        return condition

    acces = droits.niveaux(user)
    groupes = []

    def ajouter(titre, module, objets, url, detail=lambda o: "", libelle=str):
        if not acces.get(module):
            return
        lignes = [{"titre": libelle(o), "detail": detail(o), "url": url(o)} for o in objets[:par_groupe]]
        if lignes:
            groupes.append({"titre": titre, "lignes": lignes})

    change = lambda modele: (lambda o: reverse(f"admin:patrimoine_{modele}_change", args=[o.pk]))
    locataires = sorted(Locataire.objects.filter(filtre(["nom", "prenom", "email", "telephone"])).prefetch_related("baux__lot__bien"),
                        key=lambda o: (not any(b.actif for b in o.baux.all()), o.nom))

    def detail_locataire(o):
        baux = sorted(o.baux.all(), key=lambda b: (not b.actif, b.date_debut))
        if not baux:
            return o.email
        return f"{baux[0].lot} · {'bail en cours' if baux[0].actif else 'parti'}"

    ajouter("Locataires", "locataires", locataires, change("locataire"), detail_locataire)
    baux = (Bail.objects.filter(filtre(["locataires__nom", "locataires__prenom", "lot__designation", "lot__bien__nom"]))
            .distinct().select_related("lot__bien").prefetch_related("locataires").order_by("-actif", "-date_debut"))
    ajouter("Baux", "baux", baux, change("bail"),
            lambda o: (f"depuis le {o.date_debut:%d/%m/%Y} · {o.loyer_cc:.0f} € CC" if o.actif else f"terminé · {', '.join(str(l) for l in o.locataires.all())}"),
            lambda o: f"{o.lot} – {o.get_type_display()}")
    actifs = [b.pk for b in baux if b.actif][:par_groupe]
    mois = timezone.localdate().replace(day=1)
    loyers = [e for e in (Echeance.objects.filter(bail_id=pk, periode__lte=mois).select_related("bail__lot__bien").order_by("-periode").first()
                          for pk in actifs) if e]
    ajouter("Loyers", "loyers", loyers, change("echeance"), lambda e: f"{e.bail.lot} · {e.get_statut_display().lower()}",
            lambda e: f"{MOIS[e.periode.month - 1].capitalize()} {e.periode.year} · {e.total:.0f} €")
    ajouter("Biens", "biens", Bien.objects.filter(filtre(["nom", "adresse", "ville", "code_postal"])).select_related("proprietaire").order_by("vendu", "nom"),
            change("bien"), lambda o: f"{o.adresse}, {o.ville} · {o.proprietaire}{' · vendu' if o.vendu else ''}")
    ajouter("Lots", "biens", Lot.objects.filter(filtre(["designation", "bien__nom", "bien__ville"])).select_related("bien").order_by("vendu", "bien__nom", "designation"),
            change("lot"), lambda o: f"{o.get_type_display()}{' · vendu' if o.vendu else ''}")
    ajouter("Garants", "baux", Garant.objects.filter(filtre(["nom", "prenom", "email"])).select_related("locataire"),
            change("garant"), lambda o: f"garant de {o.locataire}")
    ajouter("Candidatures", "relocation", Candidature.objects.filter(filtre(["nom", "prenom", "email"])).select_related("lot__bien"),
            change("candidature"), lambda o: f"{o.lot} · {o.get_statut_display()}")
    ajouter("Prêts", "finances", Pret.objects.filter(filtre(["banque", "reference", "bien__nom"])).select_related("proprietaire"),
            change("pret"), lambda o: str(o.proprietaire))
    ajouter("Syndics", "biens", Syndic.objects.filter(filtre(["nom", "ville"])), change("syndic"))
    return groupes


@staff_member_required
def recherche(request):
    """Barre de recherche du haut : page de résultats, ou aperçu sous la barre pendant la frappe (?apercu=1)."""
    q = (request.GET.get("q") or "").strip()
    if request.GET.get("apercu"):
        from django.http import JsonResponse

        groupes = resultats_recherche(q, request.user, par_groupe=3)
        # au plus 8 lignes dans l'aperçu, groupes dans l'ordre
        reste, apercu = 8, []
        for g in groupes:
            if reste <= 0:
                break
            apercu.append({"titre": g["titre"], "lignes": g["lignes"][:reste]})
            reste -= len(apercu[-1]["lignes"])
        return JsonResponse({"groupes": apercu, "tout": f"{reverse('recherche')}?q={quote(q)}"})
    groupes = resultats_recherche(q, request.user)
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
