"""Motifs proposés par le bouton « Contacter » de la liste des locataires : sujet et message pré-remplis, modifiables avant l'envoi."""
from datetime import date


def _euros(montant):
    return f"{montant:,.2f} €".replace(",", " ").replace(".", ",")


def motifs(locataire):
    from . import mails
    from .models import Echeance

    bail = locataire.baux.filter(actif=True).select_related("lot__bien").first() or \
        locataire.baux.select_related("lot__bien").order_by("-date_debut").first()
    logement = f"{bail.lot} ({bail.lot.bien.adresse}, {bail.lot.bien.ville})" if bail else "votre logement"
    r = mails.reglage()
    signature = f"\n\nCordialement\n{r.nom_expediteur}" if r and r.nom_expediteur else "\n\nCordialement"
    bonjour = f"Bonjour {locataire.civilite + ' ' if locataire.civilite else ''}{locataire.nom},\n\n"

    impayes = [e for e in Echeance.objects.filter(bail__locataires=locataire, date_exigibilite__lte=date.today()).order_by("periode")
               if e.reste_du > 0]
    detail = "\n".join(f"- {e.periode:%m/%Y} : {_euros(e.reste_du)}" for e in impayes)
    total = sum((e.reste_du for e in impayes), 0)
    texte_impaye = (f"Sauf erreur de notre part, nous n'avons pas reçu le règlement suivant pour {logement} :\n{detail}\n"
                    f"Total dû : {_euros(total)}.\n\nMerci de procéder au règlement dans les meilleurs délais, ou de nous contacter "
                    "si vous rencontrez une difficulté." if impayes else
                    f"Sauf erreur de notre part, nous n'avons pas reçu votre dernier loyer pour {logement}.\n\n"
                    "Merci de procéder au règlement dans les meilleurs délais, ou de nous contacter si vous rencontrez une difficulté.")

    liste = [
        ("loyer", "Relance de loyer impayé", "Loyer impayé", texte_impaye),
        ("assurance", "Attestation d'assurance", "Attestation d'assurance habitation",
         f"Comme chaque année, merci de nous transmettre votre attestation d'assurance habitation à jour pour {logement}."),
        ("revision", "Révision annuelle du loyer", "Révision annuelle de votre loyer",
         f"Conformément à votre bail, le loyer de {logement} est révisé chaque année selon l'indice de référence des loyers (IRL). "
         "Vous recevrez le détail du nouveau montant, applicable à la prochaine échéance."),
        ("chaudiere", "Entretien de la chaudière", "Entretien annuel de la chaudière",
         f"L'entretien annuel de la chaudière de {logement} est à faire. Merci de nous transmettre l'attestation d'entretien "
         "dès qu'il aura été réalisé, ou de nous indiquer vos disponibilités pour un rendez-vous."),
        ("visite", "Visite ou intervention", "Intervention dans votre logement",
         f"Une intervention est prévue dans {logement}. Pourriez-vous nous indiquer vos disponibilités dans les prochains jours ?"),
        ("charges", "Régularisation des charges", "Régularisation annuelle des charges",
         f"Voici la régularisation annuelle des charges de {logement}. Le détail des dépenses est à votre disposition sur simple demande."),
        ("sortie", "Préavis et état des lieux de sortie", "Votre départ du logement",
         f"Nous avons bien reçu votre préavis pour {logement}. Afin d'organiser l'état des lieux de sortie et la remise des clés, "
         "pourriez-vous nous indiquer vos disponibilités ?"),
        ("depot", "Restitution du dépôt de garantie", "Restitution de votre dépôt de garantie",
         f"Suite à votre départ de {logement}, nous vous informons de la restitution de votre dépôt de garantie. "
         "Merci de nous confirmer vos coordonnées bancaires (RIB) pour le virement."),
        ("autre", "Autre (message libre)", "", ""),
    ]
    return [{"code": c, "libelle": l, "sujet": s, "message": (bonjour + m + signature) if m else bonjour + signature.lstrip("\n")}
            for c, l, s, m in liste]
