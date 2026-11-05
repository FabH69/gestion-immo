"""Champs utilisables dans les modèles Word : {Nom_Loc}, {Loyer}… remplis à partir d'un bail.

Les noms sont reconnus sans tenir compte des accents, des majuscules, des espaces ni des tirets bas.
CHAMPS sert aussi à la page d'aide (Modèles › Liste des champs).
"""
import re
from datetime import date
from decimal import Decimal

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
CIVILITES = {"M.": "Monsieur", "Mme": "Madame"}

_UNITES = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze", "treize",
           "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf"]
_DIZAINES = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante", 8: "quatre-vingt"}


def _moins_de_cent(n):
    if n < 20:
        return _UNITES[n]
    d, u = divmod(n, 10)
    if d in (7, 9):
        base, u = _DIZAINES[d - 1], u + 10
        return base + ("-et-" if u == 11 and d == 7 else "-") + _UNITES[u]
    base = _DIZAINES[d]
    if u == 0:
        return base + ("s" if d == 8 else "")
    return base + ("-et-" if u == 1 and d != 8 else "-") + _UNITES[u]


def _moins_de_mille(n):
    c, r = divmod(n, 100)
    if c == 0:
        return _moins_de_cent(r)
    debut = "cent" if c == 1 else _UNITES[c] + "-cent" + ("s" if r == 0 else "")
    return debut + ("" if r == 0 else "-" + _moins_de_cent(r))


def nombre_en_lettres(n):
    n = int(n)
    if n < 1000:
        return _moins_de_mille(n)
    milliers, reste = divmod(n, 1000)
    debut = "mille" if milliers == 1 else _moins_de_mille(milliers) + "-mille"
    return debut + ("" if reste == 0 else "-" + _moins_de_mille(reste))


def euros_en_lettres(montant):
    """700 → « sept-cents euros » ; 653,33 → « six-cent-cinquante-trois euros et trente-trois centimes »."""
    if montant is None:
        return ""
    montant = Decimal(montant).quantize(Decimal("0.01"))
    euros, centimes = int(montant), int((montant - int(montant)) * 100)
    texte = f"{nombre_en_lettres(euros)} euro{'s' if euros > 1 else ''}"
    if centimes:
        texte += f" et {nombre_en_lettres(centimes)} centime{'s' if centimes > 1 else ''}"
    return texte


def montant(m):
    if m is None:
        return ""
    texte = f"{Decimal(m):,.2f}".replace(",", " ").replace(".", ",")
    return texte[:-3] if texte.endswith(",00") else texte


def date_fr(d):
    return f"{d:%d/%m/%Y}" if d else ""


def date_longue(d):
    return f"{d.day} {MOIS[d.month - 1]} {d.year}" if d else ""


def _personne(p, adresse=""):
    """« Monsieur Paul DURAND, né le 02/04/1965 à Lyon, demeurant … »"""
    civ = CIVILITES.get(p.civilite, "")
    texte = " ".join(x for x in (civ, p.prenom, p.nom.upper()) if x)
    if p.date_naissance:
        texte += f", né{'e' if p.civilite == 'Mme' else ''} le {date_fr(p.date_naissance)}"
        if p.lieu_naissance:
            texte += f" à {p.lieu_naissance}"
    if adresse:
        texte += ", demeurant " + adresse.replace("\n", " ")
    return texte


def _trimestre_texte(bail):
    """Trimestre IRL en clair : « 4e trimestre 2023 » à partir du trimestre et de l'indice de référence du bail."""
    numero = (bail.irl_trimestre or "").replace("T", "")
    annee = ""
    m = re.search(r"(20\d\d)", bail.indice_reference or "")
    if m:
        annee = m.group(1)
    if not numero:
        m = re.search(r"T([1-4])", bail.indice_reference or "")
        numero = m.group(1) if m else ""
    if not numero:
        return ""
    return f"{numero}{'er' if numero == '1' else 'e'} trimestre {annee}".strip()


def _decouper_index(index):
    """« HP 12345 / HC 6789 » → (12345, 6789) ; un index simple va en heures pleines."""
    hp = re.search(r"HP\s*:?\s*([\d.,\s]+)", index or "", re.I)
    hc = re.search(r"HC\s*:?\s*([\d.,\s]+)", index or "", re.I)
    if hp or hc:
        return (hp.group(1).strip() if hp else ""), (hc.group(1).strip() if hc else "")
    return index or "", ""


# (nom, description) par groupe : sert à la page d'aide
CHAMPS = [
    ("Propriétaire (bailleur)", [
        ("Propriétaire", "Nom du propriétaire (SCI, nom propre…)"),
        ("Représentant", "Représentant légal (ex. gérant)"),
        ("Adresse_Propr", "Adresse du propriétaire (rue)"),
        ("CP_Prop", "Code postal du propriétaire"),
        ("Ville_Prop", "Ville du propriétaire"),
        ("SIRET", "SIRET du propriétaire"), ("Email_Prop", "Adresse électronique du propriétaire"),
        ("Tel_Prop", "Téléphone du propriétaire"), ("IBAN", "IBAN pour le virement du loyer"),
    ]),
    ("Logement", [
        ("Adresse", "Adresse du logement (rue)"), ("CP", "Code postal du logement"), ("Ville", "Ville du logement"),
        ("Désignation", "Nom du lot (ex. T1 bis)"), ("Type_Logement", "Appartement, maison…"), ("Étage", "Étage / porte"),
        ("Superficie", "Surface habitable en m²"), ("Nb_Pièce", "Nombre de pièces principales"),
        ("Type_Chauffage", "Chauffage"), ("Type_eau", "Eau chaude"), ("Année_construction", "Année de construction"),
        ("Équipements", "Équipements du logement"), ("Annexes", "Annexes privatives"), ("DPE", "Classe DPE"),
        ("Code_porte", "Code porte"), ("Cave", "Cave n° (ou « néant »)"), ("Parking", "Parking n° (ou « néant »)"),
        ("Type_habitat", "Immeuble collectif / individuel"), ("Régime_juridique", "Copropriété / monopropriété"),
        ("Équipements_communs", "Liste des équipements communs cochés sur le bien"),
        ("Clause_particulière", "Clause particulière saisie sur la fiche du lot (ex. WC broyeur)"),
    ]),
    ("Cases à cocher (☒ ou ☐)", [
        ("Case_Collectif", "Immeuble collectif ; idem Case_Individuel"), ("Case_Copropriété", "Copropriété ; idem Case_Monopropriété"),
        ("Case_Chauffage_individuel", "idem Case_Chauffage_collectif"), ("Case_ECS_individuelle", "Eau chaude ; idem Case_ECS_collective"),
        ("Case_Habitation", "Usage d'habitation ; idem Case_Mixte"),
        ("Case_Gardiennage", "Équipements communs : idem Case_Ascenseur, Case_Interphone, Case_Local_vélos, Case_Antenne_TV, "
                             "Case_Fibre, Case_Vide_ordures, Case_Espaces_verts, Case_Chauffage_collectif_commun, Case_Eau_chaude_collective"),
        ("Case_Zone_tendue_Oui", "Soumis au décret d'évolution des loyers ; idem Case_Zone_tendue_Non"),
        ("Case_Encadrement_Oui", "Soumis au loyer de référence majoré ; idem Case_Encadrement_Non"),
    ]),
    ("Zone tendue", [
        ("Loyer_référence", "Loyer de référence (€/m²)"), ("Loyer_référence_majoré", "Loyer de référence majoré (€/m²)"),
        ("Loyer_base", "Loyer hors complément"), ("Complément_loyer", "Complément de loyer (€)"), ("Complément_loyer_motif", "Sa justification"),
        ("Loyer_précédent", "Dernier loyer du locataire précédent"), ("Date_loyer_précédent", "Date de son dernier versement"),
        ("Date_révision_précédente", "Date de sa dernière révision"),
    ]),
    ("Paragraphes conditionnels", [
        ("SI Zone_tendue", "Écrire {SI Zone_tendue} seul sur une ligne, le paragraphe, puis {FIN SI} : gardé seulement si le bien est en zone tendue ou encadré"),
        ("SI Complément_loyer", "Idem, gardé seulement s'il y a un complément de loyer ; aussi {SI Loyer_précédent}, {SI Garants}, {SI Clause_particulière}, {SI Meublé}, {SI Colocation}"),
    ]),
    ("Bail", [
        ("Type_Bail", "Location nue, meublée…"), ("Date_début_Bail", "Date de prise d'effet"), ("Date_fin_Bail", "Date de fin (si connue)"),
        ("Durée_bail", "Durée (1 an, 3 ans…)"), ("Date_fin_prévue", "Fin de la période en cours (début + durée)"),
        ("Dépenses_énergie", "Dépenses annuelles d'énergie estimées (DPE)"), ("Année_énergie", "Année de référence des prix de l'énergie"), ("Loyer", "Loyer actuel hors charges, en chiffres"),
        ("Loyer_lettres", "Loyer hors charges en toutes lettres"), ("Charges", "Charges mensuelles"),
        ("Charges_lettres", "Charges en toutes lettres"), ("Loyer_CC", "Loyer charges comprises"),
        ("Mode_charges", "Provisions, forfait…"), ("Dépôt_Gar", "Dépôt de garantie"), ("Dépôt_Gar_lettres", "Dépôt de garantie en lettres"),
        ("Jour_paiement", "Jour d'échéance du loyer"), ("IRL_Ref", "Valeur de l'IRL de référence (ex. 145,77)"),
        ("IRL_Ref_Période", "Trimestre de l'IRL de référence (ex. 3e trimestre 2025)"), ("IRL_Trimestre", "Trimestre seul (ex. 3e trimestre)"),
        ("Date_revision", "Date anniversaire de révision (ex. 25 février)"), ("Nb_exemplaire_bail", "Nombre d'exemplaires originaux"),
    ]),
    ("Locataires", [
        ("Locataires", "Tous les locataires : « Monsieur Paul DURAND et Madame Léa MARTIN »"),
        ("Locataires_identite", "Identité complète de chaque locataire (né le… à…)"),
        ("Civilité_Loc", "Monsieur / Madame (1er locataire)"), ("Nom_Loc", "Nom du 1er locataire"), ("Prénom_Loc", "Prénom"),
        ("Date_naissance_loc", "Date de naissance"), ("lieu_naissance_loc", "Lieu de naissance"), ("Nationalité_Loc", "Nationalité"),
        ("Email_Loc", "E-mail"), ("Tel_Loc", "Téléphone"), ("Adresse_actuelle_Loc", "Adresse avant l'entrée ou après le départ"),
        ("Nom_Loc2", "Nom du 2e locataire (colocation) ; idem Prénom_Loc2, Civilité_Loc2, Date_naissance_loc2…"),
    ]),
    ("Garants", [
        ("Garants", "Tous les garants : « Monsieur Paul DURAND et Madame Marie DURAND »"),
        ("Garants_identite", "Identité complète de chaque garant (né le… à…, demeurant…)"),
        ("Garants_signatures", "Noms des garants pour les signatures"),
        ("Civilité_Garant", "Monsieur / Madame (1er garant)"), ("Nom_Garant", "Nom du 1er garant"), ("Prénom_Garant", "Prénom"),
        ("Date_naissance_Garant", "Date de naissance"), ("Lieu_naissance_Garant", "Lieu de naissance"), ("Adresse_Garant", "Adresse"),
        ("Lien_Garant", "Lien avec le locataire"), ("Nom_Garant2", "Nom du 2e garant ; idem Prénom_Garant2, Adresse_Garant2…"),
    ]),
    ("État des lieux et compteurs", [
        ("Type_EDL", "Entrée ou Sortie"), ("Case_Entrée", "☒ si état des lieux d'entrée, ☐ sinon"), ("Case_Sortie", "☒ si sortie, ☐ sinon"),
        ("Date_EDL_entrée", "Date de l'état des lieux d'entrée"), ("Date_EDL_sortie", "Date de l'état des lieux de sortie"),
        ("PDL", "N° PDL électricité"), ("PCE", "N° PCE gaz"), ("Compteur_eau", "N° du compteur d'eau froide"),
        ("Index_HP_Entrée", "Électricité heures pleines à l'entrée (ou index unique)"), ("Index_HC_Entrée", "Électricité heures creuses à l'entrée"),
        ("Index_HP_Sortie", "Électricité heures pleines à la sortie"), ("Index_HC_Sortie", "Électricité heures creuses à la sortie"),
        ("Index_Gaz_Entrée", "Gaz à l'entrée ; idem Index_Gaz_Sortie"), ("Index_Eau_froide_Entrée", "Eau froide à l'entrée ; idem _Sortie"),
        ("Index_Eau_chaude_Entrée", "Eau chaude à l'entrée ; idem _Sortie"), ("Nouvelle_adresse", "Nouvelle adresse du locataire (sortie)"),
        ("Nb_clés", "Clés et badges remis"),
    ]),
    ("Divers", [("Date_jour", "Date du jour en toutes lettres (ex. 3 novembre 2026)"), ("Date_jour_courte", "Date du jour (03/11/2026)")]),
]


def valeurs(bail, garants=None, type_edl=None, aujourdhui=None):
    """Toutes les valeurs des champs pour ce bail (garants : liste à utiliser, sinon ceux du bail)."""
    aujourdhui = aujourdhui or date.today()
    lot, bien = bail.lot, bail.lot.bien
    proprio = bien.proprietaire
    locataires = list(bail.locataires.all())
    if garants is None:
        garants = list(bail.garants.all()) or [g for l in locataires for g in l.garants.filter(bail__isnull=True)]
    v = {
        "Propriétaire": proprio.nom, "Représentant": proprio.representant, "Adresse_Propr": proprio.adresse.replace("\n", ", "),
        "Adresse_Prop": proprio.adresse.replace("\n", ", "), "CP_Prop": proprio.code_postal, "Ville_Prop": proprio.ville,
        "Ville_Propr": proprio.ville, "SIRET": proprio.siret, "Email_Prop": proprio.email, "Tel_Prop": proprio.telephone,
        "IBAN": proprio.iban,
        "Adresse": bien.adresse, "Adresse_Appart": bien.adresse, "CP": bien.code_postal, "CP_Appart": bien.code_postal,
        "Ville": bien.ville, "Ville_Appart": bien.ville, "Désignation": lot.designation, "Type_Logement": lot.get_type_display(),
        "Étage": lot.etage, "Superficie": montant(lot.surface) if lot.surface else "", "Nb_Pièce": lot.nb_pieces or "",
        "Type_Chauffage": lot.chauffage, "Type_eau": lot.eau_chaude, "Année_construction": lot.annee_construction,
        "Équipements": lot.equipements.replace("\n", ", "), "Annexes": lot.annexes.replace("\n", ", "), "DPE": lot.classe_dpe,
        "Type_Bail": bail.get_type_display(), "Date_début_Bail": date_fr(bail.date_debut), "Date_fin_Bail": date_fr(bail.date_fin),
        "Loyer": montant(bail.loyer_hc), "Loyer_lettres": euros_en_lettres(bail.loyer_hc), "Charges": montant(bail.charges),
        "Charges_lettres": euros_en_lettres(bail.charges), "Loyer_CC": montant(bail.loyer_cc), "Mode_charges": bail.get_mode_charges_display(),
        "Dépôt_Gar": montant(bail.depot_garantie), "Dépôt_Gar_lettres": euros_en_lettres(bail.depot_garantie),
        "Jour_paiement": "1er" if bail.jour_echeance == 1 else bail.jour_echeance, "IRL_Ref": str(bail.irl_valeur or "").replace(".", ","),
        "IRL_Ref_Période": _trimestre_texte(bail), "IRL_Trimestre": re.sub(r"\s*\d{4}$", "", _trimestre_texte(bail)),
        "Date_revision": f"{bail.date_debut.day} {MOIS[bail.date_debut.month - 1]}", "Nb_exemplaire_bail": 2,
        "Date_jour": date_longue(aujourdhui), "Date_jour_courte": date_fr(aujourdhui),
    }
    v["IRL de référence"], v["IRL référence Période"] = v["IRL_Ref"], v["IRL_Ref_Période"]

    # immeuble, équipements communs et cases à cocher
    def case(oui):
        return "☒" if oui else "☐"

    collectif = bien.type_habitat == "collectif" if bien.type_habitat else bien.lots.count() > 1
    v.update({
        "Type_habitat": "Immeuble collectif" if collectif else "Immeuble individuel",
        "Régime_juridique": bien.get_regime_juridique_display() if bien.regime_juridique else "",
        "Équipements_communs": ", ".join(bien.equipements_communs) or "néant",
        "Code_porte": lot.code_porte or "–", "Cave": lot.cave or "néant", "Parking": lot.parking or "néant",
        "Case_Collectif": case(collectif), "Case_Individuel": case(not collectif),
        "Case_Copropriété": case(bien.regime_juridique == "copropriete"), "Case_Monopropriété": case(bien.regime_juridique == "monopropriete"),
        "Case_Chauffage_individuel": case(lot.mode_chauffage == "individuel"), "Case_Chauffage_collectif": case(lot.mode_chauffage == "collectif"),
        "Case_ECS_individuelle": case(lot.mode_eau_chaude == "individuelle"), "Case_ECS_collective": case(lot.mode_eau_chaude == "collective"),
        "Case_Habitation": case(bail.destination == "habitation"), "Case_Mixte": case(bail.destination == "mixte"),
        "Case_Gardiennage": case(bien.gardiennage), "Case_Ascenseur": case(bien.ascenseur), "Case_Interphone": case(bien.interphone),
        "Case_Local_vélos": case(bien.local_velos), "Case_Antenne_TV": case(bien.antenne_tv), "Case_Fibre": case(bien.fibre),
        "Case_Vide_ordures": case(bien.vide_ordures), "Case_Espaces_verts": case(bien.espaces_verts),
        "Case_Chauffage_collectif_commun": case(bien.chauffage_collectif), "Case_Eau_chaude_collective": case(bien.eau_chaude_collective),
        "Case_Zone_tendue_Oui": case(bien.zone_tendue), "Case_Zone_tendue_Non": case(not bien.zone_tendue),
        "Case_Encadrement_Oui": case(bien.encadrement_loyers), "Case_Encadrement_Non": case(not bien.encadrement_loyers),
        "Type_Chauffage": " ".join(x for x in (lot.get_mode_chauffage_display() if lot.mode_chauffage else "", lot.chauffage) if x),
        "Type_eau": " ".join(x for x in (lot.get_mode_eau_chaude_display() if lot.mode_eau_chaude else "", lot.eau_chaude) if x),
    })
    if lot.energie_min and lot.energie_max:
        v["Dépenses_énergie"] = f"entre {montant(lot.energie_min)} € et {montant(lot.energie_max)} €"
    elif lot.energie_min or lot.energie_max:
        v["Dépenses_énergie"] = f"{montant(lot.energie_min or lot.energie_max)} €"
    else:
        v["Dépenses_énergie"] = ""
    v["Année_énergie"] = lot.energie_annee or ""
    loyer_signature = bail.loyer_initial or bail.loyer_hc
    v.update({
        "Loyer_référence": montant(bail.loyer_reference), "Loyer_référence_majoré": montant(bail.loyer_reference_majore),
        "Complément_loyer": montant(bail.complement_loyer) if bail.complement_loyer else "", "Complément_loyer_motif": bail.complement_loyer_motif,
        "Loyer_base": montant(loyer_signature - (bail.complement_loyer or 0)) if loyer_signature is not None else "",
        "Loyer_précédent": montant(bail.loyer_precedent) if bail.loyer_precedent else "",
        "Date_loyer_précédent": date_fr(bail.date_loyer_precedent), "Date_révision_précédente": date_fr(bail.date_revision_precedente) or "aucune",
        "Clause_particulière": lot.clause_particuliere.strip(),
        # conditions des paragraphes {SI …}
        "Zone_tendue": "Oui" if bien.zone_tendue or bien.encadrement_loyers else "",
        "Meublé": "Oui" if bail.type in ("meuble", "etudiant", "mobilite") else "",
        "Colocation": "Oui" if len(locataires) > 1 else "",
    })
    try:
        from .documents import duree_texte

        v["Durée_bail"] = duree_texte(bail)
        mois = bail.duree_mois
        a, m = divmod(bail.date_debut.month - 1 + mois, 12)
        import calendar as _cal

        annee = bail.date_debut.year + a
        jour = min(bail.date_debut.day, _cal.monthrange(annee, m + 1)[1])
        v["Date_fin_prévue"] = date_fr(bail.date_fin or bail.date_debut.replace(year=annee, month=m + 1, day=jour))
    except Exception:
        v["Durée_bail"] = ""

    v["Locataires"] = " et ".join(" ".join(x for x in (CIVILITES.get(l.civilite, ""), l.prenom, l.nom.upper()) if x) for l in locataires)
    v["Locataires_identite"] = " et ".join(_personne(l) for l in locataires)
    for i, l in enumerate(locataires[:4], start=1):
        s = "" if i == 1 else str(i)
        v.update({f"Civilité_Loc{s}": CIVILITES.get(l.civilite, ""), f"Nom_Loc{s}": l.nom, f"Prénom_Loc{s}": l.prenom,
                  f"Date_naissance_loc{s}": date_fr(l.date_naissance), f"lieu_naissance_loc{s}": l.lieu_naissance,
                  f"Nationalité_Loc{s}": l.nationalite, f"Email_Loc{s}": l.email, f"Tel_Loc{s}": l.telephone,
                  f"Adresse_actuelle_Loc{s}": l.adresse_actuelle.replace("\n", " ")})

    v["Garants"] = " et ".join(" ".join(x for x in (CIVILITES.get(g.civilite, ""), g.prenom, g.nom.upper()) if x) for g in garants)
    v["Garants_identite"] = " ET ".join(_personne(g, g.adresse) for g in garants)
    v["Garants_signatures"] = "          ".join(f"{g.prenom} {g.nom.upper()}".strip() for g in garants)
    for i in (1, 2):
        s = "" if i == 1 else "2"
        g = garants[i - 1] if len(garants) >= i else None
        v.update({f"Civilité_Garant{s}": CIVILITES.get(g.civilite, "") if g else "", f"Nom_Garant{s}": g.nom if g else "",
                  f"Prénom_Garant{s}": g.prenom if g else "", f"Date_naissance_Garant{s}": date_fr(g.date_naissance) if g else "",
                  f"Lieu_naissance_Garant{s}": g.lieu_naissance if g else "", f"Adresse_Garant{s}": g.adresse.replace("\n", " ") if g else "",
                  f"Lien_Garant{s}": g.lien if g else ""})

    # état des lieux et compteurs
    edls = {e.type: e for e in bail.etats_des_lieux.order_by("date")}
    v.update({"Type_EDL": {"entree": "Entrée", "sortie": "Sortie"}.get(type_edl, ""),
              "Case_Entrée": "☒" if type_edl == "entree" else "☐", "Case_Sortie": "☒" if type_edl == "sortie" else "☐",
              "Date_EDL_entrée": date_fr(edls["entree"].date) if "entree" in edls else (date_fr(bail.date_debut) if type_edl == "entree" else ""),
              "Date_EDL_sortie": date_fr(edls["sortie"].date) if "sortie" in edls else (date_fr(bail.date_fin) if type_edl == "sortie" else ""),
              "PDL": bail.pdl_electricite, "PCE": bail.pce_gaz, "Compteur_eau": bail.compteur_eau,
              "Nouvelle_adresse": edls["sortie"].nouvelle_adresse.replace("\n", " ") if "sortie" in edls else "",
              "Nb_clés": edls[type_edl].nb_cles if type_edl in edls else ""})
    for moment, suffixe in (("Entrée", "_entree"), ("Sortie", "_sortie")):
        hp, hc = _decouper_index(getattr(bail, "index_elec" + suffixe))
        v.update({f"Index_HP_{moment}": hp, f"Index_HC_{moment}": hc, f"Index_Elec_{moment}": getattr(bail, "index_elec" + suffixe),
                  f"Index_Gaz_{moment}": getattr(bail, "index_gaz" + suffixe),
                  f"Index_Eau_froide_{moment}": getattr(bail, "index_eau_froide" + suffixe),
                  f"Index_Eau_chaude_{moment}": getattr(bail, "index_eau_chaude" + suffixe)})
    return {k: ("" if val is None else val) for k, val in v.items()}
