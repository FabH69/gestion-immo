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
