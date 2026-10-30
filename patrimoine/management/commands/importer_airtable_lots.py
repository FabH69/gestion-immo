"""Importe la table Airtable des appartements (export CSV) en propriétaires, biens et lots.

    python manage.py importer_airtable_lots chemin/vers/Appartement.csv [--appliquer] [--avec-vendus]

Sans --appliquer, la commande affiche seulement ce qu'elle ferait. Relancer l'import met à jour
les lots déjà importés (repérés par leur identifiant Airtable) au lieu de les dupliquer.
"""
import csv
import re
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from patrimoine.models import Bien, Lot, Proprietaire


def _texte(valeur):
    return " ".join((valeur or "").split())


def _type_proprietaire(nom):
    nom_min = nom.lower()
    if nom_min.startswith("sci"):
        return Proprietaire.Type.SCI_IR
    if nom_min.startswith("sarl"):
        return Proprietaire.Type.SARL_FAMILLE
    return Proprietaire.Type.NOM_PROPRE


def _type_lot(tag):
    tag = tag.lower()
    if "garage" in tag or "parking" in tag:
        return Lot.Type.PARKING
    if "maison" in tag:
        return Lot.Type.MAISON
    if "studio" in tag:
        return Lot.Type.STUDIO
    if "local" in tag or "commerce" in tag:
        return Lot.Type.LOCAL_COMMERCIAL
    return Lot.Type.APPARTEMENT


def _nombre(valeur, type_=Decimal):
    try:
        return type_(valeur.replace(",", ".")) if valeur.strip() else None
    except (InvalidOperation, ValueError):
        return None


class Command(BaseCommand):
    help = "Importe l'export CSV de la table Airtable des appartements"

    def add_arguments(self, parser):
        parser.add_argument("fichier")
        parser.add_argument("--appliquer", action="store_true", help="Enregistre réellement les données")
        parser.add_argument("--avec-vendus", action="store_true", help="Importe aussi les lots au statut « Vendu »")

    def handle(self, fichier, appliquer, avec_vendus, **options):
        with open(fichier, encoding="utf-8-sig", newline="") as f:
            lignes = list(csv.DictReader(f))

        with transaction.atomic():
            compte = {"lots": 0, "ignorés": 0}
            for ligne in lignes:
                adresse = _texte(ligne.get("Adresse_Appart"))
                proprio_nom = _texte(ligne.get("Propriétaire"))
                vendu = _texte(ligne.get("Statut")).lower() == "vendu"
                if not adresse or not proprio_nom or (vendu and not avec_vendus):
                    compte["ignorés"] += 1
                    self.stdout.write(f"  ignoré : {_texte(ligne.get('Appartement')) or '(ligne vide)'}"
                                      f"{' (vendu)' if vendu else ''}{' (sans adresse ou propriétaire)' if not adresse or not proprio_nom else ''}")
                    continue

                proprio, _ = Proprietaire.objects.get_or_create(nom=proprio_nom, defaults={"type": _type_proprietaire(proprio_nom)})
                ville = _texte(ligne.get("Ville_Appart"))
                bien, _ = Bien.objects.get_or_create(
                    proprietaire=proprio, adresse=adresse, ville=ville,
                    defaults={"nom": re.sub(r"\s*\d+$", "", _texte(ligne.get("Appartement"))) or adresse,
                              "code_postal": _texte(ligne.get("CP_Appart")), "syndic": _texte(ligne.get("Copro"))},
                )
                etage = _texte(ligne.get("Etage")).replace("étgae", "étage")
                type_lot = _type_lot(_texte(ligne.get("Tags")))
                designation = _texte(ligne.get("Location 4")) or _texte(ligne.get("Appartement")) or f"{type_lot.label} {etage}".strip()
                chauffage = _texte(ligne.get("Type_Chauffage"))
                eau = _texte(ligne.get("Type_eau"))
                notes = [f"{k} : {_texte(ligne.get(k))}" for k in ("Tel EDF", "Tel Eau") if _texte(ligne.get(k))]
                valeurs = {
                    "bien": bien, "designation": designation, "type": type_lot, "etage": etage,
                    "surface": _nombre(ligne.get("Superficie", "")), "nb_pieces": None if type_lot == Lot.Type.PARKING else _nombre(ligne.get("Nb_Pièce", ""), int),
                    "chauffage": chauffage.lower() and f"{chauffage}", "eau_chaude": eau,
                    "regime_juridique": "copropriete" if _texte(ligne.get("Copro")) else "",
                    "pdl": _texte(ligne.get("PDL")), "vendu": vendu, "notes": "\n".join(notes),
                }
                ref = _texte(ligne.get("Record ID"))
                lot = Lot.objects.filter(ref_import=ref).first() if ref else None
                if lot:
                    for champ, valeur in valeurs.items():
                        setattr(lot, champ, valeur)
                    lot.save()
                    action = "mis à jour"
                else:
                    lot = Lot.objects.create(ref_import=ref, **valeurs)
                    action = "créé"
                compte["lots"] += 1
                self.stdout.write(f"  lot {action} : {proprio.nom} › {bien.nom} ({bien.ville}) › {lot.designation}"
                                  f" – {lot.get_type_display()}, {etage or 'étage ?'}, {lot.surface or '?'} m², {lot.nb_pieces or '?'} p.")

            self.stdout.write(f"{compte['lots']} lot(s), {compte['ignorés']} ligne(s) ignorée(s). "
                              f"Propriétaires : {Proprietaire.objects.count()}, biens : {Bien.objects.count()}.")
            if not appliquer:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Simulation : rien n'a été enregistré. Ajoutez --appliquer pour importer."))
            else:
                self.stdout.write(self.style.SUCCESS("Import enregistré."))
