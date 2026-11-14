"""Contenu des fiches bail, locataire et lot : titre, pastilles, liens vers les fiches liées, actions et encart « En bref »."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
TYPES_MEUBLES = ("meuble", "etudiant", "mobilite")


class Fiche:
    """Contenu de l'en-tête et de l'encart d'une fiche (affiché par admin/fiche_change_form.html)."""

    def __init__(self, titre):
        self.titre = titre
        self.badges, self.sous_titre, self.liens, self.actions, self.menu, self.bref, self.contenus = [], [], [], [], [], [], []
        self.pastilles = {}  # {code d'onglet: nombre}

    def badge(self, texte, classe="", url=None):
        self.badges.append({"texte": texte, "classe": classe, "url": url})

    def lien(self, icone, libelle, url):
        self.liens.append({"icone": icone, "libelle": libelle, "url": url})

    def action(self, icone, libelle, url=None, nouvel_onglet=False, point=False, principale=False, sous=None, titre=""):
        """Bouton de la barre d'actions ; `sous` = [(libellé, url, nouvel onglet)] pour un bouton à menu."""
        self.actions.append({"icone": icone, "libelle": libelle, "url": url, "nouvel_onglet": nouvel_onglet, "point": point,
                             "principale": principale, "titre": titre,
                             "sous": [{"libelle": l, "url": u, "nouvel_onglet": n} for l, u, n in sous] if sous else None})

    def dans_menu(self, libelle, url, nouvel_onglet=False):
        """Action rare, rangée dans le menu « ⋯ »."""
        self.menu.append({"libelle": libelle, "url": url, "nouvel_onglet": nouvel_onglet})

    def info(self, etiquette, valeur, detail="", classe="", bouton=None):
        """Ligne de l'encart « En bref » ; `bouton` = (libellé, url)."""
        self.bref.append({"etiquette": etiquette, "valeur": valeur, "detail": detail, "classe": classe,
                          "bouton": {"libelle": bouton[0], "url": bouton[1]} if bouton else None})

    def contenu(self, onglet, html):
        """Bloc en lecture ajouté en bas d'un onglet."""
        self.contenus.append({"code": onglet, "html": html})


def eur(montant):
    """« 560 € », « 570,50 € » (espace fine insécable pour les milliers)."""
    montant = Decimal(montant or 0)
    texte = f"{montant:,.0f}" if montant == montant.to_integral_value() else f"{montant:,.2f}"
    return texte.replace(",", " ").replace(".", ",") + " €"


def m2(surface):
    """« 32 m² », « 45,5 m² »."""
    return f"{Decimal(surface).normalize():f}".replace(".", ",") + "\u00a0m²"


def _a(modele, pk):
    return reverse(f"admin:patrimoine_{modele}_change", args=[pk])


def nom_lot(lot):
    """« Pontch 1 » plutôt que « Pontch – Pontch 1 » quand la désignation reprend déjà le nom du bien."""
    return lot.designation if lot.bien.nom.strip().lower() in lot.designation.lower() else str(lot)


def impayes(bail, aujourdhui=None):
    """Loyers exigibles pas (entièrement) réglés, du plus ancien au plus récent."""
    aujourdhui = aujourdhui or timezone.localdate()
    return [e for e in bail.echeances.filter(date_exigibilite__lte=aujourdhui).exclude(statut="paye").prefetch_related("paiements")
            .order_by("periode") if e.reste_du > 0]


def badge_impayes(fiche, baux, url):
    lignes = [e for b in baux for e in impayes(b)]
    if not lignes:
        return lignes
    reste = sum((e.reste_du for e in lignes), Decimal(0))
    if len(lignes) == 1:
        texte = f"{MOIS[lignes[0].periode.month - 1].capitalize()} non réglé · {eur(reste)}"
    else:
        texte = f"{len(lignes)} loyers non réglés · {eur(reste)}"
    fiche.badge(texte, "rouge", url)
    return lignes


def revision_en_attente(bail):
    """Date anniversaire de la révision à faire (pas encore calculée), sinon None."""
    if not bail.actif or not bail.revision_annuelle or not bail.rappel_revision:
        return None
    return next((l["anniversaire"] for l in bail.historique_revisions() if l["statut"] == "a_faire"), None)


def _assurance(fiche, bail, locataire=None):
    """Ligne « Assurance habitation » de l'encart, avec « Demander » quand l'attestation manque."""
    d = bail.date_attestation_assurance
    demander = None
    locataire = locataire or next(iter(bail.locataires.all()), None)
    if locataire and locataire.email:
        demander = ("Demander", reverse("admin:patrimoine_locataire_contacter", args=[locataire.pk])
                    + f"?motif=assurance&retour={_a('bail', bail.pk)}")
    if not d:
        fiche.info("Assurance habitation", "Attestation non reçue", "rappel annuel activé" if bail.rappel_assurance else "",
                   "orange", demander)
    elif d < timezone.localdate() - timedelta(days=365):
        fiche.info("Assurance habitation", "Attestation à renouveler", f"dernière reçue le {d:%d/%m/%Y}", "orange", demander)
    else:
        fiche.info("Assurance habitation", f"Reçue le {d:%d/%m/%Y}", "rappel annuel activé" if bail.rappel_assurance else "")


def _depot(fiche, bail):
    detenu = bail.depot_detenu
    mouvements = list(bail.mouvements_depot.all())
    if not mouvements:
        detail = "aucun mouvement enregistré" + (f" · {eur(bail.depot_garantie)} prévu au bail" if bail.depot_garantie else "")
    else:
        dernier = max(mouvements, key=lambda m: m.date)
        detail = f"{dernier.get_type_display().split(' (')[0].lower()} le {dernier.date:%d/%m/%Y}"
    bouton = None
    if bail.actif and detenu < bail.depot_garantie:
        bouton = ("Encaisser", reverse("depot_bail", args=[bail.pk, "encaisser"]))
    elif detenu > 0 and not bail.actif:
        bouton = ("Restituer", reverse("depot_bail", args=[bail.pk, "restituer"]))
    fiche.info("Dépôt de garantie", f"{eur(detenu)} détenu{'s' if detenu >= 2 else ''}", detail, "orange" if bouton else "", bouton)


def _historique_loyers(bail):
    """Tableau des 24 derniers loyers du bail, pour l'onglet « Historique »."""
    lignes = list(bail.echeances.prefetch_related("paiements").order_by("-periode")[:24])
    if not lignes:
        return format_html('<h2>Loyers</h2><p class="help">Aucun loyer enregistré pour ce bail.</p>')
    statuts = {"paye": ("Payé", "ok"), "partiel": ("Partiel", "orange"), "a_payer": ("À payer", "gris")}
    aujourdhui = timezone.localdate()
    corps = []
    for e in lignes:
        libelle, classe = statuts.get(e.statut, (e.get_statut_display(), "gris"))
        if e.statut != "paye" and e.date_exigibilite <= aujourdhui:
            libelle, classe = ("Non réglé" if e.statut == "a_payer" else "Partiel"), "rouge"
        paye_le = max((p.date for p in e.paiements.all()), default=None)
        corps.append((_a("echeance", e.pk), f"{MOIS[e.periode.month - 1]} {e.periode.year}", eur(e.total), eur(e.montant_paye),
                      f"{paye_le:%d/%m/%Y}" if paye_le else "–", classe, libelle))
    return format_html(
        '<h2>Loyers</h2><div class="tableau-defilant"><table class="historique-loyers"><thead><tr><th>Mois</th><th>Dû</th><th>Payé</th>'
        '<th>Le</th><th>Statut</th></tr></thead><tbody>{}</tbody></table></div><p class="help">Les 24 derniers mois. <a href="{}">Tous les loyers de ce bail</a></p>',
        format_html_join("", '<tr><td><a href="{}">{}</a></td><td>{}</td><td>{}</td><td>{}</td><td><span class="pill {}">{}</span></td></tr>', corps),
        reverse("admin:patrimoine_echeance_changelist") + f"?bail={bail.pk}")


def _documents_bail(bail):
    """Menus « État des lieux » et « Modèles Word »."""
    from .models import ModeleDocument

    edl = [("Fiche d'état des lieux d'entrée (PDF)", reverse("document_edl_vierge", args=[bail.pk, "entree"]), True),
           ("Fiche d'état des lieux de sortie (PDF)", reverse("document_edl_vierge", args=[bail.pk, "sortie"]), True)]
    if bail.lot.meuble or bail.type in TYPES_MEUBLES:
        edl += [("Inventaire du mobilier – entrée (PDF)", reverse("document_inventaire", args=[bail.pk, "entree"]), True),
                ("Inventaire du mobilier – sortie (PDF)", reverse("document_inventaire", args=[bail.pk, "sortie"]), True)]
    for e in bail.etats_des_lieux.order_by("date"):
        edl.append((f"État des lieux d'{'entrée' if e.type == 'entree' else 'sortie'} du {e.date:%d/%m/%Y}", reverse("document_edl", args=[e.pk]), True))
    edl.append(("Saisir un état des lieux…", reverse("admin:patrimoine_etatdeslieux_add") + f"?bail={bail.pk}", False))
    word = []
    for m in ModeleDocument.objects.filter(actif=True):
        if not m.convient(bail) or (m.type == ModeleDocument.Type.CAUTION and not bail.garants.exists()):
            continue
        url = reverse("remplir_modele", args=[m.pk, bail.pk])
        if m.type == ModeleDocument.Type.EDL:
            word += [(f"{m.nom} – entrée", url + "?edl=entree", False), (f"{m.nom} – sortie", url + "?edl=sortie", False)]
        else:
            word.append((m.nom, url, False))
    return edl, word


def fiche_bail(bail):
    locataires = list(bail.locataires.all())
    noms = ", ".join(str(l) for l in locataires)
    f = Fiche(f"Bail de {nom_lot(bail.lot)}" + (f" · {noms}" if noms else ""))
    if bail.actif:
        f.badge("En cours", "ok")
    else:
        f.badge("Terminé" + (f" le {bail.date_fin:%d/%m/%Y}" if bail.date_fin else ""), "gris")
    url_loyers = reverse("admin:patrimoine_echeance_changelist") + f"?bail={bail.pk}"
    retard = badge_impayes(f, [bail], url_loyers)
    f.sous_titre = [bail.get_type_display(), f"depuis le {bail.date_debut:%d/%m/%Y}"
                    + (f" jusqu'au {bail.date_fin:%d/%m/%Y}" if bail.date_fin else ""),
                    f"{eur(bail.loyer_cc)} charges comprises ({eur(bail.loyer_hc)} + {eur(bail.charges)})" if bail.charges
                    else f"{eur(bail.loyer_hc)} hors charges", bail.lot.bien.proprietaire.nom]
    f.lien("biens", f"Lot {nom_lot(bail.lot)}", _a("lot", bail.lot_id))
    for l in locataires:
        f.lien("personne", "Fiche du locataire" if len(locataires) == 1 else str(l), _a("locataire", l.pk))
    f.lien("loyers", "Loyers de ce bail", url_loyers)
    if locataires:
        f.lien("pdf", "Documents", _a("locataire", locataires[0].pk) + "#documents")

    edl, word = _documents_bail(bail)
    attente = revision_en_attente(bail)
    f.action("pdf", "Bail PDF", reverse("document_bail", args=[bail.pk]), nouvel_onglet=True)
    f.action("edl", "État des lieux", sous=edl)
    f.action("quittance", "Quittance", reverse("envoyer_quittance") + f"?bail={bail.pk}")
    f.action("reviser", "Réviser le loyer", reverse("revision_loyer", args=[bail.pk]), point=bool(attente),
             titre=f"Révision en attente depuis le {attente:%d/%m/%Y}" if attente else "")
    if retard:
        f.action("mail", "Relancer", reverse("relances_impayes") + f"?bail={bail.pk}")
    if bail.depot_garantie:
        f.action("depot", "Reçu de dépôt", reverse("recu_depot_bail", args=[bail.pk]))
    if word:
        f.action("word", "Modèles Word", sous=word)
    if bail.actif:
        f.action("depart", "Déclarer un départ", reverse("admin:patrimoine_changementlocataire_add")
                 + f"?lot={bail.lot_id}&bail_sortant={bail.pk}")
    if not retard:
        f.dans_menu("Relancer un impayé", reverse("relances_impayes") + f"?bail={bail.pk}")
    if attente:
        f.pastilles["revisions"] = 1

    # En bref
    charges = f" + {eur(bail.charges)} de charges" if bail.charges else " hors charges"
    detail = [bail.get_mode_charges_display().split(" avec")[0].lower() if bail.charges else "",
              f"payé le {bail.jour_echeance}{'er' if bail.jour_echeance == 1 else ''}, {'d’avance' if bail.paiement_a_echoir else 'à terme échu'}"]
    aujourdhui = timezone.localdate()
    if bail.reduction_montant and bail.reduction_debut and bail.reduction_debut <= aujourdhui and (not bail.reduction_fin or aujourdhui <= bail.reduction_fin):
        detail.append(f"réduction de {eur(bail.reduction_montant)}" + (f" jusqu'au {bail.reduction_fin:%d/%m/%Y}" if bail.reduction_fin else ""))
    f.info("Loyer actuel", eur(bail.loyer_hc) + charges, " · ".join(d for d in detail if d))
    if not bail.revision_annuelle:
        f.info("Révision", "Pas de révision prévue au bail")
    else:
        irl = f"IRL de référence {bail.irl_trimestre} : {bail.irl_valeur}".replace(".", ",") if bail.irl_trimestre and bail.irl_valeur \
            else "IRL de référence à compléter"
        if attente:
            f.info("Prochaine révision", f"En attente depuis le {attente:%d/%m/%Y}", irl, "orange",
                   ("Réviser", reverse("revision_loyer", args=[bail.pk])))
        elif bail.actif and bail.prochaine_revision():
            f.info("Prochaine révision", f"Le {bail.prochaine_revision():%d/%m/%Y}", irl)
    _depot(f, bail)
    if bail.type not in ("commercial", "professionnel", "parking"):
        _assurance(f, bail)
    garants = list(bail.garants.all())
    if garants:
        f.info("Garantie", f"{len(garants)} garant{'s' if len(garants) > 1 else ''}",
               ", ".join(dict.fromkeys(g.get_type_caution_display().lower() for g in garants)), bouton=("Voir", "#garants"))
    elif bail.type_garantie:
        f.info("Garantie", bail.get_type_garantie_display().split(" (")[0], bail.detail_garantie)
    f.contenu("historique", _historique_loyers(bail))
    return f


def fiche_locataire(locataire):
    from .models import Bail

    baux = list(locataire.baux.select_related("lot__bien__proprietaire").order_by("-actif", "-date_debut"))
    bail = next((b for b in baux if b.actif), None)
    f = Fiche(str(locataire))
    f.badge("Présent" if bail else "Parti", "ok" if bail else "gris")
    if bail:
        badge_impayes(f, [bail], reverse("admin:patrimoine_echeance_changelist") + f"?bail={bail.pk}")
    dernier = bail or (baux[0] if baux else None)
    if dernier:
        f.sous_titre.append(f"{nom_lot(dernier.lot)} depuis le {dernier.date_debut:%d/%m/%Y}" if bail else
                            f"Parti de {nom_lot(dernier.lot)}" + (f" le {dernier.date_fin:%d/%m/%Y}" if dernier.date_fin else ""))
    if locataire.email:
        f.sous_titre.append(locataire.email)
    if locataire.telephone:
        f.sous_titre.append(locataire.telephone)
    for b in baux[:3]:
        f.lien("baux", f"{'Bail en cours' if b.actif else 'Ancien bail'} : {nom_lot(b.lot)}", _a("bail", b.pk))
    if dernier:
        f.lien("biens", f"Lot {nom_lot(dernier.lot)}", _a("lot", dernier.lot_id))
        f.lien("loyers", "Loyers", reverse("admin:patrimoine_echeance_changelist") + f"?bail={dernier.pk}")

    if locataire.email:
        f.action("mail", "Contacter", reverse("admin:patrimoine_locataire_contacter", args=[locataire.pk]) + f"?retour={_a('locataire', locataire.pk)}")
    if dernier:
        f.action("quittance", "Quittance", reverse("envoyer_quittance") + f"?bail={dernier.pk}")
        if bail and impayes(bail):
            f.action("mail", "Relancer", reverse("relances_impayes") + f"?bail={bail.pk}")
        edl, word = _documents_bail(dernier)
        if word:
            f.action("word", "Modèles Word", sous=word)
    if bail:
        seul = "" if bail.locataires.count() < 2 else f"&locataire_sortant={locataire.pk}"  # colocation : seulement lui par défaut
        f.action("depart", "Déclarer un départ", reverse("admin:patrimoine_changementlocataire_add") + f"?lot={bail.lot_id}&bail_sortant={bail.pk}{seul}")
    elif not baux:
        f.action("nouveau", "Créer son bail", reverse("admin:patrimoine_bail_add") + f"?locataires={locataire.pk}")

    if bail:
        f.info("Logement", nom_lot(bail.lot), f"{bail.get_type_display()} · depuis le {bail.date_debut:%d/%m/%Y}",
               bouton=("Ouvrir le bail", _a("bail", bail.pk)))
        f.info("Loyer", f"{eur(bail.loyer_cc)} charges comprises" if bail.charges else f"{eur(bail.loyer_hc)} hors charges",
               f"payé le {bail.jour_echeance}{'er' if bail.jour_echeance == 1 else ''} du mois")
        retard = impayes(bail)
        if retard:
            f.info("Loyers non réglés", eur(sum((e.reste_du for e in retard), Decimal(0))),
                   ", ".join(f"{MOIS[e.periode.month - 1]} {e.periode.year}" for e in retard[:3]) + ("…" if len(retard) > 3 else ""),
                   "rouge", ("Relancer", reverse("relances_impayes") + f"?bail={bail.pk}"))
        _depot(f, bail)
        if bail.type not in (Bail.Type.COMMERCIAL, Bail.Type.PROFESSIONNEL, Bail.Type.PARKING):
            _assurance(f, bail, locataire)
    elif dernier:
        _depot(f, dernier)
    garants = list(locataire.garants.all())
    if garants:
        f.info("Garants", ", ".join(dict.fromkeys(str(g) for g in garants)), bouton=("Voir", "#garants"))
    documents = locataire.documents.count()
    f.pastilles["documents"] = documents or None
    return f


def fiche_lot(lot):
    bail = lot.bail_actif
    f = Fiche(nom_lot(lot) if lot.designation.strip().lower() != lot.bien.nom.strip().lower() else lot.designation)
    if lot.vendu or lot.bien.vendu:
        f.badge("Vendu" + (f" le {lot.date_vente:%d/%m/%Y}" if lot.date_vente else ""), "gris")
    elif bail:
        f.badge("Loué", "ok")
    elif lot.exploitation == "courte":
        f.badge("Courte durée", "bleu")
    else:
        f.badge("Vacant", "orange")
    if lot.lmnp:
        f.badge("LMNP", "gris")
    if bail:
        badge_impayes(f, [bail], reverse("admin:patrimoine_echeance_changelist") + f"?bail={bail.pk}")
    f.sous_titre = [x for x in [f"{lot.get_type_display()}{' meublé' if lot.meuble else ''}",
                                m2(lot.surface) if lot.surface else "",
                                f"{lot.nb_pieces} pièce{'s' if lot.nb_pieces > 1 else ''}" if lot.nb_pieces else "",
                                f"{lot.bien.nom} ({lot.bien.ville})", lot.bien.proprietaire.nom] if x]
    f.lien("biens", f"Bien {lot.bien.nom}", _a("bien", lot.bien_id))
    if bail:
        f.lien("baux", "Bail en cours", _a("bail", bail.pk))
        for l in bail.locataires.all():
            f.lien("personne", str(l), _a("locataire", l.pk))
        f.lien("loyers", "Loyers", reverse("admin:patrimoine_echeance_changelist") + f"?bail={bail.pk}")
    if lot.exploitation == "courte":
        f.lien("lcd", "Suivi courte durée", reverse("suivi_lcd") + f"?lot={lot.pk}")

    loyer = lot.loyer_propose or (bail.loyer_cc if bail else "")
    if bail:
        f.action("baux", "Ouvrir le bail", _a("bail", bail.pk), principale=True)
        f.action("depart", "Déclarer un départ", reverse("admin:patrimoine_changementlocataire_add") + f"?lot={lot.pk}&bail_sortant={bail.pk}")
    elif not (lot.vendu or lot.bien.vendu):
        f.action("nouveau", "Nouveau bail", reverse("admin:patrimoine_bail_add") + f"?lot={lot.pk}", principale=lot.exploitation != "courte")
    if not (lot.vendu or lot.bien.vendu):
        f.action("annonces", "Créer une annonce", f"{reverse('admin:patrimoine_annonce_add')}?lot={lot.pk}&loyer_cc={loyer}"
                 f"&charges={bail.charges if bail else ''}&depot_garantie={bail.depot_garantie if bail else ''}")
        f.action("candidatures", "Formulaire de candidature", f"{reverse('admin:patrimoine_candidature_add')}?lot={lot.pk}")
    if lot.photos.exists():
        f.action("photo", "Photos (zip)", reverse("photos_zip", args=[lot.pk]))

    if bail:
        f.info("Bail en cours", ", ".join(str(l) for l in bail.locataires.all()) or "sans locataire",
               f"{eur(bail.loyer_cc)} CC · depuis le {bail.date_debut:%d/%m/%Y}", bouton=("Ouvrir", _a("bail", bail.pk)))
    elif lot.loyer_propose:
        f.info("Loyer proposé", eur(lot.loyer_propose), "pour la prochaine annonce")
    f.info("Surface et DPE", (m2(lot.surface) if lot.surface else "Surface à compléter")
           + (f" · classe {lot.classe_dpe}" if lot.classe_dpe else " · DPE à compléter"),
           "", "" if lot.surface and lot.classe_dpe else "orange", None if lot.surface and lot.classe_dpe else ("Compléter", "#caracteristiques"))
    if lot.chaudiere_gaz:
        d = lot.date_entretien_chaudiere
        en_retard = not d or d < timezone.localdate() - timedelta(days=365)
        f.info("Chaudière gaz", f"Entretien le {d:%d/%m/%Y}" if d else "Entretien à renseigner", "attestation annuelle",
               "orange" if en_retard else "")
    if lot.pdl:
        f.info("Compteur électrique", f"PDL {lot.pdl}")
    anciens = lot.baux.filter(actif=False).count()
    if anciens:
        f.info("Anciens baux", f"{anciens} bail terminé" if anciens == 1 else f"{anciens} baux terminés", bouton=("Voir", "#baux"))
    return f
