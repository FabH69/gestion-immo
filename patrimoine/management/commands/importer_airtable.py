"""Importe l'ensemble des tables Airtable (exports CSV « Grid view ») dans l'application.

    python manage.py importer_airtable --appartements Appartement.csv --proprietaires Propriétaire.csv \\
        --locataires Locataire.csv --locations Location.csv --copros Copro.csv --garants Garant.csv [--appliquer]

Chaque table est facultative, mais les locations ont besoin des appartements et des locataires.
Sans --appliquer, la commande affiche seulement ce qu'elle ferait. Relancer l'import met à jour
les fiches déjà importées au lieu de les dupliquer.
"""
import csv
import re
from datetime import date, datetime
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from patrimoine.models import Bail, Bien, Garant, Locataire, Lot, MouvementDepot, Proprietaire, _ajouter_mois

from .importer_airtable_lots import _nombre, _texte

TRIMESTRES = {"1er": "T1", "2eme": "T2", "2ème": "T2", "3eme": "T3", "3ème": "T3", "4eme": "T4", "4ème": "T4"}


def _lire(fichier):
    with open(fichier, encoding="utf-8-sig", newline="") as f:
        return [ligne for ligne in csv.DictReader(f) if any(_texte(v) for k, v in ligne.items() if k != "Doc/Reçu_Gar")]


def _date(valeur):
    valeur = _texte(valeur)
    try:
        return datetime.strptime(valeur, "%d/%m/%Y").date() if valeur else None
    except ValueError:
        return None


def _telephone(valeur):
    chiffres = re.sub(r"\D", "", valeur or "")
    if len(chiffres) == 10:
        return " ".join(chiffres[i:i + 2] for i in range(0, 10, 2))
    return _texte(valeur)


def _civilite(valeur):
    return {"mr": "M.", "m": "M.", "m.": "M.", "mme": "Mme"}.get(_texte(valeur).lower(), "")


def _liens(valeur):
    """Champ lié Airtable : noms séparés par des virgules. Les doublons « … copy » sont écartés."""
    return [nom for nom in (valeur or "").split(",") if nom.strip() and not nom.strip().lower().endswith(" copy")]


def _ajouter_note(objet, note):
    if note and note not in objet.notes:
        objet.notes = f"{objet.notes}\n{note}".strip()


class Command(BaseCommand):
    help = "Importe les exports CSV des tables Airtable (appartements, propriétaires, locataires, locations, copros, garants)"

    def add_arguments(self, parser):
        for table in ("appartements", "proprietaires", "locataires", "locations", "copros", "garants"):
            parser.add_argument(f"--{table}")
        parser.add_argument("--appliquer", action="store_true", help="Enregistre réellement les données")

    def handle(self, appliquer, **fichiers):
        self.a_verifier = []
        self.noms_airtable = {}
        with transaction.atomic():
            if fichiers["appartements"]:
                self.titre("Appartements")
                call_command("importer_airtable_lots", fichiers["appartements"], "--appliquer", stdout=self.stdout)
            for table in ("proprietaires", "copros", "locataires", "locations", "garants"):
                if fichiers[table]:
                    self.titre(table.capitalize())
                    getattr(self, f"importer_{table}")(_lire(fichiers[table]))

            if self.a_verifier:
                self.titre("À vérifier")
                for point in self.a_verifier:
                    self.stdout.write(f"  • {point}")
            if not appliquer:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Simulation : rien n'a été enregistré. Ajoutez --appliquer pour importer."))
            else:
                self.stdout.write(self.style.SUCCESS("Import enregistré."))

    def titre(self, texte):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{texte}"))

    def lots_nommes(self, nom):
        """Lots correspondant à un nom d'appartement Airtable : désignation exacte, sinon nom du bien."""
        nom = _texte(nom)
        lots = Lot.objects.filter(designation__iexact=nom)
        return list(lots) or list(Lot.objects.filter(bien__nom__iexact=re.sub(r"\s*\d+$", "", nom)))

    def importer_proprietaires(self, lignes):
        regimes = {}
        for ligne in lignes:
            nom = _texte(ligne.get("Propriétaire"))
            if not nom:
                continue
            proprio = Proprietaire.objects.filter(nom=nom).first() or Proprietaire(nom=nom, type=Proprietaire.Type.NOM_PROPRE)
            fiscalite = _texte(ligne.get("Type Fiscalité")).upper()
            if nom.lower().startswith("sci"):
                proprio.type = Proprietaire.Type.SCI_IS if fiscalite == "IS" else Proprietaire.Type.SCI_IR
            elif nom.lower().startswith("sarl"):
                proprio.type = Proprietaire.Type.SARL_FAMILLE
            proprio.adresse = _texte(ligne.get("Adresse_Propr")) or proprio.adresse
            proprio.code_postal = _texte(ligne.get("CP_Prop")) or proprio.code_postal
            proprio.ville = _texte(ligne.get("Ville_Prop")) or proprio.ville
            proprio.siret = re.sub(r"\D", "", ligne.get("SIRET", ""))[:14] or proprio.siret
            proprio.save()
            if proprio not in regimes:
                self.stdout.write(f"  {proprio.nom} – {proprio.get_type_display()}{', SIRET ' + proprio.siret if proprio.siret else ''}")
            regimes.setdefault(proprio, []).append((fiscalite, _texte(ligne.get("Appartement"))))
        for proprio, liste in regimes.items():
            if len({f for f, _ in liste}) > 1:
                _ajouter_note(proprio, "Fiscalité : " + " ; ".join(f"{f} ({apparts})" for f, apparts in liste))
                proprio.save()

    def importer_copros(self, lignes):
        for ligne in lignes:
            nom = _texte(ligne.get("Nom"))
            syndic = f"{nom} ({_texte(ligne.get('Ville_Corpo'))})" if _texte(ligne.get("Ville_Corpo")) else nom
            contact = ", ".join(filter(None, [_texte(ligne.get(k)) for k in ("Nom Gestionnaire", "Mail Gestionnaire", "Tel Gestionnaire", "Adresse", "URL Espcae")]))
            echeance = _texte(ligne.get("Date Echéance Contrat"))
            biens = {lot.bien for appart in _liens(ligne.get("Appartement")) for lot in self.lots_nommes(appart)}
            if not biens:
                self.stdout.write(f"  ignoré : {nom} (aucun bien importé ne correspond à « {_texte(ligne.get('Appartement'))} »)")
                continue
            for bien in biens:
                bien.syndic = syndic[:150]
                _ajouter_note(bien, f"Syndic {nom} : {contact}" if contact else "")
                _ajouter_note(bien, f"Contrat de syndic : échéance le {echeance}" if echeance else "")
                bien.save()
                self.stdout.write(f"  {bien.nom} › syndic {syndic}")

    def importer_locataires(self, lignes):
        for ligne in lignes:
            nom = _texte(ligne.get("Nom_loc"))
            ref = _texte(ligne.get("ID"))
            if not nom or nom.lower().endswith(" copy"):
                self.stdout.write(f"  ignoré : {nom or '(ligne vide)'}{' (doublon Airtable)' if nom else ''}")
                continue
            locataire = Locataire.objects.filter(ref_import=ref).first() if ref else None
            locataire = locataire or Locataire(ref_import=ref)
            locataire.nom, locataire.prenom = nom, _texte(ligne.get("Prénom_loc"))
            locataire.civilite = _civilite(ligne.get("Civilité_Loc"))
            locataire.email = _texte(ligne.get("Mail_Loc"))
            locataire.telephone = _telephone(ligne.get("Tel_Loc"))
            locataire.date_naissance = _date(ligne.get("Date_naissance_loc"))
            lieu, cp = _texte(ligne.get("Lieu_naissance_loc")), _texte(ligne.get("CP_naissance_loc"))
            locataire.lieu_naissance = f"{lieu} ({cp})" if lieu and cp else lieu
            locataire.save()
            self.noms_airtable[ligne["Nom_loc"].lstrip()] = locataire
            self.noms_airtable.setdefault(nom, locataire)
            self.stdout.write(f"  {locataire}{'' if locataire.email else ' (sans e-mail)'}")

    def locataire_par_nom(self, nom_airtable):
        # Airtable distingue parfois deux homonymes par une espace finale (« DESSEIGNE » / « DESSEIGNE ») :
        # on cherche d'abord le nom exact tel qu'exporté dans la table des locataires.
        return (self.noms_airtable.get(nom_airtable)
                or Locataire.objects.filter(nom__iexact=_texte(nom_airtable)).order_by("id").first())

    def importer_locations(self, lignes):
        aujourdhui = date.today()
        for ligne in lignes:
            appart = _texte(ligne.get("Appartement"))
            if not appart:
                continue
            lots = Lot.objects.filter(designation__iexact=appart)
            if lots.count() != 1:
                self.stdout.write(f"  ignoré : {appart} (lot {'introuvable ou vendu' if not lots else 'ambigu'})")
                continue
            lot = lots.get()
            # Airtable exporte certains noms liés avec une espace finale, qu'on garde pour distinguer les homonymes.
            noms = [n.lstrip() for n in _liens(ligne.get("Locataire"))]
            locataires = [l for l in (self.locataire_par_nom(n) for n in noms) if l]
            if not locataires:
                self.stdout.write(f"  ignoré : {appart} (locataire « {_texte(ligne.get('Locataire'))} » introuvable)")
                continue

            ref = f"location:{appart}"[:40]
            bail = Bail.objects.filter(ref_import=ref).first() or Bail(ref_import=ref, lot=lot)
            meuble = _texte(ligne.get("type Location")).lower().startswith("meubl")
            bail.type = Bail.Type.PARKING if lot.type == Lot.Type.PARKING else Bail.Type.MEUBLE if meuble else Bail.Type.NU
            if meuble and not lot.meuble:
                lot.meuble = True
                lot.save()
            duree_ans = _nombre(ligne.get("Durée_Bail", ""), int)
            debut, fin = _date(ligne.get("Date_début_Bail")), _date(ligne.get("Date_fin_Bail"))
            if not debut and fin and duree_ans:
                debut = _ajouter_mois(fin, -12 * duree_ans)
                self.a_verifier.append(f"{appart} : date de début absente, déduite de la fin et de la durée ({debut:%d/%m/%Y}).")
            if not debut:
                debut = bail.date_debut or aujourdhui.replace(day=1)
                self.a_verifier.append(f"{appart} : date de début inconnue, mise provisoirement au {debut:%d/%m/%Y}.")
                _ajouter_note(bail, "Date de début inconnue dans Airtable : à corriger.")
            bail.date_debut = debut
            # Un bail d'habitation arrivé à terme se poursuit par tacite reconduction : on ne garde pas
            # une date de fin passée, qui arrêterait la génération des loyers.
            if fin and fin < aujourdhui and bail.type in (Bail.Type.NU, Bail.Type.MEUBLE):
                _ajouter_note(bail, f"Fin de la période initiale : {fin:%d/%m/%Y}, reconduit tacitement.")
                fin = None
            bail.date_fin = fin
            bail.loyer_hc = _nombre(ligne.get("Loyer", "")) or Decimal(0)
            bail.charges = _nombre(ligne.get("Charges", "")) or Decimal(0)
            bail.mode_charges = "provision" if bail.charges else "aucune"
            bail.depot_garantie = _nombre(ligne.get("Dépot Garantie", "")) or Decimal(0)
            irl = _nombre(ligne.get("IRL_Ref", ""))
            periode = _texte(ligne.get("IRL_Ref_Période"))
            if irl:
                trimestre = TRIMESTRES.get(periode.split(" ")[0].lower(), "")
                annee = re.search(r"\d{4}", periode)
                bail.irl_valeur, bail.irl_trimestre = irl, trimestre
                bail.indice_reference = f"IRL {trimestre} {annee.group() if annee else ''} : {irl}".replace(".", ",").replace("  ", " ")
            revision = _date(ligne.get("Date Révision Loyer"))
            bail.date_derniere_revision = revision if revision and revision <= aujourdhui else bail.date_derniere_revision
            bail.date_attestation_assurance = _date(ligne.get("Date Assurance")) or bail.date_attestation_assurance
            # Colonne facultative « Type Garantie » (Garant, Visale, Autre…) ; à défaut, déduite de la table des garants.
            garantie = _texte(ligne.get("Type Garantie")).lower()
            for code in ("garant", "visale", "autre", "aucune"):
                if garantie.startswith(code[:4]):
                    bail.type_garantie = code
            if garantie.startswith("visa") or garantie.startswith("autre"):
                bail.detail_garantie = _texte(ligne.get("Détail Garantie")) or bail.detail_garantie
            if not bail.type_garantie and _texte(ligne.get("Garant")):
                bail.type_garantie = "garant"
            bail.actif = True
            bail.save()
            bail.locataires.set(locataires)

            if _texte(ligne.get("Statut Dépot Gar")).lower().startswith("encaiss") and bail.depot_garantie and not bail.mouvements_depot.exists():
                MouvementDepot.objects.create(bail=bail, date=bail.date_debut, montant=bail.depot_garantie)
            elif bail.depot_garantie and not bail.mouvements_depot.exists():
                self.a_verifier.append(f"{appart} : dépôt de garantie de {bail.depot_garantie} € non marqué « Encaissé » dans Airtable.")
            if not bail.type_garantie and bail.type != Bail.Type.PARKING:
                self.a_verifier.append(f"{appart} : type de garantie à renseigner (garant, Visale, autre ou aucune).")
            if not irl and bail.type != Bail.Type.PARKING:
                self.a_verifier.append(f"{appart} : IRL de référence absent, la révision ne pourra pas être calculée.")
            self.stdout.write(f"  {lot} › {', '.join(str(l) for l in locataires)} – {bail.get_type_display()} depuis le "
                              f"{bail.date_debut:%d/%m/%Y}, {bail.loyer_hc} € + {bail.charges} €, dépôt {bail.depot_garantie} €")

    def importer_garants(self, lignes):
        for ligne in lignes:
            noms_locataires = _liens(ligne.get("Locataire"))
            locataire = self.locataire_par_nom(noms_locataires[0].lstrip()) if noms_locataires else None
            if not locataire:
                self.stdout.write(f"  ignoré : {_texte(ligne.get('Nom_Garant'))} (locataire introuvable)")
                continue
            bail = Bail.objects.filter(ref_import=f"location:{_texte(ligne.get('Location'))}"[:40]).first()
            personnes = [("", "")]
            if _texte(ligne.get("Nom_Garant2")) and (_texte(ligne.get("Nom_Garant2")), _texte(ligne.get("Prénom_Garant2"))) != (_texte(ligne.get("Nom_Garant")), _texte(ligne.get("Prénom_Garant"))):
                personnes.append(("2", "2"))
            for suffixe, suffixe_adresse in personnes:
                nom, prenom = _texte(ligne.get(f"Nom_Garant{suffixe}")), _texte(ligne.get(f"Prénom_Garant{suffixe}"))
                adresse = _texte(ligne.get(f"Adresse_Garant{suffixe_adresse}")) or _texte(ligne.get("Adresse_Garant"))
                cp = _texte(ligne.get(f"CP_Garant{suffixe_adresse}")) or _texte(ligne.get("CP_Garant"))
                garant, _ = Garant.objects.get_or_create(locataire=locataire, nom=nom, prenom=prenom)
                garant.bail = bail
                garant.adresse = "\n".join(filter(None, [adresse, f"{cp} {_texte(ligne.get('Ville_Garant')).title()}".strip()]))
                garant.date_naissance = _date(ligne.get(f"Date naissance_Garant{suffixe}"))
                garant.lieu_naissance = _texte(ligne.get(f"Lieu_Naisance_Garant{suffixe}"))
                garant.save()
                if bail and bail.type_garantie != "garant":
                    bail.type_garantie = "garant"
                    bail.save(update_fields=["type_garantie"])
                self.stdout.write(f"  {garant} › garant de {locataire}{' (bail ' + str(bail.lot) + ')' if bail else ''}")
            if not bail:
                self.a_verifier.append(f"Garant {_texte(ligne.get('Nom_Garant'))} : aucun bail trouvé pour « {_texte(ligne.get('Location'))} ».")
