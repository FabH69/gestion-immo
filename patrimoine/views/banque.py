"""Les deux façons d'encaisser : « Encaisser le mois » (saisie) et « Relevé de banque » (import et rapprochement)."""
from datetime import date
from decimal import Decimal

from django.contrib import admin, messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .. import droits, rapprochement
from ..models import (CompteBancaire, Echeance, LigneReleve, Paiement, PieceComptable, Proprietaire,
                      RegleRapprochement, Versement, normaliser)
from ..releves import ReleveIllisible

NOMS_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _mois(texte, defaut):
    try:
        a, m = map(int, (texte or "").split("-"))
        return date(a, m, 1)
    except ValueError:
        return defaut


def _decaler(mois, n):
    m = mois.month - 1 + n
    return date(mois.year + m // 12, m % 12 + 1, 1)


def _mode_habituel(bail):
    dernier = bail.versements.filter(annule=False).exclude(mode__in=("caf", "agence")).order_by("-date", "-pk").first()
    return dernier.mode if dernier else "virement"


def _encaisser_recu(echeance, aujourdhui):
    """« ✓ Reçu » : le reste dû, daté du jour d'échéance (aujourd'hui si le loyer n'est pas encore échu)."""
    reste = echeance.reste_du
    if reste <= 0:
        return None
    jour = min(echeance.date_exigibilite, aujourdhui)
    v = Versement.objects.create(bail=echeance.bail, date=jour, montant=reste, mode=_mode_habituel(echeance.bail),
                                 notes=f"Reçu – loyer {echeance.periode:%m/%Y}")
    v.repartir([(echeance, reste)])
    return v


def encaisser_mois(request):
    """Mode saisie : un mois à l'écran, une ligne par loyer ; « ✓ Reçu » encaisse le reste dû en un clic."""
    from ..admin.loyers import url_encaisser

    aujourdhui = timezone.localdate()
    mois = _mois(request.GET.get("mois"), aujourdhui.replace(day=1))
    proprietaire = Proprietaire.objects.filter(pk=request.GET.get("proprietaire") or 0).first()
    peut_modifier = droits.niveau(request.user, "loyers") == droits.MODIFICATION
    if request.method == "POST" and peut_modifier:
        ids = request.POST.getlist("coches") if "tous" in request.POST else [request.POST.get("un")]
        faits = []
        for e in Echeance.objects.filter(pk__in=[i for i in ids if (i or "").isdigit()]).select_related("bail"):
            if _encaisser_recu(e, aujourdhui):
                faits.append(e)
        if faits:
            messages.success(request, f"{len(faits)} loyer(s) encaissé(s), chacun daté de son jour d'échéance.")
        else:
            messages.info(request, "Rien à encaisser : cochez au moins un loyer non soldé.")
        return redirect(request.get_full_path())
    qs = (Echeance.objects.filter(periode=mois).select_related("bail__lot__bien")
          .prefetch_related("bail__locataires", Prefetch("paiements", queryset=Paiement.objects.select_related("versement")))
          .order_by("bail__lot__bien__nom", "bail__lot__designation", "nature"))
    if proprietaire:
        qs = qs.filter(bail__lot__bien__proprietaire=proprietaire)
    lignes, attendu, recu = [], Decimal(0), Decimal(0)
    for e in qs:
        paiements = list(e.paiements.all())
        paye = sum((p.montant for p in paiements), Decimal(0))
        reste = e.total - e.remise - paye
        attendu += e.total - e.remise
        recu += paye
        if reste <= 0:
            dates = [p.versement.date if p.versement_id else p.date for p in paiements]
            avance = bool(dates) and max(dates) < e.periode
            agence = any(p.versement_id and p.versement.mode == "agence" for p in paiements)
            etat = ("bleu", "Encaissé par l'agence") if agence else ("bleu", "Payé d'avance") if avance else ("ok", f"Reçu le {max(dates):%d/%m}" if dates else "Soldé (remise)")
        elif paye:
            etat = ("orange", f"Partiel : {paye:.2f} € reçus".replace(".", ","))
        elif e.date_exigibilite < aujourdhui:
            etat = ("rouge", "En retard")
        else:
            etat = ("gris", "Attendu")
        dernier = max(paiements, key=lambda p: (p.date, p.pk)) if paiements else None
        lignes.append({"e": e, "noms": ", ".join(str(l) for l in e.bail.locataires.all()) or "–", "reste": reste, "paye": paye,
                       "etat": etat, "autre": url_encaisser(e, request.get_full_path()),
                       "voir": reverse("admin:patrimoine_versement_change", args=[dernier.versement_id]) if dernier and dernier.versement_id else ""})
    retard_avant = Echeance.objects.filter(periode__lt=mois, date_exigibilite__lt=aujourdhui).exclude(statut=Echeance.Statut.PAYE)
    if proprietaire:
        retard_avant = retard_avant.filter(bail__lot__bien__proprietaire=proprietaire)
    suffixe = f"&proprietaire={proprietaire.pk}" if proprietaire else ""
    contexte = {**admin.site.each_context(request), "title": f"Encaisser {NOMS_MOIS[mois.month - 1]} {mois.year}",
                "lignes": lignes, "mois": mois, "attendu": attendu, "recu": recu, "reste": attendu - recu,
                "precedent": f"?mois={_decaler(mois, -1):%Y-%m}{suffixe}", "suivant": f"?mois={_decaler(mois, 1):%Y-%m}{suffixe}",
                "nom_precedent": NOMS_MOIS[_decaler(mois, -1).month - 1], "nom_suivant": NOMS_MOIS[_decaler(mois, 1).month - 1],
                "retard_avant": retard_avant.count(), "proprietaires": Proprietaire.objects.all(), "proprietaire": proprietaire,
                "peut_modifier": peut_modifier, "a_encaisser": [l for l in lignes if l["reste"] > 0],
                "comptes_releve": CompteBancaire.objects.filter(actif=True, mode=CompteBancaire.Mode.RELEVE).exists()}
    return render(request, "patrimoine/encaisser_mois.html", contexte)


# --- Relevé de banque ------------------------------------------------------------------------------------------------
ONGLETS = [("a_verifier", "À vérifier"), ("sures", "Sûres"), ("aucune", "Non reconnues"), ("faites", "Déjà rapprochées"), ("toutes", "Toutes")]


def _choix_manuel(request, ligne, ctx):
    """Proposition construite depuis le formulaire « Changer »."""
    P = rapprochement.Proposition
    nature = request.POST.get("nature")
    if nature == "loyer":
        bail = next((b for b in ctx.baux if str(b.pk) == request.POST.get("bail")), None)
        if bail and ligne.montant > 0:
            return rapprochement._loyer_du_bail(ligne, bail, True)
    if nature == "facture":
        piece = PieceComptable.objects.filter(pk=request.POST.get("piece") or 0, bien__proprietaire=ligne.compte.proprietaire).first()
        if piece:
            return P("facture", rapprochement.VERIFIER, f"Facture {piece}", piece=piece)
    if nature == "pret":
        pret = next((p for p in ctx.prets if str(p.pk) == request.POST.get("pret")), None)
        if pret:
            return P("pret", rapprochement.VERIFIER, f"Prêt {pret}", pret=pret)
    if nature == "depense":
        bien = next((b for b in ctx.biens if str(b.pk) == request.POST.get("bien")), None)
        if bien:
            return P("depense", rapprochement.VERIFIER, "Dépense", bien=bien, fournisseur=request.POST.get("fournisseur", "").strip(),
                     rubrique=request.POST.get("rubrique", ""))
    if nature == "tresorerie":
        return P("tresorerie", rapprochement.VERIFIER, "Mouvement de trésorerie", associe=request.POST.get("associe", "").strip() or "Associé")
    if nature == "agence":
        agence = next((a for a in ctx.agences if str(a.pk) == request.POST.get("agence")), None)
        if agence:
            return P("agence", rapprochement.VERIFIER, f"Acompte de l'agence {agence}", agence=agence)
    if nature == "ignore":
        return P("ignore", rapprochement.VERIFIER, "Ignorer")
    return None


def _retenir(request, ligne, p):
    motif = normaliser(request.POST.get("motif", ""))
    if not request.POST.get("retenir") or not motif:
        return
    A = RegleRapprochement.Action
    action = {"loyer": A.LOYER, "tresorerie": A.TRESORERIE, "depense": A.DEPENSE, "pret": A.PRET, "ignore": A.IGNORER}.get(p.nature)
    if action:
        RegleRapprochement.objects.update_or_create(compte=ligne.compte, motif=motif, defaults={
            "action": action, "bail": p.bail if action == A.LOYER else None, "bien": p.bien if action == A.DEPENSE else None,
            "pret": p.pret if action == A.PRET else None, "associe": p.associe, "rubrique": p.rubrique, "fournisseur": p.fournisseur})


def releve(request):
    comptes = list(CompteBancaire.objects.filter(actif=True).select_related("proprietaire"))
    compte = next((c for c in comptes if str(c.pk) == request.GET.get("compte")), None) or next(
        (c for c in comptes if c.mode == CompteBancaire.Mode.RELEVE), None) or (comptes[0] if comptes else None)
    peut_modifier = droits.niveau(request.user, "loyers") == droits.MODIFICATION
    contexte = {**admin.site.each_context(request), "title": "Relevé de banque", "comptes": comptes, "compte": compte,
                "peut_modifier": peut_modifier}
    if compte is None:
        return render(request, "patrimoine/releve.html", contexte)
    retour = f"{reverse('releve_banque')}?compte={compte.pk}&onglet={request.GET.get('onglet', 'a_verifier')}"
    if request.method == "POST" and peut_modifier:
        action = request.POST.get("action")
        if action == "importer" and request.FILES.get("fichier"):
            f = request.FILES["fichier"]
            try:
                imp, ok = rapprochement.importer(compte, f.name, f.read())
            except ReleveIllisible as e:
                messages.error(request, f"Relevé non importé : {e}")
                return redirect(retour)
            texte = f"{imp.nb_lignes} ligne(s) lue(s), {imp.nb_nouvelles} nouvelle(s)"
            texte += f" ({imp.nb_lignes - imp.nb_nouvelles} déjà importée(s), ignorées)." if imp.nb_lignes > imp.nb_nouvelles else "."
            (messages.success if ok is not False else messages.warning)(request, f"{texte} {imp.controle}")
            return redirect(retour)
        ctx = rapprochement.Contexte(compte)
        if action == "valider_sures":
            n = 0
            for ligne in compte.lignes.filter(statut=LigneReleve.Statut.A_TRAITER).order_by("date", "pk"):
                p = rapprochement.proposer(ligne, ctx)
                if p.certitude == rapprochement.SURE and p.validable:
                    rapprochement.appliquer(ligne, p)
                    ctx = rapprochement.Contexte(compte)  # un loyer payé ou une facture reliée ne se propose plus
                    n += 1
            messages.success(request, f"{n} ligne(s) sûre(s) validée(s).")
            return redirect(retour)
        ligne = get_object_or_404(LigneReleve, pk=request.POST.get("ligne"), compte=compte)
        if action == "derapprocher":
            rapprochement.derapprocher(ligne)
            messages.success(request, f"Ligne du {ligne.date:%d/%m/%Y} remise à traiter.")
        elif action == "joindre":
            f = request.FILES.get("piece")
            bien = next((b for b in ctx.biens if str(b.pk) == request.POST.get("bien")), None)
            if not f:
                messages.error(request, "Choisissez le fichier du justificatif (PDF ou photo).")
            else:
                try:
                    piece = rapprochement.joindre_piece(ligne, f, bien)
                except rapprochement.PieceRefusee as e:
                    messages.error(request, f"Pièce non jointe : {e}.")
                else:
                    messages.success(request, f"Justificatif joint à la ligne du {ligne.date:%d/%m/%Y} : {piece}.")
        elif action in ("valider", "choisir") and ligne.statut == LigneReleve.Statut.A_TRAITER:
            p = rapprochement.proposer(ligne, ctx) if action == "valider" else _choix_manuel(request, ligne, ctx)
            if p is None or not p.validable:
                messages.error(request, "Choix incomplet : indiquez le bail, la facture, le prêt ou le bien.")
            else:
                rapprochement.appliquer(ligne, p)
                _retenir(request, ligne, p)
                messages.success(request, f"Ligne du {ligne.date:%d/%m/%Y} rapprochée : {p.titre}.")
        return redirect(retour)
    ctx = rapprochement.Contexte(compte)
    toutes = list(compte.lignes.select_related("versement__bail__lot", "piece__bien", "pret").order_by("-date", "-pk"))
    a_traiter = [l for l in toutes if l.statut == LigneReleve.Statut.A_TRAITER]
    for l in a_traiter:
        l.prop = rapprochement.proposer(l, ctx)
    compte_onglets = {"a_verifier": [l for l in a_traiter if l.prop.certitude == rapprochement.VERIFIER],
                      "sures": [l for l in a_traiter if l.prop.certitude == rapprochement.SURE],
                      "aucune": [l for l in a_traiter if l.prop.certitude == rapprochement.AUCUNE],
                      "faites": [l for l in toutes if l.statut != LigneReleve.Statut.A_TRAITER], "toutes": toutes}
    onglet = request.GET.get("onglet") or ("a_verifier" if compte_onglets["a_verifier"] else "sures" if compte_onglets["sures"] else "aucune")
    paires = rapprochement.allers_retours([l for l in toutes if l.nature == "tresorerie" or (getattr(l, "prop", None) and l.prop.nature == "tresorerie")])
    for l in toutes:
        l.retour = paires.get(l.pk)
        l.a_justifier = rapprochement.a_justifier(l)
        l.joindre = rapprochement.peut_joindre(l) and not (l.piece_id and l.piece.fichier)
    sans_justificatif = sum(1 for l in toutes if l.a_justifier)
    annee = int(request.GET.get("annee") or (timezone.localdate().year - (1 if timezone.localdate().month <= 4 else 0)))
    contexte.update({
        "onglets": [(c, t, len(compte_onglets[c])) for c, t in ONGLETS], "onglet": onglet, "lignes": compte_onglets.get(onglet, []),
        "nb_sures": len(compte_onglets["sures"]), "sans_justificatif": sans_justificatif, "imports": compte.imports.all()[:5],
        "baux": ctx.baux, "prets": ctx.prets, "biens": ctx.biens, "agences": ctx.agences,
        "factures": sorted(ctx.pieces, key=lambda p: p.date or date.min, reverse=True)[:200],
        "comptes_courants": rapprochement.comptes_courants(compte.proprietaire),
        "fin_exercice": rapprochement.fin_exercice(compte.proprietaire, annee) if compte.engagement else None,
    })
    return render(request, "patrimoine/releve.html", contexte)
