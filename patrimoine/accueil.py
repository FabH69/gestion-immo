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
ACTIONS = [
    ("encaisser", "Encaisser un loyer", "loyers"),
    ("quittance", "Créer une quittance", "loyers"),
    ("relancer", "Relancer un impayé", "loyers"),
    ("annonce", "Créer une annonce", "relocation"),
]
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
        return bool(d.get("loyers") or d.get("relocation") == "modification")
    return True


def _donnee_permise(droit, code_donnee, droits):
    d = droits or {}
    if code_donnee == "annonce":
        return d.get("relocation") == "modification"
    return droit is None or bool(d.get(droit))


def reglages_d_origine():
    return {"blocs": [{"code": code, "visible": True, "place": place,
                       "donnees": [c for c, *_ in donnees] if donnees else None} for code, _, place, donnees in BLOCS]}


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
        choix = None
        if donnees:
            permis = [c for c, *_ in donnees]
            choix = [c for c in (b.get("donnees") if isinstance(b.get("donnees"), list) else permis) if c in permis]
        blocs.append({"code": code, "visible": bool(b.get("visible", True)),
                      "place": b.get("place") if b.get("place") in dict(PLACES) else place, "donnees": choix})
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
             "donnees": None}
        if donnees:
            affichees = set(post.getlist(f"donnees_{code}"))
            # une donnée non proposée (pas de droit) garde son réglage
            proposees = set(post.getlist(f"proposees_{code}"))
            anciennes = set(actuels[code]["donnees"] or [])
            b["donnees"] = [c for c, *_ in donnees if (c in affichees if c in proposees else c in anciennes)]
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
        choix = [{"code": c, "libelle": libelle, "coche": c in (b["donnees"] or [])}
                 for c, libelle, droit in (donnees or []) if _donnee_permise(droit, c, droits)]
        lignes.append({**b, "titre": titre, "choix": choix})
    return lignes


def disposition(user, droits):
    """Accueil : les sections à afficher dans l'ordre. Une section est soit un bloc en pleine largeur
    {"largeur": code}, soit une grille {"gauche": [codes], "droite": [codes]} faite des blocs consécutifs en colonne."""
    sections, donnees = [], {}
    for b in reglages(user)["blocs"]:
        if not b["visible"] or not _visible_pour(b["code"], droits):
            continue
        donnees[b["code"]] = set(b["donnees"] or [])
        if b["place"] == LARGEUR:
            sections.append({"largeur": b["code"]})
            continue
        if not sections or "largeur" in sections[-1]:
            sections.append({GAUCHE: [], DROITE: []})
        sections[-1][b["place"]].append(b["code"])
    for s in sections:
        if "largeur" not in s and not (s[GAUCHE] and s[DROITE]):
            s["seule"] = s[GAUCHE] + s[DROITE]  # une seule colonne remplie : elle prend toute la largeur
    return {"sections": sections, "donnees": donnees}
