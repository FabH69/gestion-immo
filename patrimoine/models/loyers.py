"""Échéances de loyer, paiements, dépôts de garantie, relances et loyers repris d'un ancien suivi."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .biens import Lot
from .location import Bail


def _somme_paiements(objet):
    """Somme des paiements d'un loyer ou d'un paiement reçu. Si la liste a été chargée d'avance
    (prefetch_related("paiements")), on l'additionne sans refaire une requête par ligne : c'est ce qui rend
    les listes rapides (accueil, relances, contrôles)."""
    deja = getattr(objet, "_prefetched_objects_cache", {}).get("paiements")
    if deja is not None:
        return sum((p.montant for p in deja), Decimal(0))
    return objet.paiements.aggregate(s=models.Sum("montant"))["s"] or 0


def _oublier_paiements(paiement):
    """Un paiement vient d'être créé ou supprimé : la liste chargée d'avance sur son loyer et son paiement reçu n'est plus à jour."""
    for nom in ("echeance", "versement"):
        objet = paiement._state.fields_cache.get(nom)
        if objet is not None:
            getattr(objet, "_prefetched_objects_cache", {}).pop("paiements", None)


class Echeance(models.Model):
    """Loyer dû pour un mois donné sur un bail (ou somme à part : régularisation annuelle des charges)."""

    class Statut(models.TextChoices):
        A_PAYER = "a_payer", "À payer"
        PARTIEL = "partiel", "Payé partiellement"
        PAYE = "paye", "Payé"

    class Nature(models.TextChoices):
        LOYER = "loyer", "Loyer du mois"
        REGULARISATION = "regularisation", "Régularisation des charges"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="echeances")
    nature = models.CharField(max_length=15, choices=Nature.choices, default=Nature.LOYER,
                              help_text="« Régularisation des charges » : complément de charges demandé une fois par an, "
                                        "en plus du loyer (mettez le montant dans « charges » et 0 en loyer)")
    periode = models.DateField("période", help_text="Premier jour du mois concerné")
    loyer_hc = models.DecimalField("loyer HC (€)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("charges (€)", max_digits=10, decimal_places=2, default=0)
    date_exigibilite = models.DateField("date d'exigibilité")
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.A_PAYER, editable=False)
    ajustee = models.BooleanField("montant modifié à la main", default=False, editable=False,
                                  help_text="Le loyer ou les charges ont été corrigés à la main : ils ne sont plus recalculés automatiquement")
    remise = models.DecimalField("remise ou abandon (€)", max_digits=10, decimal_places=2, default=0,
                                 help_text="Partie du loyer que vous ne demandez plus (geste commercial, impayé abandonné) : "
                                           "elle solde le loyer sans encaissement")
    motif_remise = models.CharField("motif de la remise", max_length=255, blank=True)

    class Meta:
        verbose_name = "loyer"
        verbose_name_plural = "loyers"
        ordering = ["-periode", "bail__lot__bien__nom"]
        constraints = [models.UniqueConstraint(fields=["bail", "periode", "nature"], name="echeance_unique_par_mois")]

    def __str__(self):
        if self.nature == self.Nature.REGULARISATION:
            return f"{self.bail.lot} – régularisation des charges {self.periode:%m/%Y}"
        return f"{self.bail.lot} – {self.periode:%m/%Y}"

    @property
    def est_regularisation(self):
        return self.nature == self.Nature.REGULARISATION

    @property
    def total(self):
        return self.loyer_hc + self.charges

    @property
    def montant_paye(self):
        return _somme_paiements(self)

    @property
    def reste_du(self):
        return self.total - self.montant_paye - (self.remise or 0)

    @property
    def document(self):
        """(code, libellé) du PDF à remettre : quittance si soldé, reçu si payé en partie (obligatoire), sinon avis d'échéance."""
        if self.statut == self.Statut.PAYE and self.montant_paye >= self.total:
            return "quittance", "Quittance"
        if self.statut == self.Statut.PARTIEL or (self.statut == self.Statut.PAYE and self.montant_paye > 0):
            return "recu-partiel", "Reçu de paiement partiel"
        return "avis-echeance", "Avis d'échéance"

    @property
    def fin_periode(self):
        return self.periode.replace(day=calendar.monthrange(self.periode.year, self.periode.month)[1])

    def mettre_a_jour_statut(self):
        getattr(self, "_prefetched_objects_cache", {}).pop("paiements", None)  # toujours relu en base
        paye = self.montant_paye
        if paye + (self.remise or 0) >= self.total:
            self.statut = self.Statut.PAYE
        elif paye > 0:
            self.statut = self.Statut.PARTIEL
        else:
            self.statut = self.Statut.A_PAYER
        self.save(update_fields=["statut"])

    @classmethod
    def rattraper(cls, aujourdhui=None, max_mois=12):
        """Mois oubliés : pour chaque bail actif, crée les loyers manquants entre son premier loyer et le mois en cours
        (trou entre deux loyers ou après le dernier ; au plus 12 mois en arrière). Arrive quand l'appli n'a pas été
        ouverte un mois, ou quand le bail a été saisi ou déplacé après coup. Jamais avant le premier loyer existant :
        pas de loyers anciens inventés. Renvoie le nombre créé."""
        aujourdhui = aujourdhui or date.today()
        courant = aujourdhui.replace(day=1)
        plancher = courant
        for _ in range(max_mois):
            plancher = (plancher - timedelta(days=1)).replace(day=1)
        crees = 0
        for bail in Bail.objects.filter(actif=True):
            premier = bail.echeances.filter(nature=cls.Nature.LOYER).order_by("periode").values_list("periode", flat=True).first()
            if premier is None:
                continue
            mois = max(premier, plancher, bail.date_debut.replace(day=1))
            while mois < courant and not (bail.date_fin and mois > bail.date_fin):
                fin = mois.replace(day=calendar.monthrange(mois.year, mois.month)[1])
                loyer, charges = bail.montants_du_mois(mois)
                e, cree = cls.objects.get_or_create(bail=bail, periode=mois, nature=cls.Nature.LOYER, defaults={
                    "loyer_hc": loyer, "charges": charges, "date_exigibilite": mois.replace(day=min(bail.jour_echeance, fin.day))})
                if cree:
                    Versement.imputer_avances(e)
                    crees += 1
                mois = (mois.replace(day=28) + timedelta(days=4)).replace(day=1)
        return crees

    @classmethod
    def generer_pour_mois(cls, periode):
        """Crée l'échéance du mois pour chaque bail actif ; renvoie le nombre créé."""
        periode = periode.replace(day=1)
        fin = periode.replace(day=calendar.monthrange(periode.year, periode.month)[1])
        crees = 0
        baux = Bail.objects.filter(actif=True, date_debut__lte=fin).exclude(date_fin__lt=periode)
        for bail in baux:
            jour = min(bail.jour_echeance, fin.day)
            loyer, charges = bail.montants_du_mois(periode)
            echeance, cree = cls.objects.get_or_create(
                bail=bail,
                periode=periode,
                nature=cls.Nature.LOYER,
                defaults={
                    "loyer_hc": loyer,
                    "charges": charges,
                    "date_exigibilite": periode.replace(day=jour),
                },
            )
            crees += cree
            if cree:
                Versement.imputer_avances(echeance)
            else:
                echeance.actualiser_loyer()
        return crees

    def actualiser_loyer(self):
        """Échéance pas encore réglée ni corrigée à la main : reprend le loyer en vigueur ce mois-là (après une révision)
        et les charges du bail."""
        if self.ajustee or self.nature != self.Nature.LOYER or self.paiements.exists():
            return False
        loyer, charges = self.bail.montants_du_mois(self.periode)
        if (self.loyer_hc, self.charges) == (loyer, charges):
            return False
        self.loyer_hc, self.charges = loyer, charges
        self.save(update_fields=["loyer_hc", "charges"])
        return True


class ModePaiement(models.TextChoices):
    VIREMENT = "virement", "Virement"
    PRELEVEMENT = "prelevement", "Prélèvement"
    CHEQUE = "cheque", "Chèque"
    ESPECES = "especes", "Espèces"
    CAF = "caf", "CAF / APL"


class Versement(models.Model):
    """Paiement reçu d'un locataire (virement, chèque, CAF…), réparti sur un ou plusieurs loyers.

    C'est la seule façon d'encaisser : chaque part est un paiement rattaché à un loyer ; une part peut aller au dépôt
    de garantie ; ce qui n'est affecté à rien reste en avance et s'impute tout seul sur les loyers suivants,
    sauf si on le rembourse au locataire. Un paiement annulé (erreur, chèque impayé, prélèvement rejeté) reste
    dans l'historique mais ne règle plus rien."""

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="versements")
    date = models.DateField("date de réception")
    montant = models.DecimalField("montant reçu (€)", max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20, choices=ModePaiement.choices, default=ModePaiement.VIREMENT)
    notes = models.CharField(max_length=255, blank=True)
    part_depot = models.DecimalField("dont dépôt de garantie (€)", max_digits=10, decimal_places=2, default=0,
                                     help_text="Partie de ce paiement qui est le dépôt de garantie (souvent avec le premier loyer)")
    rembourse = models.DecimalField("avance remboursée au locataire (€)", max_digits=10, decimal_places=2, default=0,
                                    help_text="Trop-perçu rendu au locataire : il n'est plus gardé pour les loyers suivants")
    date_remboursement = models.DateField("remboursée le", null=True, blank=True)
    annule = models.BooleanField("annulé", default=False,
                                 help_text="Erreur de saisie, chèque sans provision, prélèvement rejeté : le paiement reste dans "
                                           "l'historique mais ne règle plus aucun loyer")
    motif_annulation = models.CharField("motif de l'annulation", max_length=255, blank=True)

    class Meta:
        verbose_name = "paiement reçu"
        verbose_name_plural = "paiements reçus"
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.get_mode_display()} de {self.montant} € le {self.date:%d/%m/%Y} – {self.bail.lot}"

    @property
    def montant_reparti(self):
        return _somme_paiements(self)

    @property
    def avance(self):
        """Partie du paiement affectée à rien (trop-perçu gardé pour les loyers suivants), hors dépôt et remboursement."""
        if self.annule:
            return 0
        return self.montant - self.montant_reparti - (self.part_depot or 0) - (self.rembourse or 0)

    def annuler(self, motif=""):
        """Annule le paiement : ses parts sont retirées des loyers (qui redeviennent à payer), sa part de dépôt aussi."""
        self.paiements.all().delete()
        getattr(self, "_prefetched_objects_cache", {}).pop("paiements", None)
        self.mouvements_depot.all().delete()
        self.annule, self.motif_annulation = True, motif or self.motif_annulation
        self.save(update_fields=["annule", "motif_annulation"])

    def enregistrer_depot(self):
        """Crée le mouvement « encaissement » du dépôt de garantie pour la part de dépôt de ce paiement."""
        if self.part_depot and self.part_depot > 0:
            MouvementDepot.objects.create(bail=self.bail, versement=self, type=MouvementDepot.Type.ENCAISSEMENT, date=self.date,
                                          montant=self.part_depot, mode=self.mode,
                                          justification=f"Part du paiement du {self.date:%d/%m/%Y}")

    @staticmethod
    def repartition_proposee(bail, montant, exclure_versement=None, priorite=None):
        """[(loyer, part)] : le montant couvre d'abord le loyer choisi (bouton « Encaisser » d'une ligne), puis le loyer
        non soldé le plus ancien, puis le suivant…"""
        from decimal import Decimal
        reste = Decimal(montant)
        parts = []
        echeances = list(bail.echeances.exclude(statut=Echeance.Statut.PAYE).order_by("periode", "nature"))
        if priorite is not None:
            echeances.sort(key=lambda e: e.pk != priorite.pk)
        for e in echeances:
            du = e.reste_du
            if du <= 0:
                continue
            part = min(du, reste) if reste > 0 else Decimal(0)
            parts.append((e, part))
            reste -= part
        return parts

    def repartir(self, parts):
        """Enregistre les parts choisies [(loyer, montant)] comme paiements liés à ce versement."""
        for echeance, part in parts:
            if part and part > 0:
                Paiement.objects.create(echeance=echeance, versement=self, date=self.date, montant=part, mode=self.mode,
                                        notes=self.notes or f"{self.get_mode_display()} du {self.date:%d/%m/%Y}")

    @classmethod
    def imputer_avances(cls, echeance):
        """Nouveau loyer : utilise les avances (trop-perçus) du bail, de la plus ancienne à la plus récente."""
        for v in cls.objects.filter(bail=echeance.bail, annule=False).order_by("date", "pk"):
            du = echeance.reste_du
            if du <= 0:
                break
            avance = v.avance
            if avance > 0:
                Paiement.objects.create(echeance=echeance, versement=v, date=v.date, montant=min(avance, du), mode=v.mode,
                                        notes=f"Avance du {v.get_mode_display().lower()} du {v.date:%d/%m/%Y}")


class Paiement(models.Model):
    """Part d'un paiement reçu affectée à un loyer. Règle de base : seul un règlement reçu rend un loyer payé ;
    une part sans paiement reçu est donc refusée (dans le code et dans la base)."""
    Mode = ModePaiement

    echeance = models.ForeignKey(Echeance, on_delete=models.CASCADE, related_name="paiements", verbose_name="échéance")
    versement = models.ForeignKey(Versement, on_delete=models.CASCADE, related_name="paiements",
                                  verbose_name="paiement reçu", help_text="Paiement reçu dont ce paiement est une part")
    date = models.DateField()
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.VIREMENT)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.montant} € le {self.date:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        if not self.versement_id:
            raise ValueError("Un loyer ne se règle que par un paiement reçu : passez par « Encaisser » (Versement).")
        super().save(*args, **kwargs)
        _oublier_paiements(self)
        self.echeance.mettre_a_jour_statut()

    @classmethod
    def recu(cls, echeance, date, montant, mode=ModePaiement.VIREMENT, notes=""):
        """Enregistre un paiement reçu du locataire entièrement affecté à ce loyer (raccourci : tests, données de démo)."""
        v = Versement.objects.create(bail=echeance.bail, date=date, montant=montant, mode=mode, notes=notes)
        return cls.objects.create(echeance=echeance, versement=v, date=date, montant=montant, mode=mode, notes=notes)



@receiver(post_delete, sender=Paiement)
def _statut_apres_suppression(sender, instance, **kwargs):
    """Paiement supprimé (seul, ou avec le virement dont il est une part) : le statut du loyer est recalculé."""
    _oublier_paiements(instance)
    echeance = Echeance.objects.filter(pk=instance.echeance_id).first()
    if echeance:
        echeance.mettre_a_jour_statut()


class MouvementDepot(models.Model):
    """Compte séparé des dépôts de garantie : encaissements, retenues et restitutions, hors loyers."""

    class Type(models.TextChoices):
        ENCAISSEMENT = "encaissement", "Encaissement"
        RETENUE = "retenue", "Retenue (dégradations, charges)"
        RESTITUTION = "restitution", "Restitution au locataire"

    bail = models.ForeignKey(Bail, on_delete=models.PROTECT, related_name="mouvements_depot")
    versement = models.ForeignKey(Versement, on_delete=models.CASCADE, null=True, blank=True, related_name="mouvements_depot",
                                  verbose_name="paiement reçu", help_text="Paiement reçu dont ce dépôt est une part")
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.ENCAISSEMENT)
    date = models.DateField()
    montant = models.DecimalField("montant (€)", max_digits=10, decimal_places=2, help_text="Toujours positif")
    mode = models.CharField(max_length=20, choices=Paiement.Mode.choices, default=Paiement.Mode.VIREMENT)
    justification = models.CharField("motif", max_length=255, blank=True, help_text="Motif de la retenue (ex. casse d'une lampe) et justificatif : devis ou facture")

    class Meta:
        verbose_name = "mouvement de dépôt de garantie"
        verbose_name_plural = "dépôts de garantie"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.get_type_display()} {self.montant} € – {self.bail.lot}"

    @property
    def montant_signe(self):
        return self.montant if self.type == self.Type.ENCAISSEMENT else -self.montant


class EncaissementHistorique(models.Model):
    """Loyer encaissé repris d'un ancien suivi (Excel) : montant du mois pour un logement, sans détail par échéance."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="encaissements_historiques")
    mois = models.DateField(help_text="Premier jour du mois")
    montant = models.DecimalField("montant encaissé (€)", max_digits=10, decimal_places=2)
    charges = models.DecimalField("dont charges (€)", max_digits=10, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "loyer encaissé (historique)"
        verbose_name_plural = "loyers encaissés (historique)"
        ordering = ["lot", "-mois"]
        constraints = [models.UniqueConstraint(fields=["lot", "mois"], name="encaissement_historique_unique_par_mois")]

    def __str__(self):
        return f"{self.lot} – {self.mois:%m/%Y} : {self.montant} €"


class Relance(models.Model):
    """Relance d'un locataire (ou de sa caution) pour des loyers impayés : historique des courriers et mails préparés."""

    class Niveau(models.TextChoices):
        RAPPEL = "rappel", "1. Rappel amiable"
        RELANCE = "relance", "2. Relance"
        MISE_EN_DEMEURE = "mise_en_demeure", "3. Mise en demeure"
        GARANT = "garant", "Information de la caution"

    class Mode(models.TextChoices):
        MAIL = "mail", "Mail (Outlook)"
        COURRIER = "courrier", "Courrier (PDF)"

    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, related_name="relances")
    niveau = models.CharField(max_length=20, choices=Niveau.choices)
    date = models.DateField(default=date.today)
    montant = models.DecimalField("montant réclamé (€)", max_digits=10, decimal_places=2)
    detail = models.CharField("loyers concernés", max_length=300, blank=True)
    mode = models.CharField(max_length=10, choices=Mode.choices)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.get_niveau_display()} du {self.date:%d/%m/%Y} – {self.bail.lot}"
