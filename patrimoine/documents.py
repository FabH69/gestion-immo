"""Documents pré-remplis : bail et état des lieux."""

ELEMENTS_PIECE = [
    "Sol", "Murs", "Plafond", "Porte(s)", "Fenêtres / vitrages", "Volets / stores",
    "Prises et interrupteurs", "Éclairage", "Chauffage / radiateur", "Équipements spécifiques",
]

INVENTAIRE_MEUBLE = [
    "Literie avec couette ou couverture", "Dispositif d'occultation des fenêtres (chambres)",
    "Plaques de cuisson", "Four ou four à micro-ondes", "Réfrigérateur avec compartiment congélation",
    "Vaisselle (assiettes, verres…)", "Ustensiles de cuisine", "Table", "Sièges",
    "Étagères de rangement", "Luminaires", "Matériel d'entretien ménager",
]


def duree_texte(bail):
    mois = bail.duree_mois
    if mois % 12 == 0:
        ans = mois // 12
        return f"{ans} an{'s' if ans > 1 else ''}"
    return f"{mois} mois"
