"""Relances d'impayés : loyers en retard d'un bail, niveau de relance suggéré et textes des courriers / mails."""
from decimal import Decimal

from django.utils import timezone

from .models import Bail, Echeance, Relance

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
SUITE = {None: Relance.Niveau.RAPPEL, Relance.Niveau.RAPPEL: Relance.Niveau.RELANCE,
         Relance.Niveau.RELANCE: Relance.Niveau.MISE_EN_DEMEURE, Relance.Niveau.MISE_EN_DEMEURE: Relance.Niveau.MISE_EN_DEMEURE}
OBJETS = {
    Relance.Niveau.RAPPEL: "Rappel – loyer non réglé",
    Relance.Niveau.RELANCE: "Relance – loyer impayé",
    Relance.Niveau.MISE_EN_DEMEURE: "Mise en demeure de payer",
    Relance.Niveau.GARANT: "Information de la caution – loyers impayés",
}


def euros(montant):
    return f"{montant:,.2f}".replace(",", " ").replace(".", ",") + " €"


def mois_texte(d):
    return f"{MOIS[d.month - 1]} {d.year}"


def _de(texte):
    return f"d'{texte}" if texte[:1] in "aeiouéèêâ" else f"de {texte}"


def impayes(bail, a_date=None):
    """[(échéance, reste dû)] des loyers échus et non soldés du bail, du plus ancien au plus récent."""
    a_date = a_date or timezone.localdate()
    lignes = []
    for e in bail.echeances.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=a_date).order_by("periode"):
        reste = e.reste_du
        if reste > 0:
            lignes.append((e, reste))
    return lignes


def baux_en_impaye():
    """Baux ayant au moins un loyer échu non soldé, avec le total dû et la dernière relance."""
    ids = (Echeance.objects.exclude(statut=Echeance.Statut.PAYE).filter(date_exigibilite__lt=timezone.localdate())
           .values_list("bail_id", flat=True).distinct())
    resultat = []
    for bail in Bail.objects.filter(pk__in=ids).select_related("lot__bien").prefetch_related("locataires"):
        lignes = impayes(bail)
        if lignes:
            resultat.append({"bail": bail, "lignes": lignes, "total": sum((r for _, r in lignes), Decimal(0)),
                             "derniere": derniere_relance(bail, lignes), "suggere": niveau_suggere(bail, lignes)})
    return sorted(resultat, key=lambda x: x["lignes"][0][0].periode)


def derniere_relance(bail, lignes=None):
    """Dernière relance envoyée depuis le premier loyer impayé en cours (les relances d'un ancien impayé réglé ne comptent pas)."""
    lignes = impayes(bail) if lignes is None else lignes
    if not lignes:
        return None
    return (bail.relances.exclude(niveau=Relance.Niveau.GARANT).filter(date__gte=lignes[0][0].date_exigibilite)
            .order_by("-date", "-pk").first())


def niveau_suggere(bail, lignes=None):
    derniere = derniere_relance(bail, lignes)
    return SUITE[derniere.niveau if derniere else None]


def _liste_mois(lignes):
    noms = [mois_texte(e.periode) for e, _ in lignes]
    if len(noms) == 1:
        return f"le loyer du mois {_de(noms[0])}"
    return "les loyers des mois " + ", ".join(_de(n) for n in noms[:-1]) + " et " + _de(noms[-1])


TITRES = {"M.": "Monsieur", "Mme": "Madame"}


def _titre(personnes):
    """« Madame », « Monsieur » ou « Madame, Monsieur » selon les destinataires."""
    titres = list(dict.fromkeys(TITRES.get(getattr(p, "civilite", ""), "") for p in personnes))
    return titres[0] if len(titres) == 1 and titres[0] else "Madame, Monsieur"


def _bonjour(personnes):
    """Formule d'appel du courrier."""
    return f"{_titre(personnes)},"


def courrier(bail, niveau, lignes):
    """Objet, formule d'appel et paragraphes du courrier (mêmes textes pour le PDF et le mail)."""
    total = euros(sum((r for _, r in lignes), Decimal(0)))
    concernes = _liste_mois(lignes)
    adresse = ", ".join(x for x in [bail.lot.bien.adresse, f"{bail.lot.bien.code_postal} {bail.lot.bien.ville}".strip()] if x)
    proprio = bail.lot.bien.proprietaire
    iban = f" par virement sur le compte {proprio.iban}" if proprio.iban else ""
    locataires = list(bail.locataires.all())
    precedente = derniere_relance(bail, lignes)
    depuis = f" du {precedente.date:%d/%m/%Y}" if precedente else ""
    if niveau == Relance.Niveau.RAPPEL:
        paragraphes = [
            f"Sauf erreur de ma part, je n'ai pas encore reçu le règlement de {concernes} pour le logement situé {adresse}, "
            f"soit {total}.",
            f"Il s'agit sans doute d'un simple oubli. Je vous remercie de bien vouloir effectuer ce règlement dans les meilleurs "
            f"délais{iban}.",
            "Si vous l'avez déjà fait, merci de ne pas tenir compte de ce message.",
        ]
    elif niveau == Relance.Niveau.RELANCE:
        paragraphes = [
            f"{'Malgré mon précédent rappel' + depuis + ', je' if precedente else 'Je'} constate que {concernes} pour le logement "
            f"situé {adresse} {'reste impayé' if len(lignes) == 1 else 'restent impayés'}, soit {total}.",
            f"Je vous demande de régulariser cette situation dans un délai de 8 jours{iban}.",
            "Si vous rencontrez une difficulté passagère, je vous invite à me contacter rapidement afin que nous trouvions "
            "ensemble une solution, par exemple un échéancier de paiement.",
        ]
    elif niveau == Relance.Niveau.MISE_EN_DEMEURE:
        paragraphes = [
            f"{'Malgré mes relances (la dernière' + depuis + '), ' if precedente else ''}{concernes} pour le logement situé "
            f"{adresse} {'reste impayé' if len(lignes) == 1 else 'restent impayés'}.",
            f"Par la présente, je vous mets en demeure de me régler la somme de {total}, détaillée ci-dessous, dans un délai "
            f"de 8 jours à compter de la réception de ce courrier{iban}.",
            "À défaut de paiement dans ce délai, je me verrai contraint de vous faire délivrer un commandement de payer par "
            "commissaire de justice, visant la clause résolutoire du bail, et d'en informer la caution.",
            "Si vous rencontrez des difficultés financières, vous pouvez solliciter le Fonds de solidarité pour le logement "
            "(FSL) de votre département ou votre caisse d'allocations familiales.",
        ]
    else:  # information de la caution
        noms = " et ".join(str(l) for l in locataires) or "votre proche"
        paragraphes = [
            f"Vous vous êtes porté caution de {noms} pour le logement situé {adresse}.",
            f"Je vous informe que {concernes} {'reste impayé' if len(lignes) == 1 else 'restent impayés'}, "
            f"soit {total} à ce jour.",
            "Je vous remercie de bien vouloir vous rapprocher de votre proche afin que cette situation soit régularisée "
            "rapidement. À défaut, je serai amené à vous demander le règlement de ces sommes, conformément à votre "
            "engagement de caution.",
        ]
    paragraphes = [p[:1].upper() + p[1:] for p in paragraphes]
    personnes = destinataires(bail, niveau)
    return {"objet": OBJETS[niveau], "appel": _bonjour(personnes), "paragraphes": paragraphes,
            "politesse": f"Je vous prie d'agréer, {_titre(personnes)}, l'expression de mes salutations distinguées.",
            "total": total}


def destinataires(bail, niveau):
    if niveau == Relance.Niveau.GARANT:
        return list(bail.garants.all())
    return list(bail.locataires.all())


def texte_mail(bail, niveau, lignes):
    c = courrier(bail, niveau, lignes)
    detail = "\n".join(f"- {mois_texte(e.periode).capitalize()} : {euros(r)}" for e, r in lignes)
    personnes = destinataires(bail, niveau)
    corps = "\n\n".join(c["paragraphes"])
    joint = "\n\nVous trouverez ce courrier en pièce jointe." if niveau == Relance.Niveau.MISE_EN_DEMEURE else ""
    nom = personnes[0].nom.title() if len(personnes) == 1 and _titre(personnes) != "Madame, Monsieur" else ""
    return (f"{'Bonjour ' + _titre(personnes) + ' ' + nom + ',' if nom else 'Bonjour,'}\n\n{corps}"
            f"\n\nDétail :\n{detail}\nTotal : {c['total']}{joint}\n\nBien cordialement,")


def enregistrer(bail, niveau, lignes, mode):
    """Trace la relance (une seule par bail, niveau et jour : préparer deux fois le même courrier ne la double pas)."""
    total = sum((r for _, r in lignes), Decimal(0))
    detail = ", ".join(mois_texte(e.periode) for e, _ in lignes)[:300]
    r, _ = Relance.objects.update_or_create(bail=bail, niveau=niveau, date=timezone.localdate(),
                                            defaults={"montant": total, "detail": detail, "mode": mode})
    return r
