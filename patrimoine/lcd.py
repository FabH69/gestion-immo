"""Location courte durée : lecture des exports Airbnb / Booking et tableau de suivi mensuel."""
import calendar
import csv
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .models import ReservationLCD

PLATEFORMES = ReservationLCD.Plateforme

# Noms de colonnes possibles (exports français et anglais, anciens et récents), sans accents ni majuscules.
COLONNES = {
    "reference": ["numero de reference", "reference number", "code de confirmation", "confirmation code", "reference", "n de reservation"],
    "arrivee": ["date d'arrivee", "check-in", "date de debut", "start date", "arrivee"],
    "depart": ["date de depart", "checkout", "check-out", "date de fin", "end date", "depart"],
    "voyageur": ["guest name", "nom du client", "voyageur", "guest", "client", "booker name"],
    "statut": ["statut de la reservation", "reservation status", "status", "statut"],
    "brut": ["montant brut reservation", "montant brut", "revenus bruts", "gross earnings", "amount", "original amount", "prix"],
    "commission": ["commission", "frais de service", "service fee", "commission amount"],
    "frais": ["frais de service de paiement", "payments service fee", "payment charge", "frais de paiement rapide", "fast pay fee"],
    "taxe": ["taxe de sejour", "tourism tax", "taxes de sejour", "taxes reversees par airbnb", "occupancy taxes",
             "occupancy taxes remitted by airbnb", "city tax"],
    "versement": ["payout date", "date de versement", "date d'emission", "date"],
    "type": ["type", "type de transaction"],
    "nuits": ["nuits", "nights", "nb nuitees"],
}


def _norme(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texte.replace("’", "'").strip().lower())


def _montant(valeur):
    if valeur in (None, "", "-"):
        return Decimal(0)
    if isinstance(valeur, (int, float, Decimal)):
        return abs(Decimal(str(valeur))).quantize(Decimal("0.01"))
    texte = re.sub(r"[^\d,.\-]", "", str(valeur))
    if "," in texte and "." in texte:
        texte = texte.replace(".", "").replace(",", ".") if texte.rfind(",") > texte.rfind(".") else texte.replace(",", "")
    else:
        texte = texte.replace(",", ".")
    try:
        return abs(Decimal(texte)).quantize(Decimal("0.01")) if texte not in ("", "-", ".") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _date(valeur, jour_en_premier=True):
    if valeur in (None, "", "-"):
        return None
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    texte = str(valeur).strip()
    formats = ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d %b %Y", "%d %B %Y", "%b %d, %Y"]
    formats += ["%d/%m/%Y", "%m/%d/%Y"] if jour_en_premier else ["%m/%d/%Y", "%d/%m/%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(texte, fmt).date()
        except ValueError:
            continue
    return None


def _ordre_des_dates(lignes, colonnes):
    """Jour d'abord (31/12/2025) ou mois d'abord (12/31/2025, export Airbnb) ? Déduit des valeurs du fichier ;
    None si aucune date ne permet de trancher."""
    for ligne in lignes:
        for c in colonnes:
            m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/\d{4}.*", str(ligne.get(c) or ""))
            if m:
                premier, second = int(m.group(1)), int(m.group(2))
                if premier > 12:
                    return True
                if second > 12:
                    return False
    return None


def lire_tableau(fichier, nom):
    """Lignes (dictionnaires) d'un fichier CSV ou Excel."""
    if nom.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        feuille = load_workbook(fichier, read_only=True, data_only=True).worksheets[0]
        lignes = list(feuille.iter_rows(values_only=True))
        entetes = [str(c or "").strip() for c in lignes[0]]
        return entetes, [dict(zip(entetes, l)) for l in lignes[1:] if any(v not in (None, "") for v in l)]
    contenu = fichier.read()
    texte = contenu.decode("utf-8-sig") if isinstance(contenu, bytes) else contenu
    dialecte = csv.Sniffer().sniff(texte[:4096], delimiters=",;\t")
    lecteur = csv.DictReader(io.StringIO(texte), dialect=dialecte)
    return [h.strip() for h in lecteur.fieldnames], [l for l in lecteur if any((v or "").strip() for v in l.values())]


def detecter_plateforme(entetes):
    normes = {_norme(h) for h in entetes}
    if normes & {"code de confirmation", "confirmation code", "revenus bruts", "gross earnings"}:
        return PLATEFORMES.AIRBNB
    if normes & {"reference number", "numero de reference", "payments service fee", "frais de service de paiement"}:
        return PLATEFORMES.BOOKING
    return None


def _colonnes(entetes, plateforme):
    """Associe chaque champ à la colonne du fichier. Pour Airbnb, « Frais de service » est la commission."""
    normes = {_norme(h): h for h in entetes}
    correspondance = {}
    for champ, alias in COLONNES.items():
        for a in alias:
            if champ == "commission" and plateforme == PLATEFORMES.BOOKING and a == "frais de service":
                continue
            if a in normes and normes[a] not in correspondance.values():
                correspondance[champ] = normes[a]
                break
    return correspondance


def importer(lot, fichier, nom, plateforme=None):
    """Crée ou met à jour les réservations du fichier. Renvoie (plateforme, créées, mises à jour, ignorées)."""
    entetes, lignes = lire_tableau(fichier, nom)
    plateforme = plateforme or detecter_plateforme(entetes)
    if not plateforme:
        raise ValueError("Format non reconnu : exportez le fichier depuis Airbnb (Revenus) ou Booking (Relevés de versement).")
    col = _colonnes(entetes, plateforme)
    manquantes = [c for c in ("reference", "arrivee", "brut") if c not in col]
    if manquantes:
        raise ValueError(f"Colonnes introuvables : {', '.join(manquantes)}.")
    jour_en_premier = _ordre_des_dates(lignes, [col[c] for c in ("arrivee", "depart", "versement") if c in col])
    if jour_en_premier is None:
        jour_en_premier = any(_norme(h) in ("date de debut", "date d'arrivee", "numero de reference") for h in entetes)

    creees = mises_a_jour = ignorees = 0
    for ligne in lignes:
        get = lambda champ: ligne.get(col[champ]) if champ in col else None
        type_ligne = _norme(get("type"))
        if plateforme == PLATEFORMES.AIRBNB and type_ligne and not type_ligne.startswith(("reservation", "réservation")):
            ignorees += 1  # versements, ajustements, frais de résolution : pas des séjours
            continue
        reference = str(get("reference") or "").strip()
        arrivee = _date(get("arrivee"), jour_en_premier)
        depart = _date(get("depart"), jour_en_premier)
        if not depart and arrivee and get("nuits"):
            depart = arrivee + timedelta(days=int(_montant(get("nuits"))))
        if not reference or not arrivee or not depart:
            ignorees += 1
            continue
        statut = _norme(get("statut"))
        valeurs = {
            "lot": lot, "voyageur": str(get("voyageur") or "").strip()[:150], "arrivee": arrivee, "depart": depart,
            "montant_brut": _montant(get("brut")), "commission": _montant(get("commission")),
            "frais_service": _montant(get("frais")), "taxe_sejour": _montant(get("taxe")),
            "date_versement": _date(get("versement"), jour_en_premier),
            "annulee": "cancel" in statut or "annul" in statut,
        }
        if plateforme == PLATEFORMES.AIRBNB and valeurs["commission"] >= valeurs["taxe_sejour"]:
            # Comme le tableau Excel de suivi : brut − commission − taxe de séjour = net versé.
            # Airbnb ne versant que « brut − frais de service », la taxe est retirée des frais de service.
            valeurs["commission"] -= valeurs["taxe_sejour"]
        _, cree = ReservationLCD.objects.update_or_create(plateforme=plateforme, reference=reference, defaults=valeurs)
        creees += cree
        mises_a_jour += not cree
    return plateforme, creees, mises_a_jour, ignorees


def _repartir_par_mois(reservation):
    """Nuits de la réservation par mois (un séjour à cheval sur deux mois est réparti au prorata des nuits)."""
    nuits = {}
    jour = reservation.arrivee
    while jour < reservation.depart:
        nuits[(jour.year, jour.month)] = nuits.get((jour.year, jour.month), 0) + 1
        jour += timedelta(days=1)
    return nuits


def suivi_annuel(lot, annee):
    """Tableau mensuel : revenus par plateforme, remplissage, frais de gestion, charges, prêt et résultat."""
    zero = Decimal(0)
    mois = [{"mois": m, "jours": calendar.monthrange(annee, m)[1],
             "plateformes": {p: {"brut": zero, "commission": zero, "frais": zero, "taxe": zero, "nuits": 0, "sejours": 0} for p in PLATEFORMES.values},
             } for m in range(1, 13)]
    reservations = ReservationLCD.objects.filter(lot=lot, annulee=False, depart__gt=date(annee, 1, 1), arrivee__lt=date(annee + 1, 1, 1))
    for r in reservations:
        total_nuits = r.nuits or 1
        for (a, m), n in _repartir_par_mois(r).items():
            if a != annee:
                continue
            ligne = mois[m - 1]["plateformes"][r.plateforme]
            part = Decimal(n) / total_nuits
            ligne["brut"] += r.montant_brut * part
            ligne["commission"] += r.commission * part
            ligne["frais"] += r.frais_service * part
            ligne["taxe"] += r.taxe_sejour * part
            ligne["nuits"] += n
        if r.arrivee.year == annee:
            mois[r.arrivee.month - 1]["plateformes"][r.plateforme]["sejours"] += 1

    parametres = getattr(lot, "parametres_lcd", None)
    charges = list(lot.charges_lcd.all())
    prets = list(lot.bien.prets.all())
    for m in mois:
        premier, dernier = date(annee, m["mois"], 1), date(annee, m["mois"], m["jours"])
        p = m["plateformes"].values()
        m["brut"] = sum((x["brut"] for x in p), zero)
        m["commission"] = sum((x["commission"] for x in p), zero)
        m["frais"] = sum((x["frais"] for x in p), zero)
        m["taxe"] = sum((x["taxe"] for x in p), zero)
        m["nuits"] = sum(x["nuits"] for x in p)
        m["sejours"] = sum(x["sejours"] for x in p)
        m["net"] = m["brut"] - m["commission"] - m["frais"] - m["taxe"]
        m["remplissage"] = Decimal(m["nuits"]) / m["jours"] * 100
        m["prix_nuit"] = m["brut"] / m["nuits"] if m["nuits"] else zero
        if parametres:
            m["gestion"] = (m["net"] * parametres.commission_gestion / 100 + m["sejours"] * (parametres.menage_par_sejour + parametres.consommables_par_sejour))
        else:
            m["gestion"] = zero
        m["charges_detail"] = [(c.libelle, c.montant_mensuel) for c in charges if c.active_en(premier, dernier)]
        m["charges"] = sum((v for _, v in m["charges_detail"]), zero)
        m["pret"] = sum((l["mensualite"] + l["assurance"] for p_ in prets for l in p_.echeancier() if premier <= l["date"] <= dernier), zero)
        m["resultat"] = m["net"] - m["gestion"] - m["charges"]
        m["cash_flow"] = m["resultat"] - m["pret"]
    return mois


def totaliser(mois, jusqua=12):
    """Cumul des mois 1 à `jusqua`."""
    retenus = [m for m in mois if m["mois"] <= jusqua]
    total = {k: sum((m[k] for m in retenus), Decimal(0)) for k in ("brut", "commission", "frais", "taxe", "net", "gestion", "charges", "pret", "resultat", "cash_flow")}
    total["nuits"] = sum(m["nuits"] for m in retenus)
    total["sejours"] = sum(m["sejours"] for m in retenus)
    total["jours"] = sum(m["jours"] for m in retenus)
    total["remplissage"] = Decimal(total["nuits"]) / total["jours"] * 100 if total["jours"] else Decimal(0)
    total["prix_nuit"] = total["brut"] / total["nuits"] if total["nuits"] else Decimal(0)
    total["plateformes"] = {p: {k: sum((m["plateformes"][p][k] for m in retenus), Decimal(0)) for k in ("brut", "nuits", "sejours")} for p in PLATEFORMES.values}
    return total
