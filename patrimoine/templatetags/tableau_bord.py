from django import template
from django.utils import timezone

from patrimoine.rappels import HISTORIQUE_JOURS, a_faire, deja_fait

register = template.Library()


@register.inclusion_tag("patrimoine/_a_faire.html", takes_context=True)
def bloc_a_faire(context):
    aujourdhui = timezone.localdate()
    request = context.get("request")
    vue = "fait" if request and request.GET.get("vue") == "fait" else "a_faire"
    items = a_faire(aujourdhui)
    contexte = {"vue": vue, "nb_a_faire": len(items), "nb_retard": sum(i["en_retard"] for i in items),
                "historique_jours": HISTORIQUE_JOURS, "csrf_token": context.get("csrf_token")}
    contexte["items"] = deja_fait(aujourdhui) if vue == "fait" else items
    return contexte


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
    "rappels": '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "modeles": '<path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "reglages": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "liens": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "balles": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6a9 9 0 0 1 0 12.8M18.4 5.6a9 9 0 0 0 0 12.8"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
}


def icone(nom, taille=18):
    from django.utils.safestring import mark_safe
    return mark_safe(f'<svg class="icone" width="{taille}" height="{taille}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                     f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONES[nom]}</svg>')


register.simple_tag(icone, name="icone")


def _menu():
    """Groupes du menu : (titre, [(icône, libellé, url, préfixes qui l'activent, sous-liens)])."""
    from django.urls import reverse as r

    a = lambda nom: r(f"admin:patrimoine_{nom}_changelist")
    return [
        (None, [
            ("accueil", "Tableau de bord", r("admin:index"), ["/admin/$"], []),
            ("loyers", "Loyers", a("echeance"), [a("echeance"), a("revisionloyer"), a("mouvementdepot")],
             [("Révisions de loyer", a("revisionloyer")), ("Dépôts de garantie", a("mouvementdepot"))]),
            ("baux", "Baux", a("bail"), [a("bail")], [("États des lieux", a("etatdeslieux")), ("Garants", a("garant"))]),
            ("locataires", "Locataires", a("locataire"), [a("locataire")], []),
            ("biens", "Biens et lots", a("bien"), [a("bien"), a("lot")],
             [("Lots", a("lot")), ("Propriétaires", a("proprietaire")), ("Syndics", a("syndic"))]),
        ]),
        ("Relocation", [
            ("candidatures", "Candidatures", a("candidature"), [a("candidature")], []),
            ("annonces", "Annonces", a("annonce"), [a("annonce")], []),
            ("cles", "Changements de locataire", a("changementlocataire"), [a("changementlocataire")], []),
        ]),
        ("Courte durée", [
            ("lcd", "Airbnb / Booking", r("suivi_lcd"), [r("suivi_lcd"), a("reservationlcd"), a("chargelcd"), a("parametreslcd")],
             [("Réservations", a("reservationlcd")), ("Charges mensuelles", a("chargelcd")), ("Frais de gestion", a("parametreslcd"))]),
        ]),
        ("Finances", [
            ("encaissements", "Encaissements", r("suivi_encaissements"), [r("suivi_encaissements")], []),
            ("prets", "Prêts et endettement", a("pret"), [a("pret"), r("etat_endettement")], [("Endettement à une date", r("etat_endettement"))]),
            ("compta", "Comptabilité SCI", a("piececomptable"), [a("piececomptable"), r("annexe_2072")],
             [("Factures et pièces", a("piececomptable")), ("Annexe 2072-S-A1", r("annexe_2072"))]),
            ("etats", "États et LMNP", r("etat_loyers_encaisses"),
             [r("etat_loyers_encaisses"), r("biens_lmnp"), a("chargelmnp"), r("plus_value")],
             [("Loyers encaissés", r("etat_loyers_encaisses")), ("Biens en LMNP", r("biens_lmnp")),
              ("Charges LMNP", a("chargelmnp")), ("Simulation de plus-value", r("plus_value"))]),
        ]),
        ("Outils", [
            ("rappels", "Rappels", a("rappel"), [a("rappel")], []),
            ("modeles", "Modèles Word", a("modeledocument"), [a("modeledocument"), r("liste_champs")], [("Liste des champs", r("liste_champs"))]),
            ("liens", "Sites utiles", a("siteutile"), [a("siteutile")], []),
            ("reglages", "Réglages", r("sauvegardes"),
             [r("sauvegardes"), a("parametresmail"), "/admin/auth/", "/admin/password_change/"],
             [("Sauvegardes", r("sauvegardes")), ("Mails (Gmail)", a("parametresmail")),
              ("Utilisateurs", r("admin:auth_user_changelist")), ("Mot de passe", r("admin:password_change"))]),
            ("balles", "Balles", r("balles:accueil"), [], []),
        ]),
    ]


@register.inclusion_tag("admin/_menu_lateral.html", takes_context=True)
def menu_lateral(context):
    import re

    from patrimoine.models import Candidature, Echeance

    request = context.get("request")
    chemin = request.path if request else ""
    aujourdhui = timezone.localdate()
    pastilles = {
        "Loyers": Echeance.objects.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=aujourdhui).count(),
        "Candidatures": Candidature.objects.filter(statut=Candidature.Statut.RECUE).count(),
    }
    groupes, actif_trouve = [], False
    for titre, liens in _menu():
        lignes = []
        for ico, libelle, url, prefixes, sous in liens:
            actif = any(re.match(p, chemin) if p.endswith("$") else chemin.startswith(p) for p in prefixes)
            lignes.append({"icone": icone(ico), "libelle": libelle, "url": url, "actif": actif and not actif_trouve,
                           "pastille": pastilles.get(libelle), "sous": [{"libelle": l, "url": u, "actif": chemin.startswith(u)} for l, u in sous]})
            actif_trouve |= actif
        groupes.append({"titre": titre, "liens": lignes})
    return {"groupes": groupes, "user": context.get("user"), "liens_utiles": sites_utiles()}


# --- Tableau de bord : chiffres clés ------------------------------------------------------------------------------
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _donnees_tableau_de_bord():
    from decimal import Decimal

    from django.db.models import Prefetch

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
    prets = [p for p in Pret.objects.all() if p.date_derniere_echeance >= aujourdhui]
    crd = sum((p.capital_restant_du(aujourdhui) for p in Pret.objects.all()), Decimal(0))
    mensualites = sum((p.mensualite_totale for p in prets), Decimal(0))
    lots = list(Lot.objects.filter(vendu=False, bien__vendu=False).prefetch_related("baux"))
    loues = [l for l in lots if any(b.actif for b in l.baux.all())]
    courte = [l for l in lots if l.exploitation == "courte" and l not in loues]
    vacants = [l for l in lots if l not in loues and l not in courte and l.type not in ("cave",)]

    def statut(e):
        paye = sum((p.montant for p in e.paiements.all()), Decimal(0))
        if paye >= e.total:
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
        "crd": crd, "nb_prets": len(prets), "mensualites": mensualites, "cash_flow": attendu - mensualites,
        "nb_loues": len(loues), "nb_courte": len(courte), "vacants": vacants,
        "candidatures": Candidature.objects.filter(statut=Candidature.Statut.RECUE).select_related("lot__bien")[:4],
        "lignes": lignes[:7], "nb_lignes": len(lignes), "aujourdhui": aujourdhui,
    }


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
    Les filtres nommés dans `filtres_caches` de l'admin restent actifs mais ne s'affichent pas."""
    caches = set(getattr(cl.model_admin, "filtres_caches", ()))
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
        filtres.append({"titre": titre[:1].upper() + titre[1:], "choix": choix, "boutons": len(choix) <= 5,
                        "tous": choix[0]["actif"], "avec_titre": any(c["libelle"] in ("Oui", "Non") for c in choix), "droite": chemin.split("__")[-1] == "proprietaire" or getattr(spec, "a_droite", False)})
    return filtres



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
def boutons_annees(context, annees, annee, mode=""):
    """Années en boutons (à la place d'une liste déroulante) ; les autres paramètres de la page sont conservés.
    mode « civil » : année en cours et précédente en boutons, années antérieures dans une liste déroulante."""
    request = context.get("request")

    def lien(a):
        params = request.GET.copy() if request else {}
        params["annee"] = a
        return "?" + params.urlencode()

    if mode == "civil":
        t = timezone.localdate().year
        boutons = [{"libelle": f"Année en cours ({t})", "url": lien(t), "actif": int(annee) == t},
                   {"libelle": f"Année précédente ({t - 1})", "url": lien(t - 1), "actif": int(annee) == t - 1}]
        anterieures = [{"libelle": a, "url": lien(a), "actif": int(a) == int(annee)} for a in sorted({int(x) for x in annees}, reverse=True) if a < t - 1]
        return {"boutons": boutons, "anterieures": anterieures, "anterieure_active": any(a["actif"] for a in anterieures)}
    boutons = []
    for a in sorted(annees, reverse=True):
        boutons.append({"libelle": a, "url": lien(a), "actif": a == annee})
    return {"boutons": boutons}
