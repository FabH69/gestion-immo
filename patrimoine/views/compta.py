"""Comptabilité par régime : récapitulatifs par rubrique (LMNP, société), récapitulatif 2044 et contrôle des factures
de la conciergerie."""
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .. import compta, conciergerie
from ..declarations import annee_fiscale
from ..models import Lot, PieceComptable
from .communs import _pdf


def _annees():
    t = timezone.localdate().year
    return range(t - 10, t + 1)


def onglets(regime, actif, request=None):
    """Onglets en haut des pages d'une entrée de comptabilité (récapitulatif, factures, à codifier, bilan…)."""
    factures = reverse("admin:patrimoine_piececomptable_changelist") + f"?compta={regime}"
    liste = []
    if regime in compta.PAR_RUBRIQUE:
        liste.append(("recap", "Récapitulatif", reverse(f"compta_{regime}"), None))
    if regime == "foncier":
        if compta.biens_2072().exists():
            liste.append(("2072", "Annexe 2072 (SCI)", reverse("annexe_2072"), None))
        if compta.biens_2044().exists():
            liste.append(("2044", "Récapitulatif 2044", reverse("recap_2044"), None))
    liste += [("factures", "Factures", factures, None),
              ("codifier", "À classer", factures + "&a_codifier=oui", compta.a_codifier(regime).count() or None)]
    if regime in compta.PAR_RUBRIQUE and lots_conciergerie(regime).exists():
        liste.append(("conciergerie", "Contrôle conciergerie", reverse("conciergerie_ajouter") + f"?compta={regime}", None))
    if regime == "lmnp":
        liste += [("2031", "Bilan LMNP (2031)", reverse("biens_lmnp"), None),
                  ("amortissements", "Charges et amortissements", reverse("admin:patrimoine_chargelmnp_changelist"), None)]
    return [{"code": c, "libelle": l, "url": u, "compte": n, "actif": c == actif} for c, l, u, n in liste]


def lots_conciergerie(regime=None):
    """Logements en courte durée dont la comptabilité est tenue par rubrique (factures de conciergerie)."""
    qs = Lot.objects.filter(exploitation="courte", bien__comptabilite__in=compta.PAR_RUBRIQUE).select_related("bien")
    return qs.filter(bien__comptabilite=regime) if regime else qs


@staff_member_required
def compta_recap(request, regime):
    """Récapitulatif par rubrique (LMNP ou société), par bien ou mois par mois, comme le tableau de suivi."""
    if regime not in compta.PAR_RUBRIQUE:
        raise Http404
    aujourdhui = timezone.localdate()
    annee = annee_fiscale(request)
    biens = list(compta.biens(regime, annee))
    bien = next((b for b in biens if str(b.pk) == request.GET.get("bien")), None)
    par_mois = request.GET.get("vue") == "mois"
    recap = compta.recapitulatif(regime, annee, bien, par_mois)
    libelle = compta.libelle(regime)
    contexte = {"regime": regime, "libelle_regime": libelle, "annee": annee, "annees": _annees(), "biens": biens, "bien": bien,
                "par_mois": par_mois, "onglets": onglets(regime, "recap"), "aujourdhui": aujourdhui, **recap,
                "lien_import": reverse("admin:patrimoine_piececomptable_import") + f"?compta={regime}"}
    if request.GET.get("format") == "pdf":
        return _pdf(render_to_string("patrimoine/compta_recap_pdf.html", contexte), f"Comptabilite {libelle} {annee}")
    return render(request, "patrimoine/compta_recap.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Comptabilité {libelle}"})


@staff_member_required
def recap_2044(request):
    """Récapitulatif 2044 des biens en location nue en nom propre, au réel (Pollionnay)."""
    aujourdhui = timezone.localdate()
    annee = annee_fiscale(request)
    contexte = {"annee": annee, "annees": _annees(), "onglets": onglets("foncier", "2044"), **compta.recap_2044(annee)}
    return render(request, "patrimoine/recap_2044.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Récapitulatif 2044 – revenus {annee}"})


def _lot_de_la_piece(piece):
    return next((l for l in piece.bien.lots.all() if l.exploitation == "courte"), None) if piece.bien_id else None


def _pieces_liees(principale):
    """Pièces Ménage et Consommables créées avec la facture (elles renvoient à la pièce qui porte le PDF)."""
    return list(PieceComptable.objects.filter(bien=principale.bien, notes__contains=f"pièce n° {principale.pk} (PDF").order_by("rubrique"))


def factures_conciergerie(regime=None):
    """Factures de conciergerie enregistrées (pièce Commission qui porte le PDF), les plus récentes d'abord."""
    qs = PieceComptable.objects.filter(rubrique="commission", fournisseur__istartswith="concierg", bien__lots__exploitation="courte") \
        .exclude(fichier="").select_related("bien").distinct()
    if regime:
        qs = qs.filter(bien__comptabilite=regime)
    return qs.order_by("-mois_concerne", "-date", "-pk")


@staff_member_required
def conciergerie_ajouter(request):
    """Dépôt d'une facture de conciergerie : elle est lue, contrôlée avec les réservations du mois et enregistrée."""
    from ..lecture_factures import texte_pdf

    regime = request.GET.get("compta") or request.POST.get("compta") or ""
    lots = list(lots_conciergerie(regime if regime in compta.PAR_RUBRIQUE else None))
    if request.method == "POST":
        lot = next((l for l in lots if str(l.pk) == request.POST.get("lot")), lots[0] if len(lots) == 1 else None)
        fichier = request.FILES.get("fichier")
        if not lot or not fichier:
            messages.error(request, "Choisissez le logement et la facture (PDF).")
            return redirect(request.get_full_path())
        empreinte = PieceComptable.calculer_empreinte(fichier)
        deja = PieceComptable.objects.filter(empreinte=empreinte).first()
        if deja:
            messages.warning(request, f"Cette facture est déjà enregistrée{f' en {deja.annee}' if deja.annee else ''} : voici son contrôle.")
            return redirect("controle_conciergerie", pk=deja.pk)
        texte = texte_pdf(fichier) if fichier.name.lower().endswith(".pdf") else ""
        if not conciergerie.est_une_facture(texte):
            messages.error(request, "Ce fichier ne ressemble pas à une facture de conciergerie (aucun séjour avec son honoraire "
                                    "n'a pu être lu). Ajoutez-la avec « Ajouter des factures » pour la codifier à la main.")
            return redirect(request.get_full_path())
        principale = conciergerie.enregistrer(lot, fichier, texte)
        return redirect("controle_conciergerie", pk=principale.pk)
    regime_affiche = regime if regime in compta.PAR_RUBRIQUE else (lots[0].bien.comptabilite if lots else "lmnp")
    historique = []
    for p in factures_conciergerie(regime if regime in compta.PAR_RUBRIQUE else None)[:12]:
        historique.append({"piece": p, "lot": _lot_de_la_piece(p)})
    return render(request, "patrimoine/conciergerie_ajouter.html", {
        **admin.site.each_context(request), "lots": lots, "regime": regime_affiche, "libelle_regime": compta.libelle(regime_affiche),
        "onglets": onglets(regime_affiche, "conciergerie"), "historique": historique, "title": "Contrôle de la facture de conciergerie"})


@staff_member_required
def controle_conciergerie(request, pk):
    """Rapport de contrôle d'une facture de conciergerie, recalculé à chaque ouverture : après un import Booking ou l'ajout
    d'une réservation directe, les écarts corrigés disparaissent."""
    from ..lecture_factures import texte_pdf

    piece = get_object_or_404(PieceComptable.objects.select_related("bien"), pk=pk)
    lot = _lot_de_la_piece(piece)
    texte = ""
    if piece.fichier:
        try:
            with piece.fichier.open("rb") as f:
                texte = texte_pdf(f)
        except (FileNotFoundError, OSError):
            texte = ""
    if not lot or not conciergerie.est_une_facture(texte):
        messages.warning(request, "Cette pièce n'est pas une facture de conciergerie lisible : elle s'ouvre comme une facture ordinaire.")
        return redirect("admin:patrimoine_piececomptable_change", pk)
    facture = conciergerie.lire(texte)
    resultat = conciergerie.controler(lot, facture)
    regime = piece.bien.comptabilite or "lmnp"
    plateformes_absentes = sorted({l["plateforme"] for l in resultat["lignes"] if not l["reservation"] and l["plateforme"]})
    return render(request, "patrimoine/controle_conciergerie.html", {
        **admin.site.each_context(request), "piece": piece, "lot": lot, "facture": facture, **resultat,
        "pieces_liees": _pieces_liees(piece), "regime": regime, "libelle_regime": compta.libelle(regime),
        "onglets": onglets(regime, "conciergerie"), "plateformes_absentes": plateformes_absentes,
        "lien_suivi": reverse("suivi_lcd") + f"?lot={lot.pk}" + (f"&annee={resultat['mois'].year}" if resultat["mois"] else ""),
        "lien_reservation": reverse("admin:patrimoine_reservationlcd_add") + f"?lot={lot.pk}",
        "title": f"Contrôle de la facture {facture['numero'] or ''}".strip()})


@staff_member_required
def regimes_fiscaux(request):
    """Tableau récapitulatif : régime de chaque bien, recettes, charges, formulaire à remplir et alertes de seuils."""
    from .. import regimes

    aujourdhui = timezone.localdate()
    annee = annee_fiscale(request)
    contexte = {"annee": annee, "annees": _annees(), "en_cours": annee == aujourdhui.year, **regimes.synthese(annee),
                "seuils_reference": regimes}
    return render(request, "patrimoine/regimes_fiscaux.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Régimes fiscaux – revenus {annee}"})


@staff_member_required
def declarations(request):
    """Comptabilité › Déclarations : une carte par cerfa ; « Déclaration effectuée le … » verrouille la saisie qui l'alimente."""
    from datetime import datetime

    from .. import declarations as decl, droits
    from ..models import DeclarationFaite, Proprietaire

    annee = decl.annee_fiscale(request)
    if request.method == "POST":
        annee = int(request.POST.get("annee") or annee)
        retour = reverse("declarations") + f"?annee={annee}"
        if request.POST.get("retour", "").startswith("/comptabilite/"):
            retour = request.POST["retour"]  # case cochée depuis la page d'un dossier : on y revient
        if request.POST.get("action") == "decocher":
            d = get_object_or_404(DeclarationFaite, pk=request.POST.get("declaration"), active=True)
            decl.decocher(d)
            messages.success(request, f"{d.libelle_court} {d.annee} : case décochée, la saisie est de nouveau modifiable "
                                      "(déclaration rectificative). La déclaration reste dans l'historique.")
            return redirect(retour)
        formulaire = request.POST.get("formulaire", "")
        if formulaire not in DeclarationFaite.Formulaire.values:
            raise Http404
        proprietaire = get_object_or_404(Proprietaire, pk=request.POST["proprietaire"]) if request.POST.get("proprietaire") else None
        try:
            le = datetime.strptime(request.POST.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Indiquez la date à laquelle la déclaration a été faite.")
            return redirect(retour)
        d = decl.cocher(formulaire, proprietaire.pk if proprietaire else None, annee, le)
        messages.success(request, f"{d.libelle_court} {annee} déclarée le {le:%d/%m/%Y} : la saisie qui l'alimente est verrouillée.")
        return redirect(retour)

    acces = None if request.user.is_superuser else droits.niveaux(request.user)
    contexte = {"annee": annee, "annees": _annees(), "cartes": decl.cartes(annee, acces), "aujourdhui": timezone.localdate(),
                "lien_saisie": reverse("admin:patrimoine_piececomptable_changelist") + f"?annee={annee}",
                "historique": DeclarationFaite.objects.exclude(annee=annee).filter(active=True).select_related("proprietaire")[:12]}
    return render(request, "patrimoine/declarations.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Déclarations – revenus {annee}"})


@staff_member_required
def compta_accueil(request):
    """Comptabilité › Mes dossiers : une carte par dossier, ce qui reste à faire et les dernières factures importées."""
    from .. import droits, dossiers
    from ..models import DeclarationFaite, DepenseRecurrente

    DepenseRecurrente.generer_toutes()  # les échéances passées comptent dans les cartes
    annee = annee_fiscale(request)
    acces = None if request.user.is_superuser else droits.niveaux(request.user)
    liste = dossiers.tous()
    cartes = [dossiers.resume(d, annee, acces) for d in liste]
    ids_dossiers = {pk for d in liste for pk in dossiers.biens(d).values_list("pk", flat=True)}
    a_saisir = compta.a_codifier().select_related("bien").order_by("-pk")
    dossier_par_bien = {}
    for d in liste:
        for pk in dossiers.biens(d).values_list("pk", flat=True):
            dossier_par_bien.setdefault(pk, d)
    recentes = []
    for p in a_saisir[:8]:
        d = dossier_par_bien.get(p.bien_id)
        lien = reverse("admin:patrimoine_piececomptable_change", args=[p.pk])
        recentes.append({"piece": p, "dossier": d, "lien": lien + (f"?_changelist_filters=dossier%3D{d['code']}" if d else "")})
    factures = reverse("admin:patrimoine_piececomptable_changelist")
    manquantes = sum(1 for c in cartes for e in c["etapes"] if e["libelle"] == "Emprunts" and e["etat"] == "ko")
    contexte = {
        "annee": annee, "annees": _annees(), "cartes": cartes, "recentes": recentes,
        "nb_a_saisir": a_saisir.count(), "lien_a_saisir": f"{factures}?a_codifier=oui",
        "nb_hors_dossier": a_saisir.exclude(bien_id__in=ids_dossiers).count(), "attestations_manquantes": manquantes,
        "carte_attestation": next((c for c in cartes for e in c["etapes"] if e["libelle"] == "Emprunts" and e["etat"] == "ko"), None),
        "declarees": DeclarationFaite.objects.filter(annee=annee - 1, active=True).select_related("proprietaire"),
        "lien_import": reverse("admin:patrimoine_piececomptable_import"),
    }
    return render(request, "patrimoine/compta_accueil.html",
                  {**admin.site.each_context(request), **contexte, "title": "Mes dossiers comptables"})


@staff_member_required
def dossier_compta(request, code):
    """Saisie d'un seul dossier : ses factures et ses biens, l'aperçu de sa déclaration, la case « Déclaration effectuée »."""
    from .. import droits, dossiers

    dossier = dossiers.trouver(code)
    if dossier is None:
        raise Http404
    annee = annee_fiscale(request)
    acces = None if request.user.is_superuser else droits.niveaux(request.user)
    r = dossiers.resume(dossier, annee, acces)
    biens_dossier = list(dossiers.biens(dossier, annee))
    bien = next((b for b in biens_dossier if str(b.pk) == request.GET.get("bien")), None)
    pieces = dossiers.pieces(dossier, annee)
    if bien:  # filtre « Bien » : factures et aperçu de ce bien seulement
        pieces = pieces.filter(bien=bien)
        r["apercu"], r["resultat"] = dossiers.apercu(dossier, annee, bien)
        if dossier["formulaire"] == "2072":  # « Voir la 2072 » : seul ce bien coché, les autres restent cochables
            r["lien"] += f"&bien={bien.pk}"
    url = reverse("dossier_compta", args=[code])
    contexte = {"annee": annee, "annees": _annees(), "d": r, "dossiers": dossiers.tous(), "aujourdhui": timezone.localdate(),
                "pieces": pieces.order_by("-date_paiement", "-date", "-pk")[:15], "nb_pieces": pieces.count(),
                "biens_dossier": biens_dossier, "bien_choisi": bien,
                "filtre_biens": [{"libelle": "Tous les biens", "url": f"{url}?annee={annee}", "actif": bien is None}]
                + [{"libelle": b.nom, "url": f"{url}?annee={annee}&bien={b.pk}", "actif": b == bien} for b in biens_dossier],
                "lien_saisie": r["lien_factures"] + (f"&bien={bien.pk}" if bien else ""),
                "onglets": dossiers.onglets(dossier, "accueil", annee), "libelle_regime": dossier["titre"],
                "retour": request.get_full_path()}
    return render(request, "patrimoine/dossier_compta.html",
                  {**admin.site.each_context(request), **contexte, "title": f"{dossier['titre']} · comptabilité {annee}"})


@staff_member_required
def evolution_depenses(request):
    """Comptabilité › Évolution des dépenses : dépenses par nature sur plusieurs années, avec l'écart d'une année sur
    l'autre. Filtres : dossier, propriétaire, bien et plage d'années."""
    from .. import dossiers, synthese_depenses as sd
    from ..models import Proprietaire

    t = timezone.localdate().year

    def entier(nom, defaut):
        v = request.GET.get(nom, "")
        return int(v) if v.isdigit() and t - 15 <= int(v) <= t else defaut

    fin = entier("fin", t - 1)  # par défaut la dernière année complète : l'année en cours fausserait l'écart
    debut = min(entier("debut", fin - 3), fin)
    annees = list(range(max(debut, fin - 7), fin + 1))  # 8 années au plus, pour que le tableau reste lisible
    liste = dossiers.tous()
    dossier = dossiers.trouver(request.GET.get("dossier"))
    tous_biens = sd.biens_du_perimetre(dossier)
    proprietaires = sorted({b.proprietaire for b in tous_biens}, key=lambda p: p.nom)
    proprietaire = next((p for p in proprietaires if str(p.pk) == request.GET.get("proprietaire")), None)
    biens_choix = [b for b in tous_biens if proprietaire is None or b.proprietaire_id == proprietaire.pk]
    bien = next((b for b in biens_choix if str(b.pk) == request.GET.get("bien")), None)
    biens = sd.biens_du_perimetre(dossier, proprietaire, bien)
    contexte = {"dossiers": liste, "dossier": dossier, "proprietaires": proprietaires, "proprietaire": proprietaire,
                "biens_choix": biens_choix, "bien": bien, "debut": annees[0], "fin": fin, "choix_annees": range(t, t - 16, -1),
                "biens": biens, **sd.synthese(annees, biens)}
    return render(request, "patrimoine/evolution_depenses.html",
                  {**admin.site.each_context(request), **contexte, "title": "Évolution des dépenses"})


@staff_member_required
def declaration_revenus(request):
    """Comptabilité › Ma déclaration de revenus : cases de la 2042 et de la 2042-C-PRO à recopier, correspondance avec
    les lignes de la 2044, des 2072 et du bilan LMNP, et suivi des déficits antérieurs (foncier et LMNP)."""
    from .. import declaration_revenus as dr

    annee = annee_fiscale(request)
    if request.method == "POST":
        annee = int(request.POST.get("annee") or annee)
        nature = request.POST.get("nature")
        if nature not in ("foncier", "lmnp"):
            raise Http404
        au = annee - 1
        montants, erreurs = {}, []
        for origine in range(au - dr.DUREE_REPORT + 1, au + 1):
            brut = request.POST.get(f"montant_{origine}", "").replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
            if not brut:
                continue
            try:
                montants[origine] = Decimal(brut)
            except InvalidOperation:
                erreurs.append(str(origine))
        if erreurs or any(m < 0 for m in montants.values()):
            messages.error(request, "Montant illisible ou négatif pour " + (", ".join(erreurs) or "une année")
                           + " : saisissez un nombre positif, par exemple 1250 ou 1 250,50.")
        else:
            dr.enregistrer_stock(nature, au, montants)
            quoi = "fonciers" if nature == "foncier" else "LMNP"
            messages.success(request, f"Déficits {quoi} restant à reporter au 31/12/{au} enregistrés ; "
                                      f"les années suivantes sont recalculées à partir de ces montants.")
        return redirect(reverse("declaration_revenus") + f"?annee={annee}#deficits-{nature}")
    contexte = {"annee": annee, "annees": _annees(), **dr.aide(annee),
                "origines_saisie": list(range(annee - dr.DUREE_REPORT, annee))}
    return render(request, "patrimoine/declaration_revenus.html",
                  {**admin.site.each_context(request), **contexte, "title": f"Ma déclaration de revenus {annee}"})


@staff_member_required
def declaration_revenus_detail(request):
    """Ma déclaration de revenus › Détail des montants : chaque case, ses lignes, ses immeubles et ses pièces."""
    from .. import declaration_revenus as dr

    annee = annee_fiscale(request)
    return render(request, "patrimoine/declaration_revenus_detail.html",
                  {**admin.site.each_context(request), **dr.detail(annee), "annee": annee, "annees": _annees(),
                   "title": f"Détail des montants – revenus {annee}"})
