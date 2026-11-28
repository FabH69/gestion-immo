"""Vente d'un bien : remboursements de l'acquéreur (prorata de taxe foncière, de charges de copropriété, autres).

À la vente, le notaire reverse au vendeur le prix et, en plus, la part de taxe foncière et de charges de copropriété que
le vendeur a payée pour la période après la vente. Règle fiscale :
- revenus fonciers (2044, 2072) : le vendeur déduit la taxe foncière et les charges payées, moins le prorata remboursé ;
- LMNP, SARL de famille, société à l'IS : le remboursement diminue la charge de l'exercice de la vente.
Ce n'est ni un loyer ni une partie du prix de cession.

Les montants se saisissent sur la fiche du bien (zone « Remboursements de l'acquéreur ») ; chacun devient une pièce
comptable négative, datée de la vente, sur la ligne (ou la rubrique) de la charge qu'il rembourse. Les déclarations la
déduisent sans rien faire de plus. Les « autres remboursements » restent « à classer » : on choisit leur ligne."""
from decimal import Decimal

ZERO = Decimal("0.00")

# (champ du bien, libellé, ligne 2044/2072, rubrique LMNP/société, catégorie de la maison)
REMBOURSEMENTS = [
    ("tf", "remb_taxe_fonciere", "Remboursement du prorata de taxe foncière", "12", "taxe_fonciere", "taxe_fonciere"),
    ("copro", "remb_copro", "Remboursement du prorata de charges de copropriété", "13", "copro", "autre"),
    ("autre", "remb_autres", "Autre remboursement de l'acquéreur", "", "", "autre"),
]
FOURNISSEUR = "Acquéreur (vente du bien)"


def _codification(bien, ligne, rubrique, maison):
    if bien.est_maison:
        return {"rubrique": maison, "ligne": ""}
    if bien.comptabilite in ("lmnp", "societe"):
        return {"rubrique": rubrique, "ligne": ""}
    return {"ligne": ligne, "rubrique": ""}


def synchroniser(bien):
    """Crée, met à jour ou supprime les pièces des remboursements d'après la fiche du bien.
    Rend la liste des messages à afficher : (niveau, texte) avec niveau « info » ou « warning »."""
    from .models import PieceComptable

    messages = []
    existantes = {p.remboursement_vente: p for p in PieceComptable.objects.filter(bien=bien).exclude(remboursement_vente="")}
    saisis = any(getattr(bien, champ) for _, champ, *_ in REMBOURSEMENTS)
    if saisis and bien.vendu and not bien.date_vente:
        messages.append(("warning", "Remboursements de l'acquéreur non comptés : indiquez la date de vente, elle fixe l'année "
                                    "où ils sont déduits des charges."))
    for code, champ, libelle, ligne, rubrique, maison in REMBOURSEMENTS:
        montant = getattr(bien, champ)
        piece = existantes.get(code)
        voulu = bien.vendu and bien.date_vente and montant
        if piece and piece.est_verrouillee():
            if not voulu or piece.montant != -abs(montant) or piece.date != bien.date_vente:
                messages.append(("warning", f"{libelle} : non modifié ({piece.motif_verrou()}). {piece.comment_deverrouiller()}"))
            continue
        if not voulu:
            if piece:
                piece.delete()
            continue
        if code == "autre" and bien.remb_autres_libelle:
            libelle = f"{libelle} : {bien.remb_autres_libelle}"
        valeurs = {"date": bien.date_vente, "montant": -abs(montant), "fournisseur": FOURNISSEUR,
                   "libelle": f"{libelle} – vente du {bien.date_vente:%d/%m/%Y}"[:200]}
        if piece is None:
            piece = PieceComptable(bien=bien, remboursement_vente=code, notes="Créée depuis la fiche du bien vendu "
                                   "(remboursements de l'acquéreur). Se modifie sur la fiche du bien.",
                                   **_codification(bien, ligne, rubrique, maison))
            for k, v in valeurs.items():
                setattr(piece, k, v)
            if piece.motif_verrou():
                messages.append(("warning", f"{libelle} : non compté ({piece.motif_verrou()}). {piece.comment_deverrouiller()}"))
                continue
            piece.save()
            if code == "autre" and not bien.est_maison:
                messages.append(("info", "Autre remboursement de l'acquéreur ajouté aux factures « à classer » : choisissez la "
                                         "charge qu'il rembourse (sa ligne ou sa rubrique) pour qu'il soit déduit."))
        else:
            for k, v in valeurs.items():
                setattr(piece, k, v)
            piece.save()
    return messages


def pieces(bien):
    from .models import PieceComptable

    return list(PieceComptable.objects.filter(bien=bien).exclude(remboursement_vente="").order_by("pk"))


def prix_connu(bien):
    """Prix de vente : celui de la fiche, sinon la somme des prix de cession des immobilisations sorties pour la vente."""
    if bien.prix_vente is not None:
        return bien.prix_vente
    prix = [i.prix_cession for i in bien.immobilisations.all() if i.motif_sortie == "vente_bien" and i.prix_cession is not None]
    return sum(prix, ZERO) if prix else None


def ventilation(bien, montant):
    """Répartition d'un virement du notaire : [(libellé, montant)], prix en premier (le reste du virement), et l'écart
    avec le prix connu (None si pas de prix connu ou si tout tombe juste)."""
    parts = [(libelle.replace("Remboursement du prorata", "Prorata").replace("Autre remboursement de l'acquéreur", "Autres remboursements"),
              abs(getattr(bien, champ))) for _, champ, libelle, *_ in REMBOURSEMENTS if getattr(bien, champ)]
    prix = montant - sum((m for _, m in parts), ZERO)
    connu = prix_connu(bien)
    ecart = (connu - prix) if connu is not None and connu != prix else None
    return [("Prix de vente", prix)] + parts, ecart


MOTS_NOTAIRE = {"NOTAIRE", "NOTAIRES", "NOT", "NOTARIAL", "NOTARIALE", "SCP", "SELARL", "ETUDE", "OFFICE", "CDC", "CARPA"}


def biens_vendus_proches(biens, jour, jours=120):
    return [b for b in biens if b.vendu and b.date_vente and abs((b.date_vente - jour).days) <= jours]


def texte_ventilation(parts):
    return " + ".join(f"{libelle} {m:,.2f} €".replace(",", " ").replace(".", ",") for libelle, m in parts)
