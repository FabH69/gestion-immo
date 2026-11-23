"""Comptabilité › Déclarations : une carte par cerfa (2044, une 2072 par SCI, 2031 et 2033 pour le LMNP, récapitulatif de
chaque société), avec les étapes qui se cochent seules à partir de la saisie et la case « Déclaration effectuée le … ».

Cocher la case verrouille la saisie qui alimente ce cerfa pour cette année (factures des biens concernés, attestations de
leurs prêts). Décocher (déclaration rectificative) rouvre la saisie ; la ligne reste dans l'historique.
"""
from datetime import date

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from . import compta
from .models import AttestationPret, DeclarationFaite, Echeance, Pret, Proprietaire, biens_du_formulaire

REGIME_DU_FORMULAIRE = {"2044": "foncier", "2072": "foncier", "2031": "lmnp", "societe": "societe"}


def annee_par_defaut():
    """Année des revenus à déclarer : l'année précédente jusqu'en juin (saison des déclarations), puis l'année en cours."""
    t = timezone.localdate()
    return t.year - (1 if t.month < 6 else 0)


def annee_fiscale(request, defaut=None):
    """Année fiscale choisie une fois pour toutes les pages de comptabilité : ?annee=… la change et la garde en session."""
    valeur = request.GET.get("annee", "")
    if valeur.isdigit():
        request.session["annee_fiscale"] = int(valeur)
        return int(valeur)
    gardee = request.session.get("annee_fiscale")
    if isinstance(gardee, int):
        return gardee
    return defaut if defaut is not None else annee_par_defaut()


def _pret_court_en(pret, annee):
    debut = pret.date_premiere_echeance
    fin_mois = debut.month - 1 + pret.duree_mois - 1
    fin = date(debut.year + fin_mois // 12, fin_mois % 12 + 1, 1)
    return debut.year <= annee <= fin.year


def _declarants():
    """(formulaire, propriétaire ou None, titre, sous-titre, nom du cerfa) pour chaque cerfa à remplir."""
    res = []
    if biens_du_formulaire("2044").exists():
        res.append(("2044", None, "Nom propre", "Location nue au réel", "2044"))
    for p in Proprietaire.objects.filter(biens__regime_fiscal="sci_ir").distinct().order_by("nom"):
        res.append(("2072", p, p.nom, "SCI à l'IR", "2072"))
    if biens_du_formulaire("2031").exists():
        res.append(("2031", None, "LMNP", "Meublé, régime réel", "2031 et 2033"))
    for p in Proprietaire.objects.filter(biens__comptabilite="societe").distinct().order_by("nom"):
        res.append(("societe", p, p.nom, "Société", "Récapitulatif société"))
    return res


def _lien_formulaire(formulaire, proprietaire, annee, acces):
    if formulaire == "2044":
        return reverse("recap_2044") + f"?annee={annee}", "Voir la 2044"
    if formulaire == "2072":
        return reverse("annexe_2072") + f"?sci={proprietaire.pk}&annee={annee}", "Voir la 2072"
    if formulaire == "2031":
        if acces is None or acces.get("etats"):
            return reverse("biens_lmnp") + f"?annee={annee}", "Voir le bilan 2031"
        return reverse("compta_lmnp") + f"?annee={annee}", "Voir le récapitulatif"
    return reverse("compta_societe") + f"?annee={annee}", "Voir le récapitulatif"


def carte(formulaire, proprietaire, titre, sous_titre, cerfa, annee, acces=None):
    aujourdhui = timezone.localdate()
    pid = proprietaire.pk if proprietaire else None
    biens = biens_du_formulaire(formulaire, pid).filter(Q(vendu=False) | Q(date_vente__year__gte=annee))
    ids = list(biens.values_list("pk", flat=True))
    regime = REGIME_DU_FORMULAIRE[formulaire]
    factures = reverse("admin:patrimoine_piececomptable_changelist")

    impayes = Echeance.objects.filter(bail__lot__bien_id__in=ids, periode__year=annee, date_exigibilite__lt=aujourdhui) \
        .exclude(statut=Echeance.Statut.PAYE).count()
    a_classer = compta.a_codifier().filter(bien_id__in=ids).filter(
        Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee)).count()
    prets = [p for p in Pret.objects.filter(bien_id__in=ids) if _pret_court_en(p, annee)]
    recues = set(AttestationPret.objects.filter(pret__in=prets, annee=annee).values_list("pret_id", flat=True))
    manquantes = [p for p in prets if p.pk not in recues]

    etapes = [
        {"libelle": "Loyers", "etat": "ko" if impayes else "ok",
         "texte": f"{impayes} impayé{'s' if impayes > 1 else ''}" if impayes else "Encaissés",
         "url": reverse("admin:patrimoine_echeance_changelist") + "?retard=oui" if impayes else ""},
        {"libelle": "Factures", "etat": "todo" if a_classer else "ok",
         "texte": f"{a_classer} à classer" if a_classer else "Tout classé",
         "url": f"{factures}?compta={regime}&a_codifier=oui&annee={annee}" if a_classer else f"{factures}?compta={regime}&annee={annee}"},
        {"libelle": "Emprunts", "etat": "ko" if manquantes else "ok",
         "texte": (f"{len(manquantes)} attestation{'s' if len(manquantes) > 1 else ''} manquante{'s' if len(manquantes) > 1 else ''}"
                   if manquantes else "Attestations reçues" if prets else "Aucun prêt"),
         "url": reverse("admin:patrimoine_pret_change", args=[manquantes[0].pk]) if manquantes else ""},
    ]
    if formulaire in ("2031", "societe") and (acces is None or acces.get("etats")):
        from . import amortissements
        from .dossiers import code_de

        douteuses = amortissements.a_verifier(ids, annee).count()
        dot = amortissements.dotations_biens(ids, annee)
        etapes.append({"libelle": "Amortissements", "etat": "todo" if douteuses else "info",
                       "texte": (f"{douteuses} facture{'s' if douteuses > 1 else ''} de plus de 500 € à vérifier" if douteuses
                                 else f"Dotations {annee} : {dot:,.2f} €".replace(",", " ").replace(".", ",") if dot
                                 else "Aucune immobilisation"),
                       "url": reverse("immobilisations", args=[code_de(formulaire, proprietaire)]) + f"?annee={annee}"})

    historique = list(DeclarationFaite.objects.filter(annee=annee, formulaire=formulaire, proprietaire_id=pid))
    declaree = next((d for d in historique if d.active), None)
    if declaree:
        statut = ("lock", f"Déclarée le {declaree.date_declaration:%d/%m/%Y}")
    elif annee >= aujourdhui.year:
        statut = ("info", "Année en cours")
    elif a_classer or manquantes:
        statut = ("todo", "À terminer")
    else:
        statut = ("ok", "Prête")
    url, libelle_lien = _lien_formulaire(formulaire, proprietaire, annee, acces)
    return {"formulaire": formulaire, "proprietaire": proprietaire, "titre": titre, "sous_titre": sous_titre, "cerfa": cerfa,
            "nb_biens": len(ids), "biens": list(biens.order_by("nom").values_list("nom", flat=True)), "etapes": etapes,
            "statut": statut, "declaree": declaree, "historique": historique, "lien": url, "libelle_lien": libelle_lien,
            "pret_a_declarer": statut[0] == "ok"}


def cartes(annee, acces=None):
    return [carte(*d, annee, acces) for d in _declarants()]


def cocher(formulaire, proprietaire_id, annee, le):
    """« Déclaration effectuée le … » : une seule déclaration cochée par cerfa et par année."""
    DeclarationFaite.objects.filter(annee=annee, formulaire=formulaire, proprietaire_id=proprietaire_id, active=True) \
        .update(active=False, decochee_le=timezone.now())
    return DeclarationFaite.objects.create(annee=annee, formulaire=formulaire, proprietaire_id=proprietaire_id, date_declaration=le)


def decocher(declaration):
    declaration.active = False
    declaration.decochee_le = timezone.now()
    declaration.save(update_fields=["active", "decochee_le"])
