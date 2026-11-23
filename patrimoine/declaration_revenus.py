"""Aide à la déclaration de revenus : ce qu'il faut recopier sur impots.gouv.fr à partir de la 2044 (nom propre), des
2072 (part du foyer dans chaque SCI) et du bilan LMNP (2031 / 2033).

- Revenus fonciers → 2044 « récapitulative » du foyer, puis cases 4BA, 4BB, 4BC, 4BD et 4BE de la 2042.
  Rubrique 110 (parts de SCI : lignes 111 à 114, cases A, B, J, C, D) = cadre I de chaque 2072 × % de détention ;
  rubrique 200 (immeubles en direct : lignes 215, 240, 250, 263, cases E, F, G, I) = la 2044 de l'appli ;
  ligne 420 = D + I ; en cas de déficit, lignes 431 à 442 (plafond de 10 700 € sur le revenu global, porté jusqu'à
  21 400 € par les travaux de rénovation énergétique, intérêts d'emprunt imputables seulement sur les revenus fonciers) ;
  lignes 450-451 : déficits antérieurs non encore imputés (case 4BD), imputés sur les plus anciens, reportables 10 ans.
- Location meublée non professionnelle → 2042-C-PRO : 5NA (bénéfice) ou 5NY (déficit) au réel, 5NG / 5NH / 5NI au
  micro-BIC (recettes brutes), 5GA à 5GJ pour les déficits des dix années précédentes (5GA = année − 10, 5GJ = année − 1),
  imputables seulement sur des revenus de location meublée non professionnelle.

Le stock de déficits part des montants repris de la dernière déclaration (DeficitReporte, au 31/12 d'une année) ; les
années suivantes sont calculées ici, année par année.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

ZERO = Decimal("0")
PLAFOND_GLOBAL = Decimal("10700")
PLAFOND_MAXI = Decimal("21400")
SEUIL_MICRO_FONCIER = Decimal("15000")
DUREE_REPORT = 10
LETTRES_LMNP = "ABCDEFGHIJ"  # 5GA … 5GJ


def euros(montant):
    return Decimal(montant or 0).quantize(Decimal(1), ROUND_HALF_UP)


# --- revenus fonciers ------------------------------------------------------------------------------------------------
def parts_de_sci(annee):
    """Rubrique 110 de la 2044 : une colonne par SCI à l'IR, part du foyer (cadre I de la 2072 × % de détention)."""
    from . import annexe2072
    from .models import Proprietaire

    colonnes = []
    for sci in Proprietaire.objects.filter(biens__regime_fiscal="sci_ir").distinct().order_by("nom"):
        biens = list(sci.biens.filter(regime_fiscal="sci_ir").filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).order_by("nom"))
        if not biens:
            continue
        a = annexe2072.annexe(biens, annee)
        r = {code: montant for code, _, _, montant in a["resultat"]}
        taux = Decimal(sci.pourcentage_detention or 0) / 100
        renovation = sum((c["valeurs"].get("9b", ZERO) for c in a["colonnes"]), ZERO)
        l111, l112, l112b, l113 = (euros(r["R1"] * taux), euros(r["R3"] * taux), euros(renovation * taux), euros(r["R4"] * taux))
        colonnes.append({"sci": sci, "pourcentage": sci.pourcentage_detention, "cadre_i": a["resultat"],
                         "111": l111, "112": l112, "112bis": l112b, "113": l113, "114": l111 - l112 - l113})
    return colonnes


def immeubles_en_direct(annee):
    """Rubrique 200 de la 2044 : immeubles en nom propre au réel (montants arrondis comme sur le cerfa)."""
    from . import annexe2072, compta

    colonnes = []
    for b in compta.biens_2044().filter(Q(vendu=False) | Q(date_vente__year__gte=annee)).order_by("nom"):
        v = annexe2072.arrondir(annexe2072.colonne(b, annee)["valeurs"])
        colonnes.append({"bien": b, "215": v["5"], "224bis": v["9b"], "240": v["16"], "250": v["17"], "262": ZERO,
                         "263": v["5"] - v["16"] - v["17"]})
    return colonnes


def micro_foncier(annee):
    """Biens en location nue en nom propre au micro-foncier : recettes brutes de l'année (case 4BE)."""
    from . import annexe2072
    from .models import Bien

    biens = Bien.objects.filter(regime_fiscal="nu", option_fiscale="micro").filter(Q(vendu=False) | Q(date_vente__year__gte=annee))
    lignes = [{"bien": b, "recettes": euros(sum((m for _, m in annexe2072.loyers_par_mois(b, annee)), ZERO))} for b in biens.order_by("nom")]
    return {"biens": lignes, "total": sum((l["recettes"] for l in lignes), ZERO)}


def resultat_foncier(annee):
    """Lignes 420 à 442 de la 2044 et cases 4BA, 4BB, 4BC du foyer."""
    sci, direct = parts_de_sci(annee), immeubles_en_direct(annee)
    s = lambda cols, k: sum((c[k] for c in cols), ZERO)
    cases = {"A": s(sci, "111"), "B": s(sci, "112"), "J": s(sci, "112bis"), "C": s(sci, "113"), "D": s(sci, "114"),
             "E": s(direct, "215"), "F": s(direct, "240"), "G": s(direct, "250"), "H": s(direct, "262"), "I": s(direct, "263"),
             "K": s(direct, "224bis")}
    l = {"420": cases["D"] + cases["I"]}
    res = {"sci": sci, "direct": direct, "cases": cases, "lignes": l, "4BA": ZERO, "4BB": ZERO, "4BC": ZERO, "cas": ""}
    if l["420"] >= 0:
        res["4BA"] = l["420"]
        return res
    l["431"] = cases["A"] + cases["E"] + cases["H"]
    l["432"] = cases["C"] + cases["G"]
    l["433"] = cases["B"] + cases["F"]
    l["434"] = cases["J"] + cases["K"]
    plafond = min(PLAFOND_GLOBAL + l["434"], PLAFOND_MAXI)
    res["plafond"] = plafond
    if l["432"] > l["431"]:  # les intérêts dépassent à eux seuls les recettes
        res["cas"] = "interets"
        l["436"] = min(l["433"], plafond)
        l["437"] = l["433"] - l["436"]
        l["438"] = l["432"] - l["431"]
        l["439"] = l["437"] + l["438"]
        res["4BC"], res["4BB"] = l["436"], l["439"]
    else:
        res["cas"] = "charges"
        deficit = -l["420"]
        l["441"] = min(deficit, plafond)
        l["442"] = deficit - l["441"]
        res["4BC"], res["4BB"] = l["441"], l["442"]
    return res


# --- location meublée non professionnelle ----------------------------------------------------------------------------
def lmnp(annee):
    """Réel : bénéfice (5NA) ou déficit (5NY) des lots au réel ; micro-BIC : recettes brutes par catégorie."""
    from . import lmnp as bilan_lmnp

    reel, micro = [], {"5NG": ZERO, "5NH": ZERO, "5NI": ZERO}
    detail_micro = []
    for lot in bilan_lmnp.lots_lmnp():
        b = bilan_lmnp.bilan_lot(lot, annee)
        if not (b["total_recettes"] or b["total_charges"] or b["amortissements"]):
            continue
        if lot.bien.micro:
            case = ("5NG" if lot.tourisme_classe else "5NH") if lot.exploitation == "courte" else "5NI"
            micro[case] += euros(b["total_recettes"])
            detail_micro.append({"lot": lot, "case": case, "recettes": euros(b["total_recettes"])})
        else:
            reel.append(b)
    t = lambda k: sum((b[k] for b in reel), ZERO)
    resultat = euros(t("resultat_fiscal"))
    return {"reel": reel, "recettes": euros(t("total_recettes")), "charges": euros(t("total_charges")),
            "amort_deduit": euros(t("amort_deduit")), "amort_reporte": euros(t("amort_reporte")), "resultat": resultat,
            "5NA": max(resultat, ZERO), "5NY": max(-resultat, ZERO), "micro": micro, "detail_micro": detail_micro}


# --- report des déficits ---------------------------------------------------------------------------------------------
def _premiere_annee_de_donnees():
    from .models import EncaissementHistorique, EncaissementLCD, Paiement, PieceComptable

    annees = [qs.order_by(champ).values_list(champ, flat=True).first() for qs, champ in (
        (Paiement.objects.all(), "date"), (PieceComptable.objects.exclude(date=None), "date"),
        (EncaissementHistorique.objects.all(), "mois"), (EncaissementLCD.objects.all(), "mois"))]
    annees = [a.year for a in annees if a]
    return min(annees) if annees else None


def _resultat_de_l_annee(nature, annee):
    """(bénéfice à imputer sur les déficits antérieurs, nouveau déficit reportable) de l'année."""
    if nature == "foncier":
        r = resultat_foncier(annee)
        return r["4BA"], r["4BB"]
    r = lmnp(annee)
    return r["5NA"], r["5NY"]


def _imputer(stock, benefice, annee):
    """Impute le bénéfice sur les déficits les plus anciens encore reportables ; renvoie {origine: montant imputé}."""
    imputes = {}
    for origine in sorted(stock):
        if benefice <= 0:
            break
        if origine < annee - DUREE_REPORT:
            continue
        part = min(stock[origine], benefice)
        if part > 0:
            imputes[origine] = part
            stock[origine] -= part
            benefice -= part
    return imputes


def stock_au_debut(nature, annee):
    """Déficits non encore imputés au 31/12/(année − 1), par année d'origine, encore reportables en « année » :
    ({origine: montant}, source). source : « saisie » (repris de la déclaration au 31/12/année−1), « calcul » (roulé
    depuis une saisie plus ancienne ou depuis la première année de données de l'appli), « aucun »."""
    from .models import DeficitReporte

    ref = DeficitReporte.objects.filter(nature=nature, au_31_12__lt=annee).order_by("-au_31_12").values_list("au_31_12", flat=True).first()
    if ref is not None:
        stock = {d.annee_origine: d.montant for d in DeficitReporte.objects.filter(nature=nature, au_31_12=ref)}
        depart, source = ref + 1, "saisie" if ref == annee - 1 else "calcul"
    else:
        premiere = _premiere_annee_de_donnees()
        if premiere is None or premiere >= annee:
            return {}, "aucun", None
        stock, depart, source = {}, max(premiere, annee - DUREE_REPORT), "calcul"
    for a in range(depart, annee):
        benefice, deficit = _resultat_de_l_annee(nature, a)
        _imputer(stock, benefice, a)
        if deficit:
            stock[a] = stock.get(a, ZERO) + deficit
        stock = {o: m for o, m in stock.items() if o >= a + 1 - DUREE_REPORT and m > 0}
    stock = {o: m for o, m in stock.items() if o >= annee - DUREE_REPORT and m > 0}
    return stock, source, ref


def tableau_deficits(nature, annee, benefice, nouveau):
    """Tableau de la 2044 (lignes 450-451) ou des cases 5GA à 5GJ : une ligne par année d'origine, de année − 10 à année.
    A = non imputé au 31/12/année−1, B = imputé sur le bénéfice de l'année, C = restant à reporter au 31/12/année."""
    stock, source, ref = stock_au_debut(nature, annee)
    restant = dict(stock)
    imputes = _imputer(restant, benefice, annee)
    lignes = []
    for origine in range(annee - DUREE_REPORT, annee + 1):
        a = stock.get(origine, ZERO) if origine < annee else ZERO
        b = imputes.get(origine, ZERO)
        c = (a - b) if origine < annee else nouveau
        lignes.append({"origine": origine, "a": a, "b": b, "c": c, "perime": origine == annee - DUREE_REPORT and c > 0,
                       "case": f"5G{LETTRES_LMNP[origine - annee + DUREE_REPORT]}" if nature == "lmnp" and origine < annee else ""})
    return {"lignes": lignes, "total_a": sum((l["a"] for l in lignes), ZERO), "total_b": sum((l["b"] for l in lignes), ZERO),
            "total_c": sum((l["c"] for l in lignes if not l["perime"]), ZERO), "source": source, "ref": ref,
            "perdu": sum((l["c"] for l in lignes if l["perime"]), ZERO)}


def enregistrer_stock(nature, au_31_12, montants):
    """Remplace les déficits repris de la déclaration au 31/12/au_31_12 : {année d'origine: montant} (0 ou vide : aucun)."""
    from .models import DeficitReporte

    DeficitReporte.objects.filter(nature=nature, au_31_12=au_31_12).delete()
    for origine, montant in montants.items():
        if montant:
            DeficitReporte.objects.create(nature=nature, au_31_12=au_31_12, annee_origine=origine, montant=montant)


# --- la page ---------------------------------------------------------------------------------------------------------
def _case(code, libelle, montant, origine, formulaire="2042"):
    return {"code": code, "libelle": libelle, "montant": montant, "origine": origine, "formulaire": formulaire}


def aide(annee):
    foncier = resultat_foncier(annee)
    micro = micro_foncier(annee)
    meuble = lmnp(annee)
    deficits_fonciers = tableau_deficits("foncier", annee, foncier["4BA"], foncier["4BB"])
    deficits_lmnp = tableau_deficits("lmnp", annee, meuble["5NA"], meuble["5NY"])

    cases_foncier = []
    if foncier["sci"] or foncier["direct"]:
        if foncier["lignes"]["420"] >= 0:
            cases_foncier.append(_case("4BA", "Revenus fonciers imposables", foncier["4BA"], "2044 ligne 420 (bénéfice)"))
        else:
            cases_foncier.append(_case("4BB", "Déficit imputable sur les revenus fonciers", foncier["4BB"],
                                       "2044 ligne 439" if foncier["cas"] == "interets" else "2044 ligne 442"))
            cases_foncier.append(_case("4BC", "Déficit imputable sur le revenu global", foncier["4BC"],
                                       "2044 ligne 436" if foncier["cas"] == "interets" else "2044 ligne 441"))
    if deficits_fonciers["total_a"]:
        cases_foncier.append(_case("4BD", "Déficits antérieurs non encore imputés", deficits_fonciers["total_a"], "2044 ligne 451"))
    if micro["total"]:
        cases_foncier.append(_case("4BE", "Micro-foncier : recettes brutes sans abattement", micro["total"], "loyers encaissés des biens au micro-foncier"))

    cases_lmnp = []
    if meuble["reel"]:
        if meuble["resultat"] >= 0:
            cases_lmnp.append(_case("5NA", "Locations meublées non professionnelles, régime réel : revenus imposables", meuble["5NA"],
                                    "résultat fiscal du bilan LMNP (2031)", "2042-C-PRO"))
        else:
            cases_lmnp.append(_case("5NY", "Locations meublées non professionnelles, régime réel : déficit", meuble["5NY"],
                                    "résultat fiscal du bilan LMNP (2031)", "2042-C-PRO"))
    libelles_micro = {"5NG": "Micro-BIC : meublés de tourisme classés et chambres d'hôtes (recettes brutes)",
                      "5NH": "Micro-BIC : meublés de tourisme non classés (recettes brutes)",
                      "5NI": "Micro-BIC : autres locations meublées (recettes brutes)"}
    for code, montant in meuble["micro"].items():
        if montant:
            cases_lmnp.append(_case(code, libelles_micro[code], montant, "recettes des lots au micro-BIC", "2042-C-PRO"))
    for l in deficits_lmnp["lignes"]:
        if l["case"] and l["a"]:
            cases_lmnp.append(_case(l["case"], f"Déficit LMNP de {l['origine']} non encore déduit", l["a"],
                                    "déficits antérieurs (tableau ci-dessous)", "2042-C-PRO"))

    alertes = []
    if micro["total"] and foncier["direct"]:
        alertes.append("Un bien est au micro-foncier alors qu'un autre bien en nom propre est au réel : le micro-foncier n'est "
                       "alors pas possible, tous les biens en location nue passent sur la 2044. Vérifiez les Réglages fiscaux.")
    if micro["total"] > SEUIL_MICRO_FONCIER:
        alertes.append(f"Les recettes au micro-foncier ({micro['total']} €) dépassent 15 000 € : le régime réel (2044) s'applique.")
    if any(c["pourcentage"] in (None, 0) for c in foncier["sci"]):
        alertes.append("Une SCI a un % de détention à 0 : renseignez la part du foyer dans la fiche du propriétaire.")
    return {"annee": annee, "foncier": foncier, "micro": micro, "lmnp": meuble, "deficits_fonciers": deficits_fonciers,
            "deficits_lmnp": deficits_lmnp, "cases_foncier": cases_foncier, "cases_lmnp": cases_lmnp, "alertes": alertes}
