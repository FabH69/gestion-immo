"""Pages de l'application hors listes et fiches de l'administration.

Rangé par métier : communs.py, loyers.py, documents.py, relocation.py, courte_duree.py, finances.py, compta.py, outils.py."""
from .communs import _contexte_bail, _etat_csv, _pdf, _reponse_docx, annees_disponibles  # noqa: F401
from .loyers import (  # noqa: F401
    _dernier_irl_publie,
    _mail_revision,
    _niveau_relance,
    _noms_locataires,
    _pdf_relance,
    document_echeance,
    envoyer_quittance,
    lettre_relance,
    lettre_revision,
    MOIS_FR,
    pdf_echeance,
    preparer_mail_quittance,
    preparer_mail_relance,
    relances_impayes,
    revision_loyer,
)
from .documents import (  # noqa: F401
    _montant_fr,
    _recu_depot_word,
    _rendu_edl,
    acte_cautionnement,
    CIVILITES_LONGUES,
    document_bail,
    document_etat_des_lieux,
    document_etat_des_lieux_vierge,
    document_inventaire,
    document_recu_depot,
    liste_champs,
    recu_depot_bail,
    recu_depot_mouvement,
    remplir_modele,
)
from .relocation import (  # noqa: F401
    _verifier_pieces,
    annonce_pdf,
    candidature_publique,
    envoyer_lien_candidature,
    photos_zip,
    preparer_mail_candidature,
)
from .courte_duree import _comparaison_lcd, suivi_lcd  # noqa: F401
from .finances import (  # noqa: F401
    _decimal_saisi,
    annexe_2072,
    biens_lmnp,
    document_amortissement,
    etat_endettement,
    etat_loyers_detail,
    etat_loyers_encaisses,
    plus_value,
    suivi_encaissements,
)
from .compta import compta_recap, conciergerie_ajouter, controle_conciergerie, recap_2044  # noqa: F401
from .outils import (  # noqa: F401
    _lieu,
    enregistrer_note,
    fichier_prive,
    ne_plus_rappeler,
    rappel_fait,
    recherche,
    sauvegardes,
    tache_faite,
    telecharger_sauvegarde,
    tester_mail,
)
