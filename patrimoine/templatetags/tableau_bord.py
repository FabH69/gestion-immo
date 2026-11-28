from django import template
from django.utils import timezone

from patrimoine.rappels import a_faire

register = template.Library()


@register.inclusion_tag("patrimoine/_a_faire.html", takes_context=True)
def bloc_a_faire(context):
    """Accueil : le résumé de la page À faire, une ligne par type (le détail est sur la page À faire)."""
    aujourdhui = timezone.localdate()
    relocation = bool((context.get("droits") or {}).get("relocation"))
    items = a_faire(aujourdhui, avec_taches=relocation)
    genres = (context.get("choix_accueil") or {}).get("a_faire")
    if genres is not None:  # types choisis dans « Personnaliser l'accueil » ; un rappel important reste toujours affiché
        items = [i for i in items if i.get("genre", "rappels") in genres or i.get("important")]
    return {"nb_a_faire": len(items), "nb_retard": sum(i["en_retard"] for i in items), "csrf_token": context.get("csrf_token"),
            "groupes": a_faire_groupes(items, aujourdhui), "modifiable": (context.get("droits") or {}).get("outils") == "modification",
            "request": context.get("request")}


@register.inclusion_tag("patrimoine/_taches_changement.html", takes_context=True)
def bloc_taches_changement(context, par_changement=6):
    """Accueil : tâches restant à faire pour les sorties et entrées de locataires en cours."""
    from django.db.models import F

    from patrimoine.models import ChangementLocataire

    aujourdhui = timezone.localdate()
    changements = []
    for c in ChangementLocataire.objects.filter(termine=False).select_related("lot__bien", "bail_sortant"):
        restantes = list(c.taches.filter(faite=False).order_by(F("echeance").asc(nulls_last=True), "ordre"))
        if not restantes:
            continue
        faites, total = c.avancement
        changements.append({
            "changement": c, "faites": faites, "total": total, "autres": max(0, len(restantes) - par_changement),
            "taches": [{"tache": t, "en_retard": bool(t.echeance and t.echeance < aujourdhui)} for t in restantes[:par_changement]],
            "retard": sum(1 for t in restantes if t.echeance and t.echeance < aujourdhui),
        })
    changements.sort(key=lambda x: min((t["tache"].echeance for t in x["taches"] if t["tache"].echeance), default=aujourdhui.max))
    return {"changements": changements, "csrf_token": context.get("csrf_token"),
            "modifiable": (context.get("droits") or {}).get("relocation") == "modification"}


@register.inclusion_tag("patrimoine/_compta_a_faire.html", takes_context=True)
def bloc_compta(context):
    """Accueil : ce qui reste à faire en comptabilité (factures à saisir par dossier…), avec un lien vers le dossier.
    Sans le droit Comptabilité, rien n'est affiché ; quand tout est à jour, le bloc le dit en une ligne."""
    from patrimoine import dossiers
    from patrimoine.declarations import annee_fiscale

    request = context.get("request")
    if not (context.get("droits") or {}).get("compta") or request is None:
        return {"visible": False}
    annee = annee_fiscale(request)
    lignes = dossiers.a_faire(annee)
    genres = (context.get("choix_accueil") or {}).get("compta")
    if genres is not None:  # lignes choisies dans « Personnaliser l'accueil »
        lignes = [l for l in lignes if l.get("genre") in genres]
    return {"visible": True, "lignes": lignes, "annee": annee, "request": request}


@register.simple_tag(takes_context=True)
def boutons_actions_rapides(context):
    """Accueil, bloc « Actions rapides » : les actions choisies, dans l'ordre et avec le libellé de l'utilisateur."""
    from patrimoine import accueil

    choix = context.get("choix_accueil")
    if choix is None:  # pas de réglages : actions d'origine
        return accueil.actions_rapides(accueil.ACTIONS_D_ORIGINE, {}, context.get("droits"))
    return accueil.actions_rapides(choix.get("actions"), (context.get("libelles_accueil") or {}).get("actions"), context.get("droits"))


@register.simple_tag(takes_context=True)
def disposition_accueil(context):
    """Accueil : blocs de l'utilisateur dans son ordre, à leur place, avec les données qu'il a choisies."""
    from patrimoine import accueil

    return accueil.disposition(context.get("user"), context.get("droits"))


@register.filter
def libelle_ajouter(nom):
    """« Ajouter une annonce » plutôt que « Ajouter annonce »."""
    from ..libelles import ajouter
    return ajouter(nom)


@register.filter
def nb_filtres_actifs(filtres):
    """Nombre de filtres réglés autrement que sur « Tous » (affiché sur le bouton « Filtres » du téléphone)."""
    return sum(1 for f in filtres or () if not f.get("tous", True))


@register.filter
def get_item(conteneur, cle):
    """Accès à un élément de dictionnaire ou de liste depuis un gabarit."""
    try:
        return conteneur[cle]
    except (KeyError, IndexError, TypeError):
        return None


@register.filter
def negatif(valeur):
    """Montant de dépense affiché en négatif, arrondi à l'euro, sans « −0 »."""
    arrondi = round(valeur or 0)
    if not arrondi:
        return "0"
    return "−" + f"{arrondi:,}".replace(",", "\u202f")


@register.simple_tag
def sites_utiles():
    from patrimoine.models import SiteUtile
    return list(SiteUtile.objects.all())


# --- Menu latéral -------------------------------------------------------------------------------------------
# Icônes au trait (style Lucide), dessinées en SVG pour rester nettes à toutes les tailles.
ICONES = {
    "maison": '<path d="M3 11 12 4l9 7"/><path d="M5 9.8V20h14V9.8"/><path d="M16 6.5V4h2v4"/><path d="M9.5 20v-5h5v5"/>',
    "accueil": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
    "loyers": '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
    "baux": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    "locataires": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
    "biens": '<path d="M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16"/><path d="M16 9h3a1 1 0 0 1 1 1v11M2 21h20M8 7h4M8 11h4M8 15h4"/>',
    "candidatures": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "annonces": '<path d="M3 11v2a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14"/>',
    "cles": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m11 12 9-9M17 6l3 3M14 9l2 2"/>',
    "lcd": '<path d="M2 18V8M2 13h20v5M22 18v-3a4 4 0 0 0-4-4h-8v2"/><circle cx="6.5" cy="10.5" r="1.5"/>',
    "encaissements": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    "prets": '<path d="M3 10 12 4l9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18"/>',
    "etats": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "compta": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h8M8 14h2M12 14h4M8 18h2M12 18h4"/>',
    "foncier": '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/>',
    "lmnp": '<path d="M3 18v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6"/><path d="M3 15h18M6 10V7a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v3"/>',
    "regimes": '<path d="M9 3h6l1 4H8z"/><rect x="4" y="7" width="16" height="14" rx="2"/><path d="M8 12h8M8 16h5"/>',
    "societe": '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h6"/>',
    "rappels": '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "modeles": '<path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "reglages": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "liens": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "balles": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6a9 9 0 0 1 0 12.8M18.4 5.6a9 9 0 0 0 0 12.8"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "liste": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
    "galerie": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    "controles": '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "irl": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "surface": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h6V3M15 21v-6h6"/>',
    "dpe": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M12 12v4M12 18h.01"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "depot": '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>',
    "eclair": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "chaudiere": '<path d="M12 3c2 3 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 0-3-1-5 1-7.5z"/>',
    "declarations": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 15l2.5 2.5L16 12"/>',
    "dossiers": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "saisie": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "evolution": '<path d="M4 20V10M10 20V6M16 20v-8M22 20H2"/><path d="m3 7 6-4 6 5 6-4"/>',
    "facture": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/>',
    "booking": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/>',
    "banque": '<path d="M3 10 12 4l9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18"/>',
    "personne": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "filtre": '<path d="M4 5h16l-6 8v5l-4 2v-7z"/>',
    "pdf": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    "edl": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1M9 12l2 2 4-4"/>',
    "quittance": '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    "reviser": '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4"/>',
    "word": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m7 8 2 8 3-6 3 6 2-8"/>',
    "depart": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3M17 6l3 3M14 9l2 2"/>',
    "photo": '<rect x="3" y="5" width="18" height="15" rx="2"/><circle cx="12" cy="12.5" r="3.5"/><path d="M8 5l1.5-2h5L16 5"/>',
    "euro": '<path d="M17 6.5A7 7 0 1 0 17 17.5M4 10h9M4 14h9"/>',
    "nouveau": '<path d="M12 5v14M5 12h14"/>',
    "invest": '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "import": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
}


def icone(nom, taille=18):
    from django.utils.safestring import mark_safe
    return mark_safe(f'<svg class="icone" width="{taille}" height="{taille}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                     f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONES[nom]}</svg>')


register.simple_tag(icone, name="icone")


ADMIN = "admin"  # sous-lien réservé aux administrateurs


def _menu():
    """Groupes du menu : (titre, [(icône, libellé, url, préfixes qui l'activent, sous-liens)])."""
    from django.urls import reverse as r

    from simulateurs.registre import disponibles

    a = lambda nom: r(f"admin:patrimoine_{nom}_changelist")
    return [
        (None, [
            # « À faire » n'a plus d'entrée : on y va depuis le bloc À faire de l'accueil, qui reste allumé sur ces pages
            ("accueil", "Tableau de bord", r("admin:index"), ["/admin/$", r("a_faire"), a("rappel"), a("tache")], []),
            ("biens", "Biens et lots", a("bien"), [a("bien"), a("lot")],
             [("Propriétaires", a("proprietaire")), ("Lots", a("lot")), ("Syndics", a("syndic"))]),
            ("locataires", "Locataires", a("locataire"), [a("locataire")], []),
            ("baux", "Baux", a("bail"), [a("bail")], [("États des lieux", a("etatdeslieux")), ("Garants", a("garant"))]),
            ("loyers", "Loyers", a("echeance"), [a("echeance"), a("versement"), r("relances_impayes"), a("revisionloyer"), a("mouvementdepot"),
                                                 r("encaisser_mois")],
             [("Encaisser le mois", r("encaisser_mois")), ("Paiements reçus", a("versement")), ("Relances d'impayés", r("relances_impayes")), ("Révisions de loyer", a("revisionloyer")),
              ("Dépôts de garantie", a("mouvementdepot"))]),
        ]),
        ("Location", [
            ("annonces", "Annonces", a("annonce"), [a("annonce")], []),
            ("candidatures", "Candidatures", a("candidature"), [a("candidature")], []),
            ("cles", "Changements de locataire", a("changementlocataire"), [a("changementlocataire")], []),
        ]),
        ("Courte durée", [
            ("lcd", "Gérer mon bien", r("lcd_accueil"), [r("lcd_accueil"), r("suivi_lcd"), a("reservationlcd"), a("chargelcd"), a("parametreslcd")],
             [("Calendrier", r("lcd_calendrier")), ("Suivi mensuel", r("suivi_lcd")), ("Réservations", a("reservationlcd")),
              ("Charges mensuelles", a("chargelcd")), ("Frais de gestion", a("parametreslcd")), ("Comparatif location nue", r("comparatif_lcd"))]),
        ]),
        ("Argent", [
            ("encaissements", "Encaissements", r("suivi_encaissements"), [r("suivi_encaissements"), r("etat_loyers_encaisses")],
             [("État des loyers encaissés", r("etat_loyers_encaisses"), "etats")]),
            ("prets", "Prêts et endettement", a("pret"), [a("pret"), r("etat_endettement")], []),
            ("maison", "Ma maison", r("ma_maison"), [r("ma_maison"), r("maison_fiches")],
             [("Fiches pratiques", r("maison_fiches"))]),
        ]),
        ("Comptabilité", _menu_compta()),
        ("Investir", [
            ("invest", "Simulations", r("simulateurs:accueil"),
             [r("simulateurs:accueil"), r("simulateurs:mes_simulations"), r("simulateurs:rentabilite")],
             [("Mes simulations", r("simulateurs:mes_simulations"), "investissement"),
              ("Nouvelle simulation", r("simulateurs:rentabilite"), "investissement")]
             + [(s.titre, r(s.nom_url)) for s in disponibles()]),
            ("etats", "Plus-value", r("plus_value"), [r("plus_value")], []),
        ]),
        # Un seul menu en bas : les outils et les réglages (entrées de droits différents, filtrées une à une)
        (None, [
            ("reglages", "Paramètres", r("sauvegardes"),
             [a("modeledocument"), a("modeleannonce"), r("liste_champs"), a("siteutile"), r("import_excel"), r("controles"),
              r("sauvegardes"), a("parametresmail"), a("tachepardefaut"), "/admin/auth/", "/admin/password_change/",
              r("toutes_les_tables")],
             [("Modèles location", a("modeledocument"), "outils"), ("Modèles d'annonces", a("modeleannonce"), "outils"),
              ("Liste des champs", r("liste_champs"), "outils"), ("Sites utiles", a("siteutile"), "outils"),
              ("Import Excel", r("import_excel"), "biens"), ("Contrôles", r("controles"), "outils"),
              ("Balles", r("balles:accueil"), "balles"),
              ("Sauvegardes", r("sauvegardes"), ADMIN), ("Mails (Gmail)", a("parametresmail"), ADMIN),
              ("Tâches entrée / sortie", a("tachepardefaut"), ADMIN), ("Utilisateurs", r("admin:auth_user_changelist"), ADMIN),
              ("Mot de passe", r("admin:password_change"), ADMIN), ("Toutes les tables", r("toutes_les_tables"))]),
        ]),
    ]


# « ? » à côté d'une entrée du menu : ce qu'elle fait, en une ou deux phrases (survol, clavier ou toucher)
AIDES_MENU = {
    "Encaisser le mois": "Mode saisie : un mois à l'écran, un clic sur « ✓ Reçu » par locataire. Le paiement est daté du jour "
                         "d'échéance du loyer. « Autre montant » pour un paiement partiel, deux mois d'un coup ou la CAF.",
    "Relevé de banque": "Mode relevé : importez le relevé de la banque (OFX, CSV, CAMT ou PDF). L'appli propose loyers, factures, "
                        "prêts, frais et apports d'associés ; vous validez les lignes sûres d'un coup.",
    "Comptes bancaires": "Un compte par compte en banque, avec sa façon d'être suivi : saisie ou relevé de banque. La règle fiscale "
                         "(trésorerie pour la 2044 et la 2072, engagement pour l'IS et la 2031) est déduite du propriétaire. Un même "
                         "propriétaire peut avoir plusieurs comptes : cochez sur chacun les biens qu'il gère.",
    "Agences de gestion": "Biens confiés à une agence qui vous vire un acompte chaque mois : leurs loyers sont encaissés par "
                          "l'agence, et son récap de fin d'année donne les loyers bruts, les charges et le solde.",
    "Paiements reçus": "Tous les paiements enregistrés, quelle que soit la façon de les saisir : leur répartition sur les loyers, "
                       "l'avance qui reste, l'annulation d'un paiement rejeté.",
}


def _menu_compta():
    """Mes dossiers (accueil : une carte par dossier ; un sous-lien par dossier ; les pages des déclarations y sont
    rattachées), Toutes les factures (saisie de tous les dossiers mélangés), Évolution des dépenses (plusieurs années)
    et Réglages fiscaux."""
    from django.urls import reverse as r

    from patrimoine import dossiers

    factures = r("admin:patrimoine_piececomptable_changelist")
    importer = r("admin:patrimoine_piececomptable_import")
    conciergerie = r("conciergerie_ajouter")
    liste = dossiers.tous()
    sous_dossiers = [(d["titre"], r("dossier_compta", args=[d["code"]])) for d in liste]
    prefixes = [r("compta_accueil") + "$", r("dossier_compta", args=["x"]).rsplit("x/", 1)[0], r("declarations"), r("declaration_revenus"), r("recap_2044"),
                r("annexe_2072"), r("compta_lmnp"), r("compta_societe"), r("biens_lmnp"), r("admin:patrimoine_chargelmnp_changelist")]
    prefixes += [f"{u}?dossier={d['code']}" for d in liste for u in (factures, importer)]
    recurrentes = r("admin:patrimoine_depenserecurrente_changelist")
    sous_saisie = [("Toutes les factures", factures), ("À classer", f"{factures}?a_codifier=oui"), ("Ajouter des factures", importer),
                   ("Dépenses récurrentes", recurrentes)]
    from patrimoine.views.compta import lots_conciergerie

    if lots_conciergerie().exists():
        sous_saisie.append(("Contrôle conciergerie", conciergerie))
    return [("dossiers", "Mes dossiers", r("compta_accueil"), prefixes, sous_dossiers + [("Toutes les déclarations", r("declarations")), ("Ma déclaration de revenus", r("declaration_revenus"))]),
            ("saisie", "Toutes les factures", factures, [factures, conciergerie, recurrentes], sous_saisie),
            ("evolution", "Évolution des dépenses", r("evolution_depenses"), [r("evolution_depenses")], []),
            # banque : relevés et rapprochement (loyers, factures, prêts, apports d'associés), rangés avec la comptabilité
            ("banque", "Banque", r("releve_banque"), [r("releve_banque"), r("admin:patrimoine_comptebancaire_changelist"),
                                                      r("admin:patrimoine_agencegestion_changelist"), "/loyers/agence/"],
             [("Relevé de banque", r("releve_banque")), ("Comptes bancaires", r("admin:patrimoine_comptebancaire_changelist")),
              ("Agences de gestion", r("admin:patrimoine_agencegestion_changelist"))]),
            ("regimes", "Réglages fiscaux", r("regimes_fiscaux"), [r("regimes_fiscaux")], [])]


def _parametres_de_la_page(request):
    """Paramètres de la page, avec ceux de la liste d'origine (_changelist_filters) et le régime de comptabilité
    d'une facture ouverte directement (fiche, contrôle de conciergerie) : ils choisissent l'entrée active du menu."""
    import re

    from django.http import QueryDict

    if request is None:
        return {}
    params = {k: v for k, v in QueryDict(request.GET.get("_changelist_filters", "")).items()}
    params.update({k: v for k, v in request.GET.items()})
    if "compta" not in params:
        m = re.match(r"^/(?:admin/patrimoine/piececomptable|comptabilite/conciergerie)/(\d+)/", request.path)
        if m:
            from patrimoine.models import PieceComptable

            regime = PieceComptable.objects.filter(pk=m[1]).values_list("bien__comptabilite", flat=True).first()
            if regime:
                params["compta"] = regime
    return params


def _correspond(prefixe, chemin, params):
    """Le lien correspond-il à la page ? « /chemin$ » : chemin exact ; « /chemin?cle=valeur » : début du chemin et paramètres."""
    import re
    from urllib.parse import parse_qsl

    if prefixe.endswith("$"):
        return bool(re.match(prefixe, chemin))
    base, _, requete = prefixe.partition("?")
    return chemin.startswith(base) and all(params.get(k) == v for k, v in parse_qsl(requete))


@register.inclusion_tag("admin/_menu_lateral.html", takes_context=True)
def menu_lateral(context):
    from patrimoine.models import Candidature, Echeance

    request = context.get("request")
    chemin = request.path if request else ""
    params = _parametres_de_la_page(request)
    aujourdhui = timezone.localdate()
    pastilles = {
        "Loyers": Echeance.objects.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=aujourdhui).count(),
        "Candidatures": Candidature.objects.filter(statut=Candidature.Statut.RECUE).count(),
    }
    from patrimoine import compta, droits

    user = context.get("user")
    acces = droits.niveaux(user) if user is not None and user.is_authenticated else {}
    module_du_lien = {"biens": "biens", "locataires": "locataires", "baux": "baux", "loyers": "loyers",
                      "candidatures": "relocation", "annonces": "relocation", "cles": "relocation", "lcd": "lcd",
                      "encaissements": "finances", "prets": "finances", "maison": "finances", "etats": "etats",
                      "dossiers": "compta", "saisie": "compta", "banque": "loyers", "evolution": "compta", "regimes": "compta",
                      "rappels": "outils"}
    groupes, actif_trouve = [], False
    for titre, liens in _menu():
        lignes = []
        for ico, libelle, url, prefixes, sous in liens:
            if ico in module_du_lien and not acces.get(module_du_lien[ico]):
                continue
            actif = any(_correspond(p, chemin, params) for p in prefixes)
            pastille = pastilles.get(libelle)
            if ico == "dossiers":
                pastille = compta.a_codifier().count()  # factures importées pas encore saisies
            if ico == "regimes":
                from patrimoine import regimes

                pastille = regimes.nombre_alertes_rouges()  # incohérences entre régime, propriétaire et lots
            sous_liens = [{"libelle": s[0], "url": s[1], "actif": _correspond(s[1], chemin, params), "aide": AIDES_MENU.get(s[0], "")}
                          for s in sous if len(s) < 3 or acces.get(s[2]) or (s[2] == ADMIN and user is not None and user.is_superuser)]
            if ico == "reglages":
                # Paramètres : chacun n'y voit que ce que ses droits permettent ; on ouvre sur le premier lien visible
                if not sous_liens:
                    continue
                if not any(s["url"] == url for s in sous_liens):
                    url = sous_liens[0]["url"]
                if acces.get("outils"):
                    from patrimoine import controles

                    pastille = controles.nombre_a_traiter(user)  # contrôles qui ont au moins une ligne
            lignes.append({"icone": icone(ico), "libelle": libelle, "url": url, "actif": actif and not actif_trouve,
                           "pastille": pastille, "pastille_douce": ico in ("dossiers", "reglages"),
                           "pastille_titre": "factures à saisir" if ico == "dossiers" else "contrôles à regarder" if ico == "reglages" else "",
                           "sous": sous_liens})
            actif_trouve |= actif
        if lignes:
            groupes.append({"titre": titre, "liens": lignes})
    return {"groupes": groupes, "user": context.get("user"), "liens_utiles": sites_utiles() if acces.get("outils") else [], "sites_visibles": bool(acces.get("outils")),
            "csrf_token": context.get("csrf_token"), "mode_test": context.get("mode_test")}


# --- Tableau de bord : chiffres clés ------------------------------------------------------------------------------
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _donnees_tableau_de_bord():
    from decimal import Decimal

    from django.db.models import Prefetch

    from datetime import timedelta

    from patrimoine.models import Candidature, Echeance, Lot, Paiement, Pret

    aujourdhui = timezone.localdate()
    mois = aujourdhui.replace(day=1)
    echeances = list(Echeance.objects.filter(periode=mois).select_related("bail__lot__bien")
                     .prefetch_related("bail__locataires", Prefetch("paiements", queryset=Paiement.objects.all())))
    attendu = sum((e.total for e in echeances), Decimal(0))
    encaisse = sum((sum((p.montant for p in e.paiements.all()), Decimal(0)) for e in echeances), Decimal(0))
    retard = [e for e in Echeance.objects.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=aujourdhui)
              .prefetch_related("paiements")]
    montant_retard = sum((e.total - sum((p.montant for p in e.paiements.all()), Decimal(0)) for e in retard), Decimal(0))
    # locatif seulement : les prêts de la résidence principale (bien sans comptabilité) sont à part
    prets_locatifs = list(Pret.objects.exclude(bien__comptabilite=""))
    prets = [p for p in prets_locatifs if p.date_derniere_echeance >= aujourdhui]
    crd = sum((p.capital_restant_du(aujourdhui) for p in prets_locatifs), Decimal(0))
    mensualites = sum((p.mensualite_totale for p in prets), Decimal(0))
    impayes = [e for e in retard if e.date_exigibilite < aujourdhui - timedelta(days=JOURS_IMPAYE)]
    lcd = _courte_duree_mensuelle(aujourdhui)
    lots = list(Lot.objects.filter(vendu=False, bien__vendu=False).prefetch_related("baux"))
    loues = [l for l in lots if any(b.actif for b in l.baux.all())]
    courte = [l for l in lots if l.exploitation == "courte" and l not in loues]
    vacants = [l for l in lots if l not in loues and l not in courte and l.type not in ("cave",)]

    def statut(e):
        paye = sum((p.montant for p in e.paiements.all()), Decimal(0))
        if paye + e.remise >= e.total:
            return "ok", "Payé"
        if e.date_exigibilite < aujourdhui:
            return "rouge", "En retard"
        return ("orange", "Partiel") if paye else ("orange", "À payer")

    lignes = []
    for e in sorted(echeances, key=lambda e: (statut(e)[0] != "rouge", statut(e)[0] == "ok", e.date_exigibilite)):
        classe, libelle = statut(e)
        noms = list(e.bail.locataires.all())
        lignes.append({"lot": e.bail.lot.designation,
                       "locataire": noms[0].nom if noms else "", "initiales": "".join(x[:1] for x in [noms[0].prenom, noms[0].nom]).upper() if noms else "?",
                       "date": e.date_exigibilite, "total": e.total, "classe": classe, "libelle": libelle,
                       "url": reverse_lazy_echeance(e.pk)})
    return {
        "mois": MOIS_FR[mois.month - 1], "attendu": attendu, "encaisse": encaisse, "nb_echeances": len(echeances),
        "nb_payes": sum(1 for e in echeances if statut(e)[0] == "ok"),
        "pourcentage": int(encaisse * 100 / attendu) if attendu else 0,
        "nb_retard": len(retard), "montant_retard": montant_retard,
        "nb_impayes": len(impayes), "jours_impaye": JOURS_IMPAYE, "nb_recents": len(retard) - len(impayes),
        "montant_impayes": sum((e.total - sum((p.montant for p in e.paiements.all()), Decimal(0)) for e in impayes), Decimal(0)),
        "jour_echeance": max(set(jours), key=jours.count) if (jours := [e.date_exigibilite.day for e in echeances]) else None,
        "crd": crd, "nb_prets": len(prets), "mensualites": mensualites, "lcd": lcd, "cash_flow": attendu + lcd - mensualites,
        "nb_loues": len(loues), "nb_courte": len(courte), "vacants": vacants,
        "candidatures": Candidature.objects.filter(statut=Candidature.Statut.RECUE).select_related("lot__bien")[:4],
        "lignes": lignes[:7], "nb_lignes": len(lignes), "aujourdhui": aujourdhui,
    }


JOURS_IMPAYE = 10  # un loyer est un vrai impayé après 10 jours de retard (avant, c'est souvent un virement en route)


def _courte_duree_mensuelle(aujourdhui):
    """Ce que rapporte la courte durée par mois, en moyenne sur les 12 derniers mois complets : net des plateformes,
    moins la conciergerie, le ménage, les consommables et les charges du logement (hors résidence principale)."""
    from decimal import Decimal

    from patrimoine.lcd import suivi_annuel
    from patrimoine.models import Lot

    debut = (aujourdhui.replace(day=1).year - 1, aujourdhui.month)  # même mois l'an dernier
    total = Decimal(0)
    for lot in Lot.objects.filter(exploitation="courte", vendu=False, bien__vendu=False).exclude(bien__comptabilite=""):
        manuels = {(e.mois.year, e.mois.month): e.versement for e in lot.encaissements_lcd.all()}
        for annee in (debut[0], aujourdhui.year):
            for m in suivi_annuel(lot, annee):
                cle = (annee, m["mois"])
                if debut <= cle < (aujourdhui.year, aujourdhui.month):
                    total += manuels[cle] if cle in manuels else m["resultat"]
    return (total / 12).quantize(Decimal(1))


# Accueil, « À faire » : une ligne par type (libellé au singulier, au pluriel, bouton)
TYPES_A_FAIRE = {
    "Loyer": ("loyer non réglé", "loyers non réglés", "Relancer"),
    "Révision": ("révision de loyer à faire", "révisions de loyer à faire", "Traiter"),
    "Assurance": ("attestation d'assurance à demander", "attestations d'assurance à demander", "Demander"),
    "Chaudière": ("attestation d'entretien de chaudière", "attestations d'entretien de chaudière", "Demander"),
    "Entrée et sortie": ("tâche d'entrée ou de sortie", "tâches d'entrée et de sortie", "Voir"),
}


def a_faire_groupes(items, aujourdhui):
    """Regroupe la liste « À faire » par type : nombre, retard, précision et l'action la plus utile."""
    from datetime import time

    from django.urls import reverse

    from patrimoine.models.outils import heure_courte

    groupes = {}
    lignes = []
    for i in items:
        # rappels importants et rappels saisis (« Mes rappels ») : une ligne chacun, avec le bouton « Fait »
        if i.get("important") or (i.get("genre") == "rappels" and i.get("action")):
            jours = (i["date"] - aujourdhui).days
            a_heure = f" à {heure_courte(i['heure'])}" if i.get("heure") else ""
            quand = (f"aujourd'hui{a_heure}" if jours == 0 else f"en retard depuis le {i['date']:%d/%m}" if jours < 0
                     else f"le {i['date']:%d/%m/%Y}{a_heure}, dans {jours} jour{'s' if jours > 1 else ''}")
            titre = i["titre"] if i.get("important") else f"{i['categorie']} : {i['titre']}"
            lignes.append({"categorie": i["categorie"], "titre": titre, "detail": " · ".join(x for x in (i["detail"], quand) if x),
                           "url": i["url"], "bouton": "Ouvrir", "nombre": 1, "retard": int(i["en_retard"]), "date": i["date"],
                           "heure": i.get("heure"), "important": bool(i.get("important")), "action": i.get("action")})
            continue
        groupes.setdefault(i["categorie"], []).append(i)
    for categorie, liste in groupes.items():
        n = len(liste)
        un, plusieurs, bouton = TYPES_A_FAIRE.get(categorie, ("rappel", "rappels", "Voir"))
        titre = f"{n} {un if n == 1 else plusieurs}"
        if categorie not in TYPES_A_FAIRE:
            titre = f"{categorie} : {liste[0]['titre']}" if n == 1 else f"{n} rappels « {categorie} »"
        plus_ancien = min(i["date"] for i in liste)
        detail = (liste[0]["detail"] if n == 1 else
                  f"le plus ancien du {plus_ancien:%d/%m/%Y}" if plus_ancien < aujourdhui else f"à partir du {plus_ancien:%d/%m/%Y}")
        encaisser = None
        if categorie == "Loyer":
            url = reverse("relances_impayes")
            # le locataire a peut-être payé : « Encaisser » ouvre le formulaire prérempli (un loyer) ou la liste à encaisser
            encaisser = liste[0].get("encaisser") if n == 1 else reverse("admin:patrimoine_echeance_changelist") + "?retard=oui"
        elif n == 1:
            url, bouton = liste[0]["url"], bouton if categorie in TYPES_A_FAIRE else "Ouvrir"
        else:
            url = reverse("a_faire") + f"?type={liste[0].get('genre', '')}"
        lignes.append({"categorie": categorie, "titre": titre, "detail": detail, "url": url, "bouton": bouton, "nombre": n,
                       "retard": sum(1 for i in liste if i["en_retard"]), "date": plus_ancien, "encaisser": encaisser,
                       "detail_url": reverse("a_faire") + f"?type={liste[0].get('genre', '')}"})
    return sorted(lignes, key=lambda l: (not l.get("important"), not l["retard"], l["date"],
                                         l.get("heure") is not None, l.get("heure") or time.min))


@register.simple_tag
def donnees_accueil():
    return _donnees_tableau_de_bord()


def reverse_lazy_echeance(pk):
    from django.urls import reverse
    return reverse("admin:patrimoine_echeance_change", args=[pk])


@register.filter
def euros(valeur, decimales=0):
    """1234.5 → « 1 235 € » (espace insécable fine), négatif avec le vrai signe moins."""
    if valeur is None:
        return "–"
    from decimal import ROUND_HALF_UP, Decimal
    v = Decimal(valeur).quantize(Decimal(1) if not decimales else Decimal("0.01"), ROUND_HALF_UP)
    texte = f"{abs(v):,.{int(decimales)}f}".replace(",", " ").replace(".", ",")
    return ("−" if v < 0 else "") + texte + " €"


@register.simple_tag
def filtres_haut(cl):
    """Filtres d'une liste à afficher en haut : boutons quand il y a peu de choix, sinon liste déroulante.
    Les filtres nommés dans `filtres_caches` de l'admin restent actifs mais ne s'affichent pas ;
    ceux de `filtres_par_cartes` non plus (on les choisit en cliquant sur les cartes de totaux), mais « Effacer » les retire."""
    caches = set(getattr(cl.model_admin, "filtres_caches", ())) | set(getattr(cl.model_admin, "filtres_par_cartes", ()))
    en_liste = set(getattr(cl.model_admin, "filtres_en_liste", ()))
    filtres = []
    for spec in cl.filter_specs:
        if caches & set(spec.expected_parameters()):
            continue
        choix = [{"libelle": str(c["display"]), "url": c["query_string"], "actif": c["selected"]} for c in spec.choices(cl)]
        if len(choix) <= 1:
            continue
        titre = str(spec.title)
        champ = getattr(spec, "field", None)
        if champ is not None and champ.name == "type" and champ.model._meta.model_name == "bail":
            titre = "type de bail"
        chemin = getattr(spec, "field_path", "")
        if getattr(spec, "affichage_annees", False):
            # Tout + année en cours + année précédente en boutons ; années antérieures dans une liste déroulante
            filtres.append({"titre": "Années antérieures", "annees": True, "choix": choix[:3], "anterieures": choix[3:],
                            "anterieure_active": any(c["actif"] for c in choix[3:]), "tous": choix[0]["actif"]})
            continue
        if chemin.endswith("proprietaire") and any("bien" in f.expected_parameters() for f in cl.filter_specs):
            # changer de propriétaire efface le bien choisi (il appartiendrait à un autre propriétaire)
            from urllib.parse import parse_qsl, urlencode
            for c in choix:
                c["url"] = "?" + urlencode([(k, v) for k, v in parse_qsl(c["url"].lstrip("?")) if k != "bien"])
        filtres.append({"titre": titre[:1].upper() + titre[1:], "choix": choix, "boutons": len(choix) <= 5 and chemin not in en_liste and getattr(spec, "parameter_name", None) not in en_liste,
                        "tous": choix[0]["actif"], "avec_titre": any(c["libelle"] in ("Oui", "Non") for c in choix), "droite": chemin.split("__")[-1] == "proprietaire" or getattr(spec, "a_droite", False)})
    return filtres



@register.simple_tag
def lien_carte_filtre(cl, parametre, valeur=None):
    """Lien d'une carte de totaux qui filtre la liste (garde les autres filtres) ; sans valeur : retire ce filtre."""
    retire = [parametre, "p"]
    return cl.get_query_string({parametre: valeur} if valeur is not None else {}, remove=retire if valeur is None else ["p"])


@register.simple_tag
def valeur_filtre(cl, parametre):
    """Valeur actuelle d'un filtre de la liste (texte), ou "" s'il n'est pas choisi."""
    v = cl.params.get(parametre, "")
    return str(v[-1] if isinstance(v, list) else v)


@register.simple_tag
def lien_effacer(cl):
    """« Effacer les filtres » : garde les filtres cachés (choisis par le menu, comme l'entrée de comptabilité) ;
    rien à effacer quand seuls ceux-là sont actifs."""
    if not cl.has_active_filters:
        return ""
    caches = set(getattr(cl.model_admin, "filtres_caches", ()))
    a_retirer = [k for k in cl.get_filters_params() if k not in caches]
    return cl.get_query_string(remove=a_retirer) if a_retirer else ""


@register.simple_tag
def navigation_periode(cl):
    """Remplace la ligne des dates de Django : boutons de période rapides + « Période… » (du … au …).
    Filtre par `<champ>__gte` / `<champ>__lte`. Par mois (Loyers…) ou par année (Baux…) selon `navigation_par`."""
    import calendar
    from datetime import date, timedelta

    champ = cl.date_hierarchy
    if not champ:
        return None
    p_du, p_au = f"{champ}__gte", f"{champ}__lte"
    retire = [p_du, p_au, f"{champ}__year", f"{champ}__month", f"{champ}__day"]
    valeur = lambda cle: (lambda v: v[-1] if isinstance(v, list) else v)(cl.params.get(cle)) or ""
    du, au = valeur(p_du), valeur(p_au)
    t = timezone.localdate()
    fin_mois = lambda d: d.replace(day=calendar.monthrange(d.year, d.month)[1])
    debut_mois = t.replace(day=1)
    mois_dernier = (debut_mois - timedelta(days=1)).replace(day=1)
    il_y_a_2_mois = (mois_dernier - timedelta(days=1)).replace(day=1)
    if getattr(cl.model_admin, "navigation_par", "annee") == "mois":
        raccourcis = [("Ce mois", debut_mois, fin_mois(t)), ("Mois dernier", mois_dernier, fin_mois(mois_dernier)),
                      ("3 derniers mois", il_y_a_2_mois, fin_mois(t)), ("Cette année", date(t.year, 1, 1), date(t.year, 12, 31))]
    else:
        raccourcis = [("Cette année", date(t.year, 1, 1), date(t.year, 12, 31)),
                      ("Année dernière", date(t.year - 1, 1, 1), date(t.year - 1, 12, 31))]
    boutons = [{"libelle": l, "url": cl.get_query_string({p_du: a.isoformat(), p_au: b.isoformat()}, remove=retire),
                "actif": (du, au) == (a.isoformat(), b.isoformat())} for l, a, b in raccourcis]
    aucun = not du and not au
    boutons.append({"libelle": "Tout", "url": cl.get_query_string({}, remove=retire), "actif": aucun})
    perso = not aucun and not any(b["actif"] for b in boutons)
    autres = [(k, v[-1] if isinstance(v, list) else v) for k, v in cl.params.items() if k not in retire]
    return {"boutons": boutons, "perso": perso, "du": du, "au": au, "p_du": p_du, "p_au": p_au, "autres": autres}


@register.inclusion_tag("patrimoine/_annees.html", takes_context=True)
def boutons_annees(context, annees, annee, mode="", ouvertes=()):
    """Années en boutons (à la place d'une liste déroulante) ; les autres paramètres de la page sont conservés.
    mode « civil » : année en cours et précédente en boutons, années antérieures dans une liste déroulante.
    mode « loyers » : année en cours et années passées dont des loyers restent à encaisser (`ouvertes`) en boutons,
    toutes les autres dans une liste déroulante « Autres années »."""
    request = context.get("request")

    def lien(a):
        params = request.GET.copy() if request else {}
        params["annee"] = a
        return "?" + params.urlencode()

    if mode == "civil":
        t = timezone.localdate().year
        boutons = [{"libelle": f"{t} (en cours)", "url": lien(t), "actif": int(annee) == t},
                   {"libelle": str(t - 1), "url": lien(t - 1), "actif": int(annee) == t - 1}]
        toutes = {int(x) for x in annees} | set(range(t - 10, t - 1))  # toujours les années passées, même sans donnée
        anterieures = [{"libelle": a, "url": lien(a), "actif": int(a) == int(annee)} for a in sorted(toutes, reverse=True) if a < t - 1]
        return {"boutons": boutons, "anterieures": anterieures, "anterieure_active": any(a["actif"] for a in anterieures)}
    if mode == "loyers":
        t = timezone.localdate().year
        ouvertes = {int(a) for a in ouvertes if int(a) < t}
        boutons = [{"libelle": f"{t} (en cours)", "url": lien(t), "actif": int(annee) == t}]
        boutons += [{"libelle": f"{a} (à encaisser)", "url": lien(a), "actif": int(annee) == a} for a in sorted(ouvertes, reverse=True)]
        autres = [{"libelle": a, "url": lien(a), "actif": int(a) == int(annee)}
                  for a in sorted({int(x) for x in annees} | {int(annee)}, reverse=True) if a != t and a not in ouvertes]
        return {"boutons": boutons, "anterieures": autres, "anterieure_active": any(a["actif"] for a in autres),
                "titre_liste": "Autres années"}
    boutons = []
    for a in sorted(annees, reverse=True):
        boutons.append({"libelle": a, "url": lien(a), "actif": a == annee})
    return {"boutons": boutons}


@register.simple_tag(takes_context=True)
def note_rapide(context):
    """Texte du bloc-notes de l'utilisateur connecté."""
    from patrimoine.models import NoteRapide

    user = context.get("user")
    if user is None or not user.is_authenticated:
        return None
    return NoteRapide.objects.filter(utilisateur=user).first()


@register.simple_tag(takes_context=True)
def avec_retour(context, url, ancre=""):
    """Lien vers une fiche ou une liste qui ramène à la page affichée après « Enregistrer » (?next=…), avec une ancre
    facultative (ex. « ligne-9 » : la ligne de la déclaration se rouvre au retour)."""
    from urllib.parse import quote

    request = context.get("request")
    if not url or request is None:
        return url
    ici = request.get_full_path() + (f"#{ancre}" if ancre else "")
    return f"{url}{'&' if '?' in url else '?'}next={quote(ici, safe='/')}"


@register.simple_tag(takes_context=True)
def garde_retour(context, url):
    """Lien depuis une fiche (Supprimer, Annuler) qui garde la page d'origine (?next=…) de la fiche, s'il y en a une."""
    from urllib.parse import quote

    from patrimoine.admin.communs import _page_d_origine

    request = context.get("request")
    suite = _page_d_origine(request) if request is not None else None
    if not suite or not url:
        return url
    return f"{url}{'&' if '?' in url else '?'}next={quote(suite, safe='/')}"


@register.simple_tag(takes_context=True)
def page_d_origine(context):
    from patrimoine.admin.communs import _page_d_origine

    request = context.get("request")
    return (_page_d_origine(request) if request is not None else None) or ""


@register.filter
def signe_retranche(valeur):
    """Montant retranché affiché avec son signe : 371,03 → « − 371,03 », un avoir de −30 → « + 30,00 »."""
    from decimal import Decimal

    from django.template.defaultfilters import floatformat

    if valeur is None:
        return ""
    v = Decimal(valeur)
    return ("+ " if v < 0 else "− ") + floatformat(abs(v), "2g")
