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


def _remplir_paragraphe(xml, valeurs):
    morceaux = list(_TEXTE.finditer(xml))
    textes = [unescape(m.group(2)) for m in morceaux]
    complet = "".join(textes)
    if "{" not in complet:
        return xml
    # position de début de chaque morceau dans le texte complet
    debuts, pos = [], 0
    for t in textes:
        debuts.append(pos)
        pos += len(t)

    def morceau_de(i):
        for k in range(len(textes) - 1, -1, -1):
            if debuts[k] <= i and (textes[k] or k == 0):
                return k
        return 0

    for champ in reversed(list(_CHAMP.finditer(complet))):
        nom = champ.group(1).strip()
        if nom not in valeurs:
            continue
        debut, fin = champ.span()
        k_debut, k_fin = morceau_de(debut), morceau_de(fin - 1)
        for k in range(k_debut, k_fin + 1):
            a = max(debut - debuts[k], 0)
            b = min(fin - debuts[k], len(textes[k]))
            textes[k] = textes[k][:a] + (str(valeurs[nom]) if k == k_debut else "") + textes[k][b:]

    resultat, curseur = [], 0
    for m, t in zip(morceaux, textes):
        ouverture = m.group(1)
        if 'xml:space="preserve"' not in ouverture:
            ouverture = '<w:t xml:space="preserve">'
        resultat += [xml[curseur:m.start()], ouverture, escape(t, quote=False), m.group(3)]
        curseur = m.end()
    resultat.append(xml[curseur:])
    return "".join(resultat)


def remplir_docx(nom_modele, valeurs, couleur_champs="FF0000"):
    """Renvoie le contenu du .docx rempli. Le rouge qui signale les champs dans le modèle est retiré."""
    sortie = io.BytesIO()
    with zipfile.ZipFile(MODELES / nom_modele) as source, zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as cible:
        for element in source.infolist():
            contenu = source.read(element.filename)
            if re.match(r"word/(document|header\d*|footer\d*)\.xml$", element.filename):
                xml = contenu.decode("utf-8")
                xml = _PARAGRAPHE.sub(lambda m: _remplir_paragraphe(m.group(0), valeurs), xml)
                if couleur_champs:
                    xml = xml.replace(f'<w:color w:val="{couleur_champs}"/>', "")
                contenu = xml.encode("utf-8")
            cible.writestr(element, contenu)
    return sortie.getvalue()
