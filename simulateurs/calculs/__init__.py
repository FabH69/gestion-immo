"""Calculs des simulateurs : fonctions pures (sans base de données), faciles à tester."""
from decimal import ROUND_HALF_UP, Decimal

CENTIME = Decimal("0.01")
EURO = Decimal("1")


def arrondi(montant, pas=CENTIME):
    return Decimal(montant).quantize(pas, rounding=ROUND_HALF_UP)
