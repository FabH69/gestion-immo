import calendar
from decimal import Decimal
from datetime import date, timedelta

from django.db import models


class Proprietaire(models.Model):
    """Entité qui détient des biens : nom propre, SCI, SARL de famille..."""

    class Type(models.TextChoices):
        NOM_PROPRE = "nom_propre", "Nom propre"
        SCI_IR = "sci_ir", "SCI à l'IR"
        SCI_IS = "sci_is", "SCI à l'IS"
        SARL_FAMILLE = "sarl_famille", "SARL de famille"
        AUTRE = "autre", "Autre"

    nom = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=Type.choices)
    siret = models.CharField("SIRET", max_length=14, blank=True)
    rcs = models.CharField("RCS", max_length=60, blank=True, help_text="ex. « RCS Lyon 123 456 789 »")
    date_immatriculation = models.DateField("date d'immatriculation", null=True, blank=True)
    representant = models.CharField("représenté par", max_length=200, blank=True, help_text="Gérant de la SCI ou de la SARL, ex. « M. Fabien X, gérant »")
    adresse = models.TextField(blank=True, help_text="Numéro et rue (et complément éventuel)")
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "propriétaire"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    @property
    def adresse_complete(self):
        return "\n".join(filter(None, [self.adresse, f"{self.code_postal} {self.ville}".strip()]))


class Syndic(models.Model):
    """Syndic de copropriété ou régie."""

    nom = models.CharField(max_length=150)
    adresse = models.CharField(max_length=255, blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    site = models.URLField("site internet / espace copropriétaire", blank=True)
    contact_nom = models.CharField("contact principal", max_length=150, blank=True, help_text="Gestionnaire de la copropriété")
    contact_email = models.EmailField("e-mail du contact", blank=True)
    contact_telephone = models.CharField("téléphone du contact", max_length=20, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["nom", "ville"]

    def __str__(self):
        return f"{self.nom} ({self.ville})" if self.ville else self.nom


class SiteUtile(models.Model):
    """Lien affiché dans le menu « Sites utiles »."""

    nom = models.CharField(max_length=60, help_text="Texte affiché dans le menu")
    url = models.URLField("adresse du site", max_length=500)
    ordre = models.PositiveSmallIntegerField(default=0, help_text="Les plus petits en premier")

    class Meta:
        verbose_name = "site utile"
        verbose_name_plural = "sites utiles"
        ordering = ["ordre", "nom"]

    def __str__(self):
        return self.nom


class Bien(models.Model):
    """Immeuble ou adresse physique, qui contient un ou plusieurs lots."""

    proprietaire = models.ForeignKey(
        Proprietaire, on_delete=models.PROTECT, related_name="biens", verbose_name="propriétaire"
    )
    nom = models.CharField(max_length=200, help_text="Nom court, ex. « Immeuble rue Victor Hugo »")
    adresse = models.CharField(max_length=255)
    code_postal = models.CharField(max_length=10)
    ville = models.CharField(max_length=100)
    syndic = models.ForeignKey(Syndic, on_delete=models.SET_NULL, null=True, blank=True, related_name="biens", help_text="Syndic de copropriété ou régie")
    date_acquisition = models.DateField("date d'acquisition", null=True, blank=True)
    prix_acquisition = models.DecimalField(
        "prix d'acquisition (€)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["proprietaire__nom", "nom"]

    def __str__(self):
        return f"{self.nom} ({self.ville})"


class Lot(models.Model):
    """Unité louable : appartement, maison, parking, local..."""

    class Type(models.TextChoices):
        APPARTEMENT = "appartement", "Appartement"
        MAISON = "maison", "Maison"
        STUDIO = "studio", "Studio"
        PARKING = "parking", "Parking / garage"
        CAVE = "cave", "Cave"
        LOCAL_COMMERCIAL = "local_commercial", "Local commercial"
        BUREAU = "bureau", "Bureau"

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="lots")
    designation = models.CharField("désignation", max_length=100, help_text="ex. « Apt 2e gauche »")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.APPARTEMENT)
    surface = models.DecimalField("surface (m²)", max_digits=7, decimal_places=2, null=True, blank=True)
    nb_pieces = models.PositiveSmallIntegerField("nombre de pièces", null=True, blank=True)
    meuble = models.BooleanField("meublé", default=False)
    etage = models.CharField("étage / porte", max_length=50, blank=True)
    annee_construction = models.CharField("période de construction", max_length=30, blank=True, help_text="ex. « avant 1949 », « 1975 »")
    regime_juridique = models.CharField("régime juridique", max_length=30, blank=True, choices=[("monopropriete", "Monopropriété"), ("copropriete", "Copropriété")])
    chauffage = models.CharField(max_length=100, blank=True, help_text="ex. « individuel gaz », « collectif »")
    eau_chaude = models.CharField("eau chaude", max_length=100, blank=True, help_text="ex. « individuelle électrique »")
    classe_dpe = models.CharField("classe DPE", max_length=1, blank=True, choices=[(c, c) for c in "ABCDEFG"])
    classe_ges = models.CharField("classe GES", max_length=1, blank=True, choices=[(c, c) for c in "ABCDEFG"])
    energie_min = models.PositiveIntegerField("dépenses d'énergie annuelles estimées, min (€)", null=True, blank=True, help_text="Indiquées sur le DPE")
    energie_max = models.PositiveIntegerField("dépenses d'énergie annuelles estimées, max (€)", null=True, blank=True)
    energie_annee = models.PositiveSmallIntegerField("année de référence des prix de l'énergie", null=True, blank=True, help_text="ex. 2023, indiquée sur le DPE")
    equipements = models.TextField("équipements", blank=True, help_text="Cuisine équipée, interphone, fibre…")
    annexes = models.TextField("annexes privatives", blank=True, help_text="Cave, parking, jardin…")
    pieces = models.TextField(
        "pièces (pour l'état des lieux)", blank=True,
        help_text="Une pièce par ligne. Si vide, une liste standard est proposée selon le nombre de pièces.",
    )
    zone_tendue = models.BooleanField("zone tendue", default=False)
    pdl = models.CharField("n° PDL électricité", max_length=20, blank=True)
    vendu = models.BooleanField(default=False, help_text="Lot vendu : conservé pour l'historique")
    date_vente = models.DateField("date de vente", null=True, blank=True)
    ref_import = models.CharField("référence d'import", max_length=40, blank=True, editable=False, help_text="Identifiant Airtable d'origine")
    loyer_propose = models.DecimalField(
        "loyer proposé à la location, charges comprises (€)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Affiché aux candidats et utilisé pour le taux d'effort quand le lot est vacant",
    )
    chaudiere_gaz = models.BooleanField("chaudière gaz individuelle", default=False, help_text="Entretien annuel obligatoire à la charge du locataire")
    date_entretien_chaudiere = models.DateField("dernier entretien de la chaudière", null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["bien__nom", "designation"]

    def __str__(self):
        return f"{self.bien.nom} – {self.designation}"

    @property
    def bail_actif(self):
        return self.baux.filter(actif=True).first()

    @property
    def loyer_reference(self):
        """Loyer charges comprises à comparer aux revenus d'un candidat."""
        if self.loyer_propose:
            return self.loyer_propose
        bail = self.bail_actif
        return bail.loyer_cc if bail else None

    def liste_pieces(self):
        if self.pieces.strip():
            return [p.strip() for p in self.pieces.splitlines() if p.strip()]
        if self.type in (self.Type.PARKING, self.Type.CAVE):
            return [self.get_type_display()]
        if self.type in (self.Type.LOCAL_COMMERCIAL, self.Type.BUREAU):
            return ["Entrée", "Espace principal", "Sanitaires"]
        pieces = ["Entrée", "Séjour", "Cuisine"]
        nb_chambres = max((self.nb_pieces or 1) - 1, 0)
        pieces += ["Chambre" if nb_chambres == 1 else f"Chambre {i}" for i in range(1, nb_chambres + 1)]
        return pieces + ["Salle de bains", "WC"]


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
    actif = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    ref_import = models.CharField("référence d'import", max_length=40, blank=True, editable=False, help_text="Identifiant Airtable d'origine")

    class Meta:
        verbose_name = "bail"
        verbose_name_plural = "baux"
        ordering = ["-actif", "lot__bien__nom", "-date_debut"]

    def __str__(self):
        return f"{self.lot} – {self.get_type_display()} depuis le {self.date_debut:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        if self.loyer_initial is None:
            self.loyer_initial = self.loyer_hc
        super().save(*args, **kwargs)

    @property
    def loyer_cc(self):
        return self.loyer_hc + self.charges

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

    DUREES_MOIS = {"nu": 36, "meuble": 12, "etudiant": 9, "mobilite": 10, "commercial": 108, "professionnel": 72, "parking": 12}

    @property
    def duree_mois(self):
        if self.date_fin:
            return (self.date_fin.year - self.date_debut.year) * 12 + self.date_fin.month - self.date_debut.month
        return self.DUREES_MOIS.get(self.type, 12)


class Echeance(models.Model):
    """Loyer dû pour un mois donné sur un bail."""

    class Statut(models.TextChoices):
        A_PAYER = "a_payer", "À payer"
        PARTIEL = "partiel", "Payé partiellement"
        PAYE = "paye", "Payé"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="echeances")
    periode = models.DateField("période", help_text="Premier jour du mois concerné")
    loyer_hc = models.DecimalField("loyer HC (€)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("charges (€)", max_digits=10, decimal_places=2, default=0)
    date_exigibilite = models.DateField("date d'exigibilité")
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.A_PAYER, editable=False)

    class Meta:
        verbose_name = "loyer"
        verbose_name_plural = "loyers"
        ordering = ["-periode", "bail__lot__bien__nom"]
        constraints = [models.UniqueConstraint(fields=["bail", "periode"], name="echeance_unique_par_mois")]

    def __str__(self):
        return f"{self.bail.lot} – {self.periode:%m/%Y}"

    @property
    def total(self):
        return self.loyer_hc + self.charges

    @property
    def montant_paye(self):
        return self.paiements.aggregate(s=models.Sum("montant"))["s"] or 0

    @property
    def reste_du(self):
        return self.total - self.montant_paye

    @property
    def fin_periode(self):
        return self.periode.replace(day=calendar.monthrange(self.periode.year, self.periode.month)[1])

    def mettre_a_jour_statut(self):
        paye = self.montant_paye
        if paye >= self.total:
            self.statut = self.Statut.PAYE
        elif paye > 0:
            self.statut = self.Statut.PARTIEL
        else:
            self.statut = self.Statut.A_PAYER
        self.save(update_fields=["statut"])

    @classmethod
    def generer_pour_mois(cls, periode):
        """Crée l'échéance du mois pour chaque bail actif ; renvoie le nombre créé."""
        periode = periode.replace(day=1)
        fin = periode.replace(day=calendar.monthrange(periode.year, periode.month)[1])
        crees = 0
        baux = Bail.objects.filter(actif=True, date_debut__lte=fin).exclude(date_fin__lt=periode)
        for bail in baux:
            jour = min(bail.jour_echeance, fin.day)
            _, cree = cls.objects.get_or_create(
                bail=bail,
                periode=periode,
                defaults={
                    "loyer_hc": bail.loyer_hc,
                    "charges": bail.charges,
                    "date_exigibilite": periode.replace(day=jour),
                },
            )
            crees += cree
        return crees


class Paiement(models.Model):
    class Mode(models.TextChoices):
        VIREMENT = "virement", "Virement"
        PRELEVEMENT = "prelevement", "Prélèvement"
        CHEQUE = "cheque", "Chèque"
        ESPECES = "especes", "Espèces"
        CAF = "caf", "CAF / APL"

    echeance = models.ForeignKey(Echeance, on_delete=models.CASCADE, related_name="paiements", verbose_name="échéance")
    date = models.DateField()
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.VIREMENT)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.montant} € le {self.date:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.echeance.mettre_a_jour_statut()

    def delete(self, *args, **kwargs):
        echeance = self.echeance
        super().delete(*args, **kwargs)
        echeance.mettre_a_jour_statut()


class ChangementLocataire(models.Model):
    """Départ d'un locataire et/ou arrivée d'un nouveau sur un lot, avec sa check-list."""

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="changements")
    bail_sortant = models.ForeignKey(
        Bail, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="bail sortant",
        help_text="Rempli automatiquement avec le bail actif du lot si laissé vide.",
    )
    date_conge = models.DateField("réception du congé", null=True, blank=True)
    date_depart = models.DateField("départ prévu", null=True, blank=True,
                                   help_text="Si vide, calculé depuis le congé : 3 mois en location nue, 1 mois en meublé.")
    date_arrivee = models.DateField("arrivée prévue du nouveau locataire", null=True, blank=True)
    termine = models.BooleanField("terminé", default=False)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "changement de locataire"
        verbose_name_plural = "changements de locataire"
        ordering = ["termine", "date_depart"]

    def __str__(self):
        quand = f" – départ le {self.date_depart:%d/%m/%Y}" if self.date_depart else ""
        return f"{self.lot}{quand}"

    def save(self, *args, **kwargs):
        if not self.bail_sortant_id and self.lot_id:
            self.bail_sortant = self.lot.bail_actif
        if not self.date_depart and self.date_conge:
            meuble = self.bail_sortant and self.bail_sortant.type in (Bail.Type.MEUBLE, Bail.Type.ETUDIANT, Bail.Type.MOBILITE)
            self.date_depart = _ajouter_mois(self.date_conge, 1 if meuble else 3)
        nouveau = self.pk is None
        dates = lambda c: (c.date_conge, c.date_depart, c.date_arrivee)
        dates_changees = not nouveau and dates(ChangementLocataire.objects.get(pk=self.pk)) != dates(self)
        super().save(*args, **kwargs)
        if nouveau:
            self.creer_taches()
        elif dates_changees:
            # Ne touche pas aux dates ajustées à la main tant que les dates de référence ne bougent pas.
            self.recalculer_echeances()

    def _date_reference(self, reference):
        return {"conge": self.date_conge or self.date_depart, "depart": self.date_depart, "arrivee": self.date_arrivee}[reference]

    def creer_taches(self):
        from .checklists import MODELE_CHANGEMENT_LOCATAIRE

        for ordre, (phase, libelle, aide, reference, decalage) in enumerate(MODELE_CHANGEMENT_LOCATAIRE):
            Tache.objects.create(
                changement=self, ordre=ordre, phase=phase, libelle=libelle, aide=aide,
                reference=reference, decalage_jours=decalage,
            )
        self.recalculer_echeances()

    def recalculer_echeances(self):
        """Recalcule les dates des tâches non faites, par exemple après un report du départ."""
        for tache in self.taches.filter(faite=False):
            base = self._date_reference(tache.reference)
            tache.echeance = base + timedelta(days=tache.decalage_jours) if base else None
            tache.save(update_fields=["echeance"])

    @property
    def avancement(self):
        total = self.taches.count()
        faites = self.taches.filter(faite=True).count()
        return faites, total


class Tache(models.Model):
    class Phase(models.TextChoices):
        DEPART = "depart", "Sortie du locataire"
        ARRIVEE = "arrivee", "Entrée du nouveau locataire"

    changement = models.ForeignKey(ChangementLocataire, on_delete=models.CASCADE, related_name="taches")
    ordre = models.PositiveSmallIntegerField(default=0)
    phase = models.CharField(max_length=10, choices=Phase.choices)
    libelle = models.CharField("tâche", max_length=200)
    aide = models.TextField("à savoir", blank=True)
    reference = models.CharField(max_length=10, default="depart", editable=False)
    decalage_jours = models.IntegerField(default=0, editable=False)
    echeance = models.DateField("à faire avant le", null=True, blank=True)
    faite = models.BooleanField("faite", default=False)
    date_faite = models.DateField("faite le", null=True, blank=True)
    commentaire = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "tâche"
        ordering = ["changement", "ordre"]

    def __str__(self):
        return self.libelle

    def save(self, *args, **kwargs):
        if self.faite and not self.date_faite:
            self.date_faite = date.today()
        if not self.faite:
            self.date_faite = None
        super().save(*args, **kwargs)


def _ajouter_mois(d, n):
    mois = d.month - 1 + n
    annee, mois = d.year + mois // 12, mois % 12 + 1
    return d.replace(year=annee, month=mois, day=min(d.day, calendar.monthrange(annee, mois)[1]))


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

    class Meta:
        verbose_name = "état des lieux"
        verbose_name_plural = "états des lieux"
        ordering = ["-date"]

    def __str__(self):
        return f"EDL {self.get_type_display().lower()} – {self.bail.lot} – {self.date:%d/%m/%Y}"


class MouvementDepot(models.Model):
    """Compte séparé des dépôts de garantie : encaissements, retenues et restitutions, hors loyers."""

    class Type(models.TextChoices):
        ENCAISSEMENT = "encaissement", "Encaissement"
        RETENUE = "retenue", "Retenue (dégradations, charges)"
        RESTITUTION = "restitution", "Restitution au locataire"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="mouvements_depot")
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.ENCAISSEMENT)
    date = models.DateField()
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2, help_text="Toujours positif")
    mode = models.CharField(max_length=20, choices=Paiement.Mode.choices, default=Paiement.Mode.VIREMENT)
    justification = models.CharField(max_length=255, blank=True, help_text="Pour une retenue : devis ou facture justificative")

    class Meta:
        verbose_name = "mouvement de dépôt de garantie"
        verbose_name_plural = "dépôts de garantie"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.get_type_display()} {self.montant} € – {self.bail.lot}"

    @property
    def montant_signe(self):
        return self.montant if self.type == self.Type.ENCAISSEMENT else -self.montant




class Pret(models.Model):
    """Emprunt immobilier. L'échéancier est recalculé à partir des conditions du prêt."""

    class Type(models.TextChoices):
        AMORTISSABLE = "amortissable", "Amortissable (mensualités constantes)"
        IN_FINE = "in_fine", "In fine (intérêts seuls, capital à la fin)"

    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.PROTECT, related_name="prets", verbose_name="emprunteur")
    bien = models.ForeignKey(Bien, on_delete=models.SET_NULL, null=True, blank=True, related_name="prets", help_text="Bien financé (facultatif)")
    banque = models.CharField(max_length=100)
    reference = models.CharField("n° de prêt", max_length=50, blank=True)
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.AMORTISSABLE)
    montant = models.DecimalField("capital emprunté (€)", max_digits=12, decimal_places=2)
    taux = models.DecimalField("taux nominal annuel (%)", max_digits=5, decimal_places=3)
    duree_mois = models.PositiveSmallIntegerField("durée (mois)")
    date_premiere_echeance = models.DateField("date de la 1re échéance")
    assurance_mensuelle = models.DecimalField("assurance mensuelle (€)", max_digits=8, decimal_places=2, default=0)
    taux_assurance = models.DecimalField(
        "assurance en % du capital restant dû", max_digits=5, decimal_places=3, null=True, blank=True,
        help_text="Seulement si la banque calcule l'assurance sur le capital restant dû et l'inclut dans une échéance constante "
                  "(ex. Caisse d'Épargne) : l'assurance mensuelle ci-dessus est alors ignorée.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "prêt"
        ordering = ["proprietaire__nom", "date_premiere_echeance"]

    def __str__(self):
        return f"{self.banque} {self.reference or ''} – {self.montant:.0f} €".replace("  ", " ")

    @property
    def date_derniere_echeance(self):
        return _ajouter_mois(self.date_premiere_echeance, self.duree_mois - 1)

    @staticmethod
    def _mensualite(capital, taux_mensuel, duree):
        if taux_mensuel == 0:
            return (capital / duree).quantize(Decimal("0.01"))
        return (capital * taux_mensuel / (1 - (1 + taux_mensuel) ** -duree)).quantize(Decimal("0.01"))

    @property
    def mensualite_totale(self):
        """Échéance assurance comprise (constante quand l'assurance est incluse en % du capital restant dû)."""
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            return self._mensualite(self.montant, (self.taux + self.taux_assurance) / Decimal(1200), self.duree_mois)
        return self.mensualite_hors_assurance + self.assurance_mensuelle

    @property
    def mensualite_hors_assurance(self):
        t = self.taux / Decimal(1200)
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            # Première échéance : la part d'assurance diminue ensuite avec le capital restant dû.
            return self.mensualite_totale - (self.montant * self.taux_assurance / Decimal(1200)).quantize(Decimal("0.01"))
        if self.type == self.Type.IN_FINE:
            return (self.montant * t).quantize(Decimal("0.01"))
        if t == 0:
            return (self.montant / self.duree_mois).quantize(Decimal("0.01"))
        return (self.montant * t / (1 - (1 + t) ** -self.duree_mois)).quantize(Decimal("0.01"))

    def echeancier(self):
        """Liste des échéances : (n°, date, mensualité, intérêts, capital remboursé, assurance, capital restant dû)."""
        t = self.taux / Decimal(1200)
        restant = self.montant
        mensualite = self.mensualite_hors_assurance
        assurance_incluse = bool(self.taux_assurance) and self.type == self.Type.AMORTISSABLE
        totale = self.mensualite_totale
        lignes = []
        for n in range(1, self.duree_mois + 1):
            interets = (restant * t).quantize(Decimal("0.01"))
            assurance = (restant * self.taux_assurance / Decimal(1200)).quantize(Decimal("0.01")) if assurance_incluse else self.assurance_mensuelle
            if self.type == self.Type.IN_FINE:
                capital = restant if n == self.duree_mois else Decimal(0)
            elif assurance_incluse:
                capital = restant if n == self.duree_mois else totale - interets - assurance
            else:
                capital = restant if n == self.duree_mois else mensualite - interets
            restant -= capital
            lignes.append({
                "numero": n,
                "date": _ajouter_mois(self.date_premiere_echeance, n - 1),
                "mensualite": interets + capital,
                "interets": interets,
                "capital": capital,
                "assurance": assurance,
                "restant": restant,
            })
        return lignes

    def capital_restant_du(self, a_date):
        """Capital restant dû après les échéances passées à cette date (incluse)."""
        restant = self.montant
        for ligne in self.echeancier():
            if ligne["date"] > a_date:
                break
            restant = ligne["restant"]
        return restant

    def cumul_annee(self, annee):
        lignes = [l for l in self.echeancier() if l["date"].year == annee]
        return {
            "interets": sum((l["interets"] for l in lignes), Decimal(0)),
            "capital": sum((l["capital"] for l in lignes), Decimal(0)),
            "assurance": sum((l["assurance"] for l in lignes), Decimal(0)),
        }


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


class Rappel(models.Model):
    """Rappel saisi à la main (AG de copropriété, taxe foncière, contrôle…), ponctuel ou périodique."""

    class Periodicite(models.TextChoices):
        PONCTUEL = "ponctuel", "Une seule fois"
        MENSUEL = "mensuel", "Tous les mois"
        TRIMESTRIEL = "trimestriel", "Tous les trimestres"
        ANNUEL = "annuel", "Tous les ans"

    MOIS_PAR_PERIODE = {"mensuel": 1, "trimestriel": 3, "annuel": 12}

    class Categorie(models.TextChoices):
        AG = "ag", "Assemblée générale"
        FISCAL = "fiscal", "Impôts et déclarations"
        TRAVAUX = "travaux", "Travaux / entretien"
        ADMINISTRATIF = "administratif", "Administratif"
        AUTRE = "autre", "Autre"

    titre = models.CharField(max_length=200)
    date = models.DateField()
    categorie = models.CharField("catégorie", max_length=20, choices=Categorie.choices, default=Categorie.AUTRE)
    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, null=True, blank=True, related_name="rappels")
    periodicite = models.CharField("périodicité", max_length=12, choices=Periodicite.choices, default=Periodicite.PONCTUEL)
    actif = models.BooleanField(default=True, help_text="Décocher pour arrêter un rappel périodique sans le supprimer")
    fait = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "rappel"
        ordering = ["fait", "date"]

    def __str__(self):
        return self.titre

    def marquer_fait(self):
        """Un rappel périodique passe à l'échéance suivante ; un rappel ponctuel est clos."""
        if self.periodicite in self.MOIS_PAR_PERIODE:
            self.date = _ajouter_mois(self.date, self.MOIS_PAR_PERIODE[self.periodicite])
        else:
            self.fait = True
        self.save()


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


class Candidature(models.Model):
    """Dossier rempli par un candidat locataire via un lien personnel (sans compte)."""

    class Statut(models.TextChoices):
        ENVOYEE = "envoyee", "Lien envoyé"
        RECUE = "recue", "Dossier reçu"
        RETENUE = "retenue", "Retenue"
        REFUSEE = "refusee", "Non retenue"

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="candidatures")
    jeton = models.CharField(max_length=40, unique=True, editable=False)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ENVOYEE)
    cree_le = models.DateTimeField("créée le", auto_now_add=True)
    recue_le = models.DateTimeField("reçue le", null=True, blank=True)
    # Réponses du candidat
    civilite = models.CharField("civilité", max_length=10, blank=True, choices=[("M.", "M."), ("Mme", "Mme")])
    nom = models.CharField(max_length=100, blank=True)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    lieu_naissance = models.CharField("lieu de naissance", max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    adresse_actuelle = models.TextField("adresse actuelle", blank=True)
    situation = models.CharField("situation professionnelle", max_length=20, blank=True, choices=[
        ("cdi", "CDI"), ("cdd", "CDD / intérim"), ("independant", "Indépendant"), ("fonctionnaire", "Fonctionnaire"),
        ("etudiant", "Étudiant"), ("retraite", "Retraité"), ("autre", "Autre"),
    ])
    employeur = models.CharField(max_length=150, blank=True)
    revenus_mensuels = models.DecimalField("revenus nets mensuels (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    nb_occupants = models.PositiveSmallIntegerField("nombre d'occupants", null=True, blank=True)
    date_entree_souhaitee = models.DateField("date d'entrée souhaitée", null=True, blank=True)
    type_garantie = models.CharField("garantie", max_length=20, blank=True, choices=TYPES_GARANTIE)
    garant_nom = models.CharField("garant : nom et prénom", max_length=200, blank=True)
    garant_lien = models.CharField("garant : lien", max_length=100, blank=True)
    garant_email = models.EmailField("garant : e-mail", blank=True)
    garant_telephone = models.CharField("garant : téléphone", max_length=20, blank=True)
    garant_adresse = models.TextField("garant : adresse", blank=True)
    garant_profession = models.CharField("garant : profession", max_length=100, blank=True)
    garant_revenus = models.DecimalField("garant : revenus nets mensuels (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    message = models.TextField(blank=True)
    consentement = models.BooleanField(default=False)

    class Meta:
        ordering = ["-cree_le"]

    def __str__(self):
        qui = f"{self.prenom} {self.nom}".strip() or "(non remplie)"
        return f"{qui} – {self.lot}"

    def save(self, *args, **kwargs):
        if not self.jeton:
            import secrets

            self.jeton = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def taux_effort(self, loyer_cc):
        """Part du loyer dans les revenus du candidat (en %)."""
        if not self.revenus_mensuels or not loyer_cc:
            return None
        return round(loyer_cc / self.revenus_mensuels * 100)

    def creer_locataire(self):
        """Crée le locataire (et son garant) à partir du dossier retenu."""
        locataire = Locataire.objects.create(
            civilite=self.civilite, nom=self.nom, prenom=self.prenom, email=self.email, telephone=self.telephone,
            date_naissance=self.date_naissance, lieu_naissance=self.lieu_naissance, adresse_actuelle=self.adresse_actuelle,
        )
        if self.type_garantie == "garant" and self.garant_nom:
            prenom, _, nom = self.garant_nom.strip().partition(" ")
            Garant.objects.create(
                locataire=locataire, prenom=prenom if nom else "", nom=nom or prenom, lien=self.garant_lien,
                email=self.garant_email, telephone=self.garant_telephone, adresse=self.garant_adresse,
                profession=self.garant_profession, revenus_mensuels=self.garant_revenus,
            )
        self.statut = self.Statut.RETENUE
        self.save(update_fields=["statut"])
        return locataire


def _chemin_photo(instance, nom):
    return f"photos/lot-{instance.lot_id}/{nom}"


class PhotoLot(models.Model):
    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=_chemin_photo)
    legende = models.CharField("légende", max_length=150, blank=True, help_text="ex. « Séjour lumineux »")
    ordre = models.PositiveSmallIntegerField(default=0, help_text="La photo n° 0 sert de photo principale")

    class Meta:
        verbose_name = "photo"
        ordering = ["lot", "ordre", "pk"]

    def __str__(self):
        return self.legende or self.image.name.rsplit("/", 1)[-1]


class Annonce(models.Model):
    """Annonce de location prête à copier sur Leboncoin (ou tout autre site)."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="annonces")
    titre = models.CharField(max_length=100, blank=True, help_text="Laisser vide pour le générer")
    loyer_cc = models.DecimalField("loyer charges comprises (€/mois)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("dont charges (€/mois)", max_digits=10, decimal_places=2, default=0)
    charges_forfait = models.BooleanField("charges au forfait", default=False, help_text="Sinon : provisions avec régularisation annuelle")
    depot_garantie = models.DecimalField("dépôt de garantie (€)", max_digits=10, decimal_places=2, default=0)
    disponible_le = models.DateField("disponible le", null=True, blank=True)
    points_forts = models.TextField("points forts", blank=True, help_text="Une idée par ligne : « proche gare », « balcon plein sud »…")
    texte = models.TextField(blank=True, help_text="Laisser vide puis enregistrer pour générer le texte ; il reste modifiable ensuite.")
    cree_le = models.DateField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]

    def __str__(self):
        return self.titre or f"Annonce {self.lot}"

    def titre_genere(self):
        lot = self.lot
        morceaux = [lot.get_type_display()]
        if lot.nb_pieces and lot.type not in (Lot.Type.PARKING, Lot.Type.CAVE):
            morceaux.append(f"{lot.nb_pieces} pièce{'s' if lot.nb_pieces > 1 else ''}")
        if lot.meuble:
            morceaux.append("meublé")
        if lot.surface:
            morceaux.append(f"{lot.surface:.0f} m²")
        morceaux.append(f"– {lot.bien.ville}")
        return " ".join(morceaux)[:100]

    def texte_genere(self):
        from django.template.loader import render_to_string

        return render_to_string("patrimoine/annonce.txt", {"a": self, "lot": self.lot, "bien": self.lot.bien,
                                                           "points": [p.strip() for p in self.points_forts.splitlines() if p.strip()]}).strip()

    def mentions_manquantes(self):
        lot = self.lot
        manque = []
        if not lot.surface:
            manque.append("surface habitable")
        if not lot.classe_dpe:
            manque.append("classe énergie (DPE)")
        if not lot.classe_ges:
            manque.append("classe climat (GES)")
        if not (lot.energie_min and lot.energie_max):
            manque.append("dépenses d'énergie estimées")
        return manque

    def save(self, *args, **kwargs):
        if not self.titre:
            self.titre = self.titre_genere()
        if not self.texte:
            self.texte = self.texte_genere()
        super().save(*args, **kwargs)


class JournalFait(models.Model):
    """Trace des rappels cochés « Fait » depuis l'accueil, pour le filtre « Déjà fait »."""

    date = models.DateField(auto_now_add=True)
    titre = models.CharField(max_length=200)
    detail = models.CharField("détail", max_length=255, blank=True)
    categorie = models.CharField("catégorie", max_length=50)
    url = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "rappel fait"
        verbose_name_plural = "rappels faits"
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.titre} ({self.date:%d/%m/%Y})"


class ReservationLCD(models.Model):
    """Réservation de location courte durée importée depuis Airbnb, Booking ou saisie (réservation directe)."""

    class Plateforme(models.TextChoices):
        AIRBNB = "airbnb", "Airbnb"
        BOOKING = "booking", "Booking"
        DIRECT = "direct", "Direct"

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="reservations_lcd")
    plateforme = models.CharField(max_length=10, choices=Plateforme.choices)
    reference = models.CharField("n° de réservation", max_length=40)
    voyageur = models.CharField(max_length=150, blank=True)
    arrivee = models.DateField("arrivée")
    depart = models.DateField("départ")
    montant_brut = models.DecimalField("montant brut (€)", max_digits=10, decimal_places=2, default=0)
    commission = models.DecimalField("commission plateforme (€)", max_digits=10, decimal_places=2, default=0)
    frais_service = models.DecimalField("frais de paiement (€)", max_digits=10, decimal_places=2, default=0)
    taxe_sejour = models.DecimalField("taxe de séjour (€)", max_digits=10, decimal_places=2, default=0)
    date_versement = models.DateField("versée le", null=True, blank=True)
    annulee = models.BooleanField("annulée", default=False)

    class Meta:
        verbose_name = "réservation courte durée"
        verbose_name_plural = "réservations courte durée"
        ordering = ["-arrivee"]
        constraints = [models.UniqueConstraint(fields=["plateforme", "reference"], name="reservation_lcd_unique")]

    def __str__(self):
        return f"{self.get_plateforme_display()} {self.reference} – {self.voyageur} ({self.arrivee:%d/%m/%Y})"

    @property
    def nuits(self):
        return (self.depart - self.arrivee).days

    @property
    def net(self):
        """Ce qui revient au propriétaire : brut moins commission, frais et taxe de séjour."""
        return self.montant_brut - self.commission - self.frais_service - self.taxe_sejour


class ParametresLCD(models.Model):
    """Frais de gestion d'un logement en courte durée, appliqués à chaque séjour ou au revenu."""

    lot = models.OneToOneField(Lot, on_delete=models.CASCADE, related_name="parametres_lcd")
    menage_par_sejour = models.DecimalField("ménage par séjour (€)", max_digits=8, decimal_places=2, default=0)
    consommables_par_sejour = models.DecimalField("consommables par séjour (€)", max_digits=8, decimal_places=2, default=0)
    commission_gestion = models.DecimalField(
        "commission de conciergerie (% du revenu net)", max_digits=5, decimal_places=2, default=0,
        help_text="Laisser 0 si vous gérez vous-même",
    )

    class Meta:
        verbose_name = "paramètres courte durée"
        verbose_name_plural = "paramètres courte durée"

    def __str__(self):
        return f"Paramètres LCD – {self.lot}"


class ChargeLCD(models.Model):
    """Charge mensuelle d'un logement en courte durée (électricité, eau, internet, assurance PNO, copropriété…)."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="charges_lcd")
    libelle = models.CharField("libellé", max_length=100)
    montant_mensuel = models.DecimalField("montant mensuel (€)", max_digits=8, decimal_places=2,
                                          help_text="Pour une charge annuelle (taxe foncière…), indiquez le montant divisé par 12")
    debut = models.DateField("à partir du", null=True, blank=True)
    fin = models.DateField("jusqu'au", null=True, blank=True)

    class Meta:
        verbose_name = "charge courte durée"
        verbose_name_plural = "charges courte durée"
        ordering = ["lot", "libelle"]

    def __str__(self):
        return f"{self.libelle} – {self.montant_mensuel} €/mois"

    def active_en(self, premier_jour, dernier_jour):
        return (not self.debut or self.debut <= dernier_jour) and (not self.fin or self.fin >= premier_jour)
