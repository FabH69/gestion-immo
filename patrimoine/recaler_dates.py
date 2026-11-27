"""Remettre des loyers encaissés à leur date d'échéance.

Cas typique : plusieurs loyers anciens encaissés d'un coup, tous datés du jour de la saisie. Les états et les
déclarations comptent un loyer à la date de son paiement : chaque loyer doit garder la date de son mois.

Chaque part de paiement (un loyer réglé) prend la date d'échéance de son loyer. Un paiement reçu qui ne règle qu'un
mois, ou plusieurs mois ramenés à la même date, change simplement de date ; sinon il est séparé en un paiement reçu
par loyer (même mode, même note). Une échéance pas encore passée n'est pas touchée."""
from dataclasses import dataclass
from datetime import date

from django.db import transaction


@dataclass
class Changement:
    paiement: object
    ancienne: date
    nouvelle: date

    @property
    def versement(self):
        return self.paiement.versement

    @property
    def echeance(self):
        return self.paiement.echeance


def proposer(versements, aujourdhui=None):
    """[Changement] pour les paiements reçus donnés (annulés exclus)."""
    from .models import Paiement

    aujourdhui = aujourdhui or date.today()
    paiements = (Paiement.objects.filter(versement__in=versements, versement__annule=False)
                 .select_related("versement__bail__lot__bien", "echeance").order_by("versement_id", "echeance__periode"))
    return [Changement(p, p.date, p.echeance.date_exigibilite) for p in paiements
            if p.echeance.date_exigibilite != p.date and p.echeance.date_exigibilite <= aujourdhui]


@transaction.atomic
def appliquer(changements):
    """Applique les changements ; renvoie le nombre de loyers redatés."""
    from .models import Versement

    par_versement = {}
    for c in changements:
        par_versement.setdefault(c.paiement.versement_id, []).append(c)
    for liste in par_versement.values():
        v = Versement.objects.get(pk=liste[0].paiement.versement_id)
        dates = {c.nouvelle for c in liste}
        tout = v.paiements.count() == len(liste)
        if tout and len(dates) == 1 and not v.part_depot:
            # un seul mois (ou tous ramenés au même jour) : le paiement reçu change simplement de date
            v.date = dates.pop()
            v.save(update_fields=["date"])
            for c in liste:
                c.paiement.date = v.date
                c.paiement.save(update_fields=["date"])
            continue
        for c in liste:
            p = c.paiement
            nouveau = Versement.objects.create(bail=v.bail, date=c.nouvelle, montant=p.montant, mode=v.mode, notes=v.notes)
            p.versement, p.date = nouveau, c.nouvelle
            p.save(update_fields=["versement", "date"])
            v.montant -= p.montant
        if v.montant <= 0 and not v.part_depot and not v.rembourse and not v.paiements.exists():
            v.delete()
        else:
            v.save(update_fields=["montant"])
    return len(changements)
