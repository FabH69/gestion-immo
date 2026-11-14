"""Plus-value immobilière des particuliers (nom propre, SCI à l'IR, LMNP) – règles en vigueur en 2026.

Références : CGI art. 150 U à 150 VH (calcul et abattements), 1609 nonies G (surtaxe),
loi de finances 2025 art. 84 (réintégration des amortissements LMNP pour les cessions depuis le 15/02/2025).
Non couvert : SCI ou SARL à l'IS (plus-value professionnelle), exonérations (résidence principale, première
cession, prix ≤ 15 000 €…), biens reçus par donation ou succession (frais réels obligatoires).
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .models import _ajouter_mois

TAUX_IR = Decimal("19")
TAUX_PS = Decimal("17.2")  # prélèvements sociaux sur les plus-values immobilières
FORFAIT_FRAIS_ACQUISITION = Decimal("7.5")
FORFAIT_TRAVAUX = Decimal("15")

# Surtaxe sur les plus-values > 50 000 € : (borne basse, borne haute, taux %, formule de lissage)
TRANCHES_SURTAXE = [
    (50_000, 60_000, 2, lambda pv: pv * Decimal("0.02") - (60_000 - pv) / 20),
    (60_000, 100_000, 2, None),
    (100_000, 110_000, 3, lambda pv: pv * Decimal("0.03") - (110_000 - pv) / 10),
    (110_000, 150_000, 3, None),
    (150_000, 160_000, 4, lambda pv: pv * Decimal("0.04") - (160_000 - pv) * Decimal("0.15")),
    (160_000, 200_000, 4, None),
    (200_000, 210_000, 5, lambda pv: pv * Decimal("0.05") - (210_000 - pv) * Decimal("0.20")),
    (210_000, 250_000, 5, None),
    (250_000, 260_000, 6, lambda pv: pv * Decimal("0.06") - (260_000 - pv) * Decimal("0.25")),
    (260_000, None, 6, None),
]


def _euros(x):
    return Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def annees_detention(date_acquisition, date_cession):
    """Nombre d'années pleines de détention, de date à date."""
    annees = date_cession.year - date_acquisition.year
    if (date_cession.month, date_cession.day) < (date_acquisition.month, date_acquisition.day):
        annees -= 1
    return max(annees, 0)


def abattement_ir(annees):
    """6 % par an de la 6e à la 21e année, 4 % la 22e : exonération d'impôt sur le revenu après 22 ans."""
    if annees <= 5:
        return Decimal(0)
    if annees <= 21:
        return Decimal(6) * (annees - 5)
    return Decimal(100)


def abattement_ps(annees):
    """1,65 % par an de la 6e à la 21e année, 1,60 % la 22e, 9 % de la 23e à la 30e : exonération après 30 ans."""
    if annees <= 5:
        return Decimal(0)
    if annees <= 21:
        return Decimal("1.65") * (annees - 5)
    if annees <= 30:
        return Decimal(28) + Decimal(9) * (annees - 22)
    return Decimal(100)


def surtaxe(pv_imposable_ir):
    pv = Decimal(pv_imposable_ir)
    if pv <= 50_000:
        return Decimal(0)
    for bas, haut, taux, lissage in TRANCHES_SURTAXE:
        if pv > bas and (haut is None or pv <= haut):
            return _euros(lissage(pv) if lissage else pv * taux / 100)
    return Decimal(0)


@dataclass
class Simulation:
    date_acquisition: date
    prix_acquisition: Decimal
    date_cession: date
    prix_cession: Decimal
    frais_cession: Decimal = Decimal(0)
    frais_acquisition_reels: Decimal | None = None  # vide : forfait 7,5 %
    travaux_reels: Decimal | None = None  # vide : forfait 15 % si détention > 5 ans
    amortissements_lmnp: Decimal = Decimal(0)
    quote_part: Decimal = Decimal(100)  # part du cédant (%), ex. associé de SCI ou époux
    taux_ps: Decimal = TAUX_PS
    lignes: list = field(default_factory=list)

    def calculer(self):
        annees = annees_detention(self.date_acquisition, self.date_cession)
        plus_de_5_ans = _ajouter_mois(self.date_acquisition, 60) < self.date_cession

        frais_acq_forfait = self.frais_acquisition_reels is None
        frais_acq = _euros(self.prix_acquisition * FORFAIT_FRAIS_ACQUISITION / 100) if frais_acq_forfait else self.frais_acquisition_reels
        travaux_forfait = self.travaux_reels is None
        travaux = _euros(self.prix_acquisition * FORFAIT_TRAVAUX / 100) if travaux_forfait and plus_de_5_ans else (self.travaux_reels or Decimal(0))

        prix_cession_net = self.prix_cession - self.frais_cession
        prix_revient = self.prix_acquisition + frais_acq + travaux - self.amortissements_lmnp
        pv_brute = prix_cession_net - prix_revient
        part = self.quote_part / 100
        pv_brute_part = _euros(pv_brute * part)

        r = {
            "annees": annees, "prix_acquisition": self.prix_acquisition, "amortissements": self.amortissements_lmnp,
            "frais_acquisition": frais_acq, "frais_acquisition_forfait": frais_acq_forfait,
            "travaux": travaux, "travaux_forfait": travaux_forfait and plus_de_5_ans,
            "travaux_forfait_impossible": travaux_forfait and not plus_de_5_ans,
            "prix_cession_net": prix_cession_net, "prix_revient": prix_revient,
            "pv_brute": pv_brute, "pv_brute_part": pv_brute_part, "quote_part": self.quote_part,
            "moins_value": pv_brute <= 0,
        }
        if pv_brute <= 0:
            r.update(abattement_ir=Decimal(0), abattement_ps=Decimal(0), pv_ir=Decimal(0), pv_ps=Decimal(0),
                     impot_ir=Decimal(0), impot_ps=Decimal(0), surtaxe=Decimal(0), total=Decimal(0), net_vendeur=prix_cession_net * part)
            return r

        ab_ir, ab_ps = abattement_ir(annees), abattement_ps(annees)
        pv_ir = _euros(pv_brute_part * (100 - ab_ir) / 100)
        pv_ps = _euros(pv_brute_part * (100 - ab_ps) / 100)
        impot_ir = _euros(pv_ir * TAUX_IR / 100)
        impot_ps = _euros(pv_ps * self.taux_ps / 100)
        taxe = surtaxe(pv_ir)
        total = impot_ir + impot_ps + taxe
        r.update(
            abattement_ir=ab_ir, abattement_ps=ab_ps, pv_ir=pv_ir, pv_ps=pv_ps,
            impot_ir=impot_ir, impot_ps=impot_ps, taux_ps=self.taux_ps, surtaxe=taxe, total=total,
            net_vendeur=_euros(prix_cession_net * part - total),
            annee_exoneration_ir=self.date_acquisition.year + 22, annee_exoneration_ps=self.date_acquisition.year + 30,
        )
        return r
