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
