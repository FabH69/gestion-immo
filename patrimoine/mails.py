"""Envoi des mails par le compte Gmail réglé dans Réglages › Mails (smtp.gmail.com, autorisé par PythonAnywhere)."""
import logging

from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import escape

journal = logging.getLogger(__name__)


def reglage():
    from .models import ParametresMail

    return ParametresMail.objects.filter(pk=1).first()


def envoyer(destinataires, sujet, texte, reglage_mail=None, lever=False, html=None):
    """Envoie un mail texte (et sa version mise en forme si html) ; renvoie True si parti. Sans réglage, ne fait rien."""
    r = reglage_mail or reglage()
    if not r:
        return False
    from django.conf import settings

    backend = getattr(settings, "ENVOI_MAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
    connexion = get_connection(backend, host="smtp.gmail.com", port=587, use_tls=True,
                               username=r.adresse, password=r.mot_de_passe, timeout=20)
    expediteur = f"{r.nom_expediteur} <{r.adresse}>" if r.nom_expediteur else r.adresse
    message = EmailMultiAlternatives(sujet, texte, expediteur, [d for d in destinataires if d], reply_to=[r.adresse],
                                     connection=connexion)
    if html:
        message.attach_alternative(html, "text/html")
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


def _signature():
    r = reglage()
    return (r.nom_expediteur if r and r.nom_expediteur else "").strip()


def texte_invitation(candidature, url):
    """Version texte du mail d'invitation (rédigée comme un vrai message, pour ne pas finir en spam)."""
    lot = candidature.lot
    bonjour = f"Bonjour {candidature.prenom}," if candidature.prenom else "Bonjour,"
    adresse = ", ".join(x for x in [lot.bien.adresse, lot.bien.ville] if x)
    signature = _signature()
    return (f"{bonjour}\n\n"
            f"Merci pour l'intérêt que vous portez au logement {lot.designation}"
            f"{f' situé {adresse}' if adresse else ''}.\n\n"
            "Afin d'étudier votre candidature, je vous invite à compléter votre dossier en ligne "
            "et à y déposer vos justificatifs (pièce d'identité, justificatifs de revenus, etc.) :\n\n"
            f"{url}\n\n"
            "Ce lien vous est personnel. Si vous avez la moindre question, il vous suffit de répondre à ce message.\n\n"
            f"Bien cordialement,\n{signature}").rstrip()


def html_invitation(candidature, url):
    """Version mise en forme du même mail, avec un bouton."""
    lot = candidature.lot
    bonjour = f"Bonjour {escape(candidature.prenom)}," if candidature.prenom else "Bonjour,"
    adresse = ", ".join(x for x in [lot.bien.adresse, lot.bien.ville] if x)
    situe = f" situé {escape(adresse)}" if adresse else ""
    signature = escape(_signature())
    lien = escape(url)
    return f"""<!doctype html><html lang="fr"><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.5">
<p>{bonjour}</p>
<p>Merci pour l'intérêt que vous portez au logement <strong>{escape(lot.designation)}</strong>{situe}.</p>
<p>Afin d'étudier votre candidature, je vous invite à compléter votre dossier en ligne et à y déposer vos justificatifs
(pièce d'identité, justificatifs de revenus, etc.).</p>
<p style="margin:24px 0"><a href="{lien}" style="background:#1f5f5b;color:#fff;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:bold">Compléter mon dossier</a></p>
<p style="font-size:13px;color:#555">Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br><a href="{lien}">{lien}</a></p>
<p>Ce lien vous est personnel. Si vous avez la moindre question, il vous suffit de répondre à ce message.</p>
<p>Bien cordialement,<br>{signature}</p>
</body></html>"""


def _bonjour_locataires(locataires):
    if len(locataires) == 1 and locataires[0].civilite:
        return f"Bonjour {locataires[0].civilite} {locataires[0].nom.title()},"
    return "Bonjour,"


def texte_quittance(echeance, locataires, mois, est_quittance=True):
    """Texte d'accompagnement de la quittance (ou de l'avis d'échéance si le loyer n'est pas soldé)."""
    lot = echeance.bail.lot
    ville = lot.bien.ville or ""
    logement = (f"le logement d'{ville}" if ville[:1].lower() in "aeiouyéèêâh" else f"le logement de {ville}") if ville \
        else f"le logement {lot.designation}"
    de_mois = f"d'{mois}" if mois[:1] in "aeiouéèêâ" else f"de {mois}"
    if est_quittance:
        corps = (f"Veuillez trouver ci-joint la quittance de loyer du mois {de_mois} pour {logement}."
                 "\n\nJe vous remercie pour votre règlement.")
    else:
        corps = (f"Veuillez trouver ci-joint l'avis d'échéance du mois {de_mois} pour {logement}, "
                 f"d'un montant de {f'{echeance.total:.2f}'.replace('.', ',')} €.")
    # pas de signature : celle d'Outlook s'ajoute à l'envoi
    return (f"{_bonjour_locataires(locataires)}\n\n{corps}\n\n"
            "Je reste à votre disposition pour toute question.\n\nBien cordialement,")


def html_quittance(echeance, locataires, mois, est_quittance=True):
    paragraphes = texte_quittance(echeance, locataires, mois, est_quittance).split("\n\n")
    corps = "\n".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphes)
    return f"""<!doctype html><html lang="fr"><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.5">
{corps}
</body></html>"""


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
