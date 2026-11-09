"""Documents du bail : bail, états des lieux, inventaire, reçus de dépôt, acte de cautionnement, modèles Word."""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from ..documents import ELEMENTS_PIECE, INVENTAIRE_MEUBLE, duree_texte
from ..models import Bail, EtatDesLieux, Garant, MouvementDepot
from .communs import _contexte_bail, _pdf, _reponse_docx


@staff_member_required
def document_bail(request, pk):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    contexte = _contexte_bail(bail)
    contexte.update(duree_texte=duree_texte(bail), nb_exemplaires=1 + len(contexte["locataires"]))
    return _pdf(render_to_string("patrimoine/bail.html", contexte), f"bail {bail.lot} {bail.date_debut:%Y-%m-%d}")


def _rendu_edl(bail, edl):
    contexte = _contexte_bail(bail)
    contexte.update(
        edl=edl,
        pieces=bail.lot.liste_pieces(),
        elements=ELEMENTS_PIECE,
        meuble=bail.lot.meuble or bail.type in (Bail.Type.MEUBLE, Bail.Type.ETUDIANT, Bail.Type.MOBILITE),
        inventaire=INVENTAIRE_MEUBLE,
    )
    return _pdf(render_to_string("patrimoine/etat_des_lieux.html", contexte), f"etat des lieux {edl.get_type_display()} {bail.lot}")


@staff_member_required
def document_etat_des_lieux(request, pk):
    edl = get_object_or_404(EtatDesLieux.objects.select_related("bail__lot__bien__proprietaire"), pk=pk)
    return _rendu_edl(edl.bail, edl)


@staff_member_required
def document_etat_des_lieux_vierge(request, pk, type_edl):
    """Fiche pré-remplie depuis le bail, sans état des lieux enregistré (à imprimer avant le rendez-vous)."""
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    edl = EtatDesLieux(bail=bail, type=type_edl, date=None)
    edl.reprendre_index_du_bail()
    return _rendu_edl(bail, edl)


@staff_member_required
def document_inventaire(request, pk, type_edl):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    contexte = _contexte_bail(bail)
    contexte.update(type_edl=type_edl, pieces=bail.lot.liste_pieces(), inventaire=INVENTAIRE_MEUBLE, lignes_libres=range(5))
    return _pdf(render_to_string("patrimoine/inventaire.html", contexte), f"inventaire {type_edl} {bail.lot}")


@staff_member_required
def document_recu_depot(request, pk):
    m = get_object_or_404(MouvementDepot.objects.select_related("bail__lot__bien__proprietaire"), pk=pk, type=MouvementDepot.Type.ENCAISSEMENT)
    contexte = _contexte_bail(m.bail)
    contexte.update(m=m, aujourdhui=timezone.localdate())
    return _pdf(render_to_string("patrimoine/recu_depot.html", contexte), f"recu depot garantie {m.bail.lot}")


CIVILITES_LONGUES = {"M.": "Monsieur", "Mme": "Madame"}


def _montant_fr(montant):
    texte = f"{montant:,.2f}".replace(",", " ").replace(".", ",")
    return texte[:-3] if texte.endswith(",00") else texte


def _recu_depot_word(bail, montant):
    from .. import champs
    from ..models import ModeleDocument
    from ..word import remplir_docx

    locataires = list(bail.locataires.all())
    valeurs = champs.valeurs(bail)
    if len(locataires) > 1:
        valeurs.update({"Civilité_Loc": "Madame, Monsieur", "Prénom_Loc": "",
                        "Nom_Loc": " et ".join(f"{l.prenom} {l.nom}".strip() for l in locataires)})
    valeurs.update({"Dépôt_Gar": _montant_fr(montant), "Dépôt_Gar_lettres": champs.euros_en_lettres(montant)})
    modele = ModeleDocument.objects.filter(type="recu_depot", actif=True).order_by("-modifie_le").first()
    contenu = remplir_docx(modele.fichier.open("rb") if modele else "recu_depot_garantie.docx", valeurs)
    return _reponse_docx(contenu, f"Reçu dépôt de garantie {bail.lot} {valeurs['Nom_Loc']}")


@staff_member_required
def remplir_modele(request, pk, bail_pk):
    """Modèle Word de l'utilisateur rempli avec les données du bail (et du type d'état des lieux demandé)."""
    from .. import champs
    from ..models import ModeleDocument
    from ..word import remplir_docx

    modele = get_object_or_404(ModeleDocument, pk=pk)
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=bail_pk)
    type_edl = request.GET.get("edl") if request.GET.get("edl") in ("entree", "sortie") else None
    with modele.fichier.open("rb") as f:
        contenu = remplir_docx(f, champs.valeurs(bail, type_edl=type_edl))
    noms = " ".join(l.nom for l in bail.locataires.all())
    suffixe = {"entree": " entrée", "sortie": " sortie"}.get(type_edl, "")
    return _reponse_docx(contenu, f"{modele.nom}{suffixe} {bail.lot} {noms}".strip())


@staff_member_required
def liste_champs(request):
    """Modèles › Liste des champs : nom à écrire entre accolades, description, et valeur pour un bail choisi."""
    from .. import champs
    from ..word import normaliser

    baux = Bail.objects.select_related("lot__bien").prefetch_related("locataires").order_by("-actif", "lot__bien__nom")
    bail = baux.filter(pk=request.GET.get("bail")).first() if request.GET.get("bail") else baux.filter(actif=True).first()
    exemple = {normaliser(k): v for k, v in champs.valeurs(bail).items()} if bail else {}
    groupes = [(titre, [(nom, desc, exemple.get(normaliser(nom), "")) for nom, desc in liste]) for titre, liste in champs.CHAMPS]
    return render(request, "patrimoine/liste_champs.html", {
        **admin.site.each_context(request), "title": "Champs des modèles Word", "groupes": groupes, "baux": baux, "bail": bail})


@staff_member_required
def recu_depot_mouvement(request, pk):
    m = get_object_or_404(MouvementDepot.objects.select_related("bail__lot__bien__proprietaire"), pk=pk, type=MouvementDepot.Type.ENCAISSEMENT)
    return _recu_depot_word(m.bail, m.montant)


@staff_member_required
def recu_depot_bail(request, pk):
    bail = get_object_or_404(Bail.objects.select_related("lot__bien__proprietaire"), pk=pk)
    return _recu_depot_word(bail, bail.depot_garantie)


@staff_member_required
def acte_cautionnement(request, pk):
    garant = get_object_or_404(Garant.objects.select_related("locataire", "bail__lot__bien__proprietaire"), pk=pk)
    if not garant.bail:
        messages.error(request, "Rattachez d'abord le garant à un bail pour générer l'acte de cautionnement.")
        return redirect("admin:patrimoine_garant_change", garant.pk)
    contexte = _contexte_bail(garant.bail)
    contexte.update(g=garant, montant=garant.montant_garanti(), duree_texte=duree_texte(garant.bail))
    return _pdf(render_to_string("patrimoine/acte_cautionnement.html", contexte), f"acte cautionnement {garant}")
