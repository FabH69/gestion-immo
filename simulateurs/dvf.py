"""Prix du marché autour d'une adresse : ventes réelles DVF (« demandes de valeurs foncières »), données ouvertes de l'État.

1. L'adresse est géolocalisée par le service de géocodage public (Géoplateforme, ex-API Adresse) : coordonnées et
   code INSEE de la commune (ou de l'arrondissement à Paris, Lyon, Marseille).
2. Les ventes de la commune sont lues dans les fichiers « geo-dvf » de data.gouv.fr (un fichier CSV par commune et par
   année), gardés en cache 30 jours.
3. On garde les ventes d'un seul logement (appartement ou maison) des 3 dernières années, dans un rayon de 500 m
   (élargi à 1 km puis 2 km s'il y en a moins de 10), et on calcule le prix au m² médian et la fourchette (quartiles).

Limites : les ventes paraissent avec environ 6 mois de retard ; l'Alsace-Moselle et Mayotte ne sont pas couvertes ;
seules les ventes de la commune de l'adresse sont lues (pas celles de la commune voisine, même à 200 m).
"""
import csv
import hashlib
import io
import json
import math
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from django.core.cache import cache

GEOCODEURS = ["https://data.geopf.fr/geocodage/search", "https://api-adresse.data.gouv.fr/search/"]
URL_DVF = "https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dep}/{insee}.csv"
AGENT = "gestion-immo (simulateur d'investissement)"
DELAI = 12  # secondes
DUREE_CACHE = 30 * 24 * 3600
RAYONS = [500, 1000, 2000]
MINIMUM_VENTES = 10
ANNEES = 3
NON_COUVERTS = ("57", "67", "68", "976")


class DvfIndisponible(Exception):
    """Message lisible pour la personne : adresse introuvable, service injoignable…"""


def _lire(url):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=DELAI) as r:  # noqa: S310 (adresses fixes ci-dessus)
        return r.read()


def geocoder(adresse):
    """{label, insee, lat, lon} de la meilleure réponse, ou DvfIndisponible."""
    cle = "geo:" + hashlib.sha1(adresse.strip().lower().encode()).hexdigest()
    if (en_cache := cache.get(cle)) is not None:
        return en_cache
    erreur = None
    for base in GEOCODEURS:
        try:
            donnees = json.loads(_lire(f"{base}?{urllib.parse.urlencode({'q': adresse, 'limit': 1})}"))
        except Exception as e:  # noqa: BLE001 : on essaie le service suivant
            erreur = e
            continue
        features = donnees.get("features") or []
        if not features:
            raise DvfIndisponible("Adresse introuvable : précisez le numéro, la rue et le code postal.")
        f = features[0]
        lon, lat = f["geometry"]["coordinates"]
        pr = f.get("properties", {})
        res = {"label": pr.get("label", adresse), "insee": pr.get("citycode", ""), "ville": pr.get("city", ""),
               "lat": lat, "lon": lon, "precision": pr.get("type", "")}
        cache.set(cle, res, DUREE_CACHE)
        return res
    raise DvfIndisponible("Le service de géolocalisation de l'État ne répond pas pour le moment.") from erreur


def _departement(insee):
    return insee[:3] if insee.startswith("97") else insee[:2]


def ventes_commune(insee, annee):
    """Ventes d'un logement seul dans la commune pour une année : liste de dicts (vide si le fichier n'existe pas)."""
    cle = f"dvf:{insee}:{annee}"
    if (en_cache := cache.get(cle)) is not None:
        return en_cache
    try:
        brut = _lire(URL_DVF.format(annee=annee, dep=_departement(insee), insee=insee))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            cache.set(cle, [], DUREE_CACHE)
            return []
        raise
    ventes = analyser_csv(brut.decode("utf-8"))
    cache.set(cle, ventes, DUREE_CACHE)
    return ventes


def analyser_csv(texte):
    """Une ligne DVF par lot et par parcelle : on regroupe par mutation et on garde les ventes d'un seul logement."""
    mutations = {}
    for ligne in csv.DictReader(io.StringIO(texte)):
        if ligne.get("nature_mutation") != "Vente":
            continue
        m = mutations.setdefault(ligne["id_mutation"], {"logements": set(), "ligne": None})
        if ligne.get("type_local") in ("Appartement", "Maison"):
            m["logements"].add((ligne["type_local"], ligne.get("surface_reelle_bati"), ligne.get("lot1_numero")))
            m["ligne"] = ligne
    ventes = []
    for m in mutations.values():
        l = m["ligne"]
        if l is None or len(m["logements"]) != 1:
            continue
        try:
            valeur, surface = float(l["valeur_fonciere"]), float(l["surface_reelle_bati"])
            lat, lon = float(l["latitude"]), float(l["longitude"])
        except (TypeError, ValueError):
            continue
        if valeur <= 0 or surface < 9:
            continue
        adresse = " ".join(filter(None, [l.get("adresse_numero"), l.get("adresse_suffixe"), l.get("adresse_nom_voie")]))
        ventes.append({"date": l["date_mutation"], "type": l["type_local"], "surface": surface, "prix": valeur,
                       "pieces": l.get("nombre_pieces_principales") or "", "lat": lat, "lon": lon, "adresse": adresse.title()})
    return ventes


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def synthese(ventes, lat, lon, type_local="Appartement", aujourdhui=None):
    """Prix au m² médian, fourchette et ventes comparables autour du point, en élargissant le rayon si besoin."""
    aujourdhui = aujourdhui or date.today()
    depuis = (aujourdhui - timedelta(days=365 * ANNEES)).isoformat()
    candidates = []
    for v in ventes:
        if v["type"] == type_local and v["date"] >= depuis:
            candidates.append({**v, "distance": distance_m(lat, lon, v["lat"], v["lon"]), "prix_m2": v["prix"] / v["surface"]})
    for rayon in RAYONS:
        retenues = [v for v in candidates if v["distance"] <= rayon]
        if len(retenues) >= MINIMUM_VENTES:
            break
    if not retenues:
        return {"nombre": 0, "rayon": rayon, "type": type_local}
    prix = sorted(v["prix_m2"] for v in retenues)
    if len(prix) >= 4:
        q1, _, q3 = statistics.quantiles(prix, n=4)
        ecart = q3 - q1
        prix = [x for x in prix if q1 - 1.5 * ecart <= x <= q3 + 1.5 * ecart]  # sans les ventes aberrantes
        retenues = [v for v in retenues if q1 - 1.5 * ecart <= v["prix_m2"] <= q3 + 1.5 * ecart]
        q1, _, q3 = statistics.quantiles(prix, n=4)
    else:
        q1, q3 = prix[0], prix[-1]
    mini, maxi = prix[0], prix[-1]
    pas = max(100, math.ceil((maxi - mini) / 12 / 100) * 100) if maxi > mini else 100
    debut = math.floor(mini / pas) * pas
    histo = [0] * (int((maxi - debut) // pas) + 1)
    for x in prix:
        histo[int((x - debut) // pas)] += 1
    comparables = sorted(retenues, key=lambda v: v["date"], reverse=True)[:12]
    return {
        "nombre": len(retenues), "rayon": rayon, "type": type_local, "depuis": depuis,
        "mediane": round(statistics.median(prix)), "q1": round(q1), "q3": round(q3),
        "histogramme": {"debut": debut, "pas": pas, "effectifs": histo},
        "ventes": [{"date": v["date"], "type": v["type"], "pieces": v["pieces"], "surface": round(v["surface"]),
                    "prix": round(v["prix"]), "prix_m2": round(v["prix_m2"]), "distance": round(v["distance"] / 10) * 10,
                    "adresse": v["adresse"]} for v in comparables],
    }


def prix_du_marche(adresse, type_local="Appartement", aujourdhui=None):
    """Tout le parcours : adresse → synthèse. Lève DvfIndisponible avec un message clair en cas de souci."""
    if not (adresse or "").strip():
        raise DvfIndisponible("Saisissez l'adresse du bien pour voir les ventes autour.")
    lieu = geocoder(adresse)
    insee = lieu["insee"]
    if not insee:
        raise DvfIndisponible("Adresse trop vague : précisez la commune.")
    if _departement(insee) in NON_COUVERTS:
        raise DvfIndisponible("Les ventes DVF ne couvrent pas l'Alsace-Moselle ni Mayotte.")
    aujourdhui = aujourdhui or date.today()
    ventes = []
    try:
        for annee in range(aujourdhui.year - ANNEES, aujourdhui.year + 1):
            ventes += ventes_commune(insee, annee)
    except Exception as e:  # noqa: BLE001
        raise DvfIndisponible("Le site des données publiques (data.gouv.fr) ne répond pas pour le moment.") from e
    res = synthese(ventes, lieu["lat"], lieu["lon"], type_local, aujourdhui)
    res["lieu"] = lieu
    return res
