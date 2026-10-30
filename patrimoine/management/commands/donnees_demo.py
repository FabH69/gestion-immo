"""Crée des données fictives pour tester l'interface (python manage.py donnees_demo)."""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from patrimoine.models import Annonce, Bail, Bien, Candidature, PhotoLot, ChangementLocataire, Garant, Echeance, MouvementDepot, Pret, Rappel, Locataire, Lot, Paiement, Proprietaire


class Command(BaseCommand):
    help = "Crée des données fictives de démonstration"

    def handle(self, *args, **options):
        P = Proprietaire.Type
        nom_propre = Proprietaire.objects.create(nom="Nom propre", type=P.NOM_PROPRE)
        sci1 = Proprietaire.objects.create(nom="SCI Démo 1", type=P.SCI_IR, adresse="1 place Bellecour\n69002 Lyon", siret="12345678900011", representant="M. Jean Exemple, gérant", iban="FR76 0000 0000 0000 0000 0000 000")
        sci2 = Proprietaire.objects.create(nom="SCI Démo 2", type=P.SCI_IS)
        sarl = Proprietaire.objects.create(nom="SARL de famille Démo", type=P.SARL_FAMILLE)

        immeuble = Bien.objects.create(proprietaire=sci1, nom="Immeuble des Lilas", adresse="12 rue des Lilas", code_postal="44000", ville="Nantes")
        maison = Bien.objects.create(proprietaire=nom_propre, nom="Maison du Port", adresse="3 quai du Port", code_postal="56100", ville="Lorient")
        residence = Bien.objects.create(proprietaire=sarl, nom="Résidence Gare", adresse="8 avenue de la Gare", code_postal="35000", ville="Rennes")
        local = Bien.objects.create(proprietaire=sci2, nom="Local centre-ville", adresse="25 place du Marché", code_postal="44000", ville="Nantes")

        L = Lot.Type
        lots = [
            Lot.objects.create(bien=immeuble, designation="Apt RDC", type=L.APPARTEMENT, surface=45, nb_pieces=2, etage="Rez-de-chaussée, porte gauche", annee_construction="1975", regime_juridique="copropriete", chauffage="Individuel gaz", eau_chaude="Individuelle gaz", classe_dpe="D", equipements="Cuisine équipée (plaques, hotte), interphone, fibre", annexes="Cave n° 4"),
            Lot.objects.create(bien=immeuble, designation="Apt 1er", type=L.APPARTEMENT, surface=62, nb_pieces=3),
            Lot.objects.create(bien=immeuble, designation="Parking 1", type=L.PARKING),
            Lot.objects.create(bien=maison, designation="Maison", type=L.MAISON, surface=95, nb_pieces=4),
            Lot.objects.create(bien=residence, designation="Studio 3", type=L.STUDIO, surface=22, nb_pieces=1, meuble=True),
            Lot.objects.create(bien=residence, designation="Studio 4", type=L.STUDIO, surface=24, nb_pieces=1, meuble=True),
            Lot.objects.create(bien=local, designation="Boutique", type=L.LOCAL_COMMERCIAL, surface=70),
        ]

        martin = Locataire.objects.create(civilite="Mme", prenom="Claire", nom="Martin", email="claire.martin@example.com", date_naissance=date(1992, 4, 12), lieu_naissance="Angers", adresse_actuelle="5 rue Neuve, 49000 Angers", garant="M. Pierre Martin, 5 rue Neuve, 49000 Angers")
        durand = Locataire.objects.create(civilite="M.", prenom="Paul", nom="Durand")
        petit = Locataire.objects.create(civilite="Mme", prenom="Léa", nom="Petit")
        bernard = Locataire.objects.create(civilite="M.", prenom="Hugo", nom="Bernard")

        B = Bail.Type
        baux = [
            (lots[0], [martin], B.NU, date(2024, 9, 1), "620", "40", "620"),
            (lots[1], [durand], B.NU, date(2023, 3, 1), "780", "60", "780"),
            (lots[3], [petit], B.NU, date(2022, 7, 1), "1050", "0", "1050"),
            (lots[4], [bernard], B.MEUBLE, date(2026, 9, 1), "490", "50", "980"),
        ]
        for lot, locs, type_, debut, loyer, charges, depot in baux:
            bail = Bail.objects.create(lot=lot, type=type_, date_debut=debut, loyer_hc=Decimal(loyer), charges=Decimal(charges), depot_garantie=Decimal(depot),
                                       irl_trimestre="T2", irl_valeur=Decimal("143.58"), date_attestation_assurance=debut.replace(year=2025),
                                       date_derniere_revision=debut.replace(year=2025) if debut.year < 2025 else None)
            bail.locataires.set(locs)
            MouvementDepot.objects.create(bail=bail, date=debut, montant=bail.depot_garantie)

        for mois in (9, 10):
            Echeance.generer_pour_mois(date(2026, mois, 1))
        for e in Echeance.objects.all():
            if e.periode.month == 9 or e.bail.lot == lots[0]:
                Paiement.objects.create(echeance=e, date=e.date_exigibilite.replace(day=5), montant=e.total)
            elif e.bail.lot == lots[3]:
                Paiement.objects.create(echeance=e, date=date(2026, 10, 8), montant=Decimal("500"))

        changement = ChangementLocataire.objects.create(lot=lots[1], date_conge=date(2026, 9, 20), date_arrivee=date(2027, 1, 1))
        for tache in changement.taches.all()[:2]:
            tache.faite = True
            tache.save()

        Pret.objects.create(proprietaire=sci1, bien=immeuble, banque="Banque Démo", reference="IMM-001", montant=Decimal("320000"), taux=Decimal("1.45"), duree_mois=240, date_premiere_echeance=date(2021, 3, 5), assurance_mensuelle=Decimal("42"))
        Pret.objects.create(proprietaire=sarl, bien=residence, banque="Crédit Démo", reference="RG-77", montant=Decimal("180000"), taux=Decimal("3.9"), duree_mois=300, date_premiere_echeance=date(2024, 2, 10), assurance_mensuelle=Decimal("25"))
        Pret.objects.create(proprietaire=nom_propre, bien=maison, banque="Banque Démo", reference="MP-12", montant=Decimal("150000"), taux=Decimal("1.1"), duree_mois=180, date_premiere_echeance=date(2020, 9, 1), assurance_mensuelle=Decimal("18"))
        Pret.objects.create(proprietaire=sci2, bien=local, banque="Crédit Démo", reference="LC-03", type="in_fine", montant=Decimal("200000"), taux=Decimal("2.8"), duree_mois=120, date_premiere_echeance=date(2023, 6, 15))

        Lot.objects.filter(pk=lots[3].pk).update(chaudiere_gaz=True, date_entretien_chaudiere=date(2025, 11, 20))
        Rappel.objects.create(titre="AG de copropriété – Immeuble des Lilas", date=date(2026, 11, 18), categorie=Rappel.Categorie.AG, bien=immeuble)
        Rappel.objects.create(titre="Payer la taxe foncière", date=date(2026, 10, 15), categorie=Rappel.Categorie.FISCAL, annuel=True)

        Garant.objects.create(locataire=martin, bail=Bail.objects.get(lot=lots[0]), civilite="M.", prenom="Pierre", nom="Martin",
                              date_naissance=date(1960, 2, 3), lieu_naissance="Angers", adresse="5 rue Neuve\n49000 Angers", lien="père", duree_mois=36)
        Candidature.objects.create(
            lot=lots[5], statut=Candidature.Statut.RECUE, civilite="M.", prenom="Lucas", nom="Moreau", email="lucas.moreau@example.com",
            telephone="06 00 00 00 00", situation="cdi", employeur="Société Exemple", revenus_mensuels=Decimal("1850"), nb_occupants=1,
            type_garantie="visale", consentement=True,
        )
        Lot.objects.filter(pk=lots[5].pk).update(loyer_propose=Decimal("560"), classe_dpe="D", classe_ges="B", energie_min=480, energie_max=690, energie_annee=2023,
                                                 equipements="Kitchenette équipée, lave-linge, fibre", etage="2e étage avec ascenseur")
        self._photos_fictives(lots[5], ["Pièce principale", "Kitchenette", "Salle d'eau", "Vue"])
        Annonce.objects.create(lot=Lot.objects.get(pk=lots[5].pk), loyer_cc=Decimal("560"), charges=Decimal("50"), depot_garantie=Decimal("1020"),
                               disponible_le=date(2026, 12, 1), points_forts="À 5 minutes à pied de la gare\nRésidence calme et sécurisée\nIdéal étudiant ou jeune actif")
        Candidature.objects.create(lot=lots[5])

        self.stdout.write(self.style.SUCCESS("Données de démonstration créées."))

    def _photos_fictives(self, lot, legendes):
        """Images de remplacement unies (aucune vraie photo dans les données de démo)."""
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image, ImageDraw

        couleurs = ["#cfe3e1", "#f6e3b4", "#dfe7f2", "#e9dfd3"]
        for i, legende in enumerate(legendes):
            image = Image.new("RGB", (800, 600), couleurs[i % len(couleurs)])
            ImageDraw.Draw(image).text((30, 30), f"Photo d'exemple : {legende}", fill="#24585c")
            tampon = io.BytesIO()
            image.save(tampon, "JPEG")
            PhotoLot.objects.create(lot=lot, ordre=i, legende=legende, image=SimpleUploadedFile(f"exemple-{i}.jpg", tampon.getvalue()))
