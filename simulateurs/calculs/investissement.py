"""Rentabilité d'un investissement locatif (immeuble ou appartement, de 1 à 20 lots).

Reprend le fichier Excel « Simulateur invest » (feuilles Descriptif, Coûts et rendement, Notaire, Amortissement,
Fiscalité, SCI) en corrigeant ce qui y était cassé ou daté :
- frais de notaire : simulateur de l'appli (droits 2026 par département) ;
- impôt sur le revenu : barème 2026 avec plafonnement du quotient familial ;
- meublé au réel (LMNP) et SCI à l'IS : amortissements par composants (cassés dans l'Excel) ;
- micro-foncier refusé au-delà de 15 000 € de loyers, micro-BIC au-delà de 77 700 € ;
- IS : 15 % jusqu'à 42 500 € de bénéfice, 25 % au-delà.

Les calculs sont faits en nombres à virgule (float) : c'est une simulation (arrondie à l'euro à l'affichage),
avec de nombreuses itérations pour la recherche du prix cible.

À METTRE À JOUR chaque année : BAREME_IR, PLAFOND_QF (loi de finances), et si besoin TAUX_PS, TAUX_PFU.
"""
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal

from . import notaire

MISE_A_JOUR = "barème de l'impôt 2026 (revenus 2025)"

# Impôt sur le revenu : (plafond de la tranche, taux en %), par part
BAREME_IR = [(11600, 0), (29579, 11), (84577, 30), (181917, 41), (None, 45)]
PLAFOND_QF = 1807               # avantage maximal par demi-part au-delà des parts de base
PLAFOND_DEFICIT_FONCIER = 10700  # déficit (hors intérêts) imputable sur le revenu global
MICRO_FONCIER = 15000
ABATTEMENT_MICRO_FONCIER = 30
MICRO_BIC = 77700
ABATTEMENT_MICRO_BIC = 50
IS_SEUIL_TAUX_REDUIT = 42500
IS_TAUX_REDUIT, IS_TAUX_NORMAL = 15, 25

TYPES_LOT = [
    ("studio", "Studio"), ("t1", "T1"), ("t2", "T2"), ("t3", "T3"), ("t4", "T4"), ("t5", "T5 et +"),
    ("maison", "Maison"), ("local", "Local commercial"), ("parking", "Parking / box"), ("cave", "Cave"),
]
NOMS_TYPES = dict(TYPES_LOT)
ETAGES = ["RDC", "1er", "2e", "3e", "4e et +", "Sous-sol"]
SITUATIONS = [("couple", "Marié ou pacsé"), ("seul", "Célibataire, divorcé, veuf")]
MAX_LOTS = 20
MAX_ASSOCIES = 4

# Composants du bâti (hors terrain) : (libellé, part en %, durée en années). Repris de l'Excel.
COMPOSANTS = [
    ("Gros œuvre", 40, 50),
    ("Façade et étanchéité", 20, 20),
    ("Installations techniques", 20, 20),
    ("Agencements", 20, 5),
]


# Chiffrage des travaux : (poste, mode, unité, coût selon le niveau [rafraîchissement, rénovation, lourde], quantité)
# Valeurs de départ indicatives (TTC), à ajuster ; mode : m2 (coût × surface du lot), unite, forfait, devis.
NIVEAUX_RENOVATION = ["Rafraîchissement", "Rénovation", "Rénovation lourde"]
MODES_TRAVAUX = [("m2", "au m²"), ("unite", "à l'unité"), ("forfait", "forfait"), ("devis", "devis")]
POSTES_LOT = [
    ("Électricité", "m2", "€/m²", (20, 90, 130), None),
    ("Plomberie", "m2", "€/m²", (0, 40, 70), None),
    ("Placo, cloisons, isolation", "m2", "€/m²", (0, 45, 90), None),
    ("Peinture", "m2", "€/m²", (25, 35, 40), None),
    ("Sols", "m2", "€/m²", (20, 50, 70), None),
    ("Menuiseries (fenêtres)", "unite", "€/fenêtre", (0, 900, 900), 2),
    ("Chauffage, eau chaude", "forfait", "€", (0, 2500, 4500), None),
    ("Cuisine", "forfait", "€", (1500, 5000, 8000), None),
    ("Salle de bain", "forfait", "€", (1500, 6000, 9000), None),
]
POSTES_COMMUNS = [
    ("Toiture", "m2", "€/m² de toit", (0, 150, 250), 80),
    ("Façade", "m2", "€/m² de façade", (0, 80, 120), 120),
    ("Cage d'escalier, parties communes", "forfait", "€", (1500, 6000, 12000), None),
    ("Réseaux (eau, électricité)", "forfait", "€", (0, 4000, 8000), None),
]
IMPREVUS_PAR_DEFAUT = 10


def chiffrage_par_defaut(communs=False, niveau=1):
    """Lignes de départ d'un chiffrage (un lot, ou les parties communes)."""
    postes = POSTES_COMMUNS if communs else POSTES_LOT
    return {"imprevus": IMPREVUS_PAR_DEFAUT, "lignes": [
        {"poste": nom, "mode": mode, "unite": unite, "prix": prix[niveau], "quantite": qte, "devis": 0, "entreprise": "",
         "agrandissement": False, "piece": None} for nom, mode, unite, prix, qte in postes]}


@dataclass
class TotalTravaux:
    devis: float = 0
    estime: float = 0
    imprevus: float = 0
    agrandissement: float = 0     # part non déductible en location nue (agrandissement, construction)
    par_poste: dict = field(default_factory=dict)  # poste → (estimé, sur devis)

    @property
    def total(self):
        return self.devis + self.estime + self.imprevus

    @property
    def part_devis(self):
        return self.devis / self.total if self.total else 0


def _f(v):
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


def montant_ligne(ligne, surface):
    mode = ligne.get("mode")
    if mode == "devis":
        return _f(ligne.get("devis"))
    if mode == "m2":
        qte = ligne.get("quantite")
        return _f(ligne.get("prix")) * (_f(qte) if qte not in (None, "") else _f(surface))
    if mode == "unite":
        return _f(ligne.get("prix")) * _f(ligne.get("quantite"))
    return _f(ligne.get("prix"))


def total_chiffrage(detail, surface=0):
    """Total d'un chiffrage : postes sur devis, postes estimés, imprévus (sur les postes estimés seulement)."""
    t = TotalTravaux()
    if not detail:
        return t
    for ligne in detail.get("lignes") or []:
        m = montant_ligne(ligne, surface)
        if not m:
            continue
        poste = (ligne.get("poste") or "Autre").strip()[:80] or "Autre"
        est, dev = t.par_poste.get(poste, (0.0, 0.0))
        if ligne.get("mode") == "devis":
            t.devis += m
            dev += m
        else:
            t.estime += m
            est += m
        t.par_poste[poste] = (est, dev)
        if ligne.get("agrandissement"):
            t.agrandissement += m
    t.imprevus = t.estime * _f(detail.get("imprevus")) / 100
    if t.imprevus:
        t.par_poste["Imprévus"] = (t.imprevus, 0.0)
    return t


# ---------------------------------------------------------------------------------------------------------------------
# Saisie
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Lot:
    type: str = "t2"
    surface: float = 0
    etage: str = "RDC"
    meuble: bool = True
    loue: bool = False
    loyer_actuel: float = 0       # hors charges, par mois (si le lot est loué)
    loyer_cible: float = 0        # hors charges, par mois (loyer visé après achat / travaux)
    charges: float = 0            # charges récupérables, par mois
    travaux: float = 0            # montant saisi directement (sans chiffrage détaillé)
    meubles: float = 0
    fin_bail: str = ""
    note: str = ""
    travaux_detail: dict | None = None  # chiffrage par corps de métier (remplace `travaux`)

    @property
    def nom_type(self):
        return NOMS_TYPES.get(self.type, self.type)

    @property
    def chiffrage(self):
        return total_chiffrage(self.travaux_detail, self.surface) if self.travaux_detail else None

    @property
    def montant_travaux(self):
        return self.chiffrage.total if self.travaux_detail else (self.travaux or 0)


@dataclass
class Associe:
    parts: float = 0              # % du capital de la SCI
    revenu_imposable: float = 0
    situation: str = "couple"
    enfants: int = 0


@dataclass
class Parametres:
    nom: str = ""
    adresse: str = ""
    lien_annonce: str = ""
    departement: str = "69"
    lots: list = field(default_factory=list)
    scenario: str = "cible"       # "cible" (loyers visés) ou "actuel" (loyers en place)
    vacance: float = 0            # % de vacance locative
    travaux_communs: dict | None = None  # chiffrage des parties communes et de la toiture
    niveau_renovation: int = 1
    # achat
    prix_affiche: float = 0
    prix: float = 0
    frais_agence: float = 0
    frais_ingenierie: float = 0
    frais_bancaires_pct: float = 0.6   # dossier, courtier : % du prix
    garantie_pct: float = 0.9          # hypothèque ou caution : % du prix
    # financement
    apport_complement: float = 0
    duree: int = 20
    taux: float = 4.2
    taux_assurance: float = 0.25
    # charges annuelles
    taxe_fonciere: float = 0
    pno: float = 0
    gli_pct: float = 0
    gestion_pct: float = 0
    comptable: float = 0
    charges_non_recup: float = 0
    frais_banque: float = 0
    entretien: float = 0
    mise_en_location: float = 0
    crl: float = 0
    autres_charges: float = 0
    # fiscalité du foyer
    revenu_imposable: float = 0
    situation: str = "couple"
    enfants: int = 0
    regime: str = ""              # régime retenu pour le résumé ("" : le meilleur)
    # SCI
    associes: list = field(default_factory=list)
    distribution_pct: float = 80
    # réglages avancés
    part_terrain: float = 20
    duree_travaux: int = 15
    duree_meubles: int = 5
    duree_frais: int = 10
    taux_ps: float = 17.2
    taux_pfu: float = 30

    # --- stockage (JSON) ---
    def en_dict(self):
        return asdict(self)

    @classmethod
    def depuis_dict(cls, donnees):
        donnees = dict(donnees or {})
        noms = {f.name for f in fields(cls)}
        p = cls(**{k: v for k, v in donnees.items() if k in noms and k not in ("lots", "associes")})
        noms_lot = {f.name for f in fields(Lot)}
        p.lots = [Lot(**{k: v for k, v in l.items() if k in noms_lot}) for l in donnees.get("lots") or []]
        noms_assoc = {f.name for f in fields(Associe)}
        p.associes = [Associe(**{k: v for k, v in a.items() if k in noms_assoc}) for a in donnees.get("associes") or []]
        return p


def exemple():
    """Le projet d'Oullins de l'Excel : 3 T1 meublés, 190 000 €. Sert de démonstration sur la page publique."""
    lots = [Lot("t1", 24, "1er", True, False, 0, 730, 0, 27000, 6800),
            Lot("t1", 22, "1er", True, False, 0, 730, 0, 27000, 6800),
            Lot("t1", 22, "1er", True, False, 0, 730, 0, 29000, 7000)]
    return Parametres(nom="Exemple : immeuble de 3 lots", adresse="Grande Rue, 69600 Oullins", departement="69", lots=lots,
                      prix_affiche=199000, prix=190000, frais_ingenierie=11741, taxe_fonciere=900, pno=200, gestion_pct=7,
                      comptable=300, charges_non_recup=600, frais_banque=100, revenu_imposable=72000, situation="couple",
                      enfants=2)


def parametres_par_defaut(lots):
    """Valeurs de départ d'une nouvelle simulation (charges courantes de l'Excel)."""
    return Parametres(lots=lots, taxe_fonciere=0, pno=150 * max(1, len(lots)) if lots else 150, comptable=0,
                      revenu_imposable=0, situation="couple")


# ---------------------------------------------------------------------------------------------------------------------
# Impôt
# ---------------------------------------------------------------------------------------------------------------------

def parts_fiscales(situation, enfants):
    base = 2 if situation == "couple" else 1
    enfants = max(0, int(enfants or 0))
    return base, base + (enfants / 2 if enfants <= 2 else enfants - 1)


def _impot_brut(revenu, parts):
    q = max(0.0, revenu) / parts
    impot, bas = 0.0, 0.0
    for haut, taux in BAREME_IR:
        if q > bas:
            impot += ((q if haut is None else min(q, haut)) - bas) * taux / 100
        if haut is None or q <= haut:
            break
        bas = haut
    return impot * parts


def impot_revenu(revenu, situation="couple", enfants=0):
    """Impôt sur le revenu (barème progressif) avec plafonnement des effets du quotient familial. Sans décote."""
    base, parts = parts_fiscales(situation, enfants)
    plafond = PLAFOND_QF * (parts - base) * 2
    return max(_impot_brut(revenu, parts), _impot_brut(revenu, base) - plafond)


def impot_en_plus(revenu, situation, enfants, base_imposable, base_ps, taux_ps):
    """Impôt dû en plus à cause de l'investissement : IR sur (revenu + base) – IR sur revenu, + prélèvements sociaux."""
    ir = impot_revenu(revenu + base_imposable, situation, enfants) - impot_revenu(revenu, situation, enfants)
    return ir + max(0.0, base_ps) * taux_ps / 100


# ---------------------------------------------------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------------------------------------------------

def mensualite(capital, taux_mensuel, n):
    if n <= 0:
        return 0.0
    if taux_mensuel == 0:
        return capital / n
    return capital * taux_mensuel / (1 - (1 + taux_mensuel) ** -n)


@dataclass
class AnneePret:
    annee: int
    mensualites: float
    interets: float
    capital: float
    assurance: float
    restant: float


@dataclass
class Regime:
    id: str
    nom: str
    eligible: bool = True
    motif: str = ""
    base: float | None = None     # revenu ajouté au revenu imposable (négatif : déficit imputé)
    amortissements: float = 0      # amortissements déduits cette année
    report: float = 0              # déficit ou amortissement reporté sur les années suivantes
    detail: str = ""
    impot: float = 0               # impôt dû en plus, par an
    cash_flow: float = 0           # par mois, après impôt
    renta_nette_nette: float = 0


@dataclass
class LigneLot:
    numero: int
    lot: Lot
    loyer: float
    part_prix: float
    prix_revient: float
    travaux: float = 0            # travaux du lot + sa part des parties communes

    @property
    def loyer_m2(self):
        return self.loyer / self.lot.surface if self.lot.surface else None

    @property
    def prix_revient_m2(self):
        return self.prix_revient / self.lot.surface if self.lot.surface else None

    @property
    def renta_brute(self):
        return self.loyer * 12 / self.prix_revient if self.prix_revient else None


@dataclass
class Resultat:
    p: Parametres
    surface: float
    loyers_mois: float
    loyers_an: float              # encaissés, après vacance
    travaux: float
    meubles: float
    frais_notaire: float
    frais_bancaires: float
    garantie: float
    cout_total: float
    apport: float
    emprunt: float
    mensualite: float             # hors assurance
    assurance_mois: float
    annees: list
    interets_an1: float
    charges: dict                 # charges annuelles hors intérêts (assurance du prêt comprise)
    charges_total: float
    amortissements: dict
    amortissements_total: float
    cash_flow_avant_impot: float  # par mois
    regimes: list
    lignes_lots: list
    travaux_communs: float = 0
    travaux_deductibles: float = 0   # en location nue, l'année des travaux
    recap_travaux: dict = field(default_factory=dict)  # corps de métier → (estimé, sur devis)
    travaux_sur_devis: float = 0
    lignes_devis: list = field(default_factory=list)  # postes chiffrés sur devis, avec le lien de la pièce jointe

    @property
    def recap_lignes(self):
        """[(poste, estimé, sur devis, total, part du total)] pour l'affichage."""
        return [(poste, e, d, e + d, (e + d) / self.travaux if self.travaux else 0)
                for poste, (e, d) in self.recap_travaux.items()]

    @property
    def travaux_estimes(self):
        return self.travaux - self.travaux_sur_devis

    @property
    def part_travaux_devis(self):
        return self.travaux_sur_devis / self.travaux if self.travaux else 0

    @property
    def prix(self):
        return self.p.prix

    @property
    def negociation(self):
        return self.p.prix / self.p.prix_affiche - 1 if self.p.prix_affiche else None

    @property
    def renta_brute(self):
        return self.loyers_an / self.cout_total if self.cout_total else 0

    @property
    def renta_nette(self):
        return (self.loyers_an - self.charges_total - self.interets_an1) / self.cout_total if self.cout_total else 0

    @property
    def cout_m2(self):
        return self.cout_total / self.surface if self.surface else None

    @property
    def prix_m2(self):
        return self.p.prix / self.surface if self.surface else None

    @property
    def cout_credit(self):
        return sum(a.interets + a.assurance for a in self.annees)

    @property
    def cash_flow_avant_impot_an(self):
        return self.cash_flow_avant_impot * 12

    @property
    def eligibles(self):
        return [r for r in self.regimes if r.eligible]

    @property
    def meilleur(self):
        return max(self.eligibles, key=lambda r: r.cash_flow) if self.eligibles else None

    @property
    def retenu(self):
        return next((r for r in self.eligibles if r.id == self.p.regime), None) or self.meilleur

    @property
    def lots_mixtes(self):
        return len({l.meuble for l in self.p.lots}) > 1


def loyer_lot(lot, scenario):
    base = lot.loyer_cible if scenario == "cible" else (lot.loyer_actuel if lot.loue else 0)
    return (base or 0) + (lot.charges or 0)


def frais_notaire(prix, departement):
    if prix <= 0:
        return 0.0
    dep = departement if departement in notaire.NOMS_DEPARTEMENTS else "69"
    return float(notaire.calculer(Decimal(str(round(prix, 2))), notaire.ANCIEN, dep).total)


def simuler(p, prix=None):
    """Calcule toute la simulation. `prix` remplace le prix proposé (courbe et prix cible)."""
    if prix is not None:
        p = Parametres.depuis_dict({**p.en_dict(), "prix": prix})
    lots = p.lots
    surface = sum(l.surface or 0 for l in lots)
    loyers_mois = sum(loyer_lot(l, p.scenario) for l in lots)
    L = loyers_mois * 12 * (1 - (p.vacance or 0) / 100)
    communs = total_chiffrage(p.travaux_communs) if p.travaux_communs else None
    travaux_communs = communs.total if communs else 0.0
    travaux = sum(l.montant_travaux for l in lots) + travaux_communs
    # travaux déductibles en location nue (année des travaux) : tout sauf l'agrandissement
    agrandissement = sum(l.chiffrage.agrandissement for l in lots if l.travaux_detail) + (communs.agrandissement if communs else 0)
    travaux_deductibles = max(0.0, travaux - agrandissement)
    meubles = sum(l.meubles or 0 for l in lots)
    f_not = frais_notaire(p.prix, p.departement)
    f_banc = p.prix * (p.frais_bancaires_pct or 0) / 100
    garantie = p.prix * (p.garantie_pct or 0) / 100
    total = p.prix + p.frais_agence + p.frais_ingenierie + f_not + travaux + meubles + f_banc + garantie
    apport = f_not + f_banc + garantie + meubles + (p.apport_complement or 0)
    emprunt = max(0.0, total - apport)

    n = max(1, int(p.duree or 0) * 12)
    tm = (p.taux or 0) / 100 / 12
    mens = mensualite(emprunt, tm, n)
    ass_mois = emprunt * (p.taux_assurance or 0) / 100 / 12
    annees, crd = [], emprunt
    for a in range(1, n // 12 + 1):
        interets = capital = 0.0
        for _ in range(12):
            i = crd * tm
            interets += i
            capital += mens - i
            crd -= mens - i
        annees.append(AnneePret(a, mens * 12, interets, capital, ass_mois * 12, max(0.0, crd)))
    I = annees[0].interets if annees else 0.0

    charges = {
        "Assurance du prêt": ass_mois * 12,
        "Taxe foncière": p.taxe_fonciere,
        "Assurance propriétaire (PNO)": p.pno,
        "Assurance loyers impayés": L * (p.gli_pct or 0) / 100,
        "Gestion par une agence": L * (p.gestion_pct or 0) / 100,
        "Comptable": p.comptable,
        "Charges non récupérables": p.charges_non_recup,
        "Frais de banque": p.frais_banque,
        "Entretien chaudière": p.entretien,
        "Mise en location": p.mise_en_location,
        "Contribution sur les revenus locatifs (CRL)": p.crl,
        "Autres charges": p.autres_charges,
    }
    charges = {k: float(v or 0) for k, v in charges.items()}
    C = sum(charges.values())
    cf_avant = (L - C) / 12 - mens

    # amortissements (meublé au réel, SCI à l'IS)
    bati = p.prix * (1 - (p.part_terrain or 0) / 100)
    amort = {f"Bâti · {lib.lower()} ({part} % sur {duree} ans)": bati * part / 100 / duree for lib, part, duree in COMPOSANTS}
    frais_achat = p.frais_agence + p.frais_ingenierie + f_not + f_banc + garantie
    if frais_achat:
        amort[f"Frais d'achat (sur {p.duree_frais} ans)"] = frais_achat / max(1, p.duree_frais)
    if travaux:
        amort[f"Travaux (sur {p.duree_travaux} ans)"] = travaux / max(1, p.duree_travaux)
    if meubles:
        amort[f"Meubles (sur {p.duree_meubles} ans)"] = meubles / max(1, p.duree_meubles)
    A = sum(amort.values())

    regimes = _regimes(p, L, C, I, A, cf_avant, total, lots, travaux_deductibles)

    lignes = []
    for i, l in enumerate(lots, 1):
        prorata = (l.surface or 0) / surface if surface else 0
        part = p.prix * prorata
        lignes.append(LigneLot(i, l, loyer_lot(l, p.scenario), part,
                               part + l.montant_travaux + travaux_communs * prorata + (l.meubles or 0),
                               l.montant_travaux + travaux_communs * prorata))

    # récapitulatif des travaux par corps de métier
    recap = {}
    for chiffrage in [l.chiffrage for l in lots if l.travaux_detail] + ([communs] if communs else []):
        for poste, (est, dev) in chiffrage.par_poste.items():
            e, d = recap.get(poste, (0.0, 0.0))
            recap[poste] = (e + est, d + dev)
    sans_detail = sum(l.travaux or 0 for l in lots if not l.travaux_detail)
    if sans_detail and recap:
        e, d = recap.get("Saisi sans détail", (0.0, 0.0))
        recap["Saisi sans détail"] = (e + sans_detail, d)
    devis_total = sum(d for _, d in recap.values())
    lignes_devis = []
    sources = [(f"Lot {i + 1:03d}", l.travaux_detail) for i, l in enumerate(lots)] + [("Parties communes", p.travaux_communs)]
    for lieu, detail in sources:
        for ligne in (detail or {}).get("lignes") or []:
            if ligne.get("mode") == "devis" and _f(ligne.get("devis")):
                lignes_devis.append({"lieu": lieu, "poste": ligne.get("poste") or "Autre",
                                     "entreprise": ligne.get("entreprise") or "", "montant": _f(ligne.get("devis")),
                                     "piece_url": ligne.get("piece_url") or "", "piece_nom": ligne.get("piece_nom") or ""})

    return Resultat(p=p, surface=surface, loyers_mois=loyers_mois, loyers_an=L, travaux=travaux, meubles=meubles,
                    frais_notaire=f_not, frais_bancaires=f_banc, garantie=garantie, cout_total=total, apport=apport,
                    emprunt=emprunt, mensualite=mens, assurance_mois=ass_mois, annees=annees, interets_an1=I,
                    charges=charges, charges_total=C, amortissements=amort, amortissements_total=A,
                    cash_flow_avant_impot=cf_avant, regimes=regimes, lignes_lots=lignes, travaux_communs=travaux_communs,
                    travaux_deductibles=travaux_deductibles, recap_travaux=recap, travaux_sur_devis=devis_total,
                    lignes_devis=lignes_devis)


def _foncier_reel(L, C, I):
    """Résultat foncier au réel : (base imposable, déficit reporté). Le déficit hors intérêts s'impute sur le revenu
    global dans la limite de 10 700 € ; les intérêts ne s'imputent que sur les revenus fonciers."""
    resultat = L - C - I
    if resultat >= 0:
        return resultat, 0.0
    hors_interets = max(0.0, C - max(0.0, L - I))
    impute = min(hors_interets, PLAFOND_DEFICIT_FONCIER)
    return -impute, -resultat - impute


def _bic_reel(L, C, I, A):
    """Meublé au réel : les amortissements ne peuvent pas créer de déficit ; l'excédent est reporté.
    Un déficit (hors amortissements) ne s'impute que sur les revenus de location meublée : base 0 cette année."""
    avant = L - C - I
    if avant <= 0:
        return 0.0, 0.0, -avant + A
    deduits = min(A, avant)
    return avant - deduits, deduits, A - deduits


def _regimes(p, L, C, I, A, cf_avant, total, lots, T=0.0):
    """T : travaux déductibles en location nue (comptés l'année des travaux, année 1 de la simulation)."""
    R, sit, enf, ps = p.revenu_imposable or 0, p.situation, p.enfants or 0, p.taux_ps
    sup = lambda base, base_ps=None: impot_en_plus(R, sit, enf, base, base if base_ps is None else base_ps, ps)
    liste = []

    r = Regime("nu-micro", "Location nue · micro-foncier", eligible=L <= MICRO_FONCIER,
               motif="Loyers au-delà de 15 000 € par an", base=L * (1 - ABATTEMENT_MICRO_FONCIER / 100))
    r.impot = sup(r.base)
    liste.append(r)

    base_nu, report_nu = _foncier_reel(L, C + T, I)
    note_travaux = f"Travaux déduits l'année 1 : {T:,.0f} €".replace(",", "\u202f") if T else ""
    r = Regime("nu-reel", "Location nue · réel", base=base_nu, report=report_nu, detail=note_travaux)
    r.impot = sup(base_nu)
    liste.append(r)

    r = Regime("lmnp-micro", "Meublé (LMNP) · micro-BIC", eligible=L <= MICRO_BIC,
               motif="Loyers au-delà de 77 700 € par an", base=L * (1 - ABATTEMENT_MICRO_BIC / 100))
    r.impot = sup(r.base)
    liste.append(r)

    base_l, deduits, report_l = _bic_reel(L, C, I, A)
    r = Regime("lmnp-reel", "Meublé (LMNP) · réel", base=base_l, amortissements=deduits, report=report_l)
    r.impot = sup(base_l)
    liste.append(r)

    # SCI à l'IR : résultat foncier réparti entre associés selon leurs parts
    associes = [a for a in p.associes if (a.parts or 0) > 0] or [Associe(100, R, sit, enf)]
    impot_sci, details = 0.0, [note_travaux] if note_travaux else []
    for a in associes:
        part = (a.parts or 0) / 100
        base_a = max(base_nu * part, -PLAFOND_DEFICIT_FONCIER)
        impot_sci += impot_en_plus(a.revenu_imposable or 0, a.situation, a.enfants or 0, base_a, base_a, ps)
    if len(associes) > 1:
        details.append(f"{len(associes)} associés, impôt de chacun selon sa part")
    r = Regime("sci-ir", "SCI à l'IR (location nue)", base=base_nu, report=report_nu, detail=" · ".join(details))
    r.impot = impot_sci
    liste.append(r)

    # SCI à l'IS : la société paie l'IS ; les dividendes distribués supportent la flat tax
    res_is = L - C - I - A
    impot_is = 0.0 if res_is <= 0 else (min(res_is, IS_SEUIL_TAUX_REDUIT) * IS_TAUX_REDUIT
                                        + max(0.0, res_is - IS_SEUIL_TAUX_REDUIT) * IS_TAUX_NORMAL) / 100
    dividendes = max(0.0, res_is - impot_is) * (p.distribution_pct or 0) / 100
    r = Regime("sci-is", "SCI à l'IS", base=None, amortissements=A,
               report=max(0.0, -res_is), detail=f"IS {impot_is:,.0f} € + flat tax sur {dividendes:,.0f} € de dividendes"
               .replace(",", " "))
    r.impot = impot_is + dividendes * (p.taux_pfu or 0) / 100
    liste.append(r)

    # Lots nus et meublés mélangés : chaque lot dans sa catégorie, au réel (charges réparties au prorata des loyers)
    if len({l.meuble for l in lots}) > 1 and L > 0:
        loy = sum(loyer_lot(l, p.scenario) for l in lots) or 1
        s_m = sum(loyer_lot(l, p.scenario) for l in lots if l.meuble) / loy
        s_n = 1 - s_m
        surf = sum(l.surface or 0 for l in lots) or 1
        t_nu = T * sum(l.surface or 0 for l in lots if not l.meuble) / surf
        b_n, rep_n = _foncier_reel(L * s_n, C * s_n + t_nu, I * s_n)
        b_m, ded_m, rep_m = _bic_reel(L * s_m, C * s_m, I * s_m, A * s_m)
        r = Regime("mixte", "Votre répartition : nus au réel + meublés en LMNP réel", base=b_n + b_m, amortissements=ded_m,
                   report=rep_n + rep_m, detail=f"{s_n:.0%} des loyers en location nue, {s_m:.0%} en meublé".replace("%", " %"))
        r.impot = sup(b_n + b_m, max(0.0, b_n) + b_m)
        liste.insert(0, r)

    for r in liste:
        r.cash_flow = cf_avant - r.impot / 12
        r.renta_nette_nette = (L - C - I - r.impot) / total if total else 0
    return liste


# ---------------------------------------------------------------------------------------------------------------------
# Prix cible (la macro de l'Excel) et courbe
# ---------------------------------------------------------------------------------------------------------------------

def _cash_flow(p, prix, mode):
    r = simuler(p, prix)
    if mode == "avant":
        return r.cash_flow_avant_impot
    g = next((x for x in r.eligibles if x.id == mode), None) or r.meilleur
    return g.cash_flow if g else r.cash_flow_avant_impot


def bornes_prix(p):
    reference = p.prix_affiche or p.prix
    return reference * 0.5, reference * 1.1


def prix_cible(p, cash_flow=0.0, mode="avant"):
    """Prix d'achat qui donne le cash-flow mensuel voulu (avant impôt, ou après impôt dans le régime `mode`).
    Arrondi à la centaine inférieure. None si aucun prix entre 50 % et 110 % du prix affiché ne le donne."""
    bas, haut = bornes_prix(p)
    if haut <= 0 or _cash_flow(p, bas, mode) < cash_flow or _cash_flow(p, haut, mode) > cash_flow:
        return None
    for _ in range(40):
        milieu = (bas + haut) / 2
        if _cash_flow(p, milieu, mode) >= cash_flow:
            bas = milieu
        else:
            haut = milieu
    return int(bas // 100 * 100)


def courbe(p, points=30):
    """Cash-flow mensuel selon le prix d'achat, avant impôt et après impôt pour chaque régime possible."""
    bas, haut = bornes_prix(p)
    if haut <= 0:
        return None
    prix, series = [], {"avant": []}
    for k in range(points + 1):
        x = bas + (haut - bas) * k / points
        r = simuler(p, x)
        prix.append(round(x))
        series["avant"].append(round(r.cash_flow_avant_impot, 1))
        for g in r.regimes:
            series.setdefault(g.id, []).append(round(g.cash_flow, 1) if g.eligible else None)
    noms = {g.id: g.nom for g in simuler(p).regimes}
    return {"prix": prix, "series": series, "noms": noms, "prix_actuel": round(p.prix), "affiche": round(p.prix_affiche or p.prix)}
