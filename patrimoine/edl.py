"""État des lieux pièce par pièce (saisie sur téléphone) : liste des éléments, photos horodatées, sortie comparée à l'entrée, signature et empreinte."""
import hashlib
import io
import json
from datetime import datetime

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .documents import ELEMENTS_PIECE, INVENTAIRE_MEUBLE
from .models import Bail, ElementEDL, PhotoEDL

PIECE_INVENTAIRE = "Inventaire du mobilier"
TAILLE_PHOTO = 1600  # pixels sur le grand côté : lisible dans le PDF, légère à envoyer et à garder


def est_meuble(bail):
    return bail.lot.meuble or bail.type in (Bail.Type.MEUBLE, Bail.Type.ETUDIANT, Bail.Type.MOBILITE)


@transaction.atomic
def preparer(edl):
    """Crée la liste des éléments à la première ouverture : celle de l'entrée pour une sortie, sinon pièces du lot × éléments habituels."""
    if edl.elements.exists():
        return
    entree = edl.edl_entree()
    if entree and entree.elements.exists():
        for e in entree.elements.all():
            ElementEDL.objects.create(edl=edl, piece=e.piece, ordre=e.ordre, nom=e.nom, quantite=e.quantite,
                                      etat_entree=e.etat, commentaire_entree=e.commentaire, element_entree=e)
        return
    ordre = 0
    for piece in edl.bail.lot.liste_pieces():
        for nom in ELEMENTS_PIECE:
            ordre += 1
            ElementEDL.objects.create(edl=edl, piece=piece, ordre=ordre, nom=nom)
    if est_meuble(edl.bail):
        for nom in INVENTAIRE_MEUBLE:
            ordre += 1
            ElementEDL.objects.create(edl=edl, piece=PIECE_INVENTAIRE, ordre=ordre, nom=nom)


def pieces(edl):
    """[(pièce, [éléments])] dans l'ordre de saisie."""
    resultat = {}
    for e in edl.elements.prefetch_related("photos", "element_entree__photos"):
        resultat.setdefault(e.piece, []).append(e)
    return list(resultat.items())


def ajouter_element(edl, piece, nom):
    """Ajoute un élément à la fin de sa pièce (ou une nouvelle pièce à la fin de la liste)."""
    dans_piece = edl.elements.filter(piece=piece).order_by("-ordre").first()
    if dans_piece:
        edl.elements.filter(ordre__gt=dans_piece.ordre).update(ordre=F("ordre") + 1)
        ordre = dans_piece.ordre + 1
    else:
        dernier = edl.elements.order_by("-ordre").first()
        ordre = dernier.ordre + 1 if dernier else 1
    return ElementEDL.objects.create(edl=edl, piece=piece, ordre=ordre, nom=nom)


def _date_prise_de_vue(image):
    """Date de prise de vue écrite par l'appareil (EXIF), si elle existe. Elle est indicative : seule l'heure du serveur fait foi."""
    try:
        exif = image.getexif()
        valeur = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if not valeur:
            return None
        return timezone.make_aware(datetime.strptime(str(valeur).strip("\x00 "), "%Y:%m:%d %H:%M:%S"))
    except Exception:
        return None


def enregistrer_photo(element, fichier):
    """Redresse et réduit la photo, la range hors du dossier public, et note l'heure de réception et l'empreinte du fichier gardé."""
    from PIL import Image, ImageOps

    image = Image.open(fichier)
    prise_le = _date_prise_de_vue(image)
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((TAILLE_PHOTO, TAILLE_PHOTO))
    tampon = io.BytesIO()
    image.save(tampon, "JPEG", quality=82, optimize=True)
    contenu = tampon.getvalue()
    photo = PhotoEDL(edl=element.edl, element=element, prise_le=prise_le, empreinte=hashlib.sha256(contenu).hexdigest())
    photo.image.save("photo.jpg", ContentFile(contenu), save=False)
    photo.save()
    return photo


def contenu_signe(edl):
    """Tout ce que la signature engage, sous une forme stable : l'empreinte est calculée dessus et peut être recalculée pour vérifier."""
    return json.dumps({
        "bail": edl.bail_id, "type": edl.type, "date": edl.date.isoformat() if edl.date else "",
        "compteurs": [edl.compteur_electricite, edl.compteur_gaz, edl.compteur_eau_froide, edl.compteur_eau_chaude],
        "cles": edl.nb_cles, "nouvelle_adresse": edl.nouvelle_adresse, "observations": edl.observations,
        "signataires": [edl.signataire_bailleur, edl.signataire_locataire],
        "elements": [[e.piece, e.nom, e.etat, e.quantite, e.commentaire, e.etat_entree, [p.empreinte for p in e.photos.all()]]
                     for e in edl.elements.prefetch_related("photos")],
        "signatures": [hashlib.sha256(edl.signature_bailleur.encode()).hexdigest(), hashlib.sha256(edl.signature_locataire.encode()).hexdigest()],
        "bailleur_signe_le": edl.bailleur_signe_le.isoformat() if edl.bailleur_signe_le else "",
    }, ensure_ascii=False, sort_keys=True)


def calculer_empreinte(edl):
    return hashlib.sha256(contenu_signe(edl).encode()).hexdigest()


def integre(edl):
    """True si rien n'a changé depuis la signature (contenu et photos)."""
    if not edl.signe:
        return None
    if calculer_empreinte(edl) != edl.empreinte:
        return False
    for p in PhotoEDL.objects.filter(edl=edl):
        try:
            with p.image.open("rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != p.empreinte:
                    return False
        except (FileNotFoundError, OSError):
            return False
    return True


SIGNATURE_PREFIXE = "data:image/png;base64,"


def signature_valide(donnees):
    return isinstance(donnees, str) and donnees.startswith(SIGNATURE_PREFIXE) and 200 < len(donnees) < 400_000


def signer(edl, qui, donnees):
    """Le bailleur signe d'abord, puis le locataire : l'état des lieux est alors horodaté et verrouillé."""
    maintenant = timezone.now()
    if qui == "bailleur":
        edl.signature_bailleur, edl.bailleur_signe_le = donnees, maintenant
        edl.save(update_fields=["signature_bailleur", "bailleur_signe_le"])
        return
    edl.signature_locataire, edl.signe_le = donnees, maintenant
    edl.empreinte = calculer_empreinte(edl)
    edl.save(update_fields=["signature_locataire", "signe_le", "empreinte"])


def differences(edl):
    """Sortie : éléments dont l'état s'est dégradé depuis l'entrée."""
    return [e for e in edl.elements.all() if e.degrade]


def avancement(edl):
    elements = list(edl.elements.all())
    faits = sum(1 for e in elements if e.etat)
    return faits, len(elements)
