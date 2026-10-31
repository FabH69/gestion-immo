# Gestion immobilière

Application de gestion de parc locatif (biens, lots, locataires, baux, loyers et quittances PDF ; trésorerie à venir).

## Lancer en local

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py donnees_demo   # facultatif : données fictives
.venv/bin/python manage.py runserver
```

Puis ouvrir http://127.0.0.1:8000/.

## Loyers et quittances

- Menu **Loyers** : boutons « Générer les loyers du mois » (un loyer par bail actif), puis saisie des paiements dans chaque loyer, ou action « Marquer comme payé intégralement » sur une sélection.
- Filtre « Impayés en retard » pour voir les loyers non soldés après leur date d'échéance.
- Colonne **Document** : quittance PDF si le loyer est payé en entier, sinon avis d'échéance.
- En ligne de commande : `python manage.py generer_echeances 2026-11`.

## États

- **Loyers encaissés** (page d'accueil ou menu Loyers) : tableau par propriétaire, bien et lot, mois par mois sur une année civile, avec la répartition loyers HC / charges. Filtre par entité, export PDF et CSV pour Excel.
- **Changements de locataire** : à la saisie d'un congé (ou d'une date de départ), l'application crée une check-list datée de 22 tâches, de la sortie à l'entrée du nouveau locataire, avec un rappel des règles utiles. Les dates se recalculent si le départ ou l'arrivée est décalé. Les tâches des 30 prochains jours s'affichent sur la page d'accueil.

## Documents pré-remplis

- Fiche d'un **bail** → « Documents pré-remplis » : bail PDF (structure du contrat type de la loi du 6 juillet 1989), fiche d'état des lieux d'entrée ou de sortie à imprimer.
- Menu **États des lieux** : saisie des compteurs, clés et observations, puis fiche PDF pré-remplie (avec inventaire du mobilier si meublé).
- Bail meublé : inventaire et état détaillé du mobilier (entrée et sortie).
- **Dépôts de garantie** : suivis sur un compte séparé des loyers (encaissement, retenues justifiées, restitution), solde détenu par entité, reçu PDF pour chaque encaissement.
- Les données viennent du propriétaire, du lot (caractéristiques, pièces) et des locataires ; ce qui manque apparaît « à compléter ». Vérifier le bail avec le modèle officiel avant signature.
- **Endettement** (page d'accueil ou menu Prêts) : récapitulatif des prêts par emprunteur, capital restant dû à la date choisie et intérêts de l'année, export PDF et CSV. Tableau d'amortissement PDF par prêt (prêts amortissables ou in fine).

## Rappels et révisions

- **Bloc « À faire »** sur la page d'accueil : tout ce qui arrive dans les 30 jours ou est en retard, avec un bouton « Fait ».
  - Révision annuelle du loyer (date anniversaire du bail ; une révision non demandée depuis plus d'un an est abandonnée).
  - Attestation d'assurance habitation (1 an après la dernière reçue).
  - Attestation d'entretien de la chaudière gaz (si le lot en a une, 1 an après le dernier entretien).
  - Rappels libres (AG, taxe foncière…), ponctuels ou annuels, et tâches des changements de locataire.
- **Révision du loyer** (fiche du bail → « Réviser le loyer ») : calcul selon l'IRL, mail pré-rempli au locataire, courrier PDF, application du nouveau loyer au bail. Alerte si le logement est classé F ou G (hausse interdite).

## Candidatures et garants

- **Candidatures** : choisir le logement crée un lien personnel à envoyer au futur locataire (mail pré-rempli). Le candidat remplit le formulaire sans compte, depuis son téléphone ; le dossier apparaît avec le taux d'effort. L'action « Retenir » crée le locataire et son garant. Le lien ne fonctionne pour le candidat qu'une fois l'application en ligne.
- **Garants** : fiche complète (identité, revenus, caution solidaire ou simple, montant maximum, durée) rattachée au locataire et au bail ; acte de cautionnement PDF pré-rempli.
- **Annonces** (fiche du lot → « Créer une annonce ») : titre et texte générés avec les mentions obligatoires (loyer CC, charges, dépôt, surface, DPE/GES, dépenses d'énergie), modifiables ; boutons de copie, photos du lot en zip numéroté, lien de dépôt Leboncoin. Les photos se chargent dans la fiche du lot.

## Mise en ligne sur PythonAnywhere

1. Onglet **Web** : *Add a new web app* › *Manual configuration* › *Python 3.12*.
2. Onglet **Files** : envoyer `gestion-immo.zip` (code + base de données) dans le dossier personnel.
3. Console **Bash** : `unzip -q gestion-immo.zip && bash gestion-immo/deploiement/installer_pythonanywhere.sh`

Le script installe les bibliothèques, prépare la base, écrit la configuration du site (clé secrète
dans `~/.gestion-immo-secret`, DEBUG désactivé), vérifie les PDF et crée le compte administrateur.
Il peut être relancé après chaque mise à jour du code.
