"""Comptes bancaires, relevés importés et rapprochement des lignes du relevé.

Deux façons de suivre l'argent, compte par compte :
- « saisie » : on encaisse les loyers à la main (page « Encaisser le mois ») et on marque les factures payées à la main ;
- « relevé » : on importe le relevé de la banque (OFX, CSV, CAMT.053 ou PDF) ; chaque ligne est rapprochée d'un loyer,
  d'une facture, d'une échéance de prêt, d'une dépense, d'un mouvement de trésorerie (apport ou remboursement de
  l'associé), ou ignorée. Le moteur des loyers reste le même : une ligne de loyer crée un « paiement reçu »."""
import hashlib
import re
import unicodedata

from django.db import models

from .biens import AgenceGestion, Bien, Proprietaire
from .finances import PieceComptable, Pret
from .location import Bail
from .loyers import Versement


def normaliser(texte):
    """Majuscules sans accents ni ponctuation, espaces simples : pour comparer libellés et noms."""
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", t).split())


class CompteBancaire(models.Model):
    class Mode(models.TextChoices):
        SAISIE = "saisie", "Saisie : j'encaisse les loyers à la main"
        RELEVE = "releve", "Relevé de banque : j'importe le relevé et je valide les propositions"

    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.PROTECT, related_name="comptes_bancaires", verbose_name="titulaire",
                                     help_text="Propriétaire (nom propre, SCI, SARL…) à qui appartient le compte : ses loyers, "
                                               "ses factures et ses prêts sont cherchés sur ce compte.")
    nom = models.CharField("nom du compte", max_length=100, help_text="Comme vous l'appelez, ex. « Compte courant SCI Lilas »")
    banque = models.CharField(max_length=100, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True,
                            help_text="Sert à reconnaître le bon compte dans un relevé PDF qui en contient plusieurs, et les "
                                      "virements entre vos propres comptes.")
    mode = models.CharField(
        "façon de suivre ce compte", max_length=10, choices=Mode.choices, default=Mode.SAISIE,
        help_text="Saisie : vous cochez « Reçu » sur la page Encaisser le mois, rien à importer. "
                  "Relevé de banque : vous importez le relevé téléchargé sur le site de la banque (OFX, CSV, CAMT ou PDF) ; "
                  "l'appli propose loyers, factures, prêts et frais, vous validez. On peut changer à tout moment : "
                  "un paiement déjà saisi est retrouvé dans le relevé, sans doublon.")
    associes = models.CharField(
        "noms des associés", max_length=255, blank=True,
        help_text="Séparés par des virgules. Un virement de ou vers ces noms est proposé comme apport ou remboursement "
                  "en compte courant d'associé (jamais une recette ni une charge).")
    actif = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "compte bancaire"
        verbose_name_plural = "comptes bancaires"
        ordering = ["proprietaire__nom", "nom"]

    def __str__(self):
        return f"{self.nom} ({self.proprietaire})"

    @property
    def chiffres_iban(self):
        return re.sub(r"\D", "", self.iban or "")

    @property
    def liste_associes(self):
        return [normaliser(n) for n in (self.associes or "").split(",") if normaliser(n)]

    # --- règle fiscale -------------------------------------------------------------------------------------------
    TRESORERIE_2044, TRESORERIE_2072, ENGAGEMENT_IS, ENGAGEMENT_BIC = "tresorerie_2044", "tresorerie_2072", "engagement_is", "engagement_bic"
    LOGIQUES = {
        TRESORERIE_2044: ("Trésorerie (2044)", "Un loyer ou une facture compte l'année où l'argent arrive ou part : la date de la "
                                               "ligne du relevé est la date retenue pour la déclaration."),
        TRESORERIE_2072: ("Trésorerie (2072)", "SCI à l'IR : un loyer ou une facture compte l'année où l'argent arrive ou part ; "
                                               "la date de la ligne du relevé est la date retenue pour la 2072."),
        ENGAGEMENT_IS: ("Engagement (IS)", "Société à l'IS : un loyer compte pour le mois qu'il couvre, une facture à sa date, même "
                                           "pas encore payés. Le relevé les solde ; au 31/12 restent des loyers à recevoir et des "
                                           "factures à payer (créances et dettes du bilan)."),
        ENGAGEMENT_BIC: ("Engagement (BIC, 2031)", "SARL de famille à l'IR ou LMNP au réel : comme à l'IS, loyers au mois couvert et "
                                                   "factures à leur date ; le résultat est imposé chez les associés ou l'exploitant."),
    }

    @property
    def logique_fiscale(self):
        p = self.proprietaire
        if p.type == Proprietaire.Type.SCI_IS:
            return self.ENGAGEMENT_IS
        if p.type == Proprietaire.Type.SCI_IR:
            return self.TRESORERIE_2072
        regimes = set(p.biens.values_list("regime_fiscal", flat=True))
        if Bien.RegimeFiscal.SOCIETE_IS in regimes:
            return self.ENGAGEMENT_IS
        if p.type == Proprietaire.Type.SARL_FAMILLE or regimes & {Bien.RegimeFiscal.SOCIETE_IR, Bien.RegimeFiscal.LMNP, Bien.RegimeFiscal.LMP}:
            return self.ENGAGEMENT_BIC
        return self.TRESORERIE_2044

    @property
    def logique_libelle(self):
        return self.LOGIQUES[self.logique_fiscale][0]

    @property
    def logique_explication(self):
        return self.LOGIQUES[self.logique_fiscale][1]

    @property
    def engagement(self):
        return self.logique_fiscale in (self.ENGAGEMENT_IS, self.ENGAGEMENT_BIC)


class ImportReleve(models.Model):
    compte = models.ForeignKey(CompteBancaire, on_delete=models.CASCADE, related_name="imports")
    fichier = models.CharField("fichier", max_length=255)
    format = models.CharField(max_length=10)
    importe_le = models.DateTimeField(auto_now_add=True)
    date_debut = models.DateField(null=True, blank=True)
    date_fin = models.DateField(null=True, blank=True)
    solde_debut = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    solde_fin = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    nb_lignes = models.PositiveIntegerField(default=0)
    nb_nouvelles = models.PositiveIntegerField(default=0)
    controle = models.CharField("contrôle des soldes", max_length=255, blank=True)

    class Meta:
        verbose_name = "relevé importé"
        verbose_name_plural = "relevés importés"
        ordering = ["-importe_le"]

    def __str__(self):
        return f"{self.fichier} ({self.importe_le:%d/%m/%Y})"


class LigneReleve(models.Model):
    class Statut(models.TextChoices):
        A_TRAITER = "a_traiter", "À traiter"
        RAPPROCHEE = "rapprochee", "Rapprochée"
        IGNOREE = "ignoree", "Ignorée"

    class Nature(models.TextChoices):
        LOYER = "loyer", "Loyer"
        FACTURE = "facture", "Facture"
        PRET = "pret", "Échéance de prêt"
        DEPENSE = "depense", "Dépense créée"
        TRESORERIE = "tresorerie", "Mouvement de trésorerie"
        AGENCE = "agence", "Acompte ou solde de l'agence"
        IGNORE = "ignore", "Ignorée"

    compte = models.ForeignKey(CompteBancaire, on_delete=models.CASCADE, related_name="lignes")
    releve = models.ForeignKey(ImportReleve, on_delete=models.SET_NULL, null=True, blank=True, related_name="lignes")
    date = models.DateField()
    libelle = models.CharField("libellé", max_length=255)
    detail = models.TextField("détail", blank=True)
    montant = models.DecimalField(max_digits=12, decimal_places=2, help_text="Positif : argent reçu ; négatif : argent sorti")
    empreinte = models.CharField(max_length=64, editable=False)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.A_TRAITER)
    nature = models.CharField(max_length=12, choices=Nature.choices, blank=True)
    versement = models.ForeignKey(Versement, on_delete=models.SET_NULL, null=True, blank=True, related_name="lignes_releve",
                                  verbose_name="paiement reçu")
    piece = models.ForeignKey(PieceComptable, on_delete=models.SET_NULL, null=True, blank=True, related_name="lignes_releve",
                              verbose_name="facture")
    pret = models.ForeignKey(Pret, on_delete=models.SET_NULL, null=True, blank=True, related_name="lignes_releve", verbose_name="prêt")
    associe = models.CharField("associé", max_length=120, blank=True, help_text="Mouvement de trésorerie : qui a apporté ou repris l'argent")
    agence = models.ForeignKey(AgenceGestion, on_delete=models.SET_NULL, null=True, blank=True, related_name="lignes_releve",
                               verbose_name="agence", help_text="Virement d'une agence de gestion : acompte mensuel ou solde du récap "
                                                                "annuel (trésorerie, pas un loyer)")
    cree = models.BooleanField("créé par le rapprochement", default=False, editable=False,
                               help_text="Le paiement ou la dépense a été créé en validant cette ligne : dérapprocher le supprime")
    date_paiement_avant = models.DateField(null=True, blank=True, editable=False,
                                           help_text="Date de paiement de la facture avant le rapprochement, remise si on dérapproche")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "ligne de relevé"
        verbose_name_plural = "lignes de relevé"
        ordering = ["-date", "-pk"]
        constraints = [models.UniqueConstraint(fields=["compte", "empreinte"], name="ligne_releve_unique")]

    def __str__(self):
        return f"{self.date:%d/%m/%Y} {self.libelle} {self.montant}"

    @staticmethod
    def calculer_empreinte(compte_id, date, montant, libelle, rang=0):
        """Même date, montant et libellé (et rang parmi les lignes identiques du fichier) : même ligne, pas de doublon."""
        cle = f"{compte_id}|{date.isoformat()}|{montant:.2f}|{normaliser(libelle)}|{rang}"
        return hashlib.sha256(cle.encode()).hexdigest()

    @property
    def texte(self):
        return normaliser(f"{self.libelle} {self.detail}")

    @property
    def credit(self):
        return self.montant > 0

    @property
    def sens_tresorerie(self):
        return "Apport" if self.montant > 0 else "Remboursement"


class RegleRapprochement(models.Model):
    """Règle apprise quand on corrige ou choisit une proposition : « si le libellé contient … alors … »."""

    class Action(models.TextChoices):
        LOYER = "loyer", "Loyer de ce bail"
        TRESORERIE = "tresorerie", "Mouvement de trésorerie (associé)"
        DEPENSE = "depense", "Dépense de ce bien"
        PRET = "pret", "Échéance de ce prêt"
        IGNORER = "ignorer", "Ignorer"

    compte = models.ForeignKey(CompteBancaire, on_delete=models.CASCADE, related_name="regles")
    motif = models.CharField("si le libellé contient", max_length=100)
    action = models.CharField("alors", max_length=12, choices=Action.choices)
    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, null=True, blank=True)
    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, null=True, blank=True)
    pret = models.ForeignKey(Pret, on_delete=models.CASCADE, null=True, blank=True, verbose_name="prêt")
    rubrique = models.CharField("rubrique ou ligne", max_length=15, blank=True)
    associe = models.CharField("associé", max_length=120, blank=True)
    fournisseur = models.CharField(max_length=150, blank=True)

    class Meta:
        verbose_name = "règle de rapprochement"
        verbose_name_plural = "règles de rapprochement"
        ordering = ["compte", "motif"]

    def __str__(self):
        return f"« {self.motif} » → {self.get_action_display()}"

    def correspond(self, ligne):
        motif = normaliser(self.motif)
        return bool(motif) and motif in ligne.texte
