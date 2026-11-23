"""Propriétaires, syndics, biens, lots et photos des lots."""
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
    email = models.EmailField("adresse électronique", blank=True, help_text="Indiquée dans les baux")
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    pourcentage_detention = models.DecimalField(
        "% de détention (foyer fiscal)", max_digits=5, decimal_places=2, default=100,
        help_text="Part détenue personnellement : sert uniquement à calculer la part à reporter dans votre déclaration de revenus "
                  "(page Annexe 2072).")
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


def _chemin_photo_bien(instance, nom):
    return f"photos/biens/{nom}"


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
    # caractéristiques de l'immeuble, reprises dans les baux
    type_habitat = models.CharField("type d'habitat", max_length=12, blank=True,
                                    choices=[("collectif", "Immeuble collectif"), ("individuel", "Immeuble individuel (maison)")])
    regime_juridique = models.CharField("régime juridique", max_length=15, blank=True,
                                        choices=[("copropriete", "Copropriété"), ("monopropriete", "Monopropriété")])
    gardiennage = models.BooleanField(default=False)
    ascenseur = models.BooleanField(default=False)
    chauffage_collectif = models.BooleanField("chauffage collectif", default=False)
    antenne_tv = models.BooleanField("antenne TV collective", default=False)
    vide_ordures = models.BooleanField("vide-ordures", default=False)
    interphone = models.BooleanField("interphone / digicode", default=False)
    espaces_verts = models.BooleanField("espace(s) vert(s)", default=False)
    eau_chaude_collective = models.BooleanField("eau chaude collective", default=False)
    local_velos = models.BooleanField("local vélos", default=False)
    fibre = models.BooleanField("fibre optique", default=False)
    # zone tendue
    zone_tendue = models.BooleanField("zone tendue", default=False)
    encadrement_loyers = models.BooleanField("encadrement des loyers", default=False)
    residence_principale = models.BooleanField(
        "résidence principale", default=False,
        help_text="Logement que vous habitez (non loué) : ses prêts sont présentés à part du locatif.")
    vendu = models.BooleanField("bien vendu", default=False)
    date_vente = models.DateField("date de vente", null=True, blank=True)

    class Comptabilite(models.TextChoices):
        FONCIER = "foncier", "Revenus fonciers (2072 ou 2044)"
        LMNP = "lmnp", "LMNP (rubriques)"
        SOCIETE = "societe", "Société à l'IR, BIC (rubriques)"
        AUCUNE = "", "Aucune (résidence principale)"

    comptabilite = models.CharField(
        "comptabilité", max_length=10, choices=Comptabilite.choices, default=Comptabilite.FONCIER, blank=True,
        help_text="Déduite du régime fiscal : range le bien dans le bon menu Comptabilité et choisit la codification de "
                  "ses factures (lignes 2072 / 2044 pour les revenus fonciers, rubriques pour le meublé et les sociétés).")

    class RegimeFiscal(models.TextChoices):
        NU = "nu", "Location nue en nom propre (revenus fonciers)"
        SCI_IR = "sci_ir", "SCI à l'IR (revenus fonciers, déclaration 2072)"
        LMNP = "lmnp", "Meublé en nom propre : LMNP (non professionnel)"
        LMP = "lmp", "Meublé en nom propre : LMP (professionnel)"
        SOCIETE_IR = "societe_ir", "SARL de famille ou société à l'IR (BIC)"
        SOCIETE_IS = "societe_is", "Société à l'IS (SCI ou SARL)"
        NON_LOUE = "non_loue", "Non loué (résidence principale, bien vide)"

    class OptionFiscale(models.TextChoices):
        REEL = "reel", "Réel (charges réelles)"
        MICRO = "micro", "Micro (abattement forfaitaire)"

    # comptabilité tenue pour chaque régime
    COMPTA_DU_REGIME = {"nu": "foncier", "sci_ir": "foncier", "lmnp": "lmnp", "lmp": "lmnp", "societe_ir": "societe",
                        "societe_is": "societe", "non_loue": ""}
    REGIMES_MICRO_POSSIBLE = ("nu", "lmnp", "lmp")

    regime_fiscal = models.CharField(
        "régime fiscal", max_length=12, choices=RegimeFiscal.choices, blank=True,
        help_text="Choisit la comptabilité du bien, ses formulaires (2044, 2072, BIC) et les alertes de seuils. "
                  "La courte durée (Airbnb…) est un meublé : choisissez LMNP ou LMP, puis réglez le lot en courte durée.")
    option_fiscale = models.CharField(
        "micro ou réel", max_length=5, choices=OptionFiscale.choices, default=OptionFiscale.REEL,
        help_text="Micro : abattement forfaitaire (micro-foncier 30 %, micro-BIC 50 % ou 30 %). Réel : charges, intérêts "
                  "et amortissements déduits. Les SCI et les sociétés sont toujours au réel.")
    regime_a_verifier = models.BooleanField(
        "régime à vérifier", default=False, editable=False,
        help_text="Régime déduit automatiquement de l'ancienne comptabilité : à confirmer en enregistrant la fiche du bien.")
    notes = models.TextField(blank=True)
    photo = models.ImageField("photo du bien", upload_to=_chemin_photo_bien, blank=True,
                              help_text="Affichée sur la carte du bien en vue galerie. Sans photo, la galerie reprend la "
                                        "photo principale d'un de ses lots.")

    EQUIPEMENTS_COMMUNS = ["gardiennage", "ascenseur", "chauffage_collectif", "antenne_tv", "vide_ordures", "interphone",
                           "espaces_verts", "eau_chaude_collective", "local_velos", "fibre"]

    class Meta:
        ordering = ["proprietaire__nom", "nom"]

    @property
    def equipements_communs(self):
        return [self._meta.get_field(c).verbose_name for c in self.EQUIPEMENTS_COMMUNS if getattr(self, c)]

    def __str__(self):
        return f"{self.nom} ({self.ville})"

    def regime_deduit(self):
        """Régime qui correspond à l'ancienne « comptabilité » du bien et au type de son propriétaire."""
        type_proprio = self.proprietaire.type if self.proprietaire_id else ""
        societe = type_proprio not in ("", Proprietaire.Type.NOM_PROPRE)
        if type_proprio in (Proprietaire.Type.SCI_IS,):
            return self.RegimeFiscal.SOCIETE_IS
        if self.comptabilite == self.Comptabilite.FONCIER:
            return self.RegimeFiscal.SCI_IR if societe else self.RegimeFiscal.NU
        if self.comptabilite == self.Comptabilite.LMNP:
            return self.RegimeFiscal.SOCIETE_IR if societe else self.RegimeFiscal.LMNP
        if self.comptabilite == self.Comptabilite.SOCIETE:
            return self.RegimeFiscal.SOCIETE_IR
        return self.RegimeFiscal.NON_LOUE

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._charge = {f: getattr(instance, f) for f in ("regime_fiscal", "comptabilite") if f in field_names}
        return instance

    def save(self, *args, **kwargs):
        # le régime fiscal commande la comptabilité ; sans régime (ancienne fiche), il se déduit de la comptabilité,
        # de même quand seule la comptabilité a été changée par le code
        charge = getattr(self, "_charge", {})
        if charge and self.regime_fiscal == charge.get("regime_fiscal") and self.comptabilite != charge.get("comptabilite"):
            self.regime_fiscal = ""
        if not self.regime_fiscal:
            self.regime_fiscal = self.regime_deduit()
        self.comptabilite = self.COMPTA_DU_REGIME[self.regime_fiscal]
        if self.regime_fiscal not in self.REGIMES_MICRO_POSSIBLE:
            self.option_fiscale = self.OptionFiscale.REEL
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"regime_fiscal", "comptabilite", "option_fiscale"}
        super().save(*args, **kwargs)
        self._charge = {"regime_fiscal": self.regime_fiscal, "comptabilite": self.comptabilite}

    REGIMES_COURTS = {"nu": "Location nue", "sci_ir": "SCI à l'IR", "lmnp": "LMNP", "lmp": "LMP",
                      "societe_ir": "Société à l'IR (BIC)", "societe_is": "Société à l'IS", "non_loue": "Non loué"}

    @property
    def regime_court(self):
        return self.REGIMES_COURTS.get(self.regime_fiscal, "–")

    @property
    def micro(self):
        return self.option_fiscale == self.OptionFiscale.MICRO and self.regime_fiscal in self.REGIMES_MICRO_POSSIBLE

    @property
    def photo_carte(self):
        """URL de la photo de la carte en galerie : celle du bien, sinon la photo principale du premier lot qui en a une."""
        if self.photo:
            return self.photo.url
        for lot in self.lots.all():
            photos = list(lot.photos.all())
            if photos:
                return photos[0].image.url
        return ""

    @property
    def meuble_fiscal(self):
        """Vrai si le bien est déclaré en meublé (BIC) : LMNP, LMP ou société à l'IR."""
        return self.regime_fiscal in ("lmnp", "lmp", "societe_ir")


class Lot(models.Model):
    """Unité louable : appartement, maison, parking, local..."""

    class Type(models.TextChoices):
        APPARTEMENT = "appartement", "Appartement"
        MAISON = "maison", "Maison"
        STUDIO = "studio", "Studio"
        GARAGE = "garage", "Garage"
        PARKING = "parking", "Place de parking"
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
    mode_chauffage = models.CharField("chauffage", max_length=10, blank=True, choices=[("individuel", "Individuel"), ("collectif", "Collectif")])
    chauffage = models.CharField("précision chauffage", max_length=100, blank=True, help_text="ex. « gaz », « électrique »")
    mode_eau_chaude = models.CharField("eau chaude", max_length=12, blank=True, choices=[("individuelle", "Individuelle"), ("collective", "Collective")])
    eau_chaude = models.CharField("précision eau chaude", max_length=100, blank=True, help_text="ex. « électrique (ballon) »")
    code_porte = models.CharField("code porte", max_length=30, blank=True)
    cave = models.CharField("cave n°", max_length=30, blank=True, help_text="Vide s'il n'y en a pas")
    parking = models.CharField("parking n°", max_length=30, blank=True, help_text="Vide s'il n'y en a pas")
    clause_particuliere = models.TextField("clause particulière du bail", blank=True,
                                           help_text="Reprise dans le bail, ex. WC broyeur : ne rien jeter d'autre que du papier toilette…")
    classe_dpe = models.CharField("classe DPE", max_length=1, blank=True, choices=[(c, c) for c in "ABCDEFG"])
    classe_ges = models.CharField("classe GES", max_length=1, blank=True, choices=[(c, c) for c in "ABCDEFG"])
    numero_dpe = models.CharField("n° du DPE", max_length=13, blank=True,
                                  help_text="13 caractères, en haut à droite du DPE (ex. 2369E0123456X) : sert à le vérifier sur l'ADEME")
    date_dpe = models.DateField("date du DPE", null=True, blank=True, help_text="Date de réalisation (« établi le »)")
    fin_validite_dpe = models.DateField("fin de validité du DPE", null=True, blank=True,
                                        help_text="Calculée depuis la date du DPE si vide (10 ans ; DPE d'avant juillet 2021 : plus valables)")
    energie_min = models.PositiveIntegerField("dépenses d'énergie annuelles estimées, min (€)", null=True, blank=True, help_text="Indiquées sur le DPE")
    energie_max = models.PositiveIntegerField("dépenses d'énergie annuelles estimées, max (€)", null=True, blank=True)
    energie_annee = models.PositiveSmallIntegerField("année de référence des prix de l'énergie", null=True, blank=True, help_text="ex. 2023, indiquée sur le DPE")
    equipements = models.TextField("équipements", blank=True, help_text="Cuisine équipée, interphone, fibre…")
    annexes = models.TextField("annexes privatives", blank=True, help_text="Cave, parking, jardin…")
    pieces = models.TextField(
        "pièces (pour l'état des lieux)", blank=True,
        help_text="Une pièce par ligne. Si vide, une liste standard est proposée selon le nombre de pièces.",
    )
    pdl = models.CharField("n° PDL électricité", max_length=20, blank=True)
    exploitation = models.CharField(
        "mode d'exploitation", max_length=10, default="longue",
        choices=[("longue", "Location classique (bail)"), ("courte", "Location courte durée (Airbnb, Booking…)")],
        help_text="Courte durée : suivi dans Courte durée et bloc à compléter dans Finances › Suivi des encaissements",
    )
    lmnp = models.BooleanField("exploité en LMNP", default=False, help_text="Location meublée non professionnelle : suivi dans Courte durée › Biens en LMNP")
    tourisme_classe = models.BooleanField(
        "meublé de tourisme classé", default=False,
        help_text="Courte durée avec classement en étoiles (Atout France) : micro-BIC à 50 % jusqu'à 77 700 €. "
                  "Non classé : 30 % jusqu'à 15 000 € (règles depuis les revenus 2025).")
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
        if self.designation.strip().lower() == self.bien.nom.strip().lower():
            return self.designation  # « Chazay » plutôt que « Chazay – Chazay »
        return f"{self.bien.nom} – {self.designation}"

    @property
    def bail_actif(self):
        return self.baux.filter(actif=True).first()

    @property
    def dpe_fin_validite(self):
        """Fin de validité du DPE : saisie, sinon calculée depuis sa date (None si on ne sait pas)."""
        from .. import dpe

        return self.fin_validite_dpe or dpe.fin_validite(self.date_dpe)

    def dpe_alertes(self, aujourdhui=None):
        """Validité du DPE et conséquences de la classe (loi Climat) : liste de {niveau, texte}, la plus grave d'abord."""
        from datetime import timedelta

        from django.utils import timezone

        from .. import dpe

        aujourdhui = aujourdhui or timezone.localdate()
        alertes = []
        fin = self.dpe_fin_validite
        if fin and fin < aujourdhui:
            alertes.append({"niveau": "rouge", "texte": f"DPE expiré depuis le {fin:%d/%m/%Y} : à refaire avant tout nouveau bail ou annonce."})
        elif fin and fin <= aujourdhui + timedelta(days=dpe.PREVENIR_JOURS):
            alertes.append({"niveau": "orange", "texte": f"DPE valable jusqu'au {fin:%d/%m/%Y} : à refaire avant la prochaine relocation."})
        alertes += dpe.loi_climat(self.classe_dpe, self.bien.code_postal, aujourdhui)
        ordre = {"rouge": 0, "orange": 1, "info": 2}
        return sorted(alertes, key=lambda a: ordre[a["niveau"]])

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
