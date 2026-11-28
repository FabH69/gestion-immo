"""Prêts et factures (comptabilité)."""
from decimal import Decimal

from django.core.validators import FileExtensionValidator
from django.db import models

from .communs import _ajouter_mois, stockage_prive
from .biens import Bien, Proprietaire


class Pret(models.Model):
    """Emprunt immobilier. L'échéancier est recalculé à partir des conditions du prêt."""

    class Type(models.TextChoices):
        AMORTISSABLE = "amortissable", "Amortissable (mensualités constantes)"
        IN_FINE = "in_fine", "In fine (intérêts seuls, capital à la fin)"

    class Objet(models.TextChoices):
        ACHAT = "achat", "Achat du bien"
        TRAVAUX = "travaux", "Travaux"
        ACHAT_TRAVAUX = "achat_travaux", "Achat et travaux"
        AUTRE = "autre", "Autre"

    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.PROTECT, related_name="prets", verbose_name="emprunteur")
    bien = models.ForeignKey(Bien, on_delete=models.SET_NULL, null=True, blank=True, related_name="prets", help_text="Bien financé (facultatif)")
    banque = models.CharField(max_length=100)
    reference = models.CharField("n° de prêt", max_length=50, blank=True)
    type = models.CharField(max_length=15, choices=Type.choices, default=Type.AMORTISSABLE)
    objet = models.CharField("objet financé", max_length=15, choices=Objet.choices, blank=True,
                             help_text="Ce que le prêt a financé. Vide = prêt à compléter.")
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
    capital_constate = models.DecimalField(
        "capital restant dû constaté (€)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Montant lu sur un relevé de la banque, s'il diffère du calcul (échéances reportées, remboursement "
                  "anticipé…). L'échéancier repart de ce montant à cette date, avec la même mensualité.")
    capital_constate_le = models.DateField("constaté le", null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "prêt"
        ordering = ["proprietaire__nom", "date_premiere_echeance"]

    def __str__(self):
        return f"{self.banque} {self.reference or ''} – {self.montant:.0f} €".replace("  ", " ")

    @property
    def recale(self):
        return self.capital_constate is not None and self.capital_constate_le is not None

    @property
    def date_derniere_echeance(self):
        banque = self.lignes_banque()
        if banque:
            return banque[-1]["date"]
        if self.recale:
            lignes = self.echeancier()
            if lignes:
                return lignes[-1]["date"]
        return _ajouter_mois(self.date_premiere_echeance, self.duree_mois - 1)

    @staticmethod
    def _mensualite(capital, taux_mensuel, duree):
        if taux_mensuel == 0:
            return (capital / duree).quantize(Decimal("0.01"))
        return (capital * taux_mensuel / (1 - (1 + taux_mensuel) ** -duree)).quantize(Decimal("0.01"))

    @property
    def mensualite_totale(self):
        """Échéance assurance comprise (constante quand l'assurance est incluse en % du capital restant dû)."""
        courante = self._echeance_courante()
        if courante:
            return courante["mensualite"] + courante["assurance"]
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            return self._mensualite(self.montant, (self.taux + self.taux_assurance) / Decimal(1200), self.duree_mois)
        return self.mensualite_hors_assurance + self.assurance_mensuelle

    @property
    def mensualite_hors_assurance(self):
        courante = self._echeance_courante()
        if courante:
            return courante["mensualite"]
        t = self.taux / Decimal(1200)
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            # Première échéance : la part d'assurance diminue ensuite avec le capital restant dû.
            return self.mensualite_totale - (self.montant * self.taux_assurance / Decimal(1200)).quantize(Decimal("0.01"))
        if self.type == self.Type.IN_FINE:
            return (self.montant * t).quantize(Decimal("0.01"))
        if t == 0:
            return (self.montant / self.duree_mois).quantize(Decimal("0.01"))
        return (self.montant * t / (1 - (1 + t) ** -self.duree_mois)).quantize(Decimal("0.01"))

    @property
    def assurance_appliquee(self):
        """Phrase qui dit d'où vient l'assurance de l'échéancier : tableau de la banque, % du capital restant dû,
        montant fixe ou aucune."""
        def e(v):
            return f"{v:,.2f}".replace(",", "\u202f").replace(".", ",")

        banque = self.lignes_banque()
        if banque:
            courante = self._echeance_courante()
            if courante and courante["assurance"]:
                return (f"celle du tableau de la banque ({e(courante['assurance'])} €/mois) : retirez le tableau de la banque "
                        "pour recalculer à partir des conditions")
            return "aucune (tableau de la banque)"
        if self.taux_assurance and self.type == self.Type.AMORTISSABLE:
            return f"{e(self.taux_assurance)} % du capital restant dû, incluse dans l'échéance"
        if self.assurance_mensuelle:
            return f"montant fixe de {e(self.assurance_mensuelle)} €/mois (mettez 0 dans « assurance mensuelle » pour un prêt sans assurance)"
        return "aucune"

    def lignes_banque(self):
        """Tableau d'amortissement remis par la banque, s'il a été importé (sinon liste vide)."""
        if not self.pk:
            return []
        return [{"numero": l.rang, "date": l.date, "mensualite": l.capital + l.interets, "interets": l.interets,
                 "capital": l.capital, "assurance": l.assurance, "restant": l.restant} for l in self.lignes.all()]

    def _echeance_courante(self):
        """Échéance habituelle du tableau de la banque : la plus fréquente des 12 prochaines (ou des dernières si le prêt
        est terminé), pour ne pas afficher une échéance particulière (différé, première ou dernière échéance)."""
        from collections import Counter

        from django.utils import timezone

        lignes = self.lignes_banque()
        if not lignes:
            return None
        debut = timezone.localdate().replace(day=1)
        proches = [l for l in lignes if l["date"] >= debut][:12] or lignes[-12:]
        totaux = Counter(l["mensualite"] + l["assurance"] for l in proches)
        habituelle = totaux.most_common(1)[0][0]
        return next(l for l in proches if l["mensualite"] + l["assurance"] == habituelle)

    def echeancier(self):
        """Liste des échéances : (n°, date, mensualité, intérêts, capital remboursé, assurance, capital restant dû).

        Le tableau de la banque, s'il a été importé, fait foi. Sinon l'échéancier est calculé. Avec un capital restant dû constaté, le capital repart de ce montant après la dernière échéance passée à cette
        date ; la mensualité ne change pas et le prêt s'arrête quand tout est remboursé (durée allongée ou raccourcie)."""
        banque = self.lignes_banque()
        if banque:
            return banque
        t = self.taux / Decimal(1200)
        restant = self.montant
        mensualite = self.mensualite_hors_assurance
        assurance_incluse = bool(self.taux_assurance) and self.type == self.Type.AMORTISSABLE
        totale = self.mensualite_totale
        recale = self.recale
        a_recaler = recale
        if recale and self.capital_constate_le < self.date_premiere_echeance:
            restant, a_recaler = self.capital_constate, False
        lignes = []
        n = 0
        while restant > 0 and n < 1200:
            n += 1
            if n > self.duree_mois and not recale:
                break
            date_ = _ajouter_mois(self.date_premiere_echeance, n - 1)
            interets = (restant * t).quantize(Decimal("0.01"))
            assurance = (restant * self.taux_assurance / Decimal(1200)).quantize(Decimal("0.01")) if assurance_incluse else self.assurance_mensuelle
            fin_prevue = n >= self.duree_mois and not recale
            if self.type == self.Type.IN_FINE:
                capital = restant if n >= self.duree_mois else Decimal(0)
            elif assurance_incluse:
                capital = restant if fin_prevue else min(restant, totale - interets - assurance)
            else:
                capital = restant if fin_prevue else min(restant, mensualite - interets)
            if recale and not a_recaler and restant - capital < 1:
                capital = restant  # reliquat d'arrondi de quelques centimes : soldé avec cette échéance
            restant -= capital
            if a_recaler and date_ <= self.capital_constate_le < _ajouter_mois(self.date_premiere_echeance, n):
                restant, a_recaler = self.capital_constate, False
            lignes.append({
                "numero": n,
                "date": date_,
                "mensualite": interets + capital,
                "interets": interets,
                "capital": capital,
                "assurance": assurance,
                "restant": restant,
            })
        return lignes

    def capital_restant_du(self, a_date):
        """Capital restant dû après les échéances passées à cette date (incluse)."""
        lignes = self.echeancier()
        restant = lignes[0]["restant"] + lignes[0]["capital"] if lignes else self.montant
        for ligne in lignes:
            if ligne["date"] > a_date:
                break
            restant = ligne["restant"]
        return restant

    def mensualite_au(self, a_date):
        """Échéance assurance comprise en vigueur à cette date : la dernière échéance passée à cette date (incluse), ou la
        première si le prêt n'a pas encore commencé. Suit le tableau de la banque (assurance qui change chaque année)."""
        lignes = self.echeancier()
        if not lignes:
            return self.mensualite_totale
        ligne = lignes[0]
        for l in lignes:
            if l["date"] > a_date:
                break
            ligne = l
        return ligne["mensualite"] + ligne["assurance"]

    @property
    def a_completer(self):
        return not self.objet

    def parts_par_lot(self):
        """Part de chaque lot dans le prêt (fractions qui font 1), {lot_id: Decimal}. Lots cochés dans « Lots financés »
        avec leur % saisi, sinon au prorata de la surface (parts égales s'il manque une surface). Aucun lot coché : tous
        les lots du bien. Mise en cache sur l'objet."""
        if hasattr(self, "_parts"):
            return self._parts
        choisis = list(self.lots_finances.all()) if self.pk else []
        if choisis:
            lots = [c.lot for c in choisis]
            pourcents = [c.part for c in choisis]
        else:
            lots = list(self.bien.lots.all()) if self.bien_id else []
            pourcents = [None] * len(lots)
        if not lots:
            self._parts = {}
        elif all(p is not None for p in pourcents) and sum(pourcents):
            total = sum(pourcents)
            self._parts = {l.pk: Decimal(p) / total for l, p in zip(lots, pourcents)}
        elif all(l.surface for l in lots):
            total = sum(l.surface for l in lots)
            self._parts = {l.pk: l.surface / total for l in lots}
        else:
            self._parts = {l.pk: Decimal(1) / len(lots) for l in lots}
        return self._parts

    def part_du_lot(self, lot):
        """Fraction du prêt (intérêts, assurance, échéances) qui revient à ce lot (0 si le prêt ne le finance pas)."""
        lot_id = getattr(lot, "pk", lot)
        return self.parts_par_lot().get(lot_id, Decimal(0))

    @property
    def lots_finances_libelle(self):
        """« Tout le bien » ou « Apt 1er (60 %), Garage (40 %) »."""
        choisis = list(self.lots_finances.all()) if self.pk else []
        if not choisis:
            return "Tout le bien" if self.bien_id else "–"
        parts = self.parts_par_lot()
        return ", ".join(f"{c.lot.designation} ({parts.get(c.lot_id, 0) * 100:.0f} %)" for c in choisis)

    def montants_declares(self, annee):
        """Intérêts et assurance à déclarer pour l'année : l'attestation de la banque si elle est saisie, sinon l'échéancier.
        Renvoie aussi la source : "attestation", "banque" (tableau importé) ou "estimation" (échéancier calculé)."""
        attestation = self.attestations.filter(annee=annee).first() if self.pk else None
        if attestation:
            return {"interets": attestation.interets, "assurance": attestation.assurance, "source": "attestation"}
        cumul = self.cumul_annee(annee)
        return {"interets": cumul["interets"], "assurance": cumul["assurance"],
                "source": "banque" if self.pk and self.lignes.exists() else "estimation"}

    def cumul_annee(self, annee):
        lignes = [l for l in self.echeancier() if l["date"].year == annee]
        return {
            "interets": sum((l["interets"] for l in lignes), Decimal(0)),
            "capital": sum((l["capital"] for l in lignes), Decimal(0)),
            "assurance": sum((l["assurance"] for l in lignes), Decimal(0)),
        }


class PretLot(models.Model):
    """Lot financé par un prêt, avec sa part en %. Sans aucune ligne, le prêt finance tout le bien."""

    pret = models.ForeignKey(Pret, on_delete=models.CASCADE, related_name="lots_finances", verbose_name="prêt")
    lot = models.ForeignKey("Lot", on_delete=models.CASCADE, related_name="prets_finances")
    part = models.DecimalField("part (%)", max_digits=5, decimal_places=2, null=True, blank=True,
                               help_text="Vide sur toutes les lignes : répartition au prorata de la surface des lots.")

    class Meta:
        verbose_name = "lot financé"
        verbose_name_plural = "lots financés"
        constraints = [models.UniqueConstraint(fields=["pret", "lot"], name="pret_lot_unique")]

    def __str__(self):
        return f"{self.pret} › {self.lot}"


class LigneEcheancier(models.Model):
    """Ligne du tableau d'amortissement remis par la banque. Quand un prêt en a, elles remplacent l'échéancier calculé :
    différé, première échéance, assurance qui change chaque année et arrondis sont ceux de la banque."""

    pret = models.ForeignKey(Pret, on_delete=models.CASCADE, related_name="lignes", verbose_name="prêt")
    rang = models.PositiveSmallIntegerField("n°")
    date = models.DateField("date d'échéance")
    capital = models.DecimalField("capital remboursé", max_digits=12, decimal_places=2)
    interets = models.DecimalField("intérêts", max_digits=12, decimal_places=2)
    assurance = models.DecimalField("assurance et frais", max_digits=12, decimal_places=2, default=0)
    restant = models.DecimalField("capital restant dû après l'échéance", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "ligne du tableau d'amortissement"
        verbose_name_plural = "lignes du tableau d'amortissement"
        ordering = ["pret", "date", "rang"]

    def __str__(self):
        return f"{self.pret} – échéance {self.rang} du {self.date:%d/%m/%Y}"

    @property
    def echeance(self):
        return self.capital + self.interets + self.assurance


class AttestationPret(models.Model):
    """Montants de l'attestation fiscale annuelle de la banque : ils remplacent l'échéancier pour la déclaration de l'année
    (2044, annexe 2072), quand l'assurance réellement prélevée diffère du tableau d'amortissement."""

    pret = models.ForeignKey(Pret, on_delete=models.CASCADE, related_name="attestations", verbose_name="prêt")
    annee = models.PositiveSmallIntegerField("année")
    interets = models.DecimalField("intérêts (€)", max_digits=10, decimal_places=2)
    assurance = models.DecimalField("assurance et frais (€)", max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "attestation annuelle de la banque"
        verbose_name_plural = "attestations annuelles de la banque"
        ordering = ["pret", "-annee"]
        constraints = [models.UniqueConstraint(fields=["pret", "annee"], name="attestation_unique_par_annee")]

    def __str__(self):
        return f"{self.pret} – attestation {self.annee}"

    def declaration_qui_verrouille(self):
        """Cerfa déclaré (case « Déclaration effectuée » cochée) qui utilise les intérêts de ce prêt pour cette année."""
        bien_id = self.pret.bien_id if self.pret_id else None
        return DeclarationFaite.verrous().get(self.annee, {}).get(bien_id) if bien_id and self.annee else None

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        avant = AttestationPret.objects.filter(pk=self.pk).first() if self.pk else None
        if avant and (avant.annee, avant.interets, avant.assurance) == (self.annee, self.interets, self.assurance):
            return  # rien ne change
        for a in (avant, self):
            d = a.declaration_qui_verrouille() if a else None
            if d:
                raise ValidationError(f"{d.motif} : l'attestation {a.annee} ne peut plus changer. Pour la corriger, "
                                      f"{d.comment_debloquer}.")


class AnneeCloturee(models.Model):
    """Année comptable clôturée : ses factures ne peuvent plus être ajoutées, modifiées ni supprimées (jusqu'au déverrouillage).
    L'année d'une facture est celle de son paiement (date de la facture si la date de paiement est vide)."""

    annee = models.PositiveSmallIntegerField("année", unique=True)
    cloturee_le = models.DateTimeField("clôturée le", auto_now_add=True)

    class Meta:
        verbose_name = "année clôturée"
        verbose_name_plural = "années clôturées"
        ordering = ["-annee"]

    def __str__(self):
        return f"Année {self.annee} clôturée"

    @classmethod
    def annees(cls):
        return set(cls.objects.values_list("annee", flat=True))

    @classmethod
    def est_cloturee(cls, annee):
        return bool(annee) and cls.objects.filter(annee=annee).exists()


class DeclarationFaite(models.Model):
    """« Déclaration effectuée le … » d'un cerfa pour une année : la saisie qui l'alimente (factures des biens concernés,
    attestations de leurs prêts) est verrouillée tant que la case reste cochée. Décocher garde la ligne dans l'historique."""

    class Formulaire(models.TextChoices):
        F2044 = "2044", "2044 (revenus fonciers en nom propre)"
        F2072 = "2072", "2072 (SCI à l'IR)"
        F2031 = "2031", "2031 et 2033 (LMNP)"
        SOCIETE = "societe", "Société (récapitulatif)"

    annee = models.PositiveSmallIntegerField("année des revenus")
    formulaire = models.CharField(max_length=10, choices=Formulaire.choices)
    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.CASCADE, null=True, blank=True,
                                     help_text="SCI ou société déclarante (2072, société) ; vide pour la 2044 et le LMNP")
    date_declaration = models.DateField("déclaration effectuée le")
    active = models.BooleanField("verrouille la saisie", default=True)
    cochee_le = models.DateTimeField(auto_now_add=True)
    decochee_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "déclaration effectuée"
        verbose_name_plural = "déclarations effectuées"
        ordering = ["-annee", "formulaire", "-cochee_le"]

    def __str__(self):
        qui = f" {self.proprietaire}" if self.proprietaire_id else ""
        return f"{self.get_formulaire_display().split(' (')[0]}{qui} {self.annee} déclarée le {self.date_declaration:%d/%m/%Y}"

    @property
    def libelle_court(self):
        qui = f" · {self.proprietaire.nom}" if self.proprietaire_id else ""
        return f"{self.get_formulaire_display().split(' (')[0]}{qui}"

    def biens(self):
        return biens_du_formulaire(self.formulaire, self.proprietaire_id)

    @property
    def motif(self):
        return f"{self.libelle_court} {self.annee} déclarée le {self.date_declaration:%d/%m/%Y}"

    comment_debloquer = "décochez « Déclaration effectuée » sur la page Comptabilité › Déclarations"

    @classmethod
    def verrous(cls, blocages=True):
        """{année: {id de bien: verrou}} : déclarations cochées (une requête par formulaire déclaré) et, si blocages,
        saisies bloquées bien par bien (SaisieBloquee). La déclaration l'emporte quand un bien a les deux."""
        res = {}
        if blocages:
            for b in SaisieBloquee.objects.filter(active=True).select_related("bien"):
                res.setdefault(b.annee, {})[b.bien_id] = b
        for d in cls.objects.filter(active=True).select_related("proprietaire"):
            for pk in d.biens().values_list("pk", flat=True):
                res.setdefault(d.annee, {})[pk] = d
        return res


class SaisieBloquee(models.Model):
    """Saisie d'une année bloquée pour un seul bien (ex. un immeuble de la SCI terminé pendant qu'on finit les autres) :
    ses factures et les attestations de ses prêts ne peuvent plus changer. La date de transmission reste sur la
    déclaration de la SCI (DeclarationFaite). Débloquer garde la ligne dans l'historique."""

    annee = models.PositiveSmallIntegerField("année des revenus")
    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name="saisies_bloquees")
    active = models.BooleanField("bloque la saisie", default=True)
    bloquee_le = models.DateTimeField("bloquée le", auto_now_add=True)
    debloquee_le = models.DateTimeField("débloquée le", null=True, blank=True)

    class Meta:
        verbose_name = "saisie bloquée (par bien)"
        verbose_name_plural = "saisies bloquées (par bien)"
        ordering = ["-annee", "bien", "-bloquee_le"]

    def __str__(self):
        return self.motif

    @property
    def motif(self):
        return f"Saisie {self.annee} de {self.bien.nom} bloquée"

    comment_debloquer = "cliquez sur « Débloquer » au-dessus de ce bien sur l'annexe 2072 ou sur son dossier comptable"

    @classmethod
    def bloquees(cls, annee):
        """{id de bien: blocage actif} pour l'année."""
        return {b.bien_id: b for b in cls.objects.filter(annee=annee, active=True)}

    @classmethod
    def bloquer(cls, bien, annee):
        return cls.objects.filter(bien=bien, annee=annee, active=True).first() or cls.objects.create(bien=bien, annee=annee)

    @classmethod
    def debloquer(cls, bien, annee):
        from django.utils import timezone

        return cls.objects.filter(bien=bien, annee=annee, active=True).update(active=False, debloquee_le=timezone.now())


class DeficitReporte(models.Model):
    """Déficit encore à reporter, repris de la dernière déclaration (2044 ligne 450 colonne C, 2042-C-PRO cases 5GA à 5GJ) :
    montant restant au 31 décembre de l'année « au_31_12 », par année d'origine. Les années suivantes se calculent
    ensuite dans l'aide à la déclaration (bénéfices imputés sur les déficits les plus anciens, nouveaux déficits ajoutés)."""

    class Nature(models.TextChoices):
        FONCIER = "foncier", "Déficit foncier (2044, case 4BD)"
        LMNP = "lmnp", "Déficit LMNP (2042-C-PRO, cases 5GA à 5GJ)"

    nature = models.CharField(max_length=10, choices=Nature.choices)
    au_31_12 = models.PositiveSmallIntegerField("restant au 31/12 de")
    annee_origine = models.PositiveSmallIntegerField("année d'origine du déficit")
    montant = models.DecimalField("montant restant à reporter (€)", max_digits=12, decimal_places=2)
    saisi_le = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "déficit à reporter"
        verbose_name_plural = "déficits à reporter"
        ordering = ["nature", "-au_31_12", "annee_origine"]
        constraints = [models.UniqueConstraint(fields=["nature", "au_31_12", "annee_origine"], name="deficit_unique_par_annee")]

    def __str__(self):
        return f"{self.get_nature_display().split(' (')[0]} {self.annee_origine} : {self.montant} € restant au 31/12/{self.au_31_12}"


def biens_du_formulaire(formulaire, proprietaire_id=None):
    """Biens dont la saisie alimente ce cerfa."""
    if formulaire == "2044":
        return Bien.objects.filter(regime_fiscal="nu").exclude(option_fiscale="micro")
    if formulaire == "2072":
        return Bien.objects.filter(regime_fiscal="sci_ir", proprietaire_id=proprietaire_id)
    if formulaire == "2031":
        return Bien.objects.filter(comptabilite="lmnp")
    if formulaire == "societe":
        return Bien.objects.filter(comptabilite="societe", proprietaire_id=proprietaire_id)
    return Bien.objects.none()


def _chemin_facture(instance, nom):
    import secrets

    from django.utils import timezone

    return f"factures/{(instance.date or timezone.localdate()):%Y}/{secrets.token_hex(6)}-{nom}"


def verifier_rubrique(objet):
    """Maison : une catégorie de la maison ; LMNP ou société : une rubrique du récapitulatif (pas une catégorie de la maison)."""
    from django.core.exceptions import ValidationError

    if not objet.bien_id or not objet.rubrique:
        return
    if objet.bien.est_maison and objet.rubrique not in PieceComptable.LIBELLES_MAISON:
        raise ValidationError({"rubrique": "Choisissez une catégorie de la maison (électricité, taxe foncière, entretien…)."})
    if not objet.bien.est_maison and objet.rubrique not in PieceComptable.LIBELLES_RUBRIQUES:
        raise ValidationError({"rubrique": "Cette catégorie est réservée à la maison : choisissez une rubrique du récapitulatif."})


class PieceComptable(models.Model):
    """Facture ou recette d'un bien (comptabilité interne) : codée sur une ligne de l'annexe 2072-S-A1-SD (revenus fonciers)
    ou sur une rubrique (biens en LMNP ou en société)."""

    class Ligne(models.TextChoices):
        L2 = "2", "2 · Dépenses du propriétaire mises à la charge des locataires"
        L3 = "3", "3 · Recettes diverses (subventions ANAH, indemnités d'assurance)"
        L6 = "6", "6 · Frais d'administration et de gestion (syndic, comptable, honoraires)"
        L8 = "8", "8 · Primes d'assurance (PNO…)"
        L9 = "9", "9 · Travaux de réparation, d'entretien et d'amélioration"
        L9B = "9b", "9 bis · Travaux de rénovation énergétique (passage E/F/G vers A–D)"
        L10 = "10", "10 · Charges récupérables non récupérées au départ du locataire"
        L11 = "11", "11 · Indemnités d'éviction, frais de relogement"
        L12 = "12", "12 · Impôts (taxe foncière, CRL…)"
        L13 = "13", "13 · Provisions pour charges de copropriété"
        L14 = "14", "14 · Régularisation des provisions de copropriété de l'année précédente"
        L15 = "15", "15 · Déduction spécifique"
        L17 = "17", "17 · Intérêts et frais d'emprunt, assurance emprunteur (hors échéancier du prêt)"
        NON = "non", "Non déductible (capital d'emprunt, agrandissement, mobilier…)"

    RECETTES = {"2", "3"}

    # rubriques des biens en LMNP ou en société (ordre et libellés du tableau de suivi de Fabien)
    RUBRIQUES = [
        ("Exploitation", [("commission", "Commission"), ("menage", "Ménage"), ("consommables", "Consommables")]),
        ("Charges du logement", [("electricite", "Électricité"), ("eau", "Eau"), ("fibre", "Fibre"), ("pno", "PNO"),
                                 ("copro", "Charges copro"), ("comptable", "Comptable"), ("taxe_fonciere", "Taxe foncière"),
                                 ("impots_taxes", "Impôts et taxes (CFE)")]),
        ("Prêt", [("pret", "Rbt prêt (capital)"), ("interets_pret", "Intérêts d'emprunt"),
                  ("assurance_pret", "Assurance et frais de prêt")]),
        ("Autre", [("autre", "Autre")]),
        ("Immobilisations", [("immobilisation", "Immobilisation (à amortir)")]),
    ]
    LIBELLES_RUBRIQUES = {code: libelle for _, rubriques in RUBRIQUES for code, libelle in rubriques}

    # catégories des dépenses de la maison (résidence principale, hors comptabilité locative) : page « Ma maison »
    RUBRIQUES_MAISON = [("electricite", "Électricité"), ("chauffage", "Chauffage (gaz, fioul, bois)"), ("eau", "Eau et assainissement"),
                        ("taxe_fonciere", "Taxe foncière"), ("assurance_hab", "Assurance habitation"),
                        ("internet", "Internet et téléphone"), ("entretien", "Entretien"), ("travaux", "Travaux"), ("autre", "Autre")]
    LIBELLES_MAISON = dict(RUBRIQUES_MAISON)
    CHOIX_RUBRIQUES = [(g, r) for g, r in RUBRIQUES] + [
        ("Maison", [("chauffage", "Chauffage (gaz, fioul, bois)"), ("assurance_hab", "Assurance habitation"),
                    ("internet", "Internet et téléphone"), ("entretien", "Entretien"), ("travaux", "Travaux")])]  # les autres existent déjà

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="pieces_comptables")
    date = models.DateField("date de la facture", null=True, blank=True)
    date_paiement = models.DateField("payée le", null=True, blank=True,
                                     help_text="Année fiscale = année du paiement (date de la facture si vide)")
    fournisseur = models.CharField(max_length=150, blank=True)
    libelle = models.CharField("objet", max_length=200, blank=True)
    montant = models.DecimalField("montant TTC (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    # facture électronique (Factur-X, UBL ou CII) : lus dans le XML à l'import
    numero_facture = models.CharField("n° de facture", max_length=50, blank=True)
    siren_fournisseur = models.CharField("SIREN du fournisseur", max_length=9, blank=True)
    montant_ht = models.DecimalField("montant HT (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    montant_tva = models.DecimalField("TVA (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    format_electronique = models.CharField("facture électronique", max_length=8, blank=True, editable=False,
                                           choices=[("facturx", "Factur-X"), ("cii", "CII"), ("ubl", "UBL")],
                                           help_text="Format de la facture électronique importée (vide : PDF, photo ou saisie)")
    ligne = models.CharField("ligne de l'annexe 2072", max_length=4, choices=Ligne.choices, blank=True,
                             help_text="Vide = pièce importée pas encore codifiée (ignorée par l'annexe 2072)")
    rubrique = models.CharField(max_length=15, choices=CHOIX_RUBRIQUES, blank=True,
                                help_text="Biens en LMNP ou en société : rubrique du récapitulatif ; maison : catégorie de la dépense")
    mois_concerne = models.DateField("mois de la prestation", null=True, blank=True,
                                     help_text="Pour une facture qui couvre un autre mois que sa date (ex. conciergerie de septembre "
                                               "facturée en octobre) : place le montant dans le bon mois du récapitulatif")
    empreinte = models.CharField(max_length=64, blank=True, db_index=True, editable=False,
                                 help_text="Empreinte SHA-256 du fichier, pour repérer une facture importée deux fois")
    preremplie = models.BooleanField("préremplie automatiquement", default=False, editable=False,
                                     help_text="Champs lus dans le PDF à l'import : à vérifier avant d'être comptés dans l'annexe")
    fichier = models.FileField("facture (PDF, photo ou XML)", upload_to=_chemin_facture, storage=stockage_prive, blank=True,
                               validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "heic", "xml"])])
    notes = models.TextField(blank=True)
    charge_confirmee = models.BooleanField("c'est bien une charge (pas une immobilisation)", default=False,
                                           help_text="Plus de 500 € mais réparation ou entretien : plus d'alerte « à amortir ? »")
    recurrence = models.ForeignKey("DepenseRecurrente", null=True, blank=True, on_delete=models.SET_NULL, related_name="pieces",
                                   editable=False, verbose_name="dépense récurrente",
                                   help_text="Créée automatiquement par cette dépense récurrente")
    remboursement_vente = models.CharField(
        "remboursement de l'acquéreur", max_length=8, blank=True, editable=False,
        choices=[("tf", "Prorata de taxe foncière"), ("copro", "Prorata de charges de copropriété"), ("autre", "Autre remboursement")],
        help_text="Créée depuis la fiche du bien vendu : montant négatif, déduit des charges de l'année de la vente")
    piece_mere = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="parts", editable=False,
                                   verbose_name="facture scindée",
                                   help_text="Part d'une facture scindée sur plusieurs rubriques ou plusieurs biens "
                                             "(la pièce mère porte le PDF)")

    class Meta:
        verbose_name = "facture / pièce comptable"
        verbose_name_plural = "factures et pièces comptables"
        ordering = ["-date"]

    def __str__(self):
        if self.a_codifier:
            return f"À classer – {self.libelle or self.fichier.name.rsplit('/', 1)[-1]}"
        return f"{self.date:%d/%m/%Y} – {self.fournisseur or self.libelle} – {self.montant} €"

    def save(self, *args, **kwargs):
        if self.fichier and not self.empreinte:
            try:
                self.empreinte = self.calculer_empreinte(self.fichier)
            except Exception:
                pass
        super().save(*args, **kwargs)

    def clean(self):
        """Année clôturée : une facture comptée dans cette année ne change plus, et aucune facture ne peut y entrer.
        Une facture « à codifier » (pas encore comptée) reste modifiable : on peut lui donner une date de paiement plus tardive."""
        from django.core.exceptions import ValidationError

        super().clean()
        verifier_rubrique(self)
        fermees, declares = AnneeCloturee.annees(), DeclarationFaite.verrous()
        if not fermees and not declares:
            return
        avant = PieceComptable.objects.filter(pk=self.pk).select_related("bien").first() if self.pk else None
        motif = avant.motif_verrou(fermees, declares) if avant else None
        if motif:
            raise ValidationError(f"{motif} : cette facture ne peut plus être modifiée. {avant.comment_deverrouiller(fermees, declares)}")
        motif = self.motif_verrou(fermees, declares)
        if motif:
            raise ValidationError(f"{motif} : on ne peut plus y ajouter de facture. Si cette facture a été payée plus tard, "
                                  f"indiquez sa date de paiement. {self.comment_deverrouiller(fermees, declares)}")

    def declaration_qui_verrouille(self, declares=None):
        declares = DeclarationFaite.verrous() if declares is None else declares
        return declares.get(self.annee, {}).get(self.bien_id) if self.bien_id else None

    def motif_verrou(self, fermees=None, declares=None):
        """Pourquoi la facture est en lecture seule (année clôturée, ou cerfa déclaré), ou None.
        Une facture « à codifier » (pas encore comptée) reste modifiable."""
        if self.a_codifier or self.est_maison:  # la maison n'entre dans aucune déclaration : jamais verrouillée
            return None
        fermees = AnneeCloturee.annees() if fermees is None else fermees
        if self.annee in fermees:
            return f"L'année {self.annee} est clôturée"
        d = self.declaration_qui_verrouille(declares)
        if d:
            return d.motif
        return None

    def comment_deverrouiller(self, fermees=None, declares=None):
        fermees = AnneeCloturee.annees() if fermees is None else fermees
        if self.annee in fermees:
            return f"Pour la corriger, déverrouillez d'abord l'année {self.annee} dans la liste des factures."
        d = self.declaration_qui_verrouille(declares)
        return f"Pour la corriger, {d.comment_debloquer if d else DeclarationFaite.comment_debloquer}."

    def est_verrouillee(self, fermees=None, declares=None):
        """Vrai si la facture est comptée dans une année clôturée ou dans un cerfa déclaré."""
        return self.motif_verrou(fermees, declares) is not None

    @staticmethod
    def calculer_empreinte(fichier):
        import hashlib

        h = hashlib.sha256()
        fichier.seek(0)
        for bloc in iter(lambda: fichier.read(65536), b""):
            h.update(bloc)
        fichier.seek(0)
        return h.hexdigest()

    @property
    def par_rubrique(self):
        """Vrai si la pièce se code en rubrique (bien en LMNP ou en société), faux en revenus fonciers (ligne 2072)."""
        return bool(self.bien_id) and (self.bien.comptabilite in ("lmnp", "societe") or self.bien.est_maison)

    @property
    def est_maison(self):
        return bool(self.bien_id) and self.bien.est_maison

    @property
    def rubrique_libelle(self):
        """Libellé de la rubrique (catégorie de la maison pour la résidence principale)."""
        if self.est_maison:
            return self.LIBELLES_MAISON.get(self.rubrique, self.rubrique)
        return self.get_rubrique_display()

    @property
    def codification(self):
        return self.rubrique if self.par_rubrique else self.ligne

    @property
    def a_codifier(self):
        return self.preremplie or not self.codification or self.montant is None or self.date is None

    @property
    def facture_complete(self):
        """Pièce mère suivie de ses parts : l'ensemble des lignes d'une facture scindée (la pièce seule sinon)."""
        mere = self.piece_mere or self
        return [mere] + list(mere.parts.select_related("bien").order_by("pk"))

    @property
    def annee(self):
        d = self.date_paiement or self.date
        return d.year if d else None


def _ajouter_mois(d, n):
    import calendar
    from datetime import date

    m = d.month - 1 + n
    annee, mois = d.year + m // 12, m % 12 + 1
    return date(annee, mois, min(d.day, calendar.monthrange(annee, mois)[1]))


class DepenseRecurrente(models.Model):
    """Dépense qui revient (assurance, taxe foncière, copropriété, abonnement) : la facture de chaque échéance passée
    est créée toute seule dans la saisie, déjà classée. Rien n'est créé dans une année déclarée (saisie verrouillée)."""

    class Frequence(models.TextChoices):
        MENSUELLE = "mensuelle", "Chaque mois"
        TRIMESTRIELLE = "trimestrielle", "Chaque trimestre"
        ANNUELLE = "annuelle", "Chaque année"

    PAS = {"mensuelle": 1, "trimestrielle": 3, "annuelle": 12}

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="depenses_recurrentes")
    fournisseur = models.CharField(max_length=150)
    libelle = models.CharField("objet", max_length=200, blank=True, help_text="Ex. « Assurance PNO », « Taxe foncière »")
    montant = models.DecimalField("montant TTC (€)", max_digits=10, decimal_places=2)
    ligne = models.CharField("ligne de l'annexe 2072", max_length=4, choices=PieceComptable.Ligne.choices, blank=True,
                             help_text="Revenus fonciers (SCI, nom propre)")
    rubrique = models.CharField(max_length=15, choices=PieceComptable.CHOIX_RUBRIQUES, blank=True,
                                help_text="Biens en LMNP ou en société ; catégorie pour la maison")
    frequence = models.CharField("fréquence", max_length=15, choices=Frequence.choices, default=Frequence.MENSUELLE)
    premiere_echeance = models.DateField("1re échéance", help_text="Date de la première facture ; les suivantes tombent le même jour")
    fin = models.DateField("dernière échéance", null=True, blank=True, help_text="Vide : sans fin (jusqu'à ce que la dépense soit arrêtée)")
    active = models.BooleanField(default=True, help_text="Décocher pour ne plus créer de facture (celles déjà créées restent)")
    generee_jusqua = models.DateField("factures créées jusqu'au", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "dépense récurrente"
        verbose_name_plural = "dépenses récurrentes"
        ordering = ["bien__nom", "fournisseur"]

    def __str__(self):
        return f"{self.fournisseur} – {self.bien} – {self.montant} € ({self.get_frequence_display().lower()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.bien_id:
            par_rubrique = self.bien.comptabilite in ("lmnp", "societe") or self.bien.est_maison
            if self.bien.est_maison and not self.rubrique:
                raise ValidationError({"rubrique": "Choisissez la catégorie de la dépense (maison)."})
            if par_rubrique and not self.rubrique:
                raise ValidationError({"rubrique": "Choisissez la rubrique (bien en LMNP ou en société)."})
            verifier_rubrique(self)
            if not par_rubrique and not self.ligne:
                raise ValidationError({"ligne": "Choisissez la ligne de l'annexe 2072."})
        if self.fin and self.premiere_echeance and self.fin < self.premiere_echeance:
            raise ValidationError({"fin": "La dernière échéance est avant la première."})

    def echeances(self, jusqua):
        n, d = 0, self.premiere_echeance
        limite = min(jusqua, self.fin) if self.fin else jusqua
        while d <= limite:
            yield d
            n += 1
            d = _ajouter_mois(self.premiere_echeance, n * self.PAS[self.frequence])

    def prochaine(self, apres):
        for d in self.echeances(_ajouter_mois(apres, 13)):
            if d > apres:
                return d
        return None

    def generer(self, jusqua=None):
        """Crée les factures des échéances passées pas encore créées ; renvoie le nombre de factures créées."""
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        if not self.active:
            return 0
        jusqua = jusqua or timezone.localdate()
        # une échéance n'est créée qu'une fois : une facture supprimée à la main ne revient pas
        deja = set(self.pieces.values_list("date", flat=True))
        creees = 0
        for d in self.echeances(jusqua):
            if d in deja or (self.generee_jusqua and d <= self.generee_jusqua):
                continue
            p = PieceComptable(bien=self.bien, date=d, fournisseur=self.fournisseur, libelle=self.libelle or self.fournisseur,
                               montant=self.montant, ligne=self.ligne if not self.rubrique else "", rubrique=self.rubrique,
                               recurrence=self, notes="Créée automatiquement (dépense récurrente).")
            try:
                p.full_clean()
            except ValidationError:
                continue  # année déclarée ou clôturée : rien n'est ajouté à la saisie verrouillée
            p.save()
            creees += 1
        if self.generee_jusqua != jusqua and (not self.generee_jusqua or jusqua > self.generee_jusqua):
            DepenseRecurrente.objects.filter(pk=self.pk).update(generee_jusqua=jusqua)
            self.generee_jusqua = jusqua
        return creees

    @classmethod
    def generer_toutes(cls, jusqua=None):
        return sum(r.generer(jusqua) for r in cls.objects.filter(active=True).select_related("bien"))
