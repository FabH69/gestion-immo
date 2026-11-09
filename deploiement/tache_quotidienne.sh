#!/bin/bash
# Tâche quotidienne (PythonAnywhere › onglet Tasks › Daily, par exemple à 07:00) :
#     bash ~/gestion-immo/deploiement/tache_quotidienne.sh
# Envoie le mail des rappels importants (★) quelques jours avant leur date limite.
APPLI="$HOME/gestion-immo"
VENV="$HOME/.virtualenvs/gestion-immo"
DOMAINE="$(echo "$USER" | tr 'A-Z' 'a-z').pythonanywhere.com"
cd "$APPLI" || exit 1
export DJANGO_SECRET_KEY="$(cat "$HOME/.gestion-immo-secret" 2>/dev/null)" DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS="$DOMAINE"
"$VENV/bin/python" manage.py rappels_par_mail
