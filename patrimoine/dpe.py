"""Diagnostic de performance énergétique (DPE) : règles de validité, calendrier de la loi Climat et vérification d'un
numéro dans la base officielle de l'ADEME.

Sert à deux endroits :
- la fiche du lot (gestion locative) : date de fin de validité, rappel avant l'échéance, interdiction de louer selon la classe ;
- le simulateur d'investissement : vérifier le DPE fourni par le vendeur (le numéro existe-t-il, la classe, la surface et
  l'adresse correspondent-elles à l'annonce, est-il encore valable ?).

Base ADEME : tout DPE réalisé depuis le 1er juillet 2021 y est enregistré par le diagnostiqueur (c'est obligatoire) et publié
en données ouvertes chaque semaine. Un DPE plus ancien n'est plus valable depuis le 1er janvier 2025 : un DPE présenté
aujourd'hui doit donc être dans la base.
"""
import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from django.core.cache import cache

CLASSES = "ABCDEFG"
URL_ADEME = "https://data.ademe.fr/data-fair/api/v1/datasets/{jeu}/lines"
# jeux de données ADEME, du plus probable au moins probable (logements existants, puis neufs ; noms actuels puis anciens)
JEUX = ["dpe03existant", "dpe02neuf", "dpe-v2-logements-existants", "dpe-v2-logements-neufs"]
URL_OBSERVATOIRE = "https://observatoire-dpe-audit.ademe.fr/afficher-dpe/{numero}"
AGENT = "gestion-immo (verification DPE)"
DELAI = 12  # secondes
DUREE_CACHE = 24 * 3600
PREVENIR_JOURS = 180  # rappel 6 mois avant la fin de validité

# Loi Climat et résilience (art. L. 173-1-1 du code de la construction) : à partir de ces dates, un logement de cette
# classe n'est plus « décent » et ne peut plus être loué (nouveau bail, renouvellement ou reconduction tacite).
# SEUL ENDROIT À MODIFIER si la loi change (un amendement supprimant ces interdictions est en discussion fin 2026) :
# retirer une classe du dictionnaire supprime l'alerte correspondante partout dans l'appli.
INTERDICTIONS = {"G": date(2025, 1, 1), "F": date(2028, 1, 1), "E": date(2034, 1, 1)}
INTERDICTIONS_OUTRE_MER = {"G": date(2028, 1, 1), "F": date(2031, 1, 1)}
GEL_LOYERS = date(2022, 8, 24)  # loyers des F et G gelés (ni révision, ni hausse à la relocation) en métropole
GEL_LOYERS_OUTRE_MER = date(2024, 7, 1)
CALCUL_PETITES_SURFACES = date(2024, 7, 1)  # nouveaux seuils pour les logements de moins de 40 m²
# baisses du coefficient de conversion de l'électricité (2,3 → 1,9 au 1/1/2026, puis 1,7 annoncé au 1/1/2027) : un logement
# chauffé à l'électricité diagnostiqué avant peut gagner une classe (attestation ADEME gratuite, même fin de validité)
COEF_ELECTRICITE = [date(2026, 1, 1), date(2027, 1, 1)]


class DpeIndisponible(Exception):
    """Message lisible pour la personne : numéro mal saisi, base de l'ADEME injoignable…"""


# ---------------------------------------------------------------------------------------------------------------------
# Règles
# ---------------------------------------------------------------------------------------------------------------------

def _plus_ans(d, ans):
    try:
        return d.replace(year=d.year + ans)
    except ValueError:  # 29 février
        return d.replace(year=d.year + ans, day=28)


def fin_validite(date_etablissement):
    """Fin de validité légale d'un DPE selon sa date de réalisation (10 ans, écourtés pour les anciens modèles)."""
    if not date_etablissement:
        return None
    fin = _plus_ans(date_etablissement, 10) - timedelta(days=1)
    if date_etablissement < date(2018, 1, 1):
        return min(fin, date(2022, 12, 31))
    if date_etablissement < date(2021, 7, 1):
        return min(fin, date(2024, 12, 31))
    return fin


def outre_mer(code_postal):
    return str(code_postal or "").strip().startswith("97")


def interdiction(classe, code_postal=""):
    """Date à partir de laquelle un logement de cette classe ne peut plus être loué (None s'il n'est pas concerné)."""
    table = INTERDICTIONS_OUTRE_MER if outre_mer(code_postal) else INTERDICTIONS
    return table.get((classe or "").upper())


def loi_climat(classe, code_postal="", aujourdhui=None):
    """Conséquences de la classe pour la location : liste de {niveau, texte} (« rouge », « orange » ou « info »)."""
    aujourdhui = aujourdhui or date.today()
    classe = (classe or "").upper()
    res = []
    quand = interdiction(classe, code_postal)
    if quand and quand <= aujourdhui:
        res.append({"niveau": "rouge", "texte": f"Classe {classe} : location interdite depuis le {quand:%d/%m/%Y} selon la loi en vigueur "
                    "(logement non décent). "
                    "Pas de nouveau bail, ni de renouvellement : des travaux sont nécessaires pour louer."})
    elif quand:
        ans = (quand - aujourdhui).days / 365.25
        res.append({"niveau": "orange" if ans <= 3 else "info",
                    "texte": f"Classe {classe} : location interdite à partir du {quand:%d/%m/%Y} selon la loi en vigueur. "
                             "Prévoir les travaux pour gagner au moins une classe avant cette date."})
    gel = GEL_LOYERS_OUTRE_MER if outre_mer(code_postal) else GEL_LOYERS
    if classe in ("F", "G") and aujourdhui >= gel:
        res.append({"niveau": "orange", "texte": f"Classe {classe} : loyer gelé, ni révision annuelle ni hausse au changement de locataire."})
    return res


# ---------------------------------------------------------------------------------------------------------------------
# Base ADEME
# ---------------------------------------------------------------------------------------------------------------------

def normaliser_numero(numero):
    return re.sub(r"[\s.\-]", "", str(numero or "")).upper()


def numero_valide(numero):
    """13 caractères : année (2), département (2), type (1 lettre), numéro (7 chiffres), clé (1 lettre). Ex. 2369E0123456X"""
    return bool(re.fullmatch(r"\d{2}[0-9AB]{2}[A-Z]\d{7}[A-Z]", normaliser_numero(numero)))


def lien_observatoire(numero):
    return URL_OBSERVATOIRE.format(numero=urllib.parse.quote(normaliser_numero(numero)))


def _cle(nom):
    """« Date_fin_validité_DPE » ou « date_fin_validite_dpe » → « date_fin_validite_dpe » (les noms de colonnes ont changé)."""
    nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", nom.lower())).strip("_")


CHAMPS = {
    "numero": ["numero_dpe", "n_dpe", "ndpe"],
    "classe": ["etiquette_dpe"],
    "ges": ["etiquette_ges"],
    "date_etablissement": ["date_etablissement_dpe"],
    "date_fin_validite": ["date_fin_validite_dpe"],
    "date_visite": ["date_visite_diagnostiqueur"],
    "surface": ["surface_habitable_logement", "surface_habitable_immeuble"],
    "adresse": ["adresse_ban", "adresse_brut", "adresse_brute"],
    "code_postal": ["code_postal_ban", "code_postal_brut"],
    "commune": ["nom_commune_ban", "nom_commune_brut"],
    "type_batiment": ["type_batiment"],
    "conso_m2": ["conso_5_usages_par_m2_ep", "conso_5_usages_m2_ep"],
    "ges_m2": ["emission_ges_5_usages_par_m2", "emission_ges_5_usages_m2"],
    "annee_construction": ["annee_construction", "periode_construction"],
    "remplace": ["numero_dpe_remplace", "n_dpe_remplace"],
    "remplacant": ["numero_dpe_remplacant", "n_dpe_remplacant"],
    "etage": ["numero_etage_appartement", "n_etage_appartement"],
    "chauffage": ["type_energie_principale_chauffage", "type_energie_n_1"],
}


def lire_ligne(ligne):
    """Une ligne brute de l'ADEME → dict aux noms stables."""
    brut = {_cle(k): v for k, v in ligne.items()}
    res = {}
    for nom, candidats in CHAMPS.items():
        res[nom] = next((brut[c] for c in candidats if brut.get(c) not in (None, "")), "")
    for nom in ("classe", "ges"):
        res[nom] = str(res[nom]).strip().upper()[:1]
    for nom in ("date_etablissement", "date_fin_validite", "date_visite"):
        res[nom] = str(res[nom])[:10]
    try:
        res["surface"] = round(float(res["surface"]), 1) if res["surface"] != "" else None
    except (TypeError, ValueError):
        res["surface"] = None
    for nom in ("conso_m2", "ges_m2"):
        try:
            res[nom] = round(float(res[nom])) if res[nom] != "" else None
        except (TypeError, ValueError):
            res[nom] = None
    res["numero"] = normaliser_numero(res["numero"])
    return res


def _lire(url):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=DELAI) as r:  # noqa: S310 (adresse fixe ci-dessus)
        return json.loads(r.read())


def chercher(numero):
    """Fiche du DPE dans la base de l'ADEME ({…} avec les champs de CHAMPS), None s'il n'y est pas.

    Lève DpeIndisponible si le site de l'ADEME ne répond pas."""
    numero = normaliser_numero(numero)
    cle = "dpe:" + hashlib.sha1(numero.encode()).hexdigest()
    if (en_cache := cache.get(cle)) is not None:
        return en_cache or None
    joint, erreur = False, None
    for jeu in JEUX:
        url = URL_ADEME.format(jeu=jeu) + "?" + urllib.parse.urlencode({"q": numero, "size": 10})
        try:
            donnees = _lire(url)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):  # jeu renommé ou supprimé : on essaie le suivant
                joint = True
                continue
            erreur = e
            continue
        except Exception as e:  # noqa: BLE001
            erreur = e
            continue
        joint = True
        for ligne in donnees.get("results") or []:
            fiche = lire_ligne(ligne)
            if fiche["numero"] == numero:
                fiche["jeu"] = jeu
                cache.set(cle, fiche, DUREE_CACHE)
                return fiche
    if not joint:
        raise DpeIndisponible("La base des DPE de l'ADEME (data.ademe.fr) ne répond pas depuis le serveur. "
                              "Vérifiez le DPE vous-même avec le lien ci-dessous.") from erreur
    cache.set(cle, {}, 3600)  # introuvable : on réessaie dans une heure (le DPE vient peut-être d'être déposé)
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Vérification d'un DPE fourni pour un achat
# ---------------------------------------------------------------------------------------------------------------------

def _date(texte):
    try:
        return date.fromisoformat(str(texte)[:10])
    except ValueError:
        return None


def _mots(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z0-9]+", texte)) - {"rue", "de", "du", "des", "la", "le", "les", "l", "d", "avenue", "av",
                                                    "boulevard", "bd", "chemin", "place", "impasse", "allee", "route", "quai"}


def _n(x):
    return f"{x:g}".replace(".", ",")


def code_postal_de(adresse):
    m = re.search(r"\b(\d{5})\b", str(adresse or ""))
    return m.group(1) if m else ""


def comparer(fiche, *, surface=None, adresse="", classe_annoncee="", aujourdhui=None):
    """Alertes de cohérence entre la fiche ADEME et le projet : liste de {niveau, texte}, la plus grave d'abord."""
    aujourdhui = aujourdhui or date.today()
    alertes = []
    fin = _date(fiche.get("date_fin_validite")) or fin_validite(_date(fiche.get("date_etablissement")))
    if fin and fin < aujourdhui:
        alertes.append({"niveau": "rouge", "texte": f"DPE expiré depuis le {fin:%d/%m/%Y} : le vendeur doit en fournir un nouveau."})
    elif fin and fin < aujourdhui + timedelta(days=PREVENIR_JOURS):
        alertes.append({"niveau": "orange", "texte": f"DPE valable jusqu'au {fin:%d/%m/%Y} seulement : il doit l'être encore à la "
                        "signature de l'acte, sinon il faudra en refaire un."})
    if fiche.get("remplacant"):
        alertes.append({"niveau": "orange", "texte": f"Ce DPE a été remplacé par un DPE plus récent (n° {fiche['remplacant']}) : "
                        "demandez le nouveau au vendeur."})
    classe_annoncee = (classe_annoncee or "").strip().upper()[:1]
    if classe_annoncee and fiche.get("classe") and classe_annoncee != fiche["classe"]:
        alertes.append({"niveau": "rouge", "texte": f"Classe annoncée {classe_annoncee}, mais le DPE enregistré indique {fiche['classe']}."})
    try:
        surface = float(surface or 0)
    except (TypeError, ValueError):
        surface = 0
    if surface and fiche.get("surface"):
        ecart = fiche["surface"] / surface - 1
        if abs(ecart) > 0.10:
            alertes.append({"niveau": "orange", "texte": f"Surface du DPE {_n(fiche['surface'])} m², contre {_n(surface)} m² dans le projet ({round(ecart * 100):+d} %). "
                            "S'agit-il d'un DPE de l'immeuble entier ou d'un autre lot ?"})
    cp = code_postal_de(adresse)
    if cp and fiche.get("code_postal") and cp != str(fiche["code_postal"]):
        alertes.append({"niveau": "rouge", "texte": f"Le DPE porte sur un logement à {fiche['code_postal']} {fiche.get('commune', '')}, "
                        f"pas au {cp} de l'adresse du projet.".replace("  ", " ")})
    elif adresse and fiche.get("adresse"):
        # mots de la rue seulement (sans numéros, code postal ni commune) : au moins un en commun
        hors_rue = _mots(fiche.get("commune")) | {m for m in _mots(adresse) | _mots(fiche["adresse"]) if m.isdigit()}
        rue_projet = _mots(adresse) - hors_rue
        if rue_projet and not rue_projet & (_mots(fiche["adresse"]) - hors_rue):
            alertes.append({"niveau": "orange", "texte": f"L'adresse du DPE (« {fiche['adresse']} ») ne ressemble pas à celle du projet : "
                            "vérifiez qu'il s'agit bien du même logement."})
    alertes += loi_climat(fiche.get("classe"), fiche.get("code_postal"), aujourdhui)
    d = _date(fiche.get("date_etablissement"))
    if d and d < CALCUL_PETITES_SURFACES and fiche.get("surface") and fiche["surface"] < 40 and fiche.get("classe") in ("E", "F", "G"):
        alertes.append({"niveau": "info", "texte": "Logement de moins de 40 m² diagnostiqué avant le 1er juillet 2024 : le calcul a changé "
                        "depuis, la classe peut être meilleure. Une attestation gratuite s'obtient sur l'observatoire de l'ADEME."})
    assoupli = max((c for c in COEF_ELECTRICITE if c <= aujourdhui), default=None)
    if d and assoupli and d < assoupli and fiche.get("classe") in ("D", "E", "F", "G") and "lectri" in str(fiche.get("chauffage", "")).lower():
        alertes.append({"niveau": "info", "texte": f"Chauffage électrique et DPE fait avant le {assoupli:%d/%m/%Y} : le calcul de "
                        "l'électricité a été assoupli depuis, la classe peut être meilleure. Une attestation gratuite s'obtient sur "
                        "l'observatoire de l'ADEME (la date de fin de validité ne change pas)."})
    ordre = {"rouge": 0, "orange": 1, "info": 2}
    return sorted(alertes, key=lambda a: ordre[a["niveau"]])


def verifier(numero, *, surface=None, adresse="", classe_annoncee="", aujourdhui=None):
    """Tout le parcours pour la page : {trouve, numero, lien, fiche, fin_validite, alertes, statut}.

    Lève DpeIndisponible (message clair) si le numéro est mal saisi ou si l'ADEME ne répond pas."""
    numero = normaliser_numero(numero)
    if not numero:
        raise DpeIndisponible("Saisissez le numéro du DPE (13 caractères, en haut à droite de la première page du DPE).")
    if not numero_valide(numero):
        raise DpeIndisponible(f"« {numero} » n'est pas un numéro de DPE : il fait 13 caractères, par exemple 2369E0123456X "
                              "(en haut à droite de la première page du DPE).")
    aujourdhui = aujourdhui or date.today()
    fiche = chercher(numero)
    res = {"numero": numero, "lien": lien_observatoire(numero), "trouve": bool(fiche)}
    if not fiche:
        res["statut"] = "rouge"
        res["alertes"] = [{"niveau": "rouge", "texte": "Ce numéro n'existe pas dans la base officielle de l'ADEME. Tout DPE réalisé "
                           "depuis juillet 2021 y est enregistré (publication chaque semaine) : vérifiez la saisie, ou demandez au "
                           "vendeur un DPE valable. Un DPE déposé il y a moins de 15 jours peut ne pas encore y figurer."}]
        return res
    fin = _date(fiche.get("date_fin_validite")) or fin_validite(_date(fiche.get("date_etablissement")))
    res["fiche"] = fiche
    res["fin_validite"] = fin.isoformat() if fin else ""
    res["alertes"] = comparer(fiche, surface=surface, adresse=adresse, classe_annoncee=classe_annoncee, aujourdhui=aujourdhui)
    niveaux = {a["niveau"] for a in res["alertes"]}
    res["statut"] = "rouge" if "rouge" in niveaux else "orange" if "orange" in niveaux else "vert"
    return res
