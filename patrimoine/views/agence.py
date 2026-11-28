"""Récap annuel d'une agence de gestion : loyers encaissés, charges et honoraires, acomptes, solde."""
from datetime import date

from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .. import agence as ag
from .. import droits
from ..models import AgenceGestion, ChargeRecapAgence, Proprietaire, RecapAgence
from ..models.loyers import Echeance

LIGNES_VIDES = 4


def _date(texte):
    try:
        return date.fromisoformat((texte or "").strip())
    except ValueError:
        return None


def _charges_postees(request, biens):
    """[ChargeRecapAgence] non enregistrées, lues dans le formulaire (lignes sans montant ignorées)."""
    par_pk = {str(b.pk): b for b in biens}
    charges = []
    for i in range(int(request.POST.get("nb_charges") or 0)):
        m = ag.montant(request.POST.get(f"charge-{i}-montant"))
        bien = par_pk.get(request.POST.get(f"charge-{i}-bien")) or (biens[0] if len(biens) == 1 else None)
        if not m or bien is None:
            continue
        nature = request.POST.get(f"charge-{i}-nature")
        if nature not in ChargeRecapAgence.Nature.values:
            nature = ChargeRecapAgence.Nature.CHARGE
        code = request.POST.get(f"charge-{i}-code", "")
        if code not in dict(ag.codes_possibles(bien)):
            code = ""
        charges.append(ChargeRecapAgence(bien=bien, nature=nature, libelle=request.POST.get(f"charge-{i}-libelle", "").strip()[:200],
                                         montant=m, code=code))
    return charges


def recap_agence(request, agence, proprietaire, annee):
    agence = get_object_or_404(AgenceGestion, pk=agence)
    proprietaire = get_object_or_404(Proprietaire, pk=proprietaire)
    recap = RecapAgence.objects.filter(agence=agence, proprietaire=proprietaire, annee=annee).first() or \
        RecapAgence(agence=agence, proprietaire=proprietaire, annee=annee)
    biens = ag.biens_du_recap(agence, proprietaire)
    peut_modifier = droits.niveau(request.user, "loyers") == droits.MODIFICATION
    if request.method == "POST" and peut_modifier:
        echeances = ag.echeances_du_recap(agence, proprietaire, annee)
        encaisses = {}
        for e in echeances:
            m = ag.montant(request.POST.get(f"loyer-{e.pk}"))
            if m is not None:
                encaisses[e.pk] = m
        recap.acomptes = ag.montant(request.POST.get("acomptes"))
        recap.solde_annonce = ag.montant(request.POST.get("solde_annonce"))
        recap.date_solde = _date(request.POST.get("date_solde"))
        recap.notes = request.POST.get("notes", "")
        if request.FILES.get("fichier"):
            recap.fichier = request.FILES["fichier"]
        try:
            b = ag.appliquer(recap, encaisses, _charges_postees(request, biens))
        except ag.RecapRefuse as e:
            messages.error(request, f"Récap non appliqué : {e}.")
            return redirect(request.path)
        messages.success(request, f"Récap {annee} appliqué : loyers bruts {_e(b.loyers)}, charges {_e(b.charges)}, "
                                  f"honoraires {_e(b.honoraires)}. Ils sont repris dans les déclarations {annee}.")
        if b.non_encaisses:
            detail = ", ".join(f"{e.bail.lot} {e.periode:%m/%Y} ({_e(m)})" for e, m in b.non_encaisses)
            messages.warning(request, f"{len(b.non_encaisses)} loyer(s) non encaissé(s) par l'agence, repassé(s) en impayé : {detail}.")
        if b.ecart_solde:
            messages.warning(request, f"Écart de {_e(b.ecart_solde)} entre le solde annoncé par l'agence ({_e(b.solde_annonce)}) "
                                      f"et le solde calculé ({_e(b.solde_calcule)}).")
        if b.ecart_acomptes:
            messages.warning(request, f"Écart de {_e(b.ecart_acomptes)} entre les acomptes du récap ({_e(b.acomptes)}) et ceux "
                                      f"reçus sur le relevé de banque ({_e(b.acomptes_releve)}).")
        return redirect(request.path)

    echeances = ag.echeances_du_recap(agence, proprietaire, annee)
    b = ag.bilan(recap) if recap.pk else ag.bilan(recap, charges=[])
    groupes, courant = [], None
    for e in echeances:
        if courant is None or courant["bail"] != e.bail:
            courant = {"bail": e.bail, "noms": ", ".join(str(l) for l in e.bail.locataires.all()) or "–", "lignes": []}
            groupes.append(courant)
        valeur = ag.valeur_initiale(e)
        du = e.total - (e.remise or 0)
        courant["lignes"].append({"e": e, "du": du, "valeur": valeur, "manque": du - valeur if valeur < du else 0,
                                  "impaye": e.statut != Echeance.Statut.PAYE})
    charges = [{"c": c, "codes": ag.codes_possibles(c.bien)} for c in (recap.charges.select_related("bien") if recap.pk else [])]
    defaut = biens[0] if biens else None
    vides = [{"c": ChargeRecapAgence(bien=defaut, nature=ChargeRecapAgence.Nature.HONORAIRES if not charges and i == 0
                                     else ChargeRecapAgence.Nature.CHARGE, montant=None,
                                     code=code_defaut(defaut, i == 0 and not charges)),
              "codes": ag.codes_possibles(defaut) if defaut else []} for i in range(LIGNES_VIDES)]
    contexte = {**admin.site.each_context(request), "title": f"Récap {annee} de l'agence {agence} – {proprietaire}",
                "agence": agence, "proprietaire": proprietaire, "annee": annee, "recap": recap, "biens": biens,
                "groupes": groupes, "charges": charges + vides, "nb_charges": len(charges) + LIGNES_VIDES, "bilan": b,
                "natures": ChargeRecapAgence.Nature.choices, "peut_modifier": peut_modifier,
                "precedent": reverse("recap_agence", args=[agence.pk, proprietaire.pk, annee - 1]),
                "suivant": reverse("recap_agence", args=[agence.pk, proprietaire.pk, annee + 1]),
                "releve": reverse("releve_banque")}
    return render(request, "patrimoine/recap_agence.html", contexte)


def code_defaut(bien, honoraires):
    if bien is None:
        return ""
    return ag.code_par_defaut(bien, ChargeRecapAgence.Nature.HONORAIRES if honoraires else ChargeRecapAgence.Nature.CHARGE)


def _e(m):
    return f"{m:,.2f} €".replace(",", " ").replace(".", ",")

