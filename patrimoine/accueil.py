"""Page d'accueil personnalisable : chaque utilisateur range ses blocs (ordre, place), les affiche ou non et choisit les
données montrées dans certains blocs. Les réglages sont gardés dans PreferencesAccueil ; ce qui manque (nouveau bloc,
réglage jamais fait) prend la valeur d'origine, ce qui est inconnu est ignoré."""

from patrimoine.rappels import GENRES

LARGEUR, GAUCHE, DROITE = "largeur", "gauche", "droite"
PLACES = [(LARGEUR, "Toute la largeur"), (GAUCHE, "Colonne de gauche"), (DROITE, "Colonne de droite")]

# Données au choix dans un bloc : (code, libellé, droit nécessaire ou None)
CHIFFRES = [
    ("encaisse", "Loyers encaissés du mois", "loyers"),
    ("impayes", "Impayés", "loyers"),
    ("cash_flow", "Cash-flow locatif par mois", "finances"),
    ("crd", "Capital restant dû locatif", "finances"),
]
# Actions rapides : (code, libellé, droit — « module » ou « module:modification » —, lien, proposée d'origine)
CATALOGUE_ACTIONS = [
    ("encaisser", "Encaisser un loyer", "loyers:modification", "admin:patrimoine_echeance_virement", True),
    ("quittance", "Créer une quittance", "loyers", "envoyer_quittance", True),
    ("relancer", "Relancer un impayé", "loyers", "relances_impayes", True),
    ("annonce", "Créer une annonce", "relocation:modification", "admin:patrimoine_annonce_add", True),
    ("loyers", "Voir les loyers", "loyers", "admin:patrimoine_echeance_changelist", False),
    ("facture_import", "Ajouter des factures", "compta:modification", "admin:patrimoine_piececomptable_import", False),
    ("facture", "Saisir une facture", "compta:modification", "admin:patrimoine_piececomptable_add", False),
    ("bail", "Nouveau bail", "baux:modification", "admin:patrimoine_bail_add", False),
    ("locataire", "Nouveau locataire", "locataires:modification", "admin:patrimoine_locataire_add", False),
    ("candidature", "Nouvelle candidature", "relocation:modification", "admin:patrimoine_candidature_add", False),
    ("rappel", "Ajouter un rappel", "outils:modification", "admin:patrimoine_rappel_add", False),
    ("lcd_import", "Importer la courte durée", "lcd", "suivi_lcd", False),
    ("reservation", "Nouvelle réservation courte durée", "lcd:modification", "admin:patrimoine_reservationlcd_add", False),
    ("bien", "Ajouter un bien", "biens:modification", "admin:patrimoine_bien_add", False),
    ("simulation", "Nouvelle simulation", "investissement", "simulateurs:rentabilite", False),
    ("declaration", "Ma déclaration de revenus", "compta", "declaration_revenus", False),
]
ACTIONS = [(code, libelle, droit) for code, libelle, droit, *_ in CATALOGUE_ACTIONS]
LIENS_ACTIONS = {code: lien for code, _, _, lien, _ in CATALOGUE_ACTIONS}
ACTIONS_D_ORIGINE = [code for code, *_, origine in CATALOGUE_ACTIONS if origine]
LIBELLE_MAX = 40
COMPTA = [
    ("factures", "Factures à saisir, par dossier", None),
    ("hors_dossier", "Factures sur un bien hors dossier", None),
    ("attestations", "Attestations d'emprunt manquantes", None),
]
A_FAIRE = [(code, libelle, None) for code, libelle in GENRES]

# Blocs, dans l'ordre d'origine : (code, titre, place d'origine, données au choix)
BLOCS = [
    ("chiffres", "Chiffres clés", LARGEUR, CHIFFRES),
    ("compta", "Comptabilité à faire", GAUCHE, COMPTA),
    ("a_faire", "À faire", GAUCHE, A_FAIRE),
    ("actions", "Actions rapides", DROITE, ACTIONS),
    ("notes", "Notes rapides", DROITE, None),
]
_BLOCS = {code: (titre, place, donnees) for code, titre, place, donnees in BLOCS}


def _visible_pour(code, droits):
    """Le bloc a-t-il quelque chose à montrer avec ces droits ?"""
    d = droits or {}
    if code == "chiffres":
        return bool(d.get("loyers") or d.get("finances"))
    if code == "compta":
        return bool(d.get("compta"))
    if code == "a_faire":
        return bool(d.get("outils") or d.get("relocation"))
    if code == "actions":
        return any(_donnee_permise(droit, c, droits) for c, _, droit in ACTIONS)
    return True


def _donnee_permise(droit, code_donnee, droits):
    """Droit « module » : il suffit de le voir ; « module:modification » : il faut pouvoir le modifier."""
    if droit is None:
        return True
    module, _, niveau = droit.partition(":")
    valeur = (droits or {}).get(module)
    return valeur == niveau if niveau else bool(valeur)


def _donnees_d_origine(code, donnees):
    if not donnees:
        return None
    return list(ACTIONS_D_ORIGINE) if code == "actions" else [c for c, *_ in donnees]


def reglages_d_origine():
    return {"blocs": [{"code": code, "visible": True, "place": place, "donnees": _donnees_d_origine(code, donnees), "libelles": {}}
                      for code, _, place, donnees in BLOCS]}


def _libelles(valeur, permis):
    """Libellés choisis par l'utilisateur (actions rapides) : texte court, vide = libellé d'origine."""
    if not isinstance(valeur, dict):
        return {}
    return {c: " ".join(str(t).split())[:LIBELLE_MAX] for c, t in valeur.items() if c in permis and str(t or "").strip()}


def normaliser(reglages):
    """Réglages complets et valides, quoi qu'il y ait en base (blocs inconnus ignorés, blocs manquants ajoutés à la fin)."""
    origine = {b["code"]: b for b in reglages_d_origine()["blocs"]}
    blocs, vus = [], set()
    for b in (reglages or {}).get("blocs") or []:
        code = b.get("code") if isinstance(b, dict) else None
        if code not in _BLOCS or code in vus:
            continue
        vus.add(code)
        _, place, donnees = _BLOCS[code]
        choix, libelles = None, {}
        if donnees:
            permis = [c for c, *_ in donnees]
            stockees = b.get("donnees") if isinstance(b.get("donnees"), list) else _donnees_d_origine(code, donnees)
            choix = list(dict.fromkeys(c for c in stockees if c in permis))
            libelles = _libelles(b.get("libelles"), permis)
        blocs.append({"code": code, "visible": bool(b.get("visible", True)),
                      "place": b.get("place") if b.get("place") in dict(PLACES) else place, "donnees": choix, "libelles": libelles})
    blocs += [dict(origine[code]) for code, *_ in BLOCS if code not in vus]
    return {"blocs": blocs}


def reglages(user):
    from patrimoine.models import PreferencesAccueil

    if user is None or not user.is_authenticated:
        return reglages_d_origine()
    p = PreferencesAccueil.objects.filter(utilisateur=user).first()
    return normaliser(p.reglages if p else None)


def enregistrer(user, valeurs):
    from patrimoine.models import PreferencesAccueil

    PreferencesAccueil.objects.update_or_create(utilisateur=user, defaults={"reglages": normaliser(valeurs)})


def depuis_formulaire(post, reglages_actuels):
    """Réglages lus dans le formulaire « Personnaliser l'accueil ». L'ordre est celui des champs `ordre` envoyés ;
    les blocs absents du formulaire (pas de droit) gardent leur réglage et leur place relative à la fin."""
    ordre = [c for c in post.getlist("ordre") if c in _BLOCS]
    actuels = {b["code"]: b for b in reglages_actuels["blocs"]}
    blocs = []
    for code in ordre:
        _, place, donnees = _BLOCS[code]
        b = {"code": code, "visible": post.get(f"visible_{code}") == "1", "place": post.get(f"place_{code}") or place,
             "donnees": None, "libelles": {}}
        if donnees:
            affichees = set(post.getlist(f"donnees_{code}"))
            proposees = post.getlist(f"proposees_{code}")  # dans l'ordre de l'écran (actions rapides : ordre choisi)
            anciennes = actuels[code]["donnees"] or []
            b["donnees"] = [c for c in proposees if c in affichees]
            # une donnée non proposée (pas de droit) garde son réglage
            b["donnees"] += [c for c in anciennes if c not in proposees]
            b["libelles"] = {**{c: t for c, t in (actuels[code].get("libelles") or {}).items() if c not in proposees},
                             **{c: post.get(f"libelle_{code}_{c}", "") for c in proposees}}
        blocs.append(b)
    blocs += [actuels[code] for code in actuels if code not in ordre]
    return {"blocs": blocs}


def pour_l_ecran(user, droits):
    """Lignes de l'écran « Personnaliser l'accueil » : les blocs que l'utilisateur peut voir, avec leurs données."""
    lignes = []
    for b in reglages(user)["blocs"]:
        if not _visible_pour(b["code"], droits):
            continue
        titre, _, donnees = _BLOCS[b["code"]]
        if b["code"] == "a_faire" and not (droits or {}).get("outils"):
            donnees = None  # sans le droit Outils, le bloc montre les entrées et sorties en cours : rien à choisir
        rang = {c: i for i, c in enumerate(b["donnees"] or [])}
        choix = [{"code": c, "libelle": libelle, "coche": c in rang, "libelle_perso": (b.get("libelles") or {}).get(c, "")}
                 for c, libelle, droit in (donnees or []) if _donnee_permise(droit, c, droits)]
        if b["code"] == "actions":  # actions cochées dans l'ordre choisi, puis les autres
            choix.sort(key=lambda x: rang.get(x["code"], len(rang)))
        lignes.append({**b, "titre": titre, "choix": choix, "rangeable": b["code"] == "actions"})
    return lignes


def disposition(user, droits):
    """Accueil : les sections à afficher dans l'ordre. Une section est soit un bloc en pleine largeur
    {"largeur": code}, soit une grille {"gauche": [codes], "droite": [codes]} faite des blocs consécutifs en colonne."""
    sections, donnees, libelles = [], {}, {}
    for b in reglages(user)["blocs"]:
        if not b["visible"] or not _visible_pour(b["code"], droits):
            continue
        donnees[b["code"]] = list(b["donnees"] or [])
        libelles[b["code"]] = b.get("libelles") or {}
        if b["place"] == LARGEUR:
            sections.append({"largeur": b["code"]})
            continue
        if not sections or "largeur" in sections[-1]:
            sections.append({GAUCHE: [], DROITE: []})
        sections[-1][b["place"]].append(b["code"])
    for s in sections:
        if "largeur" not in s and not (s[GAUCHE] and s[DROITE]):
            s["seule"] = s[GAUCHE] + s[DROITE]  # une seule colonne remplie : elle prend toute la largeur
    return {"sections": sections, "donnees": donnees, "libelles": libelles}


def actions_rapides(codes, libelles, droits):
    """Boutons du bloc « Actions rapides » : dans l'ordre choisi, avec le libellé choisi, seulement ceux permis."""
    from django.urls import NoReverseMatch, reverse

    catalogue = {c: (libelle, droit) for c, libelle, droit in ACTIONS}
    boutons = []
    for c in codes or []:
        if c not in catalogue or not _donnee_permise(catalogue[c][1], c, droits):
            continue
        try:
            url = reverse(LIENS_ACTIONS[c])
        except NoReverseMatch:
            continue
        boutons.append({"code": c, "libelle": (libelles or {}).get(c) or catalogue[c][0], "url": url})
    return boutons
