"""Changements de locataire (tâches d'entrée et de sortie), candidatures et annonces."""
from datetime import date, timedelta

from django.core.validators import FileExtensionValidator
from django.db import models

from .communs import _ajouter_mois, _chemin_piece, stockage_prive
from .biens import Lot
from .location import Bail, DocumentLocataire, Garant, Locataire, TYPES_GARANTIE


CLOTURER_BAIL = "cloturer_bail"


def code_de_tache(libelle):
    return CLOTURER_BAIL if libelle.lower().startswith("clôturer le bail") else ""


class ChangementLocataire(models.Model):
    """Départ d'un locataire et/ou arrivée d'un nouveau sur un lot, avec sa check-list."""

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="changements")
    bail_sortant = models.ForeignKey(
        Bail, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="bail sortant",
        help_text="Rempli automatiquement avec le bail actif du lot si laissé vide.",
    )
    locataire_sortant = models.ForeignKey(
        Locataire, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="qui part",
        help_text="Vide : tout le bail se termine. Un colocataire : il quitte le bail, qui continue avec les autres.",
    )
    nouvelle_adresse = models.TextField("nouvelle adresse", blank=True,
                                        help_text="Reprise sur la fiche du locataire et l'état des lieux de sortie quand le bail est clôturé.")
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
        """Tâches créées à partir de la liste par défaut (Réglages > Tâches d'entrée et de sortie)."""
        from ..checklists import MODELE_CHANGEMENT_LOCATAIRE

        if TacheParDefaut.objects.exists():
            modele = [(t.phase, t.libelle, t.aide, t.reference, t.decalage_jours) for t in TacheParDefaut.objects.filter(actif=True)]
        else:
            modele = MODELE_CHANGEMENT_LOCATAIRE
        for ordre, (phase, libelle, aide, reference, decalage) in enumerate(modele):
            Tache.objects.create(
                changement=self, ordre=ordre, phase=phase, libelle=libelle, aide=aide,
                reference=reference, decalage_jours=decalage, code=code_de_tache(libelle),
            )
        self.recalculer_echeances()

    def recalculer_echeances(self):
        """Recalcule les dates des tâches non faites, par exemple après un report du départ."""
        for tache in self.taches.filter(faite=False):
            base = self._date_reference(tache.reference)
            tache.echeance = base + timedelta(days=tache.decalage_jours) if base else None
            tache.save(update_fields=["echeance"])

    @property
    def date_fin_bail(self):
        return self.date_depart or self.date_conge or date.today()

    def partants(self):
        """Locataires qui partent : le colocataire choisi, sinon tous ceux du bail."""
        if not self.bail_sortant:
            return []
        if self.locataire_sortant_id:
            return [self.locataire_sortant]
        return list(self.bail_sortant.locataires.all())

    def cloturer_bail(self):
        """Tâche « Clôturer le bail » cochée : fin du bail (ou départ du seul colocataire), nouvelle adresse reportée."""
        from .outils import Rappel

        bail = self.bail_sortant
        if bail is None:
            return "Aucun bail à clôturer pour ce changement."
        quand = self.date_fin_bail
        partants = self.partants()
        adresse = self.nouvelle_adresse.strip()
        for loc in partants:
            if adresse and loc.adresse_actuelle != adresse:
                loc.adresse_actuelle = adresse
                loc.save(update_fields=["adresse_actuelle"])
        restants = [l for l in bail.locataires.all() if l not in partants]
        if self.locataire_sortant_id and restants:
            bail.locataires.remove(self.locataire_sortant)
            bail.notes = (bail.notes + "\n" if bail.notes else "") + f"{self.locataire_sortant} est parti le {quand:%d/%m/%Y}."
            bail.save(update_fields=["notes"])
            return f"{self.locataire_sortant} a quitté le bail de {self.lot}, qui continue avec {', '.join(str(l) for l in restants)}."
        if adresse:
            edl = bail.etats_des_lieux.filter(type="sortie").first()
            if edl and not edl.nouvelle_adresse:
                edl.nouvelle_adresse = adresse
                edl.save(update_fields=["nouvelle_adresse"])
        bail.date_fin, bail.actif = quand, False
        bail.save(update_fields=["date_fin", "actif"])
        if bail.type_garantie == "visale" and not Rappel.objects.filter(titre__startswith="Mettre fin à la garantie Visale", bien=bail.lot.bien, fait=False).exists():
            Rappel.objects.create(
                titre=f"Mettre fin à la garantie Visale de {', '.join(str(l) for l in partants)} – {bail.lot}",
                date=quand, categorie=Rappel.Categorie.ADMINISTRATIF, bien=bail.lot.bien,
                notes=f"Déclarer la fin du bail sur visale.fr (espace bailleur).{' Visa : ' + bail.detail_garantie if bail.detail_garantie else ''}",
            )
        return f"Bail de {self.lot} clôturé le {quand:%d/%m/%Y} : plus de loyers à partir de cette date."

    def rouvrir_bail(self):
        """Tâche « Clôturer le bail » décochée : on revient en arrière."""
        bail = self.bail_sortant
        if bail is None:
            return ""
        if self.locataire_sortant_id and bail.actif:
            bail.locataires.add(self.locataire_sortant)
            return f"{self.locataire_sortant} est de nouveau sur le bail de {self.lot}."
        if not bail.actif and bail.date_fin == self.date_fin_bail:
            bail.date_fin, bail.actif = None, True
            bail.save(update_fields=["date_fin", "actif"])
            return f"Bail de {self.lot} rouvert."
        return ""

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
    code = models.CharField(max_length=20, blank=True, editable=False, help_text="« cloturer_bail » : cochée, elle clôture le bail")

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
        avant = Tache.objects.filter(pk=self.pk).values_list("faite", flat=True).first() if self.pk else False
        super().save(*args, **kwargs)
        self.message = ""
        if self.code == CLOTURER_BAIL and bool(avant) != self.faite:
            self.message = self.changement.cloturer_bail() if self.faite else self.changement.rouvrir_bail()


class TacheParDefaut(models.Model):
    """Liste des tâches créées à chaque nouveau changement de locataire (modifiable dans les Réglages)."""

    class Reference(models.TextChoices):
        CONGE = "conge", "réception du congé"
        DEPART = "depart", "départ du locataire"
        ARRIVEE = "arrivee", "arrivée du nouveau locataire"

    phase = models.CharField(max_length=10, choices=Tache.Phase.choices)
    ordre = models.PositiveSmallIntegerField(default=0)
    libelle = models.CharField("tâche", max_length=200)
    aide = models.TextField("à savoir", blank=True)
    reference = models.CharField("à compter de", max_length=10, choices=Reference.choices, default=Reference.DEPART)
    decalage_jours = models.IntegerField("délai (jours)", default=0,
                                         help_text="Nombre de jours après la date de référence (négatif = avant).")
    actif = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "tâche par défaut (entrée / sortie)"
        verbose_name_plural = "tâches par défaut (entrée / sortie)"
        ordering = ["-phase", "ordre", "pk"]

    def __str__(self):
        return self.libelle


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
    adresse_actuelle = models.TextField("adresse actuelle (ancien format)", blank=True)
    adresse_rue = models.CharField("adresse actuelle : n° et rue", max_length=255, blank=True)
    adresse_code_postal = models.CharField("code postal", max_length=10, blank=True)
    adresse_ville = models.CharField("ville", max_length=100, blank=True)
    situation = models.CharField("situation professionnelle", max_length=20, blank=True, choices=[
        ("cdi", "CDI"), ("cdd", "CDD / intérim"), ("independant", "Indépendant"), ("fonctionnaire", "Fonctionnaire"),
        ("etudiant", "Étudiant"), ("retraite", "Retraité"), ("autre", "Autre"),
    ])
    employeur = models.CharField(max_length=150, blank=True)
    revenus_mensuels = models.DecimalField("revenus nets mensuels (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    nb_occupants = models.PositiveSmallIntegerField("nombre d'occupants", null=True, blank=True)
    date_entree_souhaitee = models.DateField("date d'entrée souhaitée", null=True, blank=True)
    type_garantie = models.CharField("garantie", max_length=20, blank=True, choices=TYPES_GARANTIE,
                                     help_text="Choisie avant l'envoi : avec « Garant », le formulaire demande l'identité du ou des garants")
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

    @property
    def adresse_complete(self):
        if self.adresse_rue or self.adresse_ville:
            return f"{self.adresse_rue}\n{self.adresse_code_postal} {self.adresse_ville}".strip()
        return self.adresse_actuelle

    def creer_locataire(self):
        """Crée le locataire (et ses garants) à partir du dossier retenu."""
        locataire = Locataire.objects.create(
            civilite=self.civilite, nom=self.nom, prenom=self.prenom, email=self.email, telephone=self.telephone,
            date_naissance=self.date_naissance, lieu_naissance=self.lieu_naissance, adresse_actuelle=self.adresse_complete,
        )
        for g in self.garants.all():
            Garant.objects.create(
                locataire=locataire, civilite=g.civilite, nom=g.nom, prenom=g.prenom, date_naissance=g.date_naissance,
                lieu_naissance=g.lieu_naissance, adresse=g.adresse_complete, email=g.email, telephone=g.telephone, lien=g.lien,
                profession=g.profession, revenus_mensuels=g.revenus_mensuels, piece_identite_numero=g.piece_identite_numero,
                piece_identite_fichier=g.piece_identite_fichier.name,
            )
        if self.type_garantie == "garant" and self.garant_nom and not self.garants.exists():
            prenom, _, nom = self.garant_nom.strip().partition(" ")
            Garant.objects.create(
                locataire=locataire, prenom=prenom if nom else "", nom=nom or prenom, lien=self.garant_lien,
                email=self.garant_email, telephone=self.garant_telephone, adresse=self.garant_adresse,
                profession=self.garant_profession, revenus_mensuels=self.garant_revenus,
            )
        # pièces du dossier rangées dans les documents du locataire (même fichier, pas de copie)
        types = {PieceCandidature.Type.IDENTITE: DocumentLocataire.Type.IDENTITE}
        for piece in self.pieces.all():
            DocumentLocataire.objects.create(locataire=locataire, type=types.get(piece.type, DocumentLocataire.Type.JUSTIFICATIF),
                                             titre=piece.get_type_display()[:150], fichier=piece.fichier.name)
        self.statut = self.Statut.RETENUE
        self.save(update_fields=["statut"])
        return locataire

    def retenir(self):
        """Dossier retenu : locataire, garants et pièces créés, autres candidatures du logement non retenues,
        tâche « choisir le locataire » cochée. Renvoie le locataire."""
        locataire = self.creer_locataire()
        Candidature.objects.filter(lot=self.lot, statut__in=[self.Statut.ENVOYEE, self.Statut.RECUE]).exclude(pk=self.pk) \
            .update(statut=self.Statut.REFUSEE)
        for t in Tache.objects.filter(changement__lot=self.lot, changement__termine=False, faite=False,
                                      libelle__istartswith="Étudier les dossiers"):
            t.faite = True
            t.save()
        return locataire

    def valeurs_bail(self, locataire):
        """Valeurs proposées pour le nouveau bail (reprises de la dernière annonce du logement, sinon du lot)."""
        lot = self.lot
        annonce = lot.annonces.order_by("-pk").first()
        meuble = lot.meuble
        valeurs = {"lot": lot.pk, "locataires": locataire.pk, "type": Bail.Type.MEUBLE if meuble else Bail.Type.NU}
        debut = self.date_entree_souhaitee or (annonce.disponible_le if annonce else None)
        if debut:
            valeurs["date_debut"] = debut.strftime("%d/%m/%Y")
        if annonce:
            hc = annonce.loyer_cc - annonce.charges
            valeurs.update(loyer_hc=hc, loyer_initial=hc, charges=annonce.charges,
                           depot_garantie=annonce.depot_garantie or hc * (2 if meuble else 1),
                           mode_charges="forfait" if annonce.charges_forfait else ("provision" if annonce.charges else "aucune"))
        if self.type_garantie:
            valeurs["type_garantie"] = self.type_garantie
        return valeurs


class GarantCandidature(models.Model):
    """Garant proposé par un candidat dans le formulaire en ligne (un ou deux par dossier)."""

    candidature = models.ForeignKey(Candidature, on_delete=models.CASCADE, related_name="garants")
    civilite = models.CharField("civilité", max_length=10, blank=True, choices=[("M.", "M."), ("Mme", "Mme")])
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100)
    date_naissance = models.DateField("date de naissance")
    lieu_naissance = models.CharField("lieu de naissance", max_length=100)
    adresse_rue = models.CharField("n° et rue", max_length=255)
    code_postal = models.CharField("code postal", max_length=10)
    ville = models.CharField(max_length=100)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=20, blank=True)
    lien = models.CharField("lien avec le candidat", max_length=100, blank=True, help_text="ex. « père », « mère »")
    profession = models.CharField(max_length=100, blank=True)
    revenus_mensuels = models.DecimalField("revenus nets mensuels (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    piece_identite_numero = models.CharField("n° de carte d'identité ou de passeport", max_length=40)
    piece_identite_fichier = models.FileField("copie de la pièce d'identité", upload_to=_chemin_piece, storage=stockage_prive, blank=True,
                                              help_text="Photo ou PDF, 10 Mo maximum")

    class Meta:
        verbose_name = "garant proposé"
        verbose_name_plural = "garants proposés"
        ordering = ["pk"]

    def __str__(self):
        return f"{self.civilite} {self.prenom} {self.nom}".strip()

    @property
    def adresse_complete(self):
        return f"{self.adresse_rue}\n{self.code_postal} {self.ville}"


class Annonce(models.Model):
    """Annonce de location prête à copier sur Leboncoin (ou tout autre site)."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="annonces")
    modele = models.ForeignKey("ModeleAnnonce", on_delete=models.SET_NULL, null=True, blank=True, related_name="annonces",
                               verbose_name="modèle d'annonce",
                               help_text="Proposé automatiquement d'après le lot. Son texte remplace la description générée ; "
                                         "loyer, dépôt de garantie et mentions obligatoires sont ajoutés à la suite.")
    titre = models.CharField(max_length=100, blank=True, help_text="Laisser vide pour le générer")
    loyer_cc = models.DecimalField("loyer charges comprises (€/mois)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("dont charges (€/mois)", max_digits=10, decimal_places=2, default=0)
    charges_forfait = models.BooleanField("charges au forfait", default=False, help_text="Sinon : provisions avec régularisation annuelle")
    depot_garantie = models.DecimalField("dépôt de garantie (€)", max_digits=10, decimal_places=2, default=0,
                                         help_text="Calculé automatiquement : loyer hors charges × 1 mois (location nue) ou × 2 mois (meublé).")
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

        contexte = {"a": self, "lot": self.lot, "bien": self.lot.bien,
                    "points": [p.strip() for p in self.points_forts.splitlines() if p.strip()]}
        if self.modele_id and self.modele.texte.strip():
            # texte du modèle, puis loyer, dépôt et mentions obligatoires (loi Alur)
            return (self.modele.texte.strip() + "\n\n" + render_to_string("patrimoine/annonce_conditions.txt", contexte).strip()).strip()
        return render_to_string("patrimoine/annonce.txt", contexte).strip()

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
            self.titre = (self.modele.titre if self.modele_id and self.modele.titre else self.titre_genere())[:100]
        if not self.depot_garantie and self.loyer_cc:
            # 1 mois de loyer HC en location nue, 2 mois en meublé (calculé avant le texte, qui l'indique)
            self.depot_garantie = (self.loyer_cc - self.charges) * (2 if self.lot.meuble else 1)
        if not self.texte:
            self.texte = self.texte_genere()
        super().save(*args, **kwargs)


class PieceCandidature(models.Model):
    """Justificatif déposé par un candidat (liste limitée par le décret n° 2015-1437 du 5 novembre 2015)."""

    class Type(models.TextChoices):
        IDENTITE = "identite", "Pièce d'identité (carte d'identité, passeport ou titre de séjour)"
        DOMICILE = "domicile", "Justificatif de domicile (3 dernières quittances, attestation d'hébergement ou taxe foncière)"
        ACTIVITE = "activite", "Situation professionnelle (contrat de travail, attestation employeur, carte d'étudiant…)"
        SALAIRES = "salaires", "3 derniers bulletins de salaire (ou justificatifs de ressources)"
        IMPOTS = "impots", "Dernier avis d'imposition"
        AUTRE = "autre", "Autre ressource (bourse, pension, aides au logement…)"

    candidature = models.ForeignKey(Candidature, on_delete=models.CASCADE, related_name="pieces")
    type = models.CharField(max_length=10, choices=Type.choices)
    fichier = models.FileField(upload_to=_chemin_piece, storage=stockage_prive)
    ajoute_le = models.DateTimeField("déposé le", auto_now_add=True)

    class Meta:
        verbose_name = "justificatif"
        ordering = ["type", "pk"]

    def __str__(self):
        return f"{self.get_type_display()} – {self.fichier.name.rsplit('/', 1)[-1]}"


class ModeleAnnonce(models.Model):
    """Texte d'annonce type (description du logement), rattaché à un lot : proposé quand on crée une annonce pour ce lot."""

    nom = models.CharField(max_length=150)
    lot = models.ForeignKey(Lot, on_delete=models.SET_NULL, null=True, blank=True, related_name="modeles_annonce",
                            verbose_name="lot par défaut", help_text="Le modèle est proposé automatiquement pour les annonces de ce lot")
    titre = models.CharField("titre de l'annonce", max_length=100, blank=True, help_text="Laisser vide pour le générer d'après le lot")
    texte = models.TextField(blank=True, help_text="Description du logement. Laisser vide avec un fichier Word : le texte en est repris. "
                                                   "Loyer, dépôt de garantie et mentions obligatoires s'ajoutent tout seuls dans l'annonce.")
    fichier = models.FileField("fichier Word d'origine (.docx)", upload_to="modeles-annonces/", storage=stockage_prive, blank=True,
                               validators=[FileExtensionValidator(["docx"], "Enregistrez le modèle au format Word .docx.")])
    actif = models.BooleanField(default=True, help_text="Décochez pour ne plus le proposer sans le supprimer")
    notes = models.TextField(blank=True)
    modifie_le = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "modèle d'annonce"
        verbose_name_plural = "modèles d'annonces"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    @classmethod
    def par_defaut(cls, lot_id):
        return cls.objects.filter(lot_id=lot_id, actif=True).order_by("-modifie_le").first() if lot_id else None

    def save(self, *args, **kwargs):
        if not self.texte.strip() and self.fichier:
            from ..word import texte_docx

            try:
                self.fichier.open("rb")
                self.texte = texte_docx(self.fichier)
            except Exception:
                pass
        super().save(*args, **kwargs)
