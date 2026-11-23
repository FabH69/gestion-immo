"""Rattache toutes les données d'un bien (ses locataires, baux, loyers, compta…) à un lot d'un autre bien.

Cas d'usage : un garage saisi comme bien à part (« Garage Saint Pierre ») alors qu'il est en réalité
un lot du bien « Saint Pierre ». La commande déplace tout vers ce lot et laisse l'ancien bien vide
(il n'est pas supprimé : on peut le supprimer ensuite depuis l'administration).

    python manage.py deplacer_vers_lot                         # liste les biens et leurs lots (avec leur n°)
    python manage.py deplacer_vers_lot --source "Garage Saint Pierre" --cible "Saint Pierre" --lot-cible Garage
    python manage.py deplacer_vers_lot ... --appliquer         # fait réellement le déplacement

Sans --appliquer, rien n'est modifié : la commande affiche seulement ce qui serait déplacé.
Biens et lots se désignent par leur nom (majuscules indifférentes) ou par leur n° (id).
La commande peut être relancée sans risque : ce qui a déjà été déplacé n'est plus trouvé.

Ce qui est déplacé :
- depuis le(s) lot(s) de l'ancien bien vers le lot cible : baux (avec leurs locataires, échéances de loyer,
  paiements, versements, dépôt de garantie, relances, révisions, états des lieux, garants), historique
  des encaissements, photos, annonces, candidatures, changements de locataire, courte durée…
- depuis l'ancien bien vers le bien cible : factures et pièces comptables, dépenses récurrentes, prêts,
  rappels, immobilisations, simulations.
- les champs vides du lot cible sont complétés avec ceux du lot d'origine (surface, DPE, code porte…).
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone

from patrimoine.models import Bail, Bien, Lot

# champs du lot d'origine qui ne sont jamais recopiés sur le lot cible
CHAMPS_LOT_NON_COPIES = {"id", "bien", "designation", "type", "ref_import", "vendu", "date_vente", "notes"}


def _trouver(queryset, valeur, quoi):
    valeur = str(valeur).strip()
    if valeur.isdigit():
        objet = queryset.filter(pk=int(valeur)).first()
        if objet:
            return objet
    trouves = list(queryset.filter(**{f"{'nom' if queryset.model is Bien else 'designation'}__iexact": valeur}))
    if len(trouves) == 1:
        return trouves[0]
    if not trouves:
        raise CommandError(f"{quoi} introuvable : « {valeur} ». Lancez la commande sans option pour voir la liste.")
    raise CommandError(f"Plusieurs {quoi}s s'appellent « {valeur} » : indiquez plutôt son n° ({', '.join(str(o.pk) for o in trouves)}).")


def _relations(cible):
    """Les clés étrangères de toute l'application qui pointent vers le modèle `cible` (Bien ou Lot)."""
    for modele in apps.get_models():
        for champ in modele._meta.get_fields():
            if (champ.is_relation and not champ.auto_created and champ.concrete
                    and (champ.many_to_one or champ.one_to_one) and champ.related_model is cible):
                yield modele, champ


def _vide(valeur):
    return valeur is None or valeur == ""


def _conflits(modele, champ, objets, destination):
    """Objets qui ne peuvent pas être déplacés : la destination a déjà son équivalent (contrainte d'unicité)."""
    if champ.one_to_one:
        return list(objets) if modele.objects.filter(**{champ.name: destination}).exists() else []
    conflits = []
    for contrainte in modele._meta.constraints:
        if isinstance(contrainte, models.UniqueConstraint) and champ.name in contrainte.fields:
            autres = [f for f in contrainte.fields if f != champ.name]
            for objet in objets:
                filtre = {champ.name: destination, **{f: getattr(objet, f) for f in autres}}
                if modele.objects.filter(**filtre).exists():
                    conflits.append(objet)
    return conflits


class Command(BaseCommand):
    help = "Rattache les données d'un bien (locataires, baux, loyers, compta) à un lot d'un autre bien"

    def add_arguments(self, parser):
        parser.add_argument("--source", help="Bien à vider, ex. « Garage Saint Pierre » (nom ou n°)")
        parser.add_argument("--cible", help="Bien qui reçoit les données, ex. « Saint Pierre » (nom ou n°)")
        parser.add_argument("--lot-cible", help="Lot du bien cible qui reçoit les données (désignation ou n°). "
                                                "Facultatif si le bien cible n'a qu'un seul garage ou parking.")
        parser.add_argument("--lot-source", help="Lot de l'ancien bien à déplacer, s'il en a plusieurs (désignation ou n°)")
        parser.add_argument("--appliquer", action="store_true", help="Fait réellement le déplacement (sinon : simulation)")

    # ------------------------------------------------------------------ affichage
    def _ligne(self, texte="", style=None):
        self.stdout.write(style(texte) if style else texte)

    def _lister_biens(self):
        self._ligne("Biens et lots (n° entre crochets) :")
        for bien in Bien.objects.select_related("proprietaire").prefetch_related("lots").order_by("nom"):
            self._ligne(f"  [{bien.pk}] {bien.nom} ({bien.proprietaire})")
            for lot in bien.lots.all():
                self._ligne(f"      lot [{lot.pk}] {lot.designation} ({lot.get_type_display()})")
        self._ligne()
        self._ligne('Exemple : python manage.py deplacer_vers_lot --source "Garage Saint Pierre" --cible "Saint Pierre" --lot-cible Garage')

    def _decrire_bail(self, bail):
        locataires = ", ".join(str(l) for l in bail.locataires.all()) or "sans locataire"
        etat = "actif" if bail.actif else "terminé"
        debut = f"{bail.date_debut:%d/%m/%Y}" if bail.date_debut else "?"
        nb_paiements = sum(e.paiements.count() for e in bail.echeances.all())
        return (f"bail du {debut} ({etat}) : {locataires} – {bail.echeances.count()} loyer(s), {nb_paiements} paiement(s), "
                f"{bail.mouvements_depot.count()} mouvement(s) de dépôt de garantie")

    # ------------------------------------------------------------------ commande
    def handle(self, source, cible, lot_cible, lot_source, appliquer, **options):
        if not source or not cible:
            self._lister_biens()
            return

        bien_source = _trouver(Bien.objects.all(), source, "bien")
        bien_cible = _trouver(Bien.objects.all(), cible, "bien")
        if bien_source == bien_cible:
            raise CommandError("Le bien source et le bien cible sont le même bien.")

        lots_cible = bien_cible.lots.all()
        if lot_cible:
            lot_dest = _trouver(lots_cible, lot_cible, f"lot de « {bien_cible.nom} »")
        else:
            garages = [l for l in lots_cible if l.type in (Lot.Type.GARAGE, Lot.Type.PARKING)]
            if len(garages) != 1:
                details = ", ".join(f"[{l.pk}] {l.designation}" for l in lots_cible) or "aucun lot"
                raise CommandError(f"Précisez --lot-cible : lots de « {bien_cible.nom} » : {details}.")
            lot_dest = garages[0]

        lots_source = list(bien_source.lots.all())
        if lot_source:
            lots_source = [_trouver(bien_source.lots.all(), lot_source, f"lot de « {bien_source.nom} »")]

        mode = "DÉPLACEMENT" if appliquer else "SIMULATION (rien n'est modifié ; ajoutez --appliquer pour le faire)"
        self._ligne(mode, self.style.WARNING if not appliquer else self.style.SUCCESS)
        self._ligne(f"De : [{bien_source.pk}] {bien_source.nom} – lot(s) : "
                    f"{', '.join(f'[{l.pk}] {l.designation}' for l in lots_source) or 'aucun'}")
        self._ligne(f"Vers : [{bien_cible.pk}] {bien_cible.nom} – lot [{lot_dest.pk}] {lot_dest.designation}")
        self._ligne()

        # avertissements : la compta suit le propriétaire et le régime du bien cible
        if bien_source.proprietaire_id != bien_cible.proprietaire_id:
            self._ligne(f"⚠ Propriétaires différents : « {bien_source.proprietaire} » → « {bien_cible.proprietaire} ». "
                        "Les loyers et factures déplacés compteront dans les déclarations du nouveau propriétaire.",
                        self.style.WARNING)
        if bien_source.regime_fiscal != bien_cible.regime_fiscal:
            self._ligne(f"⚠ Régimes fiscaux différents : « {bien_source.get_regime_fiscal_display()} » → "
                        f"« {bien_cible.get_regime_fiscal_display()} ».", self.style.WARNING)

        # inventaire de ce qui part
        a_deplacer = []  # (libellé, modèle, champ, objets, destination)
        for lot in lots_source:
            for modele, champ in _relations(Lot):
                objets = list(modele.objects.filter(**{champ.name: lot}))
                if objets:
                    a_deplacer.append((modele, champ, objets, lot_dest))
        for modele, champ in _relations(Bien):
            if modele is Lot:
                continue  # le lot d'origine reste sur l'ancien bien (vidé)
            objets = list(modele.objects.filter(**{champ.name: bien_source}))
            if objets:
                a_deplacer.append((modele, champ, objets, bien_cible))

        # champs vides du lot cible complétés depuis le lot d'origine (s'il n'y en a qu'un)
        a_completer = {}
        if len(lots_source) == 1:
            for champ in Lot._meta.concrete_fields:
                if champ.name in CHAMPS_LOT_NON_COPIES or isinstance(champ, models.BooleanField):
                    continue
                valeur = getattr(lots_source[0], champ.attname)
                if not _vide(valeur) and _vide(getattr(lot_dest, champ.attname)):
                    a_completer[champ] = valeur

        if not a_deplacer and not a_completer:
            self._ligne("Rien à déplacer : l'ancien bien est déjà vide.", self.style.SUCCESS)
            return

        conflits = []
        for modele, champ, objets, destination in a_deplacer:
            nom = modele._meta.verbose_name_plural
            cle = f" ({champ.verbose_name})" if modele._meta.model_name == "immobilisation" else ""
            self._ligne(f"• {len(objets)} {nom}{cle}")
            if modele is Bail:
                for bail in objets:
                    self._ligne(f"    – {self._decrire_bail(bail)}")
            for objet in _conflits(modele, champ, objets, destination):
                conflits.append(f"{nom} : « {objet} » existe déjà sur la destination")
        if a_completer:
            self._ligne("• champs du lot cible complétés : " + ", ".join(str(c.verbose_name) for c in a_completer))

        if conflits:
            self._ligne()
            for conflit in conflits:
                self._ligne(f"✗ {conflit}", self.style.ERROR)
            raise CommandError("Doublons sur la destination : rien n'a été déplacé. Corrigez-les puis relancez.")

        if not appliquer:
            self._ligne()
            self._ligne("Simulation terminée. Relancez la même ligne avec --appliquer pour faire le déplacement.",
                        self.style.WARNING)
            return

        with transaction.atomic():
            for modele, champ, objets, destination in a_deplacer:
                modele.objects.filter(pk__in=[o.pk for o in objets]).update(**{champ.name: destination})
            if a_completer:
                for champ, valeur in a_completer.items():
                    setattr(lot_dest, champ.attname, valeur)
                lot_dest.save(update_fields=[c.name for c in a_completer])
            for lot in lots_source:
                note = (f"Données déplacées le {timezone.localdate():%d/%m/%Y} vers « {bien_cible.nom} – "
                        f"{lot_dest.designation} ».")
                if note not in lot.notes:
                    lot.notes = f"{lot.notes}\n{note}".strip()
                    lot.save(update_fields=["notes"])

        self._ligne()
        self._ligne(f"Terminé : « {bien_source.nom} » est maintenant vide. Vous pouvez le supprimer depuis "
                    "l'administration si vous n'en avez plus besoin.", self.style.SUCCESS)
