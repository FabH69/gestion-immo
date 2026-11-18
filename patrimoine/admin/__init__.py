"""Écrans de l'administration.

Rangé par métier : communs.py, biens.py, location.py, loyers.py, relocation.py, courte_duree.py, finances.py, outils.py."""
from .communs import (  # noqa: F401
    _changeform_view_django,
    _changeform_view_libelles,
    _changelist_view_django,
    _changelist_view_titre_simple,
    _get_inline_formsets_django,
    _get_inline_formsets_libelles,
    _message_user_django,
    _message_user_libelles,
    euros_liste,
    LotsALouerMixin,
    PLUSIEURS_D_AFFILEE,
)
from .biens import (  # noqa: F401
    BailInline,
    BienAdmin,
    BienInline,
    LotAdmin,
    LotInline,
    PhotoInline,
    ProprietaireAdmin,
    SyndicAdmin,
    VenduFilter,
)
from .location import (  # noqa: F401
    BailAdmin,
    BailEnCoursFilter,
    BailForm,
    DocumentLocataireInline,
    EtatDesLieuxAdmin,
    GarantActifFilter,
    GarantAdmin,
    GarantBailInline,
    GarantInline,
    LocataireAdmin,
    LocataireForm,
    MouvementDepotInline,
    PresentFilter,
)
from .loyers import (  # noqa: F401
    BailEcheanceFilter,
    DepotEnCoursFilter,
    EcheanceAdmin,
    EcheanceStatutFilter,
    EncaissementHistoriqueAdmin,
    EnRetardFilter,
    ModifierEcheancesForm,
    MouvementDepotAdmin,
    PaiementInline,
    RevisionLoyerAdmin,
)
from .relocation import (  # noqa: F401
    AnnonceAdmin,
    CandidatureAdmin,
    ChangementLocataireAdmin,
    GarantCandidatureInline,
    ModeleAnnonceAdmin,
    PieceCandidatureInline,
    TacheActiveFilter,
    TacheAdmin,
    TacheEnRetardFilter,
    TacheEntreeInline,
    TacheInlineBase,
    TacheParDefautAdmin,
    TacheSortieInline,
)
from .courte_duree import ChargeLCDAdmin, ChargeLMNPAdmin, ParametresLCDAdmin, ReservationLCDAdmin  # noqa: F401
from .finances import (  # noqa: F401
    ACodifierFilter,
    AnneeFiscaleFilter,
    BienPieceFilter,
    ChampFichiersMultiples,
    EntreeFichiersMultiples,
    ImportFacturesForm,
    PieceComptableAdmin,
    PieceComptableForm,
    PretAdmin,
)
from .outils import ModeleDocumentAdmin, ParametresMailAdmin, RappelAdmin, SiteUtileAdmin  # noqa: F401
