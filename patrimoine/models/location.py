"""Locataires, baux, garants, états des lieux, documents des locataires et révisions de loyer."""
import calendar
from decimal import Decimal
from datetime import date, timedelta
from django.db import models

from .communs import _ajouter_mois, _chemin_piece, stockage_prive
from .biens import Lot


class Locataire(models.Model):
    civilite = models.CharField("civilité", max_length=10, blank=True, choices=[("M.", "M."), ("Mme", "Mme")])
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    lieu_naissance = models.CharField("lieu de naissance", max_length=100, blank=True)
    nationalite = models.CharField("nationalité", max_length=60, blank=True)
    adresse_actuelle = models.TextField("adresse actuelle", blank=True, help_text="Adresse avant l'entrée dans les lieux ou après le départ")
    garant = models.CharField(max_length=200, blank=True, help_text="Nom et coordonnées du garant")
    notes = models.TextField(blank=True)
    ref_import = models.CharField("référence d'import", max_length=40, blank=True, editable=False, help_text="Identifiant Airtable d'origine")

    class Meta:
        ordering = ["nom", "prenom"]

    def __str__(self):
        return f"{self.civilite} {self.prenom} {self.nom}".strip()


def mois_depot_maximum(type_bail):
    """Plafond légal du dépôt de garantie en mois de loyer HC : 1 en location nue, 2 en meublé, 0 en bail mobilité."""
    return {"nu": 1, "meuble": 2, "etudiant": 2, "mobilite": 0}.get(type_bail)


TYPES_GARANTIE = [("garant", "Garant (caution d'une personne)"), ("visale", "Garantie Visale"), ("autre", "Autre garantie"), ("aucune", "Aucune")]


class Bail(models.Model):
    class Type(models.TextChoices):
        NU = "nu", "Location nue (3 ans)"
        MEUBLE = "meuble", "Location meublée (1 an)"
        ETUDIANT = "etudiant", "Meublé étudiant (9 mois)"
        MOBILITE = "mobilite", "Bail mobilité"
        COMMERCIAL = "commercial", "Bail commercial (3-6-9)"
        PROFESSIONNEL = "professionnel", "Bail professionnel"
        PARKING = "parking", "Location de parking"

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="baux")
    locataires = models.ManyToManyField(Locataire, related_name="baux")
    type = models.CharField(max_length=20, choices=Type.choices)
    date_debut = models.DateField("date de début")
    date_fin = models.DateField("date de fin", null=True, blank=True)
    loyer_initial = models.DecimalField("loyer initial hors charges (€)", max_digits=10, decimal_places=2, null=True, blank=True, help_text="Loyer à la signature du bail")
    loyer_hc = models.DecimalField("loyer actuel hors charges (€)", max_digits=10, decimal_places=2, help_text="Mis à jour à chaque révision appliquée")
    charges = models.DecimalField("provision sur charges (€)", max_digits=10, decimal_places=2, default=0)
    depot_garantie = models.DecimalField("dépôt de garantie (€)", max_digits=10, decimal_places=2, default=0)
    type_garantie = models.CharField("type de garantie", max_length=10, blank=True, choices=TYPES_GARANTIE)
    detail_garantie = models.CharField(
        "précisions sur la garantie", max_length=200, blank=True,
        help_text="Visale : n° de visa. Autre : caution bancaire, garantie d'un organisme, Garantme…",
    )
    jour_echeance = models.PositiveSmallIntegerField("jour d'échéance", default=1)
    paiement_a_echoir = models.BooleanField("paiement à échoir", default=True, help_text="Coché : loyer payé d'avance en début de mois")
    mode_charges = models.CharField(
        "mode de règlement des charges", max_length=20, default="provision",
        choices=[("provision", "Provisions avec régularisation annuelle"), ("forfait", "Forfait"), ("aucune", "Pas de charges")],
    )
    destination = models.CharField(max_length=30, default="habitation", choices=[("habitation", "Usage d'habitation"), ("mixte", "Usage mixte professionnel et d'habitation")])
    indice_reference = models.CharField(
        "indice de référence (IRL/ILC)", max_length=50, blank=True, help_text="ex. « IRL T2 2026 : 146,12 »"
    )
    irl_trimestre = models.CharField("trimestre IRL de référence", max_length=2, blank=True, choices=[("T1", "T1"), ("T2", "T2"), ("T3", "T3"), ("T4", "T4")])
    irl_valeur = models.DecimalField("valeur de l'IRL de référence", max_digits=7, decimal_places=2, null=True, blank=True, help_text="IRL utilisé pour le loyer actuel")
    revision_annuelle = models.BooleanField("révision annuelle du loyer", default=True)
    date_derniere_revision = models.DateField("dernière révision", null=True, blank=True)
    date_attestation_assurance = models.DateField("dernière attestation d'assurance reçue", null=True, blank=True)
    rappel_assurance = models.BooleanField("rappel annuel de l'attestation d'assurance", default=True)
    rappel_revision = models.BooleanField(
        "rappel de la révision annuelle", default=True,
        help_text="Décoché : la révision de ce bail n'apparaît plus dans « À faire » (révision volontairement non appliquée).")
    actif = models.BooleanField(default=True)
    # réduction temporaire du loyer (geste commercial, travaux…) : déduite du loyer HC des mois concernés, au prorata des jours
    reduction_montant = models.DecimalField("réduction mensuelle (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    reduction_debut = models.DateField("du", null=True, blank=True)
    reduction_fin = models.DateField("au", null=True, blank=True)
    # relevés des compteurs (repris dans les états des lieux d'entrée et de sortie)
    pdl_electricite = models.CharField("électricité : n° PDL", max_length=20, blank=True, help_text="Point de livraison, 14 chiffres (facture ou compteur Linky)")
    index_elec_entree = models.CharField("index à l'entrée", max_length=50, blank=True, help_text="En heures pleines / creuses : « HP 12345 / HC 6789 »")
    index_elec_sortie = models.CharField("index à la sortie", max_length=50, blank=True)
    heures_creuses = models.BooleanField("heures pleines / heures creuses", default=False,
                                         help_text="Cocher si le compteur électrique est en option heures pleines / heures creuses")
    plages_heures_creuses = models.TextField("plages d'heures creuses", blank=True,
                                             help_text="Une plage par ligne, ex. « 22h00 – 06h00 » puis « 12h30 – 14h30 ». "
                                                       "Les autres heures sont en heures pleines.")
    pce_gaz = models.CharField("gaz : n° PCE", max_length=20, blank=True, help_text="Point de comptage et d'estimation, 14 chiffres ou GI + 6 chiffres")
    index_gaz_entree = models.CharField("index à l'entrée", max_length=50, blank=True)
    index_gaz_sortie = models.CharField("index à la sortie", max_length=50, blank=True)

    @property
    def liste_heures_creuses(self):
        """Plages d'heures creuses, une par ligne (vide si le logement n'est pas en heures pleines / creuses)."""
        if not self.heures_creuses:
            return []
        return [l.strip() for l in self.plages_heures_creuses.splitlines() if l.strip()]

    compteur_eau = models.CharField("eau froide : n° de compteur", max_length=30, blank=True)
    index_eau_froide_entree = models.CharField("index à l'entrée", max_length=50, blank=True)
    index_eau_froide_sortie = models.CharField("index à la sortie", max_length=50, blank=True)
    compteur_eau_chaude = models.CharField("eau chaude : n° de compteur", max_length=30, blank=True, help_text="S'il y a un compteur d'eau chaude séparé")
    index_eau_chaude_entree = models.CharField("index à l'entrée", max_length=50, blank=True)
    index_eau_chaude_sortie = models.CharField("index à la sortie", max_length=50, blank=True)
    # zone tendue (à remplir si le bien est en zone tendue ou soumis à l'encadrement des loyers)
    loyer_reference = models.DecimalField("loyer de référence (€/m²)", max_digits=6, decimal_places=2, null=True, blank=True,
                                          help_text="Selon l'arrêté préfectoral : type de logement, nombre de pièces, époque, meublé ou non")
    loyer_reference_majore = models.DecimalField("loyer de référence majoré (€/m²)", max_digits=6, decimal_places=2, null=True, blank=True)
    complement_loyer = models.DecimalField("complément de loyer (€)", max_digits=10, decimal_places=2, null=True, blank=True,
                                           help_text="Seulement si le logement a des caractéristiques exceptionnelles")
    complement_loyer_motif = models.CharField("justification du complément", max_length=255, blank=True)
    loyer_precedent = models.DecimalField("dernier loyer du locataire précédent (€)", max_digits=10, decimal_places=2, null=True, blank=True,
                                          help_text="Obligatoire si le précédent locataire est parti depuis moins de 18 mois")
    date_loyer_precedent = models.DateField("date de son dernier versement", null=True, blank=True)
    date_revision_precedente = models.DateField("date de sa dernière révision", null=True, blank=True)
    notes = models.TextField(blank=True)
    ref_import = models.CharField("référence d'import", max_length=40, blank=True, editable=False, help_text="Identifiant Airtable d'origine")

    # compteur de l'état des lieux -> index du bail (suffixe _entree ou _sortie)
    COMPTEURS_EDL = {"compteur_electricite": "index_elec", "compteur_gaz": "index_gaz",
                     "compteur_eau_froide": "index_eau_froide", "compteur_eau_chaude": "index_eau_chaude"}

    class Meta:
        verbose_name = "bail"
        verbose_name_plural = "baux"
        ordering = ["-actif", "lot__bien__nom", "-date_debut"]

    def __str__(self):
        return f"{self.lot} – {self.get_type_display()} depuis le {self.date_debut:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        if self.loyer_initial is None:
            self.loyer_initial = self.loyer_hc
        if not self.pdl_electricite and self.lot_id and self.lot.pdl:
            self.pdl_electricite = self.lot.pdl
        nouveau = self.pk is None
        avant = None if nouveau else Bail.objects.filter(pk=self.pk).values("actif", "date_fin").first()
        reduction = lambda b: (b.get("reduction_montant"), b.get("reduction_debut"), b.get("reduction_fin"))
        reduction_avant = None if nouveau else Bail.objects.filter(pk=self.pk).values("reduction_montant", "reduction_debut", "reduction_fin").first()
        super().save(*args, **kwargs)
        if nouveau and self.lot_id and self.date_debut and self.date_debut <= date.today():
            # nouveau locataire déjà entré : le bail précédent resté ouvert sur ce lot se termine la veille
            for ancien in Bail.objects.filter(lot_id=self.lot_id, actif=True, date_fin__isnull=True,
                                              date_debut__lt=self.date_debut).exclude(pk=self.pk):
                ancien.date_fin, ancien.actif = self.date_debut - timedelta(days=1), False
                ancien.save()
        if not self.actif and self.date_fin and avant != {"actif": self.actif, "date_fin": self.date_fin}:
            self.nettoyer_apres_depart()
        if reduction_avant is not None and reduction(reduction_avant) != (self.reduction_montant, self.reduction_debut, self.reduction_fin):
            # réduction ajoutée ou modifiée : les loyers non réglés (et non corrigés à la main) sont recalculés
            for e in self.echeances.all():
                e.actualiser_loyer()

    PAIEMENT_SUPPOSE = "Supposé réglé"

    def nettoyer_apres_depart(self):
        """Locataire parti : retire les loyers des mois qui suivent son départ (non réglés, ou seulement « supposés
        réglés » par un rattrapage automatique) et calcule le mois de départ au prorata. Un loyer réellement encaissé
        après le départ passe au bail suivant du lot. Renvoie la liste de ce qui a été fait."""
        if not self.date_fin:
            return []
        faits, a_verifier = [], []
        auto = lambda e: all(p.notes.startswith(self.PAIEMENT_SUPPOSE) for p in e.paiements.all())
        suivant = (Bail.objects.filter(lot_id=self.lot_id, date_debut__gt=self.date_debut).exclude(pk=self.pk)
                   .order_by("date_debut").first())
        for e in self.echeances.filter(periode__gt=self.date_fin).order_by("periode"):
            if auto(e):
                e.delete()
                faits.append(f"{e.periode:%m/%Y} retiré")
                continue
            fin_mois = e.periode.replace(day=calendar.monthrange(e.periode.year, e.periode.month)[1])
            autre = suivant.echeances.filter(periode=e.periode).first() if suivant and suivant.date_debut <= fin_mois else None
            if suivant and suivant.date_debut <= fin_mois and (autre is None or (not autre.ajustee and auto(autre))):
                if autre:
                    autre.delete()
                e.bail = suivant
                e.save(update_fields=["bail"])
                faits.append(f"{e.periode:%m/%Y} encaissé rattaché au locataire suivant")
            else:
                a_verifier.append(f"{e.periode:%m/%Y}")
        e = self.echeances.filter(periode=self.date_fin.replace(day=1)).first()
        if e and not e.ajustee:
            paiements = list(e.paiements.all())
            if not paiements:
                if e.actualiser_loyer():
                    faits.append(f"{e.periode:%m/%Y} au prorata")
            elif len(paiements) == 1 and auto(e):
                loyer, charges = self.montants_du_mois(e.periode)
                if (e.loyer_hc, e.charges) != (loyer, charges):
                    e.loyer_hc, e.charges = loyer, charges
                    e.save(update_fields=["loyer_hc", "charges"])
                    paiements[0].montant = loyer + charges
                    paiements[0].save(update_fields=["montant"])
                    faits.append(f"{e.periode:%m/%Y} au prorata")
        if a_verifier:
            faits.append("encaissé après le départ, à vérifier : " + ", ".join(a_verifier))
        return faits

    @property
    def depot_maximum(self):
        """Dépôt de garantie maximum autorisé (None si la loi ne fixe pas de plafond pour ce type de bail)."""
        mois = mois_depot_maximum(self.type)
        return None if mois is None or self.loyer_hc is None else self.loyer_hc * mois

    @property
    def loyer_cc(self):
        return self.loyer_hc + self.charges

    def loyer_au(self, jour):
        """Loyer HC en vigueur à une date : dernière révision appliquée avant cette date, sinon loyer initial."""
        appliquees = self.revisions.filter(appliquee=True)
        revision = appliquees.filter(date_effet__lte=jour).order_by("-date_effet").first()
        if revision:
            return revision.nouveau_loyer
        suivante = appliquees.filter(date_effet__gt=jour).order_by("date_effet").first()
        if suivante:
            return suivante.ancien_loyer
        # sans révision enregistrée : loyer initial avant la dernière révision connue, loyer actuel ensuite
        if self.loyer_initial is not None and self.date_derniere_revision and jour < self.date_derniere_revision:
            return self.loyer_initial
        return self.loyer_hc

    def historique_revisions(self, aujourdhui=None):
        """Une ligne par date anniversaire depuis la signature, avec la révision faite ou l'année manquée."""
        aujourdhui = aujourdhui or date.today()
        revisions = list(self.revisions.order_by("date_effet"))
        lignes, n = [], 1
        while True:
            anniversaire = _ajouter_mois(self.date_debut, 12 * n)
            if anniversaire > aujourdhui or (self.date_fin and not self.actif and anniversaire > self.date_fin):
                break
            fin_periode = _ajouter_mois(anniversaire, 12)
            r = next((r for r in revisions if anniversaire <= r.date_effet < fin_periode), None)
            ligne = {"anniversaire": anniversaire, "revision": r}
            if r:
                ligne["statut"] = "appliquee" if r.appliquee else "calculee"
                ligne["pourcentage"] = (r.nouvel_irl / r.ancien_irl - 1) * 100 if r.ancien_irl else None
            elif self.date_derniere_revision and anniversaire <= self.date_derniere_revision:
                ligne["statut"] = "sans_detail"
            elif not self.revision_annuelle:
                ligne["statut"] = "pas_de_clause"
            elif fin_periode > aujourdhui:
                ligne["statut"] = "a_faire"
            else:
                ligne["statut"] = "manquee"
            lignes.append(ligne)
            n += 1
        return lignes

    @property
    def depot_detenu(self):
        """Solde du dépôt de garantie encore détenu (compte séparé des loyers)."""
        return sum((m.montant_signe for m in self.mouvements_depot.all()), Decimal(0))

    def prochaine_revision(self, aujourdhui=None):
        """Prochaine date anniversaire de révision, après la dernière révision.

        Une révision non demandée dans l'année qui suit sa date anniversaire est perdue
        (pas de rattrapage) : on ne remonte donc pas plus d'un an en arrière.
        """
        if not self.revision_annuelle:
            return None
        aujourdhui = aujourdhui or date.today()
        depart = max(self.date_derniere_revision or self.date_debut, _ajouter_mois(aujourdhui, -12))
        prochaine = _ajouter_mois(self.date_debut, 12 * max(depart.year - self.date_debut.year, 0))
        while prochaine <= depart:
            prochaine = _ajouter_mois(prochaine, 12)
        return prochaine

    def montants_du_mois(self, periode):
        """Loyer HC et charges dus pour un mois : au prorata des jours occupés le mois d'entrée et le mois de sortie."""
        debut = periode.replace(day=1)
        nb_jours = calendar.monthrange(debut.year, debut.month)[1]
        fin = debut.replace(day=nb_jours)
        loyer, charges = self.loyer_au(debut), self.charges
        premier = max(debut, self.date_debut)
        dernier = min(fin, self.date_fin) if self.date_fin else fin
        jours = (dernier - premier).days + 1
        if 0 < jours < nb_jours:
            ratio = Decimal(jours) / Decimal(nb_jours)
            loyer, charges = (loyer * ratio).quantize(Decimal("0.01")), (charges * ratio).quantize(Decimal("0.01"))
        if self.reduction_montant and self.reduction_debut:
            # réduction temporaire, au prorata des jours du mois couverts à la fois par la réduction et par le bail
            r_debut, r_fin = max(premier, self.reduction_debut), min(dernier, self.reduction_fin or dernier)
            jours_reduits = (r_fin - r_debut).days + 1
            if jours_reduits > 0:
                reduction = (self.reduction_montant * Decimal(jours_reduits) / Decimal(nb_jours)).quantize(Decimal("0.01"))
                loyer = max(loyer - reduction, Decimal(0))
        return loyer, charges

    DUREES_MOIS = {"nu": 36, "meuble": 12, "etudiant": 9, "mobilite": 10, "commercial": 108, "professionnel": 72, "parking": 12}

    @property
    def duree_mois(self):
        if self.date_fin:
            return (self.date_fin.year - self.date_debut.year) * 12 + self.date_fin.month - self.date_debut.month
        return self.DUREES_MOIS.get(self.type, 12)


class EtatDesLieux(models.Model):
    class Type(models.TextChoices):
        ENTREE = "entree", "Entrée"
        SORTIE = "sortie", "Sortie"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="etats_des_lieux")
    type = models.CharField(max_length=10, choices=Type.choices)
    date = models.DateField()
    compteur_electricite = models.CharField("index électricité", max_length=50, blank=True)
    compteur_gaz = models.CharField("index gaz", max_length=50, blank=True)
    compteur_eau_froide = models.CharField("index eau froide", max_length=50, blank=True)
    compteur_eau_chaude = models.CharField("index eau chaude", max_length=50, blank=True)
    nb_cles = models.CharField("clés et badges remis", max_length=100, blank=True)
    nouvelle_adresse = models.TextField("nouvelle adresse du locataire (sortie)", blank=True)
    observations = models.TextField(blank=True)
    # saisie pièce par pièce sur téléphone, signée à l'écran puis verrouillée
    signataire_bailleur = models.CharField("signataire pour le bailleur", max_length=150, blank=True)
    signataire_locataire = models.CharField("signataire(s) pour le locataire", max_length=200, blank=True)
    signature_bailleur = models.TextField(blank=True, editable=False)
    signature_locataire = models.TextField(blank=True, editable=False)
    bailleur_signe_le = models.DateTimeField("bailleur signé le", null=True, blank=True, editable=False)
    signe_le = models.DateTimeField("signé et verrouillé le", null=True, blank=True, editable=False)
    empreinte = models.CharField("empreinte SHA-256 du document signé", max_length=64, blank=True, editable=False)

    class Meta:
        verbose_name = "état des lieux"
        verbose_name_plural = "états des lieux"
        ordering = ["-date"]

    def __str__(self):
        return f"EDL {self.get_type_display().lower()} – {self.bail.lot} – {self.date:%d/%m/%Y}"

    def reprendre_index_du_bail(self):
        """Complète les index vides avec ceux relevés dans le bail ; renvoie les champs du bail encore vides ici renseignés."""
        suffixe = "_entree" if self.type == self.Type.ENTREE else "_sortie"
        a_completer = []
        for champ, index in Bail.COMPTEURS_EDL.items():
            du_bail = getattr(self.bail, index + suffixe)
            if not getattr(self, champ) and du_bail:
                setattr(self, champ, du_bail)
            elif getattr(self, champ) and not du_bail:
                setattr(self.bail, index + suffixe, getattr(self, champ))
                a_completer.append(index + suffixe)
        return a_completer

    def save(self, *args, **kwargs):
        """Reprend les index relevés dans le bail, et complète le bail avec ceux saisis ici."""
        a_completer = self.reprendre_index_du_bail()
        super().save(*args, **kwargs)
        if a_completer:
            self.bail.save(update_fields=a_completer)

    @property
    def signe(self):
        return self.signe_le is not None

    def edl_entree(self):
        """Pour une sortie : l'état des lieux d'entrée du même bail (le plus récent), qui sert de référence."""
        if self.type != self.Type.SORTIE:
            return None
        return self.bail.etats_des_lieux.filter(type=self.Type.ENTREE).exclude(pk=self.pk).order_by("-date", "-pk").first()


def _chemin_photo_edl(instance, nom):
    import secrets

    return f"etats-des-lieux/{instance.edl_id}/{secrets.token_hex(6)}.jpg"


class ElementEDL(models.Model):
    """Une ligne de l'état des lieux : un élément d'une pièce (sol, murs…), un compteur ou un meuble de l'inventaire."""

    class Etat(models.TextChoices):
        NEUF = "neuf", "Neuf"
        TRES_BON = "tb", "Très bon"
        BON = "b", "Bon"
        USAGE = "u", "Usagé"
        MAUVAIS = "m", "Mauvais"
        ABSENT = "absent", "Absent / hors service"

    edl = models.ForeignKey(EtatDesLieux, on_delete=models.CASCADE, related_name="elements")
    piece = models.CharField("pièce", max_length=100)
    ordre = models.PositiveSmallIntegerField(default=0)
    nom = models.CharField("élément", max_length=150)
    etat = models.CharField("état", max_length=10, choices=Etat.choices, blank=True)
    quantite = models.PositiveSmallIntegerField("quantité", null=True, blank=True)
    commentaire = models.TextField(blank=True)
    etat_entree = models.CharField("état à l'entrée", max_length=10, choices=Etat.choices, blank=True, editable=False)
    commentaire_entree = models.TextField("commentaire à l'entrée", blank=True, editable=False)
    element_entree = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, editable=False, related_name="+")

    class Meta:
        verbose_name = "élément d'état des lieux"
        verbose_name_plural = "éléments d'état des lieux"
        ordering = ["ordre", "pk"]

    def __str__(self):
        return f"{self.piece} – {self.nom}"

    RANG = {"neuf": 5, "tb": 4, "b": 3, "u": 2, "m": 1, "absent": 0}

    @property
    def degrade(self):
        """Sortie : l'état est moins bon qu'à l'entrée."""
        if not (self.etat and self.etat_entree):
            return False
        return self.RANG[self.etat] < self.RANG[self.etat_entree]


class PhotoEDL(models.Model):
    """Photo prise pendant l'état des lieux : heure de réception par le serveur, date de prise de vue (si l'appareil la donne) et empreinte du fichier."""

    edl = models.ForeignKey(EtatDesLieux, on_delete=models.CASCADE, related_name="photos")
    element = models.ForeignKey(ElementEDL, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=_chemin_photo_edl, storage=stockage_prive)
    recue_le = models.DateTimeField("reçue par le serveur le", auto_now_add=True)
    prise_le = models.DateTimeField("prise de vue (appareil)", null=True, blank=True)
    empreinte = models.CharField("empreinte SHA-256", max_length=64)

    class Meta:
        verbose_name = "photo d'état des lieux"
        verbose_name_plural = "photos d'état des lieux"
        ordering = ["recue_le", "pk"]

    def __str__(self):
        return f"Photo {self.element} – {self.recue_le:%d/%m/%Y %H:%M}"


class RevisionLoyer(models.Model):
    """Révision annuelle du loyer selon l'IRL : nouveau loyer = loyer actuel × nouvel IRL / ancien IRL."""

    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, related_name="revisions")
    date_effet = models.DateField("date d'effet")
    ancien_loyer = models.DecimalField("ancien loyer HC (€)", max_digits=10, decimal_places=2)
    ancien_irl = models.DecimalField("ancien IRL", max_digits=7, decimal_places=2)
    nouvel_irl = models.DecimalField("nouvel IRL", max_digits=7, decimal_places=2)
    trimestre = models.CharField("trimestre et année du nouvel IRL", max_length=20, help_text="ex. « T2 2026 »")
    nouveau_loyer = models.DecimalField("nouveau loyer HC (€)", max_digits=10, decimal_places=2)
    appliquee = models.BooleanField("appliquée au bail", default=False)

    class Meta:
        verbose_name = "révision de loyer"
        verbose_name_plural = "révisions de loyer"
        ordering = ["-date_effet"]

    def __str__(self):
        return f"Révision {self.date_effet:%m/%Y} – {self.bail.lot}"

    @staticmethod
    def calculer(ancien_loyer, ancien_irl, nouvel_irl):
        return (ancien_loyer * nouvel_irl / ancien_irl).quantize(Decimal("0.01"))

    @property
    def hausse(self):
        return self.nouveau_loyer - self.ancien_loyer

    def appliquer(self):
        bail = self.bail
        bail.loyer_hc = self.nouveau_loyer
        bail.irl_valeur = self.nouvel_irl
        bail.indice_reference = f"IRL {self.trimestre} : {self.nouvel_irl}"
        bail.date_derniere_revision = self.date_effet
        bail.save()
        self.appliquee = True
        self.save(update_fields=["appliquee"])
        # les échéances déjà générées et pas encore réglées passent au nouveau loyer
        for e in bail.echeances.filter(periode__gte=self.date_effet.replace(day=1)):
            e.actualiser_loyer()


class Garant(models.Model):
    """Personne qui se porte caution pour un locataire sur un bail."""

    class TypeCaution(models.TextChoices):
        SOLIDAIRE = "solidaire", "Caution solidaire"
        SIMPLE = "simple", "Caution simple"

    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, related_name="garants", null=True, blank=True)
    locataire = models.ForeignKey(Locataire, on_delete=models.CASCADE, related_name="garants", verbose_name="locataire garanti")
    civilite = models.CharField("civilité", max_length=10, blank=True, choices=[("M.", "M."), ("Mme", "Mme")])
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    lieu_naissance = models.CharField("lieu de naissance", max_length=100, blank=True)
    adresse = models.TextField(blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    lien = models.CharField("lien avec le locataire", max_length=100, blank=True, help_text="ex. « père », « employeur »")
    profession = models.CharField(max_length=100, blank=True)
    revenus_mensuels = models.DecimalField("revenus nets mensuels (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    piece_identite_numero = models.CharField("n° de pièce d'identité", max_length=40, blank=True)
    piece_identite_fichier = models.FileField("copie de la pièce d'identité", upload_to=_chemin_piece, storage=stockage_prive, blank=True)
    type_caution = models.CharField("type de caution", max_length=10, choices=TypeCaution.choices, default=TypeCaution.SOLIDAIRE)
    montant_max = models.DecimalField(
        "montant maximum garanti (€)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Si vide : loyer et charges sur la durée de l'engagement",
    )
    duree_mois = models.PositiveSmallIntegerField(
        "durée de l'engagement (mois)", null=True, blank=True,
        help_text="Si vide : engagement à durée indéterminée (résiliable, avec effet à la fin du bail en cours)",
    )

    class Meta:
        ordering = ["nom", "prenom"]

    def __str__(self):
        return f"{self.civilite} {self.prenom} {self.nom}".strip()

    def montant_garanti(self):
        if self.montant_max:
            return self.montant_max
        if self.bail and self.duree_mois:
            return self.bail.loyer_cc * self.duree_mois
        return None


class DocumentLocataire(models.Model):
    """Document rangé sur la fiche du locataire : bail signé, état des lieux signé, attestation d'assurance…"""

    class Type(models.TextChoices):
        BAIL = "bail", "Bail signé"
        EDL = "edl", "État des lieux signé"
        CAUTION = "caution", "Acte de cautionnement signé"
        ASSURANCE = "assurance", "Attestation d'assurance"
        IDENTITE = "identite", "Pièce d'identité"
        JUSTIFICATIF = "justificatif", "Justificatif (revenus, domicile…)"
        COURRIER = "courrier", "Courrier"
        AUTRE = "autre", "Autre"

    locataire = models.ForeignKey(Locataire, on_delete=models.CASCADE, related_name="documents")
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.AUTRE)
    titre = models.CharField(max_length=150, blank=True, help_text="Facultatif, ex. « Attestation MAIF 2026 »")
    fichier = models.FileField(upload_to=_chemin_piece, storage=stockage_prive)
    date = models.DateField(default=date.today)

    class Meta:
        verbose_name = "document"
        ordering = ["-date", "-pk"]

    def __str__(self):
        return self.titre or self.get_type_display()
