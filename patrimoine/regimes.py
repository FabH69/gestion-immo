"""Régimes fiscaux : tableau récapitulatif des biens (régime, recettes, charges, formulaire) et alertes de seuils.

Chaque bien a un régime (location nue, SCI à l'IR, LMNP, LMP, société à l'IR ou à l'IS) et une option micro ou réel.
Les montants viennent des mêmes calculs que les autres pages : annexe 2072 pour les revenus fonciers (loyers encaissés,
factures codées, forfait, intérêts), bilan LMNP pour le meublé (loyers ou réservations, charges, intérêts).
Seuils : règles en vigueur depuis les revenus 2025 (loi du 19 novembre 2024 sur les meublés de tourisme).
"""
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse

from .models import Bien, Proprietaire

ZERO = Decimal("0.00")

# --- seuils (revenus 2025 et suivants) ---------------------------------------------------------------------------------
MICRO_FONCIER = Decimal("15000")          # recettes brutes foncières du foyer
ABATTEMENT_MICRO_FONCIER = Decimal("30")
MICRO_BIC_CLASSIQUE = Decimal("77700")    # meublé classique et meublé de tourisme classé (abattement 50 %)
MICRO_BIC_NON_CLASSE = Decimal("15000")   # meublé de tourisme non classé (abattement 30 %)
SEUIL_LMP = Decimal("23000")              # recettes meublées du foyer (et supérieures aux autres revenus d'activité)
PART_MEUBLE_SCI = Decimal("10")           # % de recettes meublées au-delà duquel une SCI risque l'IS
PROCHE = Decimal("0.8")                   # alerte « vous approchez » à 80 % du seuil

FORMULAIRES = {
    ("nu", "micro"): ("Déclaration 2042, case 4BE (micro-foncier)",
                      "Reportez les loyers bruts encaissés : l'abattement de 30 % est appliqué par les impôts."),
    ("nu", "reel"): ("Déclaration 2044 (revenus fonciers), puis report sur la 2042",
                     "Le récapitulatif 2044 de l'appli donne les montants ligne par ligne."),
    ("sci_ir", "reel"): ("Déclaration 2072 de la SCI, puis 2044 de chaque associé",
                         "La SCI dépose la 2072 ; chaque associé reporte sa part (cadre I) sur sa propre 2044."),
    ("lmnp", "micro"): ("Déclaration 2042-C-PRO, locations meublées non professionnelles (micro-BIC)",
                        "Reportez les recettes brutes : abattement de 50 % (classique ou tourisme classé) ou 30 % (tourisme non classé)."),
    ("lmnp", "reel"): ("Liasse 2031 et 2033 (BIC réel), puis 2042-C-PRO",
                       "La liasse se dépose en ligne avant mai ; le bilan LMNP de l'appli prépare les montants. Faites-la valider par un comptable."),
    ("lmp", "micro"): ("Déclaration 2042-C-PRO, locations meublées professionnelles (micro-BIC)",
                       "En LMP, des cotisations sociales (SSI) sont dues sur le bénéfice, et la plus-value suit le régime professionnel."),
    ("lmp", "reel"): ("Liasse 2031 et 2033 (BIC réel), puis 2042-C-PRO en LMP",
                      "Cotisations sociales (SSI) sur le bénéfice ; un déficit s'impute sur le revenu global. Comptable conseillé."),
    ("societe_ir", "reel"): ("Liasse 2031 et 2033 de la société, puis 2042-C-PRO de chaque associé",
                             "La société dépose la liasse ; chaque associé reporte sa quote-part du bénéfice ou du déficit."),
    ("societe_is", "reel"): ("Liasse 2065 et 2033 (impôt sur les sociétés)",
                             "La société paie elle-même l'impôt : à préparer avec votre comptable (pas d'aide détaillée dans l'appli)."),
    ("non_loue", "reel"): ("Rien à déclarer", "Bien non loué : aucun revenu locatif."),
}


def formulaire(bien):
    option = "micro" if bien.micro else "reel"
    return FORMULAIRES.get((bien.regime_fiscal, option)) or FORMULAIRES.get((bien.regime_fiscal, "reel")) or ("", "")


def lien_page(bien, annee):
    """Page de l'appli qui prépare la déclaration du bien."""
    r = bien.regime_fiscal
    if r == "nu" and not bien.micro:
        return f"{reverse('recap_2044')}?annee={annee}", "Récapitulatif 2044"
    if r == "sci_ir":
        return f"{reverse('annexe_2072')}?sci={bien.proprietaire_id}&annee={annee}&bien={bien.pk}", "Annexe 2072"
    if r in ("lmnp", "lmp"):
        return f"{reverse('biens_lmnp')}?annee={annee}", "Bilan LMNP (2031)"
    if r in ("societe_ir", "societe_is"):
        return f"{reverse('compta_societe')}?annee={annee}", "Récapitulatif société"
    return "", ""


def categorie_meuble(lot):
    """classique, tourisme classé ou tourisme non classé (courte durée)."""
    if lot.exploitation == "courte":
        return "classe" if lot.tourisme_classe else "non_classe"
    return "classique"


def _lots_actifs(bien, annee):
    return [l for l in bien.lots.all() if not l.vendu or (l.date_vente and l.date_vente.year >= annee)]


def chiffres_bien(bien, annee):
    """Recettes, charges, résultat imposable estimé du bien pour l'année, et recettes meublées par catégorie."""
    r = bien.regime_fiscal
    if r in ("nu", "sci_ir"):
        from .annexe2072 import colonne

        v = colonne(bien, annee)["valeurs"]
        recettes = v.get("5", ZERO)
        charges = v.get("16", ZERO) + v.get("17", ZERO)
        if bien.micro:
            resultat = (recettes * (100 - ABATTEMENT_MICRO_FONCIER) / 100).quantize(Decimal("1"))
        else:
            resultat = v.get("18", ZERO)
        return {"recettes": recettes, "charges": charges, "resultat": resultat, "par_categorie": {}}
    if r in ("lmnp", "lmp", "societe_ir", "societe_is"):
        from .lmnp import bilan_lot

        recettes = charges = resultat = ZERO
        par_categorie = {"classique": ZERO, "classe": ZERO, "non_classe": ZERO}
        for lot in _lots_actifs(bien, annee):
            b = bilan_lot(lot, annee)
            recettes += b["total_recettes"]
            charges += b["total_charges"]
            resultat += b["micro_imposable"] if bien.micro else b["resultat_fiscal"]
            par_categorie[categorie_meuble(lot)] += b["total_recettes"]
        return {"recettes": recettes, "charges": charges, "resultat": resultat, "par_categorie": par_categorie}
    return {"recettes": ZERO, "charges": ZERO, "resultat": ZERO, "par_categorie": {}}


def _alerte(niveau, texte, bien=None):
    return {"niveau": niveau, "texte": texte, "bien": bien}


def alertes_bien(bien):
    """Incohérences entre le régime du bien, son propriétaire et ses lots."""
    a = []
    r = bien.regime_fiscal
    type_proprio = bien.proprietaire.type
    nom_propre = type_proprio == Proprietaire.Type.NOM_PROPRE
    if bien.regime_a_verifier:
        a.append(_alerte("info", "Régime déduit automatiquement de l'ancienne comptabilité : ouvrez la fiche du bien, "
                                 "vérifiez le régime et l'option micro ou réel, puis enregistrez.", bien))
    if r in ("nu", "lmnp", "lmp") and not nom_propre:
        a.append(_alerte("rouge", f"Le propriétaire « {bien.proprietaire} » est une société : ce régime est réservé aux biens "
                                  "détenus en nom propre. Choisissez SCI à l'IR, société à l'IR ou à l'IS.", bien))
    if r in ("sci_ir", "societe_ir", "societe_is") and nom_propre:
        a.append(_alerte("rouge", f"Le propriétaire « {bien.proprietaire} » est en nom propre : choisissez location nue, "
                                  "LMNP ou LMP.", bien))
    if r == "sci_ir" and type_proprio == Proprietaire.Type.SCI_IS:
        a.append(_alerte("rouge", "Le propriétaire est une SCI à l'IS : choisissez « Société à l'IS ».", bien))
    lots = [l for l in bien.lots.all() if not l.vendu]
    meubles = [l for l in lots if l.meuble or l.exploitation == "courte"]
    if r in ("nu", "sci_ir") and meubles:
        noms = ", ".join(l.designation for l in meubles)
        if r == "nu":
            a.append(_alerte("orange", f"Lot meublé ou en courte durée ({noms}) dans un bien déclaré en location nue : "
                                       "un meublé se déclare en BIC. Passez le bien en LMNP, ou décochez « meublé ».", bien))
        else:
            a.append(_alerte("orange", f"Lot meublé ou en courte durée ({noms}) dans une SCI à l'IR : au-delà de "
                                       f"{PART_MEUBLE_SCI} % de recettes meublées, la SCI risque de passer à l'impôt sur les "
                                       "sociétés. À voir avec votre comptable.", bien))
    if r in ("lmnp", "lmp", "societe_ir"):
        nus = [l for l in lots if not l.meuble and l.exploitation != "courte" and l.type not in ("parking", "cave", "garage")]
        if nus:
            noms = ", ".join(l.designation for l in nus)
            a.append(_alerte("orange", f"Lot non coché « meublé » ({noms}) dans un bien déclaré en meublé : cochez « meublé » "
                                       "sur la fiche du lot s'il est bien loué meublé.", bien))
    if r not in ("lmnp", "lmp", "societe_ir", "societe_is"):
        lmnp = [l for l in lots if l.lmnp]
        if lmnp:
            noms = ", ".join(l.designation for l in lmnp)
            a.append(_alerte("orange", f"Lot coché « exploité en LMNP » ({noms}) alors que le bien n'est pas en meublé : "
                                       "choisissez le régime LMNP sur le bien, ou décochez la case du lot.", bien))
    if r in ("lmnp", "lmp") and bien.micro:
        non_classes = [l for l in lots if l.exploitation == "courte" and not l.tourisme_classe]
        if non_classes:
            a.append(_alerte("info", "Courte durée non classée en micro-BIC : abattement de 30 % seulement, et micro impossible "
                                     f"au-delà de {MICRO_BIC_NON_CLASSE:,.0f} € de recettes. Le réel est souvent plus "
                                     "intéressant.".replace(",", " "), bien))
    return a


def _euros(m):
    return f"{m:,.0f} €".replace(",", " ")


def alertes_seuils(lignes):
    """Seuils calculés sur l'ensemble des biens du foyer (biens en nom propre, plus la part des SCI à l'IR)."""
    a = []
    # micro-foncier : loyers bruts du foyer (location nue + part des SCI à l'IR)
    foncier = sum((l["recettes"] for l in lignes if l["bien"].regime_fiscal == "nu"), ZERO)
    foncier += sum((l["recettes"] * (l["bien"].proprietaire.pourcentage_detention or 0) / 100
                    for l in lignes if l["bien"].regime_fiscal == "sci_ir"), ZERO)
    en_micro = [l["bien"] for l in lignes if l["bien"].regime_fiscal == "nu" and l["bien"].micro]
    if en_micro and foncier > MICRO_FONCIER:
        a.append(_alerte("rouge", f"Micro-foncier impossible : les loyers bruts du foyer ({_euros(foncier)}, part des SCI comprise) "
                                  f"dépassent {_euros(MICRO_FONCIER)}. Passez au réel : déclaration 2044."))
    elif en_micro and foncier > MICRO_FONCIER * PROCHE:
        a.append(_alerte("orange", f"Vous approchez du plafond du micro-foncier : {_euros(foncier)} sur {_euros(MICRO_FONCIER)}."))
    elif not en_micro and ZERO < foncier <= MICRO_FONCIER and any(l["bien"].regime_fiscal == "nu" for l in lignes):
        a.append(_alerte("info", f"Loyers bruts du foyer : {_euros(foncier)}, sous le plafond de {_euros(MICRO_FONCIER)}. "
                                 "Le micro-foncier (30 % d'abattement) est possible : il vaut le coup si vos charges et "
                                 "intérêts font moins de 30 % des loyers."))

    # micro-BIC : recettes meublées du foyer (nom propre), par catégorie
    meubles = [l for l in lignes if l["bien"].regime_fiscal in ("lmnp", "lmp")]
    total_meuble = sum((l["recettes"] for l in meubles), ZERO)
    non_classe = sum((l["par_categorie"].get("non_classe", ZERO) for l in meubles), ZERO)
    micro_bic = [l["bien"] for l in meubles if l["bien"].micro]
    if micro_bic:
        if total_meuble > MICRO_BIC_CLASSIQUE:
            a.append(_alerte("rouge", f"Micro-BIC impossible : les recettes meublées ({_euros(total_meuble)}) dépassent "
                                      f"{_euros(MICRO_BIC_CLASSIQUE)}. Passez au réel (liasse 2031)."))
        elif non_classe > MICRO_BIC_NON_CLASSE:
            a.append(_alerte("rouge", f"Micro-BIC impossible : la courte durée non classée rapporte {_euros(non_classe)}, au-delà "
                                      f"de {_euros(MICRO_BIC_NON_CLASSE)}. Passez au réel, ou faites classer le logement."))
        elif total_meuble > MICRO_BIC_CLASSIQUE * PROCHE or non_classe > MICRO_BIC_NON_CLASSE * PROCHE:
            a.append(_alerte("orange", "Vous approchez du plafond du micro-BIC : "
                                       f"{_euros(total_meuble)} de recettes meublées, dont {_euros(non_classe)} en courte durée non classée."))

    # LMP : recettes meublées du foyer au-delà de 23 000 € (et des autres revenus d'activité)
    en_lmnp = any(l["bien"].regime_fiscal == "lmnp" for l in meubles)
    en_lmp = any(l["bien"].regime_fiscal == "lmp" for l in meubles)
    if en_lmnp and total_meuble > SEUIL_LMP:
        a.append(_alerte("rouge", f"Risque de passage en LMP : vos recettes meublées ({_euros(total_meuble)}) dépassent "
                                  f"{_euros(SEUIL_LMP)}. Si elles dépassent aussi les autres revenus d'activité du foyer "
                                  "(salaires…), vous êtes LMP : cotisations sociales et règles de plus-value différentes."))
    elif en_lmnp and total_meuble > SEUIL_LMP * PROCHE:
        a.append(_alerte("orange", f"Vous approchez du seuil LMP : {_euros(total_meuble)} de recettes meublées sur {_euros(SEUIL_LMP)}."))
    if en_lmp and total_meuble <= SEUIL_LMP:
        a.append(_alerte("orange", f"Bien déclaré en LMP alors que les recettes meublées ({_euros(total_meuble)}) ne dépassent pas "
                                   f"{_euros(SEUIL_LMP)} : vous êtes en principe LMNP."))
    return a


def synthese(annee):
    """Lignes du tableau récapitulatif, alertes par bien et alertes de seuils du foyer."""
    biens = Bien.objects.filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).select_related("proprietaire") \
        .prefetch_related("lots").order_by("proprietaire__nom", "nom")
    lignes = []
    for b in biens:
        chiffres = chiffres_bien(b, annee)
        nom_form, aide = formulaire(b)
        url, libelle_lien = lien_page(b, annee)
        lignes.append({"bien": b, **chiffres, "formulaire": nom_form, "aide": aide, "lien": url, "libelle_lien": libelle_lien,
                       "lots": [{"lot": l, "categorie": _libelle_lot(l)} for l in b.lots.all() if not l.vendu],
                       "alertes": alertes_bien(b)})
    seuils = alertes_seuils(lignes)
    total = {k: sum((l[k] for l in lignes), ZERO) for k in ("recettes", "charges", "resultat")}
    nb_alertes = sum(len(l["alertes"]) for l in lignes) + len(seuils)
    return {"lignes": lignes, "seuils": seuils, "total": total, "nb_alertes": nb_alertes}


def _libelle_lot(lot):
    if lot.exploitation == "courte":
        return "Courte durée, tourisme classé" if lot.tourisme_classe else "Courte durée, non classé"
    return "Meublé" if lot.meuble else "Nu"


def nombre_alertes_rouges():
    """Pour une pastille éventuelle : incohérences graves sur les fiches (sans calcul des montants)."""
    biens = Bien.objects.filter(vendu=False).select_related("proprietaire").prefetch_related("lots")
    return sum(1 for b in biens for a in alertes_bien(b) if a["niveau"] == "rouge")
