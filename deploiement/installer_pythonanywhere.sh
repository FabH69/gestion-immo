#!/bin/bash
# Installe ou met à jour l'appli sur PythonAnywhere. À lancer depuis une console Bash :
#     bash ~/gestion-immo/deploiement/installer_pythonanywhere.sh
# Prérequis : une appli web créée dans l'onglet Web, en « Manual configuration », Python 3.12.
set -e
PYTHON="${PYTHON:-python3.12}"
APPLI="$HOME/gestion-immo"
VENV="$HOME/.virtualenvs/gestion-immo"
DOMAINE="$(echo "$USER" | tr 'A-Z' 'a-z').pythonanywhere.com"
WSGI="${WSGI:-/var/www/$(echo "$DOMAINE" | tr '.' '_')_wsgi.py}"
SECRET="$HOME/.gestion-immo-secret"

cd "$APPLI"
echo "1/5 Installation des bibliothèques (quelques minutes)…"
[ -d "$VENV" ] || $PYTHON -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt

[ -f "$SECRET" ] || "$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(50))" > "$SECRET"
chmod 600 "$SECRET"
export DJANGO_SECRET_KEY="$(cat "$SECRET")" DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS="$DOMAINE"

echo "2/5 Base de données et fichiers de style…"
"$VENV/bin/python" manage.py migrate -v0
"$VENV/bin/python" manage.py collectstatic --noinput -v0

echo "3/5 Vérification des PDF…"
if "$VENV/bin/python" -c "import weasyprint; weasyprint.HTML(string='<p>ok</p>').write_pdf()" 2>/dev/null; then
  echo "    PDF : OK"
else
  echo "    PDF : NON DISPONIBLES sur ce serveur (le reste de l'appli fonctionne)"
fi

echo "4/5 Configuration du site $DOMAINE…"
if [ ! -f "$WSGI" ]; then
  echo "ERREUR : créez d'abord l'appli web (onglet Web > Add a new web app > Manual configuration > Python 3.12), puis relancez."
  exit 1
fi
SITE_PACKAGES="$("$VENV/bin/python" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
cat > "$WSGI" <<WSGI_EOF
import os, site, sys
site.addsitedir("$SITE_PACKAGES")
sys.path.insert(0, "$APPLI")
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ["DJANGO_SECRET_KEY"] = open("$SECRET").read().strip()
os.environ["DJANGO_DEBUG"] = "0"
os.environ["DJANGO_ALLOWED_HOSTS"] = "$DOMAINE"
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
WSGI_EOF
touch "$WSGI"

echo "5/5 Compte administrateur"
if "$VENV/bin/python" manage.py shell -c "from django.contrib.auth.models import User; exit(0 if User.objects.filter(is_superuser=True).exists() else 1)"; then
  echo "    Un compte existe déjà."
else
  echo "    Choisissez votre mot de passe (il ne s'affiche pas pendant la saisie) :"
  "$VENV/bin/python" manage.py createsuperuser --username fabien --email ""
fi
echo
echo "Terminé. Ouvrez https://$DOMAINE et connectez-vous avec l'identifiant « fabien »."
