"""Courte durée › Gérer mon bien : accueil avec indicateurs, calendrier du mois et réglage des calendriers .ics (roue crantée)."""
from datetime import date

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .. import ical
from ..models import CalendrierICS, Lot

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _lots_lcd():
    lots = Lot.objects.filter(vendu=False, bien__vendu=False).select_related("bien").order_by("bien__nom", "designation")
    return [l for l in lots if l.exploitation == "courte" or l.reservations_lcd.exists() or l.calendriers_ics.exists()]


def _contexte(request, titre):
    if not request.user.has_perm("patrimoine.view_reservationlcd"):
        raise PermissionDenied
    lots = _lots_lcd()
    choisi = request.GET.get("lot") or request.POST.get("lot")
    lot = next((l for l in lots if str(l.pk) == choisi), lots[0] if lots else None)
    return {**admin.site.each_context(request), "title": titre, "lots": lots, "lot": lot,
            "peut_regler": request.user.has_perm("patrimoine.change_reservationlcd")}


@staff_member_required
def lcd_accueil(request):
    contexte = _contexte(request, "Gérer mon bien en courte durée")
    lot = contexte["lot"]
    if lot:
        aujourdhui = timezone.localdate()
        semaines, _ = ical.mois_calendrier(lot, aujourdhui.year, aujourdhui.month)
        contexte.update(k=ical.indicateurs(lot, aujourdhui), semaines=semaines, nom_mois=f"{MOIS[aujourdhui.month - 1]} {aujourdhui.year}",
                        calendriers=list(lot.calendriers_ics.all()), annee=aujourdhui.year)
    return render(request, "patrimoine/lcd_accueil.html", contexte)


@staff_member_required
def lcd_calendrier(request):
    contexte = _contexte(request, "Calendrier courte durée")
    lot = contexte["lot"]
    aujourdhui = timezone.localdate()
    try:
        annee, mois = (int(x) for x in request.GET.get("mois", "").split("-"))
        date(annee, mois, 1)
    except ValueError:
        annee, mois = aujourdhui.year, aujourdhui.month
    if lot:
        semaines, liste = ical.mois_calendrier(lot, annee, mois)
        precedent = (annee - 1, 12) if mois == 1 else (annee, mois - 1)
        suivant = (annee + 1, 1) if mois == 12 else (annee, mois + 1)
        premier = date(annee, mois, 1)
        jours = len([j for s in semaines for j in s if j["dans_mois"]])
        nuits = ical.nuits_occupees(liste, premier, date(suivant[0], suivant[1], 1))
        contexte.update(semaines=semaines, sejours=liste, nom_mois=f"{MOIS[mois - 1]} {annee}",
                        precedent=f"{precedent[0]}-{precedent[1]:02d}", suivant=f"{suivant[0]}-{suivant[1]:02d}",
                        ce_mois=(annee, mois) == (aujourdhui.year, aujourdhui.month), nuits=nuits, jours=jours,
                        occupation=round(nuits * 100 / jours), calendriers=list(lot.calendriers_ics.all()))
    return render(request, "patrimoine/lcd_calendrier.html", contexte)


@staff_member_required
def lcd_calendriers(request):
    """Roue crantée : les calendriers .ics du logement (plusieurs possibles), par lien ou par fichier."""
    contexte = _contexte(request, "Réglage des calendriers")
    if not contexte["peut_regler"]:
        raise PermissionDenied
    lot = contexte["lot"]
    if request.method == "POST" and lot:
        action = request.POST.get("action", "")
        retour = f"{reverse('lcd_calendriers')}?lot={lot.pk}"
        if action == "ajouter":
            nom = request.POST.get("nom", "").strip()[:80]
            url = request.POST.get("url", "").strip()
            fichier = request.FILES.get("fichier")
            if url and not url.lower().startswith(("https://", "http://", "webcal://")):
                messages.error(request, "Le lien doit commencer par https://")
                return redirect(retour)
            url = "https://" + url[len("webcal://"):] if url.lower().startswith("webcal://") else url
            plateforme = request.POST.get("plateforme") if request.POST.get("plateforme") in CalendrierICS.Plateforme.values else "autre"
            if not nom:
                nom = CalendrierICS.Plateforme(plateforme).label
            if not (url or fichier):
                messages.error(request, "Indiquez le lien du calendrier ou choisissez un fichier .ics.")
                return redirect(retour)
            c = CalendrierICS.objects.create(lot=lot, nom=nom, plateforme=plateforme, url=url[:1000])
            _mettre_a_jour(request, c, fichier)
            return redirect(retour)
        c = CalendrierICS.objects.filter(lot=lot, pk=request.POST.get("calendrier")).first()
        if not c:
            return redirect(retour)
        if action == "actualiser":
            _mettre_a_jour(request, c, request.FILES.get("fichier"))
        elif action == "lien":
            url = request.POST.get("url", "").strip()
            if url and not url.lower().startswith(("https://", "http://", "webcal://")):
                messages.error(request, "Le lien doit commencer par https://")
            else:
                c.url = ("https://" + url[len("webcal://"):] if url.lower().startswith("webcal://") else url)[:1000]
                c.save(update_fields=["url"])
                if c.url:
                    _mettre_a_jour(request, c, None)
        elif action == "pause":
            c.actif = not c.actif
            c.save(update_fields=["actif"])
            messages.success(request, f"« {c.nom} » {'réactivé' if c.actif else 'mis en pause : ses dates ne comptent plus'}.")
        elif action == "supprimer":
            c.delete()
            messages.success(request, f"Calendrier « {c.nom} » supprimé.")
        return redirect(retour)
    if lot:
        contexte["calendriers"] = [(c, c.evenements.count(), c.evenements.filter(fin__gte=timezone.localdate()).count()) for c in lot.calendriers_ics.all()]
    contexte["plateformes"] = CalendrierICS.Plateforme.choices
    return render(request, "patrimoine/lcd_calendriers.html", contexte)


def _mettre_a_jour(request, calendrier, fichier):
    if fichier:
        try:
            if fichier.size > ical.TAILLE_MAX:
                raise ValueError("fichier trop gros pour un calendrier")
            n, _ = ical.importer(calendrier, fichier.read().decode("utf-8", errors="replace"))
        except ValueError as erreur:
            messages.error(request, f"{fichier.name} : {erreur}.")
        else:
            messages.success(request, f"« {calendrier.nom} » : {calendrier.dernier_message}.")
        return
    if calendrier.url:
        ok, message = ical.synchroniser(calendrier)
        (messages.success if ok else messages.error)(request, f"« {calendrier.nom} » : {message}.")
