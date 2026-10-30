# Gestion immobilière

Application de gestion de parc locatif (biens, lots, locataires, baux, puis quittances et trésorerie).

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
