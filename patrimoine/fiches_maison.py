"""Fiches pratiques de « Ma maison » : entretien courant de la résidence principale, fréquences conseillées, obligations,
gestes à faire soi-même, signes d'alerte et papiers à garder. Chaque fiche peut créer en un clic le rappel qui va avec
(rattaché à la maison, catégorie Travaux / entretien, 📅 agenda, mail 7 jours avant).

Le contenu est écrit ici (pas en base) : ce sont des repères généraux. Les fréquences réglementaires peuvent varier
selon le règlement sanitaire du département ou la commune (SPANC) : la fiche le précise quand c'est le cas.
"""
from datetime import date

from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from .models import Rappel

# thèmes, dans l'ordre d'affichage
THEMES = [
    ("chauffage", "🔥", "Chauffage et eau chaude"),
    ("air", "🌬️", "Air et sécurité"),
    ("exterieur", "🏠", "Toiture et extérieur"),
    ("eau", "💧", "Eau et assainissement"),
]

# Chaque fiche : slug, thème, icône, titre, résumé, fréquence (texte), obligatoire (bool) + texte de l'obligation,
# qui (pro / vous), coût indicatif, meilleur moment, gestes (vous-même), alertes, papiers à garder,
# rappels proposés [(titre, périodicité, mois conseillé ou None)]. Les titres des rappels reprennent ceux des
# entretiens courants de maison.py, pour qu'un rappel déjà créé soit reconnu.
FICHES = [
    {
        "slug": "chaudiere", "theme": "chauffage", "icone": "🔥", "titre": "Chaudière",
        "resume": "Entretien annuel obligatoire des chaudières gaz, fioul ou bois, par un professionnel.",
        "frequence": "Tous les ans",
        "obligatoire": True, "obligation": "Obligatoire chaque année pour les chaudières de 4 à 400 kW (gaz, fioul, bois, "
                                          "charbon). C'est à l'occupant de le faire faire.",
        "qui": "Un chauffagiste. Un contrat d'entretien évite d'avoir à y penser et inclut souvent le dépannage.",
        "cout": "100 à 200 € la visite (contrat annuel : 120 à 250 €)",
        "moment": "Fin d'été ou début d'automne, avant de rallumer le chauffage : les chauffagistes sont plus disponibles.",
        "gestes": ["Vérifier la pression du circuit à froid : en général entre 1 et 1,5 bar (voir la notice).",
                   "Purger les radiateurs à l'automne s'ils sont froids en haut ou font des bruits d'eau.",
                   "Ne rien stocker contre la chaudière, ne pas boucher les aérations du local.",
                   "Installer un détecteur de monoxyde de carbone (CO) à proximité."],
        "alertes": ["Flamme jaune ou orange au lieu de bleue (gaz).", "Odeur inhabituelle, maux de tête en présence de la chaudière.",
                    "Pression qui baisse souvent, voyant ou code d'erreur.", "Traces noires autour de l'appareil."],
        "garder": "L'attestation d'entretien remise sous 15 jours : à garder au moins 2 ans. L'assureur peut la demander après un sinistre.",
        "rappels": [("Entretien de la chaudière", "annuel", 9)],
    },
    {
        "slug": "ramonage", "theme": "chauffage", "icone": "🧹", "titre": "Ramonage (cheminée, poêle, insert)",
        "resume": "Ramonage du conduit de fumée par un professionnel, une ou deux fois par an.",
        "frequence": "1 à 2 fois par an",
        "obligatoire": True, "obligation": "Obligatoire, la fréquence est fixée par le règlement sanitaire du département : "
                                          "souvent 2 fois par an pour le bois et le fioul (dont une pendant la période de chauffe), "
                                          "1 fois pour le gaz.",
        "qui": "Un ramoneur professionnel (pas de hérisson tout seul : seul le certificat du pro compte).",
        "cout": "50 à 90 € par conduit",
        "moment": "Avant l'hiver (septembre-octobre), et en fin d'hiver si deux ramonages sont demandés.",
        "gestes": ["Brûler du bois sec (moins de 20 % d'humidité) : moins de suie et de bistre.",
                   "Vider régulièrement le cendrier, nettoyer la vitre de l'insert à froid.",
                   "Ne jamais brûler de déchets, cartons ou bois traité."],
        "alertes": ["Le feu tire mal ou la fumée revient dans la pièce.", "Odeur de fumée dans les pièces voisines.",
                    "Dépôts noirs et brillants (bistre) dans le conduit."],
        "garder": "Le certificat de ramonage : sans lui, l'assurance peut réduire l'indemnisation en cas d'incendie.",
        "rappels": [("Ramonage du conduit", "annuel", 9)],
    },
    {
        "slug": "pompe-a-chaleur", "theme": "chauffage", "icone": "❄️", "titre": "Pompe à chaleur et climatisation",
        "resume": "Contrôle par un pro au moins tous les 2 ans, filtres à nettoyer vous-même plusieurs fois par an.",
        "frequence": "Tous les 2 ans (pro), filtres tous les 1 à 3 mois en saison",
        "obligatoire": True, "obligation": "Entretien obligatoire au moins tous les 2 ans pour les pompes à chaleur et "
                                          "climatisations de 4 à 70 kW. Contrôle d'étanchéité du fluide selon la quantité de gaz.",
        "qui": "Un frigoriste ou chauffagiste avec attestation de capacité « fluides frigorigènes ».",
        "cout": "150 à 250 € la visite",
        "moment": "Au printemps pour la clim, en fin d'été pour une pompe à chaleur de chauffage.",
        "gestes": ["Nettoyer les filtres des unités intérieures (aspirateur puis eau tiède) tous les 1 à 3 mois en saison.",
                   "Dégager l'unité extérieure : feuilles, neige, plantes à au moins 50 cm.",
                   "Vérifier que l'eau de condensation s'écoule bien."],
        "alertes": ["Moins de chaud ou de froid qu'avant, givre persistant sur l'unité extérieure.",
                    "Bruits nouveaux, odeur de moisi à la mise en route.", "Consommation électrique en hausse."],
        "garder": "L'attestation d'entretien (2 ans au moins) et la fiche d'intervention sur le fluide.",
        "rappels": [("Entretien pompe à chaleur / clim", "biennal", 4)],
    },
    {
        "slug": "chauffe-eau", "theme": "chauffage", "icone": "🚿", "titre": "Chauffe-eau (ballon électrique)",
        "resume": "Un geste par mois sur le groupe de sécurité, un détartrage tous les 2 à 3 ans.",
        "frequence": "Groupe de sécurité : tous les mois · détartrage : tous les 2 à 3 ans",
        "obligatoire": False, "obligation": "",
        "qui": "Vous pour le groupe de sécurité ; un plombier pour le détartrage et le contrôle de l'anode.",
        "cout": "100 à 200 € le détartrage",
        "moment": "N'importe quand ; plutôt l'été pour un détartrage (coupure d'eau chaude).",
        "gestes": ["Actionner le groupe de sécurité (la petite manette sous le ballon) une fois par mois quelques secondes.",
                   "Régler la température vers 55-60 °C : assez chaud contre les bactéries (légionelle), pas trop pour le calcaire.",
                   "Chauffer en heures creuses si votre contrat le permet."],
        "alertes": ["Eau qui goutte en permanence au groupe de sécurité.", "Eau chaude moins longtemps qu'avant, bruits de crépitement.",
                    "Eau tiède ou rouillée."],
        "garder": "La facture d'installation (garantie de la cuve souvent 5 ans ou plus).",
        "rappels": [("Détartrage du chauffe-eau", "biennal", 7)],
    },
    {
        "slug": "vmc", "theme": "air", "icone": "🌀", "titre": "VMC (ventilation)",
        "resume": "Bouches à nettoyer tous les 3 à 6 mois, filtres de double flux à changer, caisson vérifié par un pro.",
        "frequence": "Bouches : tous les 3 à 6 mois · pro : tous les 3 ans environ",
        "obligatoire": False, "obligation": "",
        "qui": "Vous pour les bouches et les filtres ; un pro pour le moteur et les gaines.",
        "cout": "80 à 200 € la visite d'un pro",
        "moment": "À l'automne, avant la saison où l'on aère le moins.",
        "gestes": ["Démonter les bouches d'extraction (cuisine, salle de bains, WC), les laver à l'eau savonneuse.",
                   "VMC double flux : changer ou nettoyer les filtres tous les 6 à 12 mois.",
                   "Ne jamais boucher les entrées d'air des fenêtres ni couper la VMC, même en hiver.",
                   "Test simple : une feuille de papier doit rester collée à la bouche d'extraction."],
        "alertes": ["Buée sur les vitres, moisissures dans les angles.", "Odeurs qui persistent, bruit ou vibration du moteur."],
        "garder": "",
        "rappels": [("Entretien de la VMC", "annuel", 10)],
    },
    {
        "slug": "detecteurs", "theme": "air", "icone": "🚨", "titre": "Détecteurs de fumée et de CO",
        "resume": "Au moins un détecteur de fumée par logement : à tester chaque mois, à remplacer tous les 10 ans.",
        "frequence": "Test : tous les mois · piles : tous les ans (sauf modèle 10 ans)",
        "obligatoire": True, "obligation": "Au moins un détecteur de fumée (norme NF EN 14604) obligatoire dans chaque logement, "
                                          "de préférence dans le couloir des chambres.",
        "qui": "Vous.",
        "cout": "15 à 30 € le détecteur (10 ans de pile)",
        "moment": "Test le 1er du mois ; piles au changement d'heure en octobre, par exemple.",
        "gestes": ["Appuyer sur le bouton test une fois par mois.", "Dépoussiérer l'appareil (aspirateur) deux fois par an.",
                   "Remplacer l'appareil à la date indiquée au dos (en général 10 ans).",
                   "Avec une chaudière, un poêle ou un insert : ajouter un détecteur de monoxyde de carbone (CO)."],
        "alertes": ["Bip court toutes les minutes : pile faible.", "Pas de son au test : à remplacer tout de suite."],
        "garder": "",
        "rappels": [("Piles des détecteurs de fumée", "annuel", 10)],
    },
    {
        "slug": "electricite", "theme": "air", "icone": "⚡", "titre": "Tableau électrique",
        "resume": "Tester les interrupteurs différentiels deux fois par an, repérer les circuits.",
        "frequence": "Tous les 6 mois",
        "obligatoire": False, "obligation": "",
        "qui": "Vous pour le test ; un électricien pour toute anomalie.",
        "cout": "",
        "moment": "Au printemps et à l'automne.",
        "gestes": ["Appuyer sur le bouton « T » de chaque interrupteur différentiel 30 mA : il doit couper, puis le remonter.",
                   "Étiqueter chaque disjoncteur (cuisine, chambres, chauffe-eau…).",
                   "Ne jamais remplacer un disjoncteur par un calibre plus fort."],
        "alertes": ["Disjoncteur qui saute souvent.", "Odeur de brûlé, prise ou tableau qui chauffe.",
                    "Installation de plus de 15 ans jamais vérifiée : un diagnostic électrique peut valoir le coup."],
        "garder": "",
        "rappels": [("Tester le disjoncteur différentiel", "semestriel", 4)],
    },
    {
        "slug": "toiture-gouttieres", "theme": "exterieur", "icone": "🍂", "titre": "Toiture et gouttières",
        "resume": "Gouttières à vider une à deux fois par an, toiture à regarder chaque année et après chaque tempête.",
        "frequence": "Gouttières : 1 à 2 fois par an · toiture : coup d'œil chaque année, démoussage tous les 5 à 10 ans",
        "obligatoire": False, "obligation": "",
        "qui": "Vous depuis le sol ou une échelle stable (jamais seul sur un toit) ; un couvreur pour les tuiles et le démoussage.",
        "cout": "Nettoyage des gouttières : 100 à 300 € · démoussage : 15 à 40 € le m²",
        "moment": "En fin d'automne après la chute des feuilles, et au printemps.",
        "gestes": ["Vider les feuilles et mousses des gouttières, vérifier que les descentes ne sont pas bouchées.",
                   "Regarder la toiture aux jumelles : tuiles cassées ou déplacées, faîtage, solins autour de la cheminée.",
                   "Après une tempête : photos et déclaration à l'assureur dans les 5 jours ouvrés (30 jours après l'arrêté "
                   "en cas de catastrophe naturelle).",
                   "Élaguer les branches qui touchent le toit."],
        "alertes": ["Tache d'humidité au plafond ou dans les combles.", "Eau qui déborde des gouttières quand il pleut.",
                    "Tuiles au sol, mousse épaisse."],
        "garder": "Les factures du couvreur : garantie décennale pendant 10 ans pour une réfection.",
        "rappels": [("Nettoyage des gouttières", "annuel", 11), ("Contrôle de la toiture", "annuel", 4)],
    },
    {
        "slug": "robinets-gel", "theme": "eau", "icone": "🧊", "titre": "Plomberie, fuites et gel",
        "resume": "Robinets extérieurs à purger avant l'hiver, robinet d'arrêt à manœuvrer, fuites à repérer au compteur.",
        "frequence": "Tous les ans avant l'hiver",
        "obligatoire": False, "obligation": "",
        "qui": "Vous.",
        "cout": "",
        "moment": "Fin octobre, avant les premières gelées.",
        "gestes": ["Fermer et purger les robinets extérieurs, ranger les tuyaux d'arrosage.",
                   "Ouvrir et fermer le robinet d'arrêt général une ou deux fois par an pour qu'il ne se bloque pas.",
                   "Chercher une fuite : tout fermer, relever le compteur, le relire 2 heures plus tard sans tirer d'eau.",
                   "Calorifuger les tuyaux dans les pièces non chauffées (garage, vide sanitaire)."],
        "alertes": ["Le compteur tourne alors que tout est fermé.", "Facture d'eau en hausse sans raison.",
                    "Chasse d'eau qui coule en continu."],
        "garder": "",
        "rappels": [("Robinets extérieurs avant l'hiver", "annuel", 10)],
    },
    {
        "slug": "adoucisseur", "theme": "eau", "icone": "🧂", "titre": "Adoucisseur d'eau",
        "resume": "Sel à recharger régulièrement, désinfection de la résine une fois par an.",
        "frequence": "Sel : tous les 1 à 2 mois · désinfection : tous les ans",
        "obligatoire": False, "obligation": "",
        "qui": "Vous pour le sel et la désinfection ; un pro tous les 1 à 2 ans pour le réglage.",
        "cout": "Sel : 5 à 10 € le sac de 25 kg · visite : 100 à 150 €",
        "moment": "N'importe quand.",
        "gestes": ["Garder le bac à sel au moins à moitié plein (pastilles spéciales adoucisseur).",
                   "Désinfecter la résine une fois par an (produit vendu en magasin de bricolage).",
                   "Garder un peu de calcaire : une dureté réglée vers 8 à 15 °f protège les tuyaux.",
                   "Laisser un robinet d'eau non adoucie pour boire, si possible."],
        "alertes": ["Traces de calcaire qui reviennent.", "Le sel ne baisse plus dans le bac (voûte de sel à casser).",
                    "Eau au goût salé."],
        "garder": "",
        "rappels": [("Entretien de l'adoucisseur", "annuel", None)],
    },
    {
        "slug": "fosse-septique", "theme": "eau", "icone": "🚽", "titre": "Fosse septique et SPANC",
        "resume": "Vidange quand les boues atteignent la moitié du volume (souvent tous les 4 ans), contrôle de la commune.",
        "frequence": "Vidange : souvent tous les 4 ans · SPANC : selon la commune, au plus tous les 10 ans",
        "obligatoire": True, "obligation": "Vidange obligatoire par un vidangeur agréé dès que les boues atteignent 50 % du "
                                          "volume. Contrôle périodique du service public d'assainissement non collectif (SPANC), "
                                          "à la fréquence fixée par la commune (10 ans au plus).",
        "qui": "Un vidangeur agréé par la préfecture.",
        "cout": "200 à 400 € la vidange",
        "moment": "Au printemps ou en été (terrain sec, camion plus facile à approcher).",
        "gestes": ["Nettoyer le bac dégraisseur tous les 6 mois s'il y en a un.",
                   "Pas de lingettes, huiles, peintures ni désherbants dans les canalisations.",
                   "Javel et produits d'entretien avec modération : ils tuent les bactéries de la fosse.",
                   "Ne pas rouler ni planter d'arbres sur la fosse et l'épandage."],
        "alertes": ["Mauvaises odeurs, évacuations lentes, gargouillis.", "Herbe très verte ou sol humide au-dessus de l'épandage."],
        "garder": "Le bordereau de suivi des matières de vidange, et le rapport du SPANC (à fournir en cas de vente).",
        "rappels": [("Vidange de la fosse septique", "quadriennal", 5), ("Contrôle SPANC (assainissement)", "decennal", None)],
    },
]

PAR_SLUG = {f["slug"]: f for f in FICHES}

# rappel (titre en minuscules) → fiche, pour relier les entretiens de « Ma maison » à leur fiche
FICHE_DU_RAPPEL = {titre.lower(): f for f in FICHES for titre, _, _ in f["rappels"]}

# conseils généraux : calendrier des saisons, économies, papiers, assurance
SAISONS = [
    ("🍂", "Automne", ["Chaudière et ramonage avant de rallumer le chauffage", "Purger les radiateurs",
                       "Vider les gouttières après la chute des feuilles", "Purger les robinets extérieurs avant le gel",
                       "Nettoyer les bouches de VMC", "Changer les piles des détecteurs"]),
    ("❄️", "Hiver", ["Surveiller la pression de la chaudière", "Aérer 5 à 10 minutes par jour, même quand il fait froid",
                     "Regarder la toiture après chaque tempête", "Dégager l'unité extérieure de la pompe à chaleur (neige, feuilles)"]),
    ("🌱", "Printemps", ["Entretien de la clim avant l'été", "Coup d'œil à la toiture et deuxième nettoyage des gouttières",
                         "Tester les interrupteurs différentiels", "Repérer les fissures de façade et les joints de fenêtres"]),
    ("☀️", "Été", ["Prendre rendez-vous avec le chauffagiste et le ramoneur (ils sont plus disponibles)",
                   "Détartrer le chauffe-eau, vidanger la fosse si besoin", "Élaguer les arbres proches du toit"]),
]

CONSEILS = [
    ("💶", "Faire baisser les factures", [
        "Chauffage : 19 °C dans les pièces de vie, 16 à 17 °C dans les chambres. Un degré de moins, c'est environ 7 % de chauffage en moins.",
        "Un thermostat programmable baisse la température la nuit et en votre absence.",
        "Chauffe-eau en heures creuses, à 55-60 °C.",
        "Joints de portes et fenêtres, bas de porte : peu cher et vite rentabilisé.",
        "Comparer sa consommation d'une année sur l'autre : le tableau de « Ma maison » montre l'écart par catégorie.",
    ]),
    ("📁", "Papiers à garder", [
        "Attestations d'entretien de la chaudière et de la pompe à chaleur : au moins 2 ans.",
        "Certificats de ramonage : ils comptent pour l'assurance en cas d'incendie.",
        "Factures de gros travaux (toiture, charpente, gros œuvre) : 10 ans, la durée de la garantie décennale.",
        "Ajoutez chaque facture dans « Ma maison » (PDF ou photo) : tout est rangé au même endroit.",
    ]),
    ("🛡️", "Assurance habitation", [
        "Relire ses garanties une fois par an : valeur à neuf, franchise, dépendances, objets de valeur.",
        "Prévenir l'assureur après des travaux qui changent la maison (véranda, piscine, pièce en plus).",
        "Après un an de contrat, elle peut être résiliée à tout moment : le nouvel assureur s'occupe des démarches.",
        "Comparer chaque année avec l'échéance : le montant payé figure dans « Ma maison » (catégorie assurance).",
    ]),
]


def _prochaine_date(mois, aujourdhui):
    """1er du mois conseillé à venir (au moins 15 jours plus tard), sinon dans un mois."""
    if not mois:
        mois_suivant = aujourdhui.month % 12 + 1
        return date(aujourdhui.year + (aujourdhui.month == 12), mois_suivant, 1)
    d = date(aujourdhui.year, mois, 1)
    if (d - aujourdhui).days < 15:
        d = date(aujourdhui.year + 1, mois, 1)
    return d


def url_rappel(bien, titre, periodicite, mois=None, slug=None, retour=None, aujourdhui=None):
    """Lien qui ouvre le rappel pré-rempli pour la maison (titre, périodicité, mois conseillé, 📅 agenda, mail 7 jours avant)."""
    aujourdhui = aujourdhui or timezone.localdate()
    params = {"titre": titre, "bien": bien.pk, "categorie": Rappel.Categorie.TRAVAUX, "periodicite": periodicite,
              "date": _prochaine_date(mois, aujourdhui).isoformat(), "dans_agenda": "on", "prevenir_jours": 7,
              "next": retour or reverse("ma_maison") + f"?bien={bien.pk}"}
    if slug:
        params["notes"] = "Fiche pratique : " + reverse("maison_fiche", args=[slug])
    return reverse("admin:patrimoine_rappel_add") + "?" + urlencode(params)


def rappels_de_la_fiche(fiche, bien, aujourdhui=None):
    """Pour chaque rappel proposé par la fiche : le rappel déjà suivi pour la maison (le plus proche) ou le lien pour le créer."""
    lignes = []
    for titre, periodicite, mois in fiche["rappels"]:
        ligne = {"titre": titre, "periodicite": Rappel.Periodicite(periodicite).label, "rappel": None, "url": None}
        if bien is not None:
            existants = Rappel.objects.filter(bien=bien, titre__iexact=titre).order_by("fait", "-actif", "date")
            ligne["rappel"] = existants.first()
            if ligne["rappel"] is None:
                retour = reverse("maison_fiche", args=[fiche["slug"]]) + f"?bien={bien.pk}"
                ligne["url"] = url_rappel(bien, titre, periodicite, mois, fiche["slug"], retour, aujourdhui)
        lignes.append(ligne)
    return lignes


def suivis(bien):
    """Slugs des fiches dont au moins un rappel est déjà suivi (actif) pour la maison."""
    if bien is None:
        return set()
    titres = {t.strip().lower() for t in Rappel.objects.filter(bien=bien, actif=True, fait=False).values_list("titre", flat=True)}
    return {f["slug"] for f in FICHES if any(t.lower() in titres for t, _, _ in f["rappels"])}


def par_theme(bien=None):
    """Fiches groupées par thème pour la page de liste, avec « suivi » quand un rappel existe déjà."""
    deja = suivis(bien)
    groupes = []
    for code, icone, libelle in THEMES:
        fiches = [{**f, "suivi": f["slug"] in deja} for f in FICHES if f["theme"] == code]
        if fiches:
            groupes.append({"code": code, "icone": icone, "libelle": libelle, "fiches": fiches})
    return groupes
