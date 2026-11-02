"""Envoi des mails par le compte Gmail réglé dans Réglages › Mails (smtp.gmail.com, autorisé par PythonAnywhere)."""
import logging

from django.core.mail import EmailMessage, get_connection

journal = logging.getLogger(__name__)


def reglage():
    from .models import ParametresMail

    return ParametresMail.objects.filter(pk=1).first()


def envoyer(destinataires, sujet, texte, reglage_mail=None, lever=False):
    """Envoie un mail texte ; renvoie True si parti. Sans réglage, ne fait rien (renvoie False)."""
    r = reglage_mail or reglage()
    if not r:
        return False
    from django.conf import settings

    backend = getattr(settings, "ENVOI_MAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
    connexion = get_connection(backend, host="smtp.gmail.com", port=587, use_tls=True,
                               username=r.adresse, password=r.mot_de_passe, timeout=20)
    expediteur = f"{r.nom_expediteur} <{r.adresse}>" if r.nom_expediteur else r.adresse
    message = EmailMessage(sujet, texte, expediteur, [d for d in destinataires if d], reply_to=[r.adresse], connection=connexion)
    try:
        message.send()
        return True
    except Exception:
        journal.exception("Envoi du mail impossible : %s", sujet)
        if lever:
            raise
        return False


def lien_candidature(candidature, request):
    from django.urls import reverse

    return request.build_absolute_uri(reverse("candidature_publique", args=[candidature.jeton]))


def texte_invitation(candidature, url):
    lot = candidature.lot
    return (f"Bonjour,\n\nSuite à votre intérêt pour le logement {lot.designation} ({lot.bien.adresse}, {lot.bien.ville}), "
            f"voici le formulaire de candidature à remplir en ligne :\n{url}\n\n"
            "Vous pourrez y déposer vos justificatifs. Cordialement")


def dossier_recu(candidature, request):
    """Après l'envoi du formulaire : alerte au propriétaire et accusé de réception au candidat."""
    from django.urls import reverse

    r = reglage()
    if not r:
        return
    lot = candidature.lot
    qui = f"{candidature.prenom} {candidature.nom}".strip()
    if r.alerte_nouveau_dossier:
        url = request.build_absolute_uri(reverse("admin:patrimoine_candidature_change", args=[candidature.pk]))
        envoyer([r.adresse], f"Nouveau dossier de candidature – {lot.designation} – {qui}",
                f"{qui} a rempli le dossier de candidature pour {lot.designation} ({lot.bien.ville}).\n"
                f"Revenus nets : {candidature.revenus_mensuels or '–'} € / mois. "
                f"Justificatifs déposés : {candidature.pieces.count()}.\n\nVoir le dossier : {url}", r)
    if r.accuse_au_candidat and candidature.email:
        envoyer([candidature.email], f"Votre dossier de candidature – {lot.designation}",
                f"Bonjour {candidature.prenom},\n\nNous avons bien reçu votre dossier de candidature pour le logement "
                f"{lot.designation} ({lot.bien.ville}). Nous revenons vers vous rapidement.\n\nCordialement\n{r.nom_expediteur}".rstrip(), r)
