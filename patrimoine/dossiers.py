"""Comptabilité par dossier : un dossier par déclarant et par cerfa (2044 du nom propre, 2072 de chaque SCI, 2031 du LMNP,
récapitulatif de chaque société). L'accueil du menu Comptabilité montre une carte par dossier ; la saisie d'un dossier
ne montre que ses factures et ses biens, avec l'aperçu de sa déclaration recalculé à chaque saisie.

Code d'un dossier : « 2044 », « 2031 », « 2072-<id de la SCI> », « societe-<id de la société> »."""
from decimal import Decimal

from django.db.models import Q, Sum
from django.urls import reverse

from . import compta, declarations
from .models import PieceComptable, biens_du_formulaire

ZERO = Decimal("0.00")


def code_de(formulaire, proprietaire):
    return formulaire if proprietaire is None else f"{formulaire}-{proprietaire.pk}"


def tous():
    """Les dossiers, dans l'ordre de l'accueil."""
    return [{"code": code_de(f, p), "formulaire": f, "proprietaire": p, "titre": t, "sous_titre": s, "cerfa": c}
            for f, p, t, s, c in declarations._declarants()]


def trouver(code):
    formulaire, _, pk = (code or "").partition("-")
    if (formulaire in ("2044", "2031") and not pk) or (formulaire in ("2072", "societe") and pk.isdigit()):
        return next((d for d in tous() if d["code"] == code), None)
    return None


def biens(dossier, annee=None):
    qs = biens_du_formulaire(dossier["formulaire"], dossier["proprietaire"].pk if dossier["proprietaire"] else None)
    if annee:
        qs = qs.filter(Q(vendu=False) | Q(date_vente__year__gte=annee))
    return qs.select_related("proprietaire").order_by("nom")


def _de_l_annee(qs, annee):
    return qs.filter(Q(date_paiement__year=annee) | Q(date_paiement__isnull=True, date__year=annee))


def pieces(dossier, annee=None):
    qs = PieceComptable.objects.filter(bien__in=biens(dossier)).select_related("bien")
    return _de_l_annee(qs, annee) if annee else qs


def a_saisir(dossier):
    """Factures importées pas encore saisies (à classer), toutes années : une pièce non lue n'a parfois pas de date."""
    return compta.a_codifier().filter(bien__in=biens(dossier))


def apercu(dossier, annee, bien=None):
    """Lignes clés de la déclaration (provisoires) et le résultat : [(libellé, montant, en gras)], résultat.
    bien : seulement ce bien du dossier (la part de ce bien dans la déclaration)."""
    f = dossier["formulaire"]
    if f == "2072":
        from . import annexe2072

        res = annexe2072.annexe([bien] if bien else list(biens(dossier, annee)), annee)["resultat"]
        return [(f"{code} · {libelle}", montant, code == "R5") for code, libelle, _, montant in res], res[-1][3]
    if f == "2044":
        if bien:
            from .annexe2072 import colonne

            v = colonne(bien, annee)["valeurs"]
            toutes = [{"numero": n, "libelle": l, "genre": g, "total": v.get(code, ZERO)} for n, l, code, g in compta.LIGNES_2044]
        else:
            toutes = compta.recap_2044(annee)["lignes"]
        lignes = [l for l in toutes if l["genre"] == "total" or l["numero"] == "250"]
        resultat = lignes[-1]["total"] if lignes else ZERO
        return [(f"{l['numero']} · {l['libelle']}".strip(" ·"), l["total"], l is lignes[-1]) for l in lignes], resultat
    if f == "2031":
        from . import lmnp

        blocs = lmnp.bilan(annee)["blocs"]
        if bien:
            blocs = [b for b in blocs if b["lot"].bien_id == bien.pk]
        t = {k: sum((b[k] for b in blocs), ZERO) for k in ("total_recettes", "total_charges", "resultat", "amort_deduit", "resultat_fiscal")}
        return [("Recettes", t["total_recettes"], False), ("Charges", t["total_charges"], False),
                ("Résultat avant amortissements", t["resultat"], False), ("Amortissements déduits", t["amort_deduit"], False),
                ("Résultat fiscal", t["resultat_fiscal"], True)], t["resultat_fiscal"]
    recap = compta.recapitulatif("societe", annee, bien)
    lignes = [(g["titre"] or "Autres charges", sum((l["total"] for l in g["lignes"]), ZERO), False) for g in recap["groupes"]]
    return lignes + [("Total des charges", recap["total"], True)], None


def charges_saisies(qs):
    """Total des factures saisies, avec leur signe ; la régularisation de la ligne 14 (2044 : 230) se retranche,
    comme sur le cerfa, qu'elle soit saisie en positif ou en négatif."""
    saisies = qs.exclude(compta.MANQUE)
    regul = sum((abs(p.montant) for p in saisies.filter(ligne="14")), ZERO)
    return (saisies.exclude(ligne="14").aggregate(s=Sum("montant"))["s"] or ZERO) - regul


def resume(dossier, annee, acces=None):
    """Carte d'un dossier : étapes et statut de la déclaration, factures saisies et à saisir, charges, aperçu."""
    carte = declarations.carte(dossier["formulaire"], dossier["proprietaire"], dossier["titre"], dossier["sous_titre"],
                               dossier["cerfa"], annee, acces)
    de_l_annee = pieces(dossier, annee)
    lignes, resultat = apercu(dossier, annee)
    return {**carte, **dossier, **resume_liens(dossier, annee),
            "nb_a_saisir": a_saisir(dossier).count(), "nb_saisies": de_l_annee.exclude(compta.MANQUE).count(),
            "charges": charges_saisies(de_l_annee),
            "apercu": lignes, "resultat": resultat}


def dossier_du_bien(bien_id, liste=None):
    for d in liste if liste is not None else tous():
        if biens(d).filter(pk=bien_id).exists():
            return d
    return None


def onglets(dossier, actif, annee):
    r = resume_liens(dossier, annee)
    liste = [("accueil", "Vue d'ensemble", r["url"]), ("factures", "Factures", r["lien_factures"]),
             ("a_saisir", "À saisir", r["lien_a_saisir"]), ("ajouter", "Ajouter des factures", r["lien_import"])]
    return [{"code": c, "libelle": l, "url": u, "compte": None, "actif": c == actif} for c, l, u in liste]


def resume_liens(dossier, annee):
    factures = reverse("admin:patrimoine_piececomptable_changelist")
    return {"url": reverse("dossier_compta", args=[dossier["code"]]),
            "lien_factures": f"{factures}?dossier={dossier['code']}&annee={annee}",
            "lien_a_saisir": f"{factures}?dossier={dossier['code']}&a_codifier=oui",
            "lien_import": f"{reverse('admin:patrimoine_piececomptable_import')}?dossier={dossier['code']}"}
