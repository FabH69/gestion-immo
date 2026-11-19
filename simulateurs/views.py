"""Simulateurs publics : accessibles sans compte (vitrine), sans lecture ni écriture en base.

Les formulaires sont en GET : un résultat se partage par son adresse et se recalcule à chaque chargement.
"""
from django.shortcuts import render

from .calculs import notaire
from .calculs import pret as calcul_pret
from .forms import FraisNotaireForm, PretForm
from .registre import SIMULATEURS


def _page(request, gabarit, slug, **contexte):
    simulateur = next(s for s in SIMULATEURS if s.slug == slug)
    return render(request, gabarit, {"simulateur": simulateur, "simulateurs": SIMULATEURS, **contexte})


def _formulaire(request, classe, champ_declencheur):
    """Formulaire lié si la page a été soumise (présence du champ principal dans l'adresse), sinon vierge."""
    if champ_declencheur in request.GET:
        return classe(request.GET)
    return classe(initial={k: v for k, v in request.GET.items()} or None)


def accueil(request):
    return render(request, "simulateurs/accueil.html", {"simulateurs": SIMULATEURS})


def frais_notaire(request):
    form = _formulaire(request, FraisNotaireForm, "prix")
    resultat = None
    if form.is_bound and form.is_valid():
        d = form.cleaned_data
        resultat = notaire.calculer(d["prix"], d["type_bien"], d["departement"], d["primo_accedant"],
                                    d.get("mobilier") or 0, d["formalites"])
    return _page(request, "simulateurs/frais_notaire.html", "frais-de-notaire", form=form, r=resultat,
                 mise_a_jour=notaire.MISE_A_JOUR,
                 nom_departement=notaire.NOMS_DEPARTEMENTS.get(resultat.departement) if resultat else "")


def pret(request):
    form = _formulaire(request, PretForm, "taux")
    resultat = endettement = None
    if form.is_bound and form.is_valid():
        d = form.cleaned_data
        resultat = calcul_pret.simuler(d["capital"], d["taux"], d["duree_annees"] * 12, d.get("taux_assurance") or 0,
                                     d["mode_assurance"], d.get("frais") or 0)
        endettement = resultat.taux_endettement(d.get("revenus"), d.get("autres_credits"))
    return _page(request, "simulateurs/pret.html", "pret-immobilier", form=form, r=resultat, endettement=endettement,
                 endettement_max=calcul_pret.TAUX_ENDETTEMENT_MAX)



# =====================================================================================================================
# Module investisseur (rentabilité locative) : public pour calculer, espace connecté pour enregistrer
# =====================================================================================================================
import json  # noqa: E402

from django.contrib import messages  # noqa: E402
from django.http import Http404, JsonResponse  # noqa: E402
from django.shortcuts import get_object_or_404, redirect  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.views.decorators.http import require_POST  # noqa: E402

from . import dvf  # noqa: E402
from .calculs import investissement as inv  # noqa: E402
from .forms import BLOCS_INVEST, CHOIX_INVEST, departement_de, lire_lots, lire_parametres  # noqa: E402
from .models import DevisTravaux, PhotoDossier, SimulationInvestissement  # noqa: E402

SLUG_INVEST = "rentabilite"


def _niveau_invest(user):
    from patrimoine import droits

    return droits.niveau(user, "investissement")


def _peut_enregistrer(user):
    from patrimoine import droits

    return _niveau_invest(user) == droits.MODIFICATION


def _vers_connexion(request):
    return redirect(f"{reverse('admin:login')}?next={request.get_full_path()}")


def connecte(vue):
    """Pages de l'espace connecté : sans compte, on passe par la connexion (les droits sont vérifiés par le middleware)."""
    from functools import wraps

    @wraps(vue)
    def enveloppe(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _vers_connexion(request)
        return vue(request, *args, **kwargs)
    return enveloppe


def _blocs(p):
    """Champs des blocs avec leur valeur actuelle, prêts pour le gabarit."""
    return [(code, titre, sous, [{"nom": n, "libelle": l, "aide": a, "genre": g, "valeur": getattr(p, n),
                                   "choix": CHOIX_INVEST.get(n)} for n, l, a, g in champs])
            for code, titre, sous, champs in BLOCS_INVEST]


def _regimes_possibles():
    return [("", "Le meilleur pour ce projet"), ("mixte", "Votre répartition (lots nus et meublés)"),
            ("nu-micro", "Location nue · micro-foncier"), ("nu-reel", "Location nue · réel"),
            ("jeanbrun", "Location nue · dispositif Jeanbrun"),
            ("lmnp-micro", "Meublé (LMNP) · micro-BIC"), ("lmnp-reel", "Meublé (LMNP) · réel"),
            ("sci-ir", "SCI à l'IR (location nue)"), ("sci-is", "SCI à l'IS")]


def _contexte_invest(request, p, simulation=None):
    r = inv.simuler(p)
    associes = list(p.associes) + [inv.Associe() for _ in range(inv.MAX_ASSOCIES - len(p.associes))]
    if not p.associes:
        associes[0] = inv.Associe(parts=100)
    return {
        "p": p, "r": r, "simulation": simulation, "blocs": _blocs(p), "associes": associes,
        "associe1_foyer": not p.associes or (p.associes[0].revenu_imposable == p.revenu_imposable
                                             and p.associes[0].situation == p.situation),
        "types_lot": inv.TYPES_LOT, "etages": inv.ETAGES, "situations": inv.SITUATIONS, "regimes_possibles": _regimes_possibles(),
        "courbe": inv.courbe(p), "prix_zero": inv.prix_cible(p, 0, "avant"),
        "peut_enregistrer": request.user.is_authenticated and _peut_enregistrer(request.user),
        "mise_a_jour": inv.MISE_A_JOUR, "max_lots": inv.MAX_LOTS,
        "travaux_config": {"postes_lot": inv.chiffrage_par_defaut(False, 0)["lignes"], "postes_communs": inv.chiffrage_par_defaut(True, 0)["lignes"],
                           "prix_niveaux": {nom: list(prix) for nom, _, _, prix, _ in inv.POSTES_LOT + inv.POSTES_COMMUNS},
                           "niveaux": inv.NIVEAUX_RENOVATION, "modes": inv.MODES_TRAVAUX, "imprevus": inv.IMPREVUS_PAR_DEFAUT,
                           "url_devis": reverse("simulateurs:simulation_devis", args=[simulation.pk]) if simulation else None,
                           "peut_joindre": bool(simulation) and request.user.is_authenticated and _peut_enregistrer(request.user)},
        "travaux_communs_json": json.dumps(p.travaux_communs) if p.travaux_communs else "",
        "lots_detail_json": [json.dumps(l.travaux_detail) if l.travaux_detail else "" for l in p.lots],
        "dpe_json": json.dumps(p.dpe), "classes_dpe": "ABCDEFG",
    }


def rentabilite(request):
    """Étape 1 (démarrage : lots et types) puis paramètres et résultats."""
    if request.method == "POST":  # démarrage envoyé
        lots = lire_lots(request.POST)
        adresse = (request.POST.get("adresse") or "").strip()[:255]
        p = inv.parametres_par_defaut(lots)
        p.nom = (request.POST.get("nom") or "").strip()[:200] or "Nouvelle simulation"
        p.adresse, p.lien_annonce = adresse, (request.POST.get("lien_annonce") or "").strip()[:500]
        p.departement = departement_de(adresse) or p.departement
        return _page(request, "simulateurs/rentabilite.html", SLUG_INVEST, **_contexte_invest(request, p))
    if "exemple" in request.GET:
        return _page(request, "simulateurs/rentabilite.html", SLUG_INVEST, **_contexte_invest(request, inv.exemple()))
    return _page(request, "simulateurs/rentabilite_demarrage.html", SLUG_INVEST, types_lot=inv.TYPES_LOT, etages=inv.ETAGES,
                 cartes=range(inv.MAX_LOTS), max_lots=inv.MAX_LOTS)


@require_POST
def rentabilite_calcul(request):
    """Recalcul (à chaque saisie, par la page) : renvoie le résumé et les résultats ; page entière sans JavaScript."""
    p = lire_parametres(request.POST)
    simulation = None
    pk = request.POST.get("simulation")
    if pk and pk.isdigit() and request.user.is_authenticated and _niveau_invest(request.user):
        simulation = SimulationInvestissement.objects.filter(pk=pk).first()
    contexte = _contexte_invest(request, p, simulation)
    if request.headers.get("X-Fragment"):
        return render(request, "simulateurs/_rentabilite_resultats.html", contexte)
    return _page(request, "simulateurs/rentabilite.html", SLUG_INVEST, **contexte)


@connecte
def mes_simulations(request):
    simulations = []
    for s in SimulationInvestissement.objects.select_related("bien"):
        try:
            r = inv.simuler(s.parametres)
        except Exception:  # noqa: BLE001 : une simulation abîmée ne doit pas casser la liste
            r = None
        simulations.append((s, r))
    return _page(request, "simulateurs/mes_simulations.html", SLUG_INVEST, simulations=simulations,
                 peut_enregistrer=_peut_enregistrer(request.user), mes_simulations_actif=True)


@connecte
def simulation(request, pk):
    s = get_object_or_404(SimulationInvestissement, pk=pk)
    return _page(request, "simulateurs/rentabilite.html", SLUG_INVEST, **_contexte_invest(request, s.parametres, s))


@connecte
@require_POST
def simulation_enregistrer(request):
    p = lire_parametres(request.POST)
    pk = request.POST.get("simulation")
    s = get_object_or_404(SimulationInvestissement, pk=pk) if pk else SimulationInvestissement()
    s.nom, s.adresse, s.donnees = p.nom or "Simulation sans nom", p.adresse, p.en_dict()
    s.save()
    # devis joints qui ne sont plus utilisés par aucune ligne : retirés (le fichier aussi)
    utilises = _pieces(p)
    _retirer_devis(s.devis.exclude(pk__in=utilises))
    messages.success(request, f"Simulation « {s.nom} » enregistrée.")
    return redirect(s)


@connecte
@require_POST
def simulation_dupliquer(request, pk):
    s = get_object_or_404(SimulationInvestissement, pk=pk)
    copie = SimulationInvestissement.objects.create(nom=f"{s.nom} (copie)"[:200], adresse=s.adresse,
                                                    donnees={**s.donnees, "nom": f"{s.nom} (copie)"[:200]},
                                                    dossier={**(s.dossier or {}), "couverture": None})  # photos non recopiées
    # les devis joints sont recopiés (même fichier) pour que chaque simulation garde les siens
    correspondance = {}
    for d in s.devis.all():
        correspondance[d.pk] = DevisTravaux.objects.create(simulation=copie, fichier=d.fichier.name, nom=d.nom).pk
    if correspondance:
        donnees = json.loads(json.dumps(copie.donnees))
        for detail in [l.get("travaux_detail") for l in donnees.get("lots", [])] + [donnees.get("travaux_communs")]:
            for ligne in (detail or {}).get("lignes", []):
                if ligne.get("piece") in correspondance:
                    ligne["piece"] = correspondance[ligne["piece"]]
        copie.donnees = donnees
        copie.save(update_fields=["donnees"])
    messages.success(request, "Copie créée : changez un paramètre pour comparer les deux versions.")
    return redirect(copie)


def _retirer_devis(devis):
    """Supprime des devis joints ; le fichier part aussi s'il n'est plus partagé avec une copie de la simulation."""
    for d in list(devis):
        nom = d.fichier.name
        d.delete()
        if nom and not DevisTravaux.objects.filter(fichier=nom).exists():
            d.fichier.storage.delete(nom)


def _pieces(p):
    """Identifiants des devis joints aux lignes du chiffrage."""
    details = [l.travaux_detail for l in p.lots] + [p.travaux_communs]
    return {ligne["piece"] for d in details if d for ligne in d.get("lignes", []) if isinstance(ligne.get("piece"), int)}


EXTENSIONS_DEVIS = (".pdf", ".jpg", ".jpeg", ".png", ".heic")
TAILLE_MAX_DEVIS = 15 * 1024 * 1024


@connecte
@require_POST
def simulation_devis(request, pk):
    """Joint un devis (PDF ou photo) à une ligne du chiffrage : renvoie son identifiant et son lien."""
    s = get_object_or_404(SimulationInvestissement, pk=pk)
    f = request.FILES.get("fichier")
    if not f:
        return JsonResponse({"erreur": "Choisissez un fichier."}, status=400)
    if not f.name.lower().endswith(EXTENSIONS_DEVIS):
        return JsonResponse({"erreur": "Format accepté : PDF, JPG, PNG ou HEIC."}, status=400)
    if f.size > TAILLE_MAX_DEVIS:
        return JsonResponse({"erreur": "Fichier trop lourd (15 Mo au plus)."}, status=400)
    d = DevisTravaux.objects.create(simulation=s, fichier=f, nom=f.name[:200])
    return JsonResponse({"id": d.pk, "nom": d.nom, "url": d.url})


# ---------------------------------------------------------------------------------------------------------------------
# Dossier de financement (« dossier banque »)
# ---------------------------------------------------------------------------------------------------------------------

EXTENSIONS_PHOTO = (".jpg", ".jpeg", ".png", ".webp")
TAILLE_MAX_PHOTO = 12 * 1024 * 1024
MAX_PHOTOS = 12


def _contexte_dossier(s):
    """Contexte des pages du dossier, avec la carte et les prix du marché quand les services publics répondent."""
    from . import dossier

    p = s.parametres
    lieu = marche = None
    erreur_marche = ""
    if p.adresse:
        type_local = "Maison" if any(l.type == "maison" for l in p.lots) else "Appartement"
        try:
            lieu = dvf.geocoder(p.adresse)
            marche = dvf.prix_du_marche(p.adresse, type_local)
        except dvf.DvfIndisponible as e:
            erreur_marche = str(e)
    return {**dossier.contexte(s, marche, lieu, s.photos.all()), "erreur_marche": erreur_marche}


@connecte
def simulation_dossier(request, pk):
    """Préparer le dossier banque : réglages, photos, DPE ; aperçu des pages à droite."""
    from . import dossier

    s = get_object_or_404(SimulationInvestissement, pk=pk)
    if request.method == "POST":
        s.dossier = dossier.lire_reglages(request.POST, len(s.parametres.lots))
        s.save(update_fields=["dossier", "modifie_le"])
        messages.success(request, "Dossier banque enregistré.")
        return redirect("simulateurs:simulation_dossier", pk=s.pk)
    return _page(request, "simulateurs/dossier.html", SLUG_INVEST, peut_enregistrer=_peut_enregistrer(request.user),
                 **_contexte_dossier(s))


@connecte
def simulation_dossier_imprimer(request, pk):
    """Le dossier seul, en pages A4, à imprimer ou à enregistrer en PDF depuis le navigateur."""
    s = get_object_or_404(SimulationInvestissement, pk=pk)
    return render(request, "simulateurs/dossier_imprimer.html", _contexte_dossier(s))


@connecte
@require_POST
def simulation_dossier_photo(request, pk):
    """Ajouter, légender ou retirer une photo du dossier banque."""
    s = get_object_or_404(SimulationInvestissement, pk=pk)
    action = request.POST.get("action")
    if action == "ajouter":
        fichiers = request.FILES.getlist("photos")
        refusees = 0
        for f in fichiers:
            if s.photos.count() >= MAX_PHOTOS or not f.name.lower().endswith(EXTENSIONS_PHOTO) or f.size > TAILLE_MAX_PHOTO:
                refusees += 1
                continue
            PhotoDossier.objects.create(simulation=s, fichier=f, ordre=s.photos.count(),
                                        legende=f.name.rsplit(".", 1)[0][:120])
        if refusees:
            messages.warning(request, f"{refusees} photo(s) refusée(s) : JPG, PNG ou WebP de 12 Mo au plus, {MAX_PHOTOS} photos au plus.")
        elif fichiers:
            messages.success(request, f"{len(fichiers)} photo(s) ajoutée(s).")
    elif action in ("legende", "supprimer"):
        ph = get_object_or_404(PhotoDossier, pk=request.POST.get("photo"), simulation=s)
        if action == "legende":
            ph.legende = (request.POST.get("legende") or "").strip()[:120]
            ph.save(update_fields=["legende"])
        else:
            nom = ph.fichier.name
            ph.delete()
            ph.fichier.storage.delete(nom)
    return redirect(reverse("simulateurs:simulation_dossier", args=[s.pk]) + "#photos")


@connecte
@require_POST
def simulation_supprimer(request, pk):
    s = get_object_or_404(SimulationInvestissement, pk=pk)
    nom = s.nom
    _retirer_devis(s.devis.all())
    for ph in list(s.photos.all()):
        ph.fichier.storage.delete(ph.fichier.name)
    s.delete()
    messages.success(request, f"Simulation « {nom} » supprimée.")
    return redirect("simulateurs:mes_simulations")


TYPES_VERS_LOT = {"studio": ("studio", 1), "t1": ("appartement", 1), "t2": ("appartement", 2), "t3": ("appartement", 3),
                  "t4": ("appartement", 4), "t5": ("appartement", 5), "maison": ("maison", None),
                  "local": ("local_commercial", None), "parking": ("parking", None), "cave": ("cave", None)}


def _decouper_adresse(adresse):
    """« 12 Grande Rue, 69600 Oullins » → (« 12 Grande Rue », « 69600 », « Oullins »)."""
    import re

    m = re.match(r"^\s*(.*?)[,\s]+(\d{5})\s+(.+?)\s*$", adresse or "")
    if m:
        return m.group(1).strip(" ,"), m.group(2), m.group(3)
    return (adresse or "").strip(), "", ""


def _dpe_du_lot(d):
    """Champs DPE du lot créé : numéro, et classes et dates si le DPE a été trouvé à l'ADEME."""
    from datetime import date

    if not d:
        return {}
    v = d.get("verifie") or {}
    champs = {"numero_dpe": d["numero"]}
    classe = v.get("classe") or d.get("classe_annoncee")
    if classe and classe in "ABCDEFG":
        champs["classe_dpe"] = classe
    if v.get("ges") and v["ges"] in "ABCDEFG":
        champs["classe_ges"] = v["ges"]
    for champ, cle in (("date_dpe", "date_etablissement"), ("fin_validite_dpe", "fin_validite")):
        try:
            champs[champ] = date.fromisoformat(v.get(cle, ""))
        except ValueError:
            pass
    return champs


@connecte
def simulation_creer_bien(request, pk):
    """Transforme une simulation (projet acheté) en bien et lots dans la gestion locative."""
    from django.db import transaction

    from patrimoine import droits
    from patrimoine.models import Bien, Lot, Proprietaire

    s = get_object_or_404(SimulationInvestissement, pk=pk)
    if not (request.user.is_superuser or droits.niveau(request.user, "biens") == droits.MODIFICATION):
        raise Http404
    p = s.parametres
    rue, cp, ville = _decouper_adresse(p.adresse)
    proprietaires = Proprietaire.objects.all()
    if request.method == "POST":
        proprietaire = proprietaires.filter(pk=request.POST.get("proprietaire")).first()
        if proprietaire is None:
            messages.error(request, "Choisissez le propriétaire du bien.")
        else:
            with transaction.atomic():
                bien = Bien.objects.create(
                    proprietaire=proprietaire, nom=(request.POST.get("nom") or s.nom)[:200],
                    adresse=(request.POST.get("adresse") or rue)[:255], code_postal=(request.POST.get("code_postal") or cp)[:10],
                    ville=(request.POST.get("ville") or ville)[:100], prix_acquisition=round(p.prix, 2) or None)
                dpe_par_lot = {d["lot"]: d for d in p.dpe if d.get("lot")}
                for i, l in enumerate(p.lots, 1):
                    type_lot, pieces = TYPES_VERS_LOT.get(l.type, ("appartement", None))
                    Lot.objects.create(bien=bien, designation=f"Lot {i:03d} · {l.nom_type}"[:100], type=type_lot,
                                       nb_pieces=pieces, surface=round(l.surface, 2) or None, meuble=l.meuble,
                                       etage=l.etage[:50], **_dpe_du_lot(dpe_par_lot.get(i)))
                s.bien = bien
                s.save(update_fields=["bien"])
            messages.success(request, f"Bien « {bien.nom} » créé avec {len(p.lots)} lot(s). Complétez la fiche si besoin.")
            return redirect(reverse("admin:patrimoine_bien_change", args=[bien.pk]))
    return _page(request, "simulateurs/creer_bien.html", SLUG_INVEST, simulation=s, p=p, rue=rue, cp=cp, ville=ville,
                 proprietaires=proprietaires)


def rentabilite_dvf(request):
    """Prix du marché (DVF) autour de l'adresse, en JSON pour la page."""
    type_local = "Maison" if request.GET.get("type") == "maison" else "Appartement"
    try:
        return JsonResponse(dvf.prix_du_marche(request.GET.get("adresse", "")[:255], type_local))
    except dvf.DvfIndisponible as e:
        return JsonResponse({"erreur": str(e)})


def rentabilite_dpe(request):
    """Vérification d'un DPE fourni par le vendeur, dans la base de l'ADEME, en JSON pour la page."""
    from patrimoine import dpe as regles

    numero = request.GET.get("numero", "")[:30]
    try:
        return JsonResponse(regles.verifier(numero, surface=request.GET.get("surface"), adresse=request.GET.get("adresse", "")[:255],
                                            classe_annoncee=request.GET.get("classe", "")[:1]))
    except regles.DpeIndisponible as e:
        valide = regles.numero_valide(numero)
        return JsonResponse({"erreur": str(e), "lien": regles.lien_observatoire(numero) if valide else ""})
