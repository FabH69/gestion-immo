"""« Ma maison » : dépenses de la résidence principale (hors comptabilité locative) et entretiens à prévoir.

Rien n'est créé en double : les dépenses sont des factures (PieceComptable) classées dans une catégorie de la maison,
les mensualités viennent des dépenses récurrentes, les entretiens sont des rappels (Rappel) rattachés à la maison,
et le prêt vient de « Prêts et endettement ». Ces factures n'entrent dans aucune déclaration ni dans « À classer ».
"""
from collections import defaultdict
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from .fiches_maison import FICHE_DU_RAPPEL
from .models import Bien, DepenseRecurrente, PieceComptable, Rappel

ZERO = Decimal("0")
MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# entretiens courants d'une maison, proposés en un clic tant qu'aucun rappel du même nom n'existe
# (titre, périodicité, obligatoire, précision)
ENTRETIENS_COURANTS = [
    ("Entretien de la chaudière", "annuel", True, "chaudière gaz, fioul ou bois de 4 à 400 kW : attestation à garder"),
    ("Ramonage du conduit", "annuel", True, "1 ou 2 fois par an selon le règlement sanitaire du département"),
    ("Entretien pompe à chaleur / clim", "biennal", True, "au moins tous les 2 ans au-delà de 4 kW de fluide"),
    ("Vidange de la fosse septique", "quadriennal", False, "en général tous les 4 ans (boues à 50 % du volume)"),
    ("Contrôle SPANC (assainissement)", "decennal", True, "contrôle périodique par la commune, au plus tous les 10 ans"),
    ("Piles des détecteurs de fumée", "annuel", False, ""),
    ("Nettoyage des gouttières", "annuel", False, ""),
    ("Entretien de la VMC", "annuel", False, ""),
]


def maisons():
    return Bien.objects.filter(residence_principale=True, comptabilite="").select_related("proprietaire").order_by("nom")


def _annee(p):
    d = p.date_paiement or p.date
    return d.year if d else None


def _ecart(avant, apres):
    montant = apres - avant
    pct = round(montant * 100 / avant) if avant else None
    sens = "hausse" if montant > 0 else "baisse" if montant < 0 else "stable"
    return {"montant": montant, "pct": pct, "sens": sens}


def synthese(bien, aujourdhui=None):
    """Tout ce qu'affiche la page pour une maison."""
    aujourdhui = aujourdhui or timezone.localdate()
    t = aujourdhui.year
    annees = [t - 3, t - 2, t - 1, t]
    DepenseRecurrente.generer_toutes()  # mensualités passées des dépenses récurrentes
    pieces = list(PieceComptable.objects.filter(bien=bien).exclude(montant=None).select_related("recurrence"))

    # dépenses par catégorie et par année
    par_cat = defaultdict(lambda: defaultdict(lambda: ZERO))
    for p in pieces:
        a = _annee(p)
        if a in annees:
            par_cat[p.rubrique or ""][a] += p.montant
    lignes = []
    for code, libelle in PieceComptable.RUBRIQUES_MAISON + [("", "À classer (sans catégorie)")]:
        if code not in par_cat:
            continue
        valeurs = [par_cat[code][a] for a in annees]
        lignes.append({"code": code, "libelle": libelle, "valeurs": valeurs, "ecart": _ecart(valeurs[1], valeurs[2])})
    for code in par_cat:  # catégorie inconnue (facture classée avant le passage du bien en résidence principale)
        if code and code not in PieceComptable.LIBELLES_MAISON:
            valeurs = [par_cat[code][a] for a in annees]
            lignes.append({"code": code, "libelle": PieceComptable.LIBELLES_RUBRIQUES.get(code, code), "valeurs": valeurs,
                           "ecart": _ecart(valeurs[1], valeurs[2])})
    totaux = [sum((l["valeurs"][i] for l in lignes), ZERO) for i in range(len(annees))]
    recurrentes = set(DepenseRecurrente.objects.filter(bien=bien, active=True).values_list("rubrique", flat=True))
    for l in lignes:
        l["recurrente"] = l["code"] in recurrentes

    # année en cours comparée à la même période de l'an dernier (jusqu'au jour d'aujourd'hui)
    def periode(annee):
        fin = aujourdhui.replace(year=annee) if not (aujourdhui.month == 2 and aujourdhui.day == 29) else aujourdhui.replace(year=annee, day=28)
        total = ZERO
        for p in pieces:
            d = p.date_paiement or p.date
            if d and d.year == annee and d <= fin:
                total += p.montant
        return total

    en_cours, an_dernier = periode(t), periode(t - 1)
    mois_ecoules = aujourdhui.month

    # prêt(s) de la maison
    prets = []
    for pret in bien.prets.all():
        restant = pret.capital_restant_du(aujourdhui)
        if restant and restant > 0:
            prets.append({"pret": pret, "restant": restant, "mensualite": pret.mensualite_au(aujourdhui) or ZERO})

    return {
        "bien": bien, "annees": annees, "lignes": lignes, "totaux": totaux, "ecart_total": _ecart(totaux[1], totaux[2]),
        "en_cours": en_cours, "an_dernier": an_dernier, "ecart_periode": _ecart(an_dernier, en_cours) if an_dernier else None,
        "periode": f"janv. → {MOIS_COURTS[aujourdhui.month - 1]}" if aujourdhui.month > 1 else "janvier",
        "moyenne_mois": (en_cours / mois_ecoules).quantize(Decimal("1")) if mois_ecoules else ZERO,
        "prets": prets, "mensualites": sum((p["mensualite"] for p in prets), ZERO),
        "capital_restant": sum((p["restant"] for p in prets), ZERO),
        "entretiens": entretiens(bien, pieces, aujourdhui),
        "suggestions": suggestions(bien),
        "dernieres": sorted(pieces, key=lambda p: (p.date_paiement or p.date, p.pk), reverse=True)[:8],
        "nb_factures": len(pieces),
    }


def entretiens(bien, pieces, aujourdhui):
    """Rappels de la maison encore actifs, du plus proche au plus lointain, avec la dernière fois et son coût
    (la facture du même nom la plus récente, créée par « ✓ Fait »)."""
    lignes = []
    for r in Rappel.objects.filter(bien=bien, actif=True, fait=False).order_by("date", "heure"):
        facture = max((p for p in pieces if p.libelle and p.libelle.strip().lower() == r.titre.strip().lower() and (p.date_paiement or p.date)),
                      key=lambda p: p.date_paiement or p.date, default=None)
        dernier = r.fait_le or (facture.date_paiement or facture.date if facture else None)
        jours = (r.date - aujourdhui).days
        if jours < 0:
            etat, texte = "ko", f"En retard de {-jours} jour{'s' if -jours > 1 else ''}"
        elif jours == 0:
            etat, texte = "ko", "Aujourd'hui"
        elif jours <= 30:
            etat, texte = "todo", f"Dans {jours} jour{'s' if jours > 1 else ''}"
        else:
            etat, texte = "ok", f"{MOIS_COURTS[r.date.month - 1].capitalize()} {r.date.year}"
        fiche = FICHE_DU_RAPPEL.get(r.titre.strip().lower())
        lignes.append({"rappel": r, "dernier": dernier, "cout": facture.montant if facture else None, "etat": etat,
                       "etat_texte": texte, "a_faire": jours <= 30, "fiche": fiche["slug"] if fiche else None})
    return lignes


def suggestions(bien):
    """Entretiens courants pas encore suivis, avec le lien qui ouvre le rappel pré-rempli."""
    deja = {t.strip().lower() for t in Rappel.objects.filter(bien=bien).values_list("titre", flat=True)}
    retour = reverse("ma_maison") + f"?bien={bien.pk}"
    liste = []
    for titre, periodicite, obligatoire, precision in ENTRETIENS_COURANTS:
        if titre.lower() in deja:
            continue
        params = {"titre": titre, "bien": bien.pk, "categorie": Rappel.Categorie.TRAVAUX, "periodicite": periodicite,
                  "dans_agenda": "on", "prevenir_jours": 7, "next": retour}
        liste.append({"titre": titre, "obligatoire": obligatoire, "precision": precision,
                      "periodicite": Rappel.Periodicite(periodicite).label,
                      "url": reverse("admin:patrimoine_rappel_add") + "?" + urlencode(params)})
    return liste


def liens(bien):
    """Boutons de la page : ajouter une facture, une dépense récurrente, un entretien, voir les listes."""
    retour = reverse("ma_maison") + f"?bien={bien.pk}"
    q = lambda **kw: "?" + urlencode({**kw, "next": retour})
    return {
        "ajouter_facture": reverse("admin:patrimoine_piececomptable_add") + q(bien=bien.pk),
        "ajouter_recurrente": reverse("admin:patrimoine_depenserecurrente_add") + q(bien=bien.pk),
        "ajouter_entretien": reverse("admin:patrimoine_rappel_add") + q(bien=bien.pk, categorie=Rappel.Categorie.TRAVAUX, periodicite="annuel"),
        "factures": reverse("admin:patrimoine_piececomptable_changelist") + f"?bien={bien.pk}",
        "recurrentes": reverse("admin:patrimoine_depenserecurrente_changelist") + f"?bien__id__exact={bien.pk}",
        "fiches": reverse("maison_fiches") + f"?bien={bien.pk}",
        "retour": retour,
    }

