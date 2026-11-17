"""Mensualités d'un prêt amortissable à taux fixe, coût total, TAEG et tableau d'amortissement.

Assurance emprunteur, au choix :
- sur le capital initial : montant fixe chaque mois (cas le plus courant des contrats de groupe des banques) ;
- sur le capital restant dû : montant qui baisse au fil du prêt (cas courant en délégation d'assurance).
Frais de dossier et de garantie (caution, hypothèque) : payés au départ, ils entrent dans le coût total et le TAEG.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from . import arrondi

ASSURANCE_INITIAL, ASSURANCE_RESTANT = "initial", "restant"
TAUX_ENDETTEMENT_MAX = Decimal("35")  # recommandation du Haut Conseil de stabilité financière, assurance comprise


def mensualite(capital, taux_annuel, duree_mois):
    """Mensualité hors assurance (capital + intérêts), arrondie au centime."""
    capital, t = Decimal(capital), Decimal(taux_annuel) / 1200
    if t == 0:
        return arrondi(capital / duree_mois)
    return arrondi(capital * t / (1 - (1 + t) ** -duree_mois))


@dataclass
class Echeance:
    numero: int
    interets: Decimal
    capital: Decimal
    assurance: Decimal
    restant: Decimal

    @property
    def annee(self):
        return (self.numero - 1) // 12 + 1

    @property
    def mensualite(self):
        return self.interets + self.capital

    @property
    def total(self):
        return self.mensualite + self.assurance


@dataclass
class Simulation:
    capital: Decimal
    taux: Decimal
    duree_mois: int
    taux_assurance: Decimal
    mode_assurance: str
    frais: Decimal
    mensualite: Decimal
    echeances: list = field(default_factory=list)

    @property
    def assurance_mensuelle(self):
        """Assurance de la première échéance (fixe si elle porte sur le capital initial)."""
        return self.echeances[0].assurance if self.echeances else Decimal(0)

    @property
    def mensualite_totale(self):
        return self.mensualite + self.assurance_mensuelle

    @property
    def cout_interets(self):
        return sum((e.interets for e in self.echeances), Decimal(0))

    @property
    def cout_assurance(self):
        return sum((e.assurance for e in self.echeances), Decimal(0))

    @property
    def cout_total(self):
        """Coût du crédit : intérêts + assurance + frais (hors capital remboursé)."""
        return self.cout_interets + self.cout_assurance + self.frais

    @property
    def total_rembourse(self):
        return self.capital + self.cout_total

    @property
    def taeg(self):
        return taeg(self.capital, [e.total for e in self.echeances], self.frais)

    def par_annee(self):
        """Tableau d'amortissement résumé par année de remboursement."""
        annees = {}
        for e in self.echeances:
            a = annees.setdefault(e.annee, {"annee": e.annee, "interets": Decimal(0), "capital": Decimal(0),
                                            "assurance": Decimal(0), "total": Decimal(0)})
            a["interets"] += e.interets
            a["capital"] += e.capital
            a["assurance"] += e.assurance
            a["total"] += e.total
            a["restant"] = e.restant
        return list(annees.values())

    def taux_endettement(self, revenus_mensuels, autres_credits=Decimal(0)):
        """Part des revenus consacrée aux crédits (en %), assurance comprise."""
        if not revenus_mensuels:
            return None
        return arrondi((self.mensualite_totale + Decimal(autres_credits or 0)) * 100 / Decimal(revenus_mensuels))


def simuler(capital, taux, duree_mois, taux_assurance=Decimal(0), mode_assurance=ASSURANCE_INITIAL, frais=Decimal(0)):
    capital, taux, taux_assurance = Decimal(capital), Decimal(taux), Decimal(taux_assurance or 0)
    m = mensualite(capital, taux, duree_mois)
    s = Simulation(capital=capital, taux=taux, duree_mois=duree_mois, taux_assurance=taux_assurance,
                   mode_assurance=mode_assurance, frais=Decimal(frais or 0), mensualite=m)
    t = taux / 1200
    restant = capital
    assurance_fixe = arrondi(capital * taux_assurance / 1200)
    for n in range(1, duree_mois + 1):
        interets = arrondi(restant * t)
        part_capital = restant if n == duree_mois else min(m - interets, restant)  # la dernière solde les arrondis
        assurance = assurance_fixe if mode_assurance == ASSURANCE_INITIAL else arrondi(restant * taux_assurance / 1200)
        restant -= part_capital
        s.echeances.append(Echeance(n, interets, part_capital, assurance, restant))
    return s


def taeg(capital, versements, frais=Decimal(0)):
    """Taux annuel effectif global (méthode actuarielle, en %) : le taux qui égalise le montant réellement reçu
    (capital moins frais) et les échéances versées, assurance comprise."""
    recu = float(capital) - float(frais)
    flux = [float(v) for v in versements]
    if recu <= 0 or not flux or sum(flux) <= recu:
        return Decimal(0)

    def valeur(r):
        return sum(v / (1 + r) ** (k + 1) for k, v in enumerate(flux)) - recu

    bas, haut = 0.0, 0.05
    while valeur(haut) > 0:
        haut *= 2
    for _ in range(100):
        milieu = (bas + haut) / 2
        if valeur(milieu) > 0:
            bas = milieu
        else:
            haut = milieu
    return arrondi(Decimal(((1 + bas) ** 12 - 1) * 100))
