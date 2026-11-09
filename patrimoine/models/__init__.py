"""Modèles de l'application.

Rangé par métier : communs.py, biens.py, location.py, loyers.py, relocation.py, courte_duree.py, finances.py, outils.py."""
from .communs import _ajouter_mois, _chemin_piece, stockage_prive  # noqa: F401
from .biens import _chemin_photo, Bien, Lot, PhotoLot, Proprietaire, Syndic  # noqa: F401
from .location import (  # noqa: F401
    Bail,
    DocumentLocataire,
    EtatDesLieux,
    Garant,
    Locataire,
    mois_depot_maximum,
    RevisionLoyer,
    TYPES_GARANTIE,
)
from .loyers import Echeance, EncaissementHistorique, MouvementDepot, Paiement, Relance  # noqa: F401
from .relocation import (  # noqa: F401
    Annonce,
    Candidature,
    ChangementLocataire,
    GarantCandidature,
    ModeleAnnonce,
    PieceCandidature,
    Tache,
    TacheParDefaut,
)
from .courte_duree import ChargeLCD, ChargeLMNP, EncaissementLCD, ParametresLCD, ReservationLCD  # noqa: F401
from .finances import _chemin_facture, LigneEcheancier, PieceComptable, Pret  # noqa: F401
from .outils import JournalFait, ModeleDocument, NoteRapide, ParametresMail, Rappel, SiteUtile  # noqa: F401
