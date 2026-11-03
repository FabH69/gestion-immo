"""Remplissage des modèles Word (.docx) de l'utilisateur : les champs s'écrivent {Nom_Du_Champ} dans le document.

Word découpe souvent un champ en plusieurs morceaux (correcteur orthographique, mise en forme) : on reconstitue
le texte de chaque paragraphe, on remplace les champs, et on garde la mise en forme du morceau où commence le champ.
"""
import io
import re
import zipfile
from html import escape, unescape
from pathlib import Path

MODELES = Path(__file__).resolve().parent / "modeles"

_PARAGRAPHE = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_TEXTE = re.compile(r"(<w:t(?: [^>]*)?>)([^<]*)(</w:t>)")
_CHAMP = re.compile(r"\{([^{}]+)\}")


def normaliser(nom):
    """Nom de champ comparé sans accents, majuscules, espaces ni tirets : {Nom_loc} = {Nom_Loc} = {nom loc}."""
    import unicodedata

    sans_accents = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sans_accents.lower())


def _remplacer_paragraphe(xml, trouver):
    """trouver(texte complet) renvoie des (début, fin, remplacement) ; la mise en forme du premier morceau est gardée."""
    morceaux = list(_TEXTE.finditer(xml))
    textes = [unescape(m.group(2)) for m in morceaux]
    complet = "".join(textes)
    remplacements = trouver(complet)
    if not remplacements:
        return xml
    debuts, pos = [], 0
    for t in textes:
        debuts.append(pos)
        pos += len(t)

    def morceau_de(i):
        for k in range(len(textes) - 1, -1, -1):
            if debuts[k] <= i and (textes[k] or k == 0):
                return k
        return 0

    for debut, fin, valeur in sorted(remplacements, reverse=True):
        k_debut, k_fin = morceau_de(debut), morceau_de(fin - 1)
        for k in range(k_debut, k_fin + 1):
            a = max(debut - debuts[k], 0)
            b = min(fin - debuts[k], len(textes[k]))
            textes[k] = textes[k][:a] + (str(valeur) if k == k_debut else "") + textes[k][b:]

    resultat, curseur = [], 0
    for m, t in zip(morceaux, textes):
        ouverture = m.group(1)
        if 'xml:space="preserve"' not in ouverture:
            ouverture = '<w:t xml:space="preserve">'
        resultat += [xml[curseur:m.start()], ouverture, escape(t, quote=False), m.group(3)]
        curseur = m.end()
    resultat.append(xml[curseur:])
    return "".join(resultat)


def _remplir_paragraphe(xml, valeurs):
    normalisees = {normaliser(k): v for k, v in valeurs.items()}

    def trouver(complet):
        if "{" not in complet:
            return []
        trouves = []
        for champ in _CHAMP.finditer(complet):
            nom = champ.group(1).strip()
            valeur = valeurs.get(nom, normalisees.get(normaliser(nom)))
            if valeur is not None:
                trouves.append((champ.start(), champ.end(), valeur))
        return trouves

    return _remplacer_paragraphe(xml, trouver)


def champs_du_modele(source):
    """Noms des champs {…} présents dans un modèle (chemin ou fichier ouvert)."""
    noms = []
    with zipfile.ZipFile(source) as z:
        for nom in z.namelist():
            if re.match(r"word/(document|header\d*|footer\d*)\.xml$", nom):
                xml = z.read(nom).decode("utf-8")
                for p in _PARAGRAPHE.finditer(xml):
                    texte = "".join(unescape(m.group(2)) for m in _TEXTE.finditer(p.group(0)))
                    noms += [c.group(1).strip() for c in _CHAMP.finditer(texte)]
    return list(dict.fromkeys(noms))


def remplacer_textes_docx(source, remplacements):
    """Remplace des textes exacts (même coupés en plusieurs morceaux par Word) ; sert à transformer un document rempli en modèle."""
    def trouver(complet):
        trouves = []
        for ancien, nouveau in remplacements:
            i = complet.find(ancien)
            while i >= 0:
                trouves.append((i, i + len(ancien), nouveau))
                i = complet.find(ancien, i + len(ancien))
        return trouves

    return _transformer_docx(source, lambda xml: _PARAGRAPHE.sub(lambda m: _remplacer_paragraphe(m.group(0), trouver), xml))


def _transformer_docx(source, transformer):
    sortie = io.BytesIO()
    with zipfile.ZipFile(source) as entree, zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as cible:
        for element in entree.infolist():
            contenu = entree.read(element.filename)
            if re.match(r"word/(document|header\d*|footer\d*)\.xml$", element.filename):
                contenu = transformer(contenu.decode("utf-8")).encode("utf-8")
            cible.writestr(element, contenu)
    return sortie.getvalue()


def remplir_docx(modele, valeurs, couleur_champs="FF0000"):
    """Renvoie le contenu du .docx rempli (modèle : nom d'un fichier de patrimoine/modeles, chemin ou fichier ouvert).
    Les noms de champs sont reconnus sans tenir compte des accents ni des majuscules. Le rouge et le surlignage jaune qui signalent les champs sont retirés."""
    if isinstance(modele, str) and not Path(modele).is_absolute():
        modele = MODELES / modele

    def transformer(xml):
        xml = _PARAGRAPHE.sub(lambda m: _remplir_paragraphe(m.group(0), valeurs), xml)
        if couleur_champs:
            xml = xml.replace(f'<w:color w:val="{couleur_champs}"/>', "")
        xml = xml.replace('<w:highlight w:val="yellow"/>', "")  # surlignage jaune qui repère les champs dans le modèle
        return xml

    return _transformer_docx(modele, transformer)
