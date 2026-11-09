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
# le code est rangé par métier (dossiers models/, views/, admin/) : les anciens fichiers uniques sont retirés
for ancien in patrimoine/models.py patrimoine/views.py patrimoine/admin.py; do
  if [ -d "${ancien%.py}" ]; then rm -f "$ancien"; fi
done
echo "1/5 Installation des bibliothèques (quelques minutes)…"
[ -d "$VENV" ] || $PYTHON -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt

[ -f "$SECRET" ] || "$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(50))" > "$SECRET"
chmod 600 "$SECRET"
export DJANGO_SECRET_KEY="$(cat "$SECRET")" DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS="$DOMAINE"

echo "2/5 Base de données et fichiers de style…"
# sauvegarde complète avant toute modification (Réglages › Sauvegardes pour la récupérer)
if [ -f db.sqlite3 ]; then
  "$VENV/bin/python" manage.py shell -c "from patrimoine import sauvegarde; print('    Sauvegarde avant mise à jour :', sauvegarde.creer().name)" \
    || { echo "ERREUR : la sauvegarde avant mise à jour a échoué, rien n'a été modifié."; exit 1; }
fi
"$VENV/bin/python" manage.py migrate -v0
"$VENV/bin/python" manage.py collectstatic --noinput -v0
# Reprises de données : chacune ne tourne qu'une fois (puis de nouveau seulement si le fichier change),
# pour ne jamais recréer ce que vous avez supprimé ni écraser ce que vous avez corrigé dans l'appli.
FAIT="$HOME/.gestion-immo-maj"
mkdir -p "$FAIT"
une_fois() {  # une_fois <fichier de référence> <commande…>
  local temoin="$FAIT/$(basename "$1").$(sha1sum "$1" | cut -c1-12)"
  [ -f "$temoin" ] && return 0
  shift
  "$@" && touch "$temoin"
}
if [ -f donnees/prets.csv ]; then
  une_fois donnees/prets.csv "$VENV/bin/python" manage.py importer_prets donnees/prets.csv --appliquer | tail -1
fi
# fiches syndics complétées une seule fois (vos modifications ultérieures ne sont pas écrasées)
if [ -f donnees/Copro.csv ] && [ ! -f "$HOME/.gestion-immo-syndics-importes" ]; then
  "$VENV/bin/python" manage.py importer_airtable --copros donnees/Copro.csv --appliquer | tail -1 && touch "$HOME/.gestion-immo-syndics-importes"
fi
# mises à jour de données ponctuelles (ne créent que ce qui manque)
for maj in donnees/maj_*.py; do
  [ -f "$maj" ] && { une_fois "$maj" "$VENV/bin/python" manage.py shell -c "exec(open('$maj', encoding='utf-8').read())" || echo "    ATTENTION : $maj n'a pas pu s'appliquer (il sera retenté à la prochaine mise à jour)"; }
done
if [ -f donnees/lcd/initialiser.sh ]; then
  bash donnees/lcd/initialiser.sh "$VENV/bin/python"
fi

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
