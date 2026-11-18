"""Contrôle de la facture mensuelle de la conciergerie (courte durée) avec les réservations Airbnb, Booking et directes.

La facture liste chaque séjour : « Réservation du 4 au 6 septembre Airbnb », « Honoraire 85.14 € x 20% = 17.03 € »,
« Frais de ménage 28 € », « Consommable 2 € », puis le total TTC de la ligne. L'appli retrouve chaque séjour dans les
réservations du logement, recalcule la base (net versé par la plateforme, moins le ménage), l'honoraire, le ménage et
les consommables, et signale les écarts. La facture est enregistrée en comptabilité, répartie en trois rubriques
(Commission, Ménage, Consommables) dans le mois des séjours.
"""
import re
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.files.base import ContentFile

from .models import PieceComptable, ReservationLCD

MOIS = {"janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12}
CENTIME = Decimal("0.01")
TOLERANCE = Decimal("0.015")

_SEJOUR = re.compile(r"R[ée]servation\s+(?:du\s+)?(\d{1,2})(?:er)?(?:\s+([a-zéèêûôà]+))?\s+(?:au|a|à)\s+(\d{1,2})(?:er)?\s+([a-zéèêûôà]+)", re.I)
_HONORAIRE = re.compile(r"Honoraires?\s+([\d\s]+(?:[.,]\d+)?)\s*€?\s*x\s*([\d.,]+)\s*%\s*=\s*([\d\s]+(?:[.,]\d+)?)", re.I)
_MENAGE = re.compile(r"(?:Frais\s+de\s+)?m[ée]nage\s+([\d\s]+(?:[.,]\d+)?)", re.I)
_CONSO = re.compile(r"Consommables?\s+([\d\s]+(?:[.,]\d+)?)", re.I)
_TTC = re.compile(r"(\d[\d\s]*[.,]\d{2})\s*€\s*TTC", re.I)
_PLATEFORME = re.compile(r"\b(airbnb|booking|direct)\b", re.I)


def _e(montant):
    """12.4 → « 12,40 € » ; signe moins typographique."""
    if montant is None:
        return "?"
    m = Decimal(montant).quantize(CENTIME)
    return ("−" if m < 0 else "") + f"{abs(m):.2f}".replace(".", ",") + " €"


def _t(taux):
    """Decimal('20.00') → « 20 % »."""
    return (f"{Decimal(taux).normalize():f}".replace(".", ",") + " %") if taux is not None else "?"


def _nombre(texte):
    try:
        return Decimal(re.sub(r"\s", "", texte or "").replace(",", ".")).quantize(CENTIME)
    except (InvalidOperation, ValueError):
        return None


def est_une_facture(texte):
    """Vrai si le texte ressemble à une facture de conciergerie (au moins un séjour avec son honoraire)."""
    return bool(texte) and bool(_SEJOUR.search(texte)) and bool(_HONORAIRE.search(texte))


def lire(texte):
    """Numéro, date, totaux et lignes de séjour de la facture."""
    numero = re.search(r"Facture\s+n[°o]\s*([\w-]+)", texte, re.I)
    emise = re.search(r"Date\s+de\s+facturation\s*:?\s*(\d{2})/(\d{2})/(\d{4})", texte, re.I)
    emise = date(int(emise[3]), int(emise[2]), int(emise[1])) if emise else None
    fournisseur = re.search(r"^\s*(Conciergerie[^\n]*?)\s*$", texte, re.I | re.M)
    total = re.search(r"Total\s+TTC[^\d]*(\d[\d\s]*[.,]\d{2})", texte, re.I)
    # coupe le texte en blocs, un par séjour (les pieds de page et totaux qui s'intercalent sont ignorés)
    debuts = [m.start() for m in _SEJOUR.finditer(texte)]
    lignes = []
    for i, debut in enumerate(debuts):
        bloc = texte[debut:debuts[i + 1] if i + 1 < len(debuts) else len(texte)]
        s, h = _SEJOUR.match(bloc), _HONORAIRE.search(bloc)
        if not h:
            continue
        reste = bloc[h.end():]
        mois_depart = MOIS.get(s[4].lower())
        mois_arrivee = MOIS.get((s[2] or s[4]).lower())
        plateforme = _PLATEFORME.search(bloc[:h.start()])
        menage, conso, ttc = _MENAGE.search(reste), _CONSO.search(reste), _TTC.search(reste)
        lignes.append({
            "texte": " ".join(bloc[:h.start()].split()),
            "jour_arrivee": int(s[1]), "mois_arrivee": mois_arrivee, "jour_depart": int(s[3]), "mois_depart": mois_depart,
            "plateforme": plateforme[1].lower() if plateforme else "",
            "base": _nombre(h[1]), "taux": _nombre(h[2]), "honoraire": _nombre(h[3]),
            "menage": _nombre(menage[1]) if menage else Decimal("0.00"), "consommables": _nombre(conso[1]) if conso else Decimal("0.00"),
            "ttc": _nombre(ttc[1]) if ttc else None,
        })
    return {"numero": numero[1] if numero else "", "date": emise, "fournisseur": fournisseur[1].strip() if fournisseur else "Conciergerie",
            "total_ttc": _nombre(total[1]) if total else None, "lignes": lignes}


def _annee(mois, emise):
    """Les séjours facturés précèdent la facture : un mois après celui de la facture est celui de l'année d'avant."""
    if not emise or not mois:
        return emise.year if emise else date.today().year
    return emise.year - 1 if mois > emise.month + 1 else emise.year


def controler(lot, facture):
    """Rapproche chaque ligne de la facture d'une réservation du logement ; renvoie les lignes annotées et les anomalies."""
    parametres = getattr(lot, "parametres_lcd", None)
    menage_prevu = parametres.menage_par_sejour if parametres else None
    conso_prevu = parametres.consommables_par_sejour if parametres else None
    taux_prevu = parametres.commission_gestion if parametres else None
    hors_menage = parametres.commission_hors_menage if parametres else True
    emise = facture["date"]

    lignes, anomalies, trouvees = [], [], set()
    for l in facture["lignes"]:
        annee = _annee(l["mois_arrivee"], emise)
        arrivee = date(annee, l["mois_arrivee"], l["jour_arrivee"]) if l["mois_arrivee"] else None
        annee_dep = annee + (1 if l["mois_depart"] and l["mois_arrivee"] and l["mois_depart"] < l["mois_arrivee"] else 0)
        depart = date(annee_dep, l["mois_depart"], l["jour_depart"]) if l["mois_depart"] else None
        notes = []
        reservation = ReservationLCD.objects.filter(lot=lot, arrivee=arrivee, depart=depart, annulee=False).first() if arrivee and depart else None
        if reservation and reservation.pk in trouvees:
            notes.append(("alerte", "Séjour facturé deux fois sur cette facture."))
        if reservation:
            trouvees.add(reservation.pk)
            if l["plateforme"] and l["plateforme"] != reservation.plateforme:
                notes.append(("alerte", f"Plateforme : {l['plateforme'].capitalize()} sur la facture, {reservation.get_plateforme_display()} dans les réservations."))
            base_attendue = (reservation.net - (menage_prevu or l["menage"] if hors_menage else 0)).quantize(CENTIME)
            if l["base"] is not None and abs(l["base"] - base_attendue) > TOLERANCE:
                notes.append(("alerte", f"Base de l'honoraire : {_e(l['base'])} facturés, {_e(base_attendue)} attendus "
                                        f"(net versé {_e(reservation.net)}{' moins le ménage' if hors_menage else ''})."))
        else:
            quoi = {"booking": "importez l'export Booking du mois", "direct": "réservation directe : ajoutez-la dans les réservations",
                    "airbnb": "importez l'export Airbnb du mois"}.get(l["plateforme"], "vérifiez les dates")
            notes.append(("alerte", f"Séjour absent des réservations du logement : {quoi}."))
        if l["base"] is not None and l["taux"] is not None and l["honoraire"] is not None:
            calcule = (l["base"] * l["taux"] / 100).quantize(CENTIME, ROUND_HALF_UP)
            ecart = l["honoraire"] - calcule
            if abs(ecart) > TOLERANCE:
                notes.append(("alerte", f"Honoraire : {_e(l['honoraire'])} facturés pour {_e(l['base'])} × {_t(l['taux'])} = {_e(calcule)}."))
            elif ecart:
                notes.append(("info", f"Arrondi : {_e(l['honoraire'])} au lieu de {_e(calcule)} ({'+' if ecart > 0 else ''}{_e(ecart)})."))
        if taux_prevu is not None and l["taux"] is not None and l["taux"] != taux_prevu:
            notes.append(("alerte", f"Taux : {_t(l['taux'])} facturés, {_t(taux_prevu)} prévus dans les frais de gestion du logement."))
        if menage_prevu is not None and l["menage"] != menage_prevu:
            notes.append(("alerte", f"Ménage : {_e(l['menage'])} facturés, {_e(menage_prevu)} prévus."))
        if conso_prevu is not None and l["consommables"] != conso_prevu:
            notes.append(("alerte", f"Consommables : {_e(l['consommables'])} facturés, {_e(conso_prevu)} prévus."))
        somme = sum((v for v in (l["honoraire"], l["menage"], l["consommables"]) if v is not None), Decimal("0.00"))
        if l["ttc"] is not None and abs(l["ttc"] - somme) > TOLERANCE:
            notes.append(("alerte", f"Total de la ligne : {_e(l['ttc'])} au lieu de {_e(somme)} (honoraire + ménage + consommables)."))
        lignes.append({**l, "arrivee": arrivee, "depart": depart, "reservation": reservation, "notes": notes,
                       "etat": "alerte" if any(n[0] == "alerte" for n in notes) else ("info" if notes else "ok")})
        anomalies += [n for n in notes if n[0] == "alerte"]

    # séjours du mois non facturés (mois d'arrivée des séjours de la facture)
    mois = Counter((x["arrivee"].year, x["arrivee"].month) for x in lignes if x["arrivee"])
    non_facturees = []
    for (a, m), _ in mois.most_common(1):
        non_facturees = list(ReservationLCD.objects.filter(lot=lot, annulee=False, arrivee__year=a, arrivee__month=m)
                             .exclude(pk__in=trouvees).order_by("arrivee"))
    totaux = {k: sum((x[k] or Decimal("0.00") for x in lignes), Decimal("0.00")) for k in ("honoraire", "menage", "consommables", "ttc")}
    if facture["total_ttc"] is not None and abs(facture["total_ttc"] - totaux["ttc"]) > Decimal("0.02"):
        anomalies.append(("alerte", f"Total de la facture : {_e(facture['total_ttc'])} au lieu de {_e(totaux['ttc'])} (somme des lignes)."))
    mois_sejours = date(*mois.most_common(1)[0][0], 1) if mois else None
    return {"lignes": lignes, "anomalies": anomalies, "non_facturees": non_facturees, "totaux": totaux, "mois": mois_sejours,
            "infos": sum(1 for x in lignes for n in x["notes"] if n[0] == "info")}


def enregistrer(lot, fichier, texte):
    """Enregistre la facture en comptabilité, répartie en Commission (avec le PDF), Ménage et Consommables, dans le mois des
    séjours. Renvoie la pièce principale (celle qui porte le PDF)."""
    facture = lire(texte)
    resultat = controler(lot, facture)
    t = resultat["totaux"]
    mois = resultat["mois"]
    titre = f"Facture {facture['numero']}".strip() + (f" – séjours de {['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'][mois.month - 1]} {mois.year}" if mois else "")
    coherent = facture["total_ttc"] is None or abs(facture["total_ttc"] - t["ttc"]) <= Decimal("0.02")
    commun = {"bien": lot.bien, "date": facture["date"], "fournisseur": facture["fournisseur"][:150], "mois_concerne": mois}
    if not coherent:
        # lecture incomplète : une seule pièce au total de la facture, à répartir à la main
        fichier.seek(0)
        principale = PieceComptable(**commun, libelle=titre[:200], montant=facture["total_ttc"], rubrique="commission", preremplie=True,
                                    notes="Facture de conciergerie lue partiellement : répartissez-la entre Commission, Ménage et Consommables.")
        principale.fichier.save(getattr(fichier, "name", "conciergerie.pdf").rsplit("/", 1)[-1], ContentFile(fichier.read()), save=False)
        principale.save()
        return principale
    fichier.seek(0)
    principale = PieceComptable(**commun, libelle=f"{titre} (honoraires)"[:200], montant=t["honoraire"], rubrique="commission",
                                notes=f"Total de la facture : {_e(t['ttc'])} TTC, réparti en honoraires, ménage et consommables.")
    principale.fichier.save(getattr(fichier, "name", "conciergerie.pdf").rsplit("/", 1)[-1], ContentFile(fichier.read()), save=False)
    principale.save()
    for rubrique, montant, quoi in (("menage", t["menage"], "ménage"), ("consommables", t["consommables"], "consommables")):
        if montant:
            PieceComptable.objects.create(**commun, libelle=f"{titre} ({quoi})"[:200], montant=montant, rubrique=rubrique,
                                          notes=f"Même facture que la pièce n° {principale.pk} (PDF joint à celle-ci).")
    return principale
