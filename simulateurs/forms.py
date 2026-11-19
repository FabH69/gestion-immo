import json
from decimal import Decimal, InvalidOperation

from django import forms

from .calculs import notaire, pret


class MontantField(forms.DecimalField):
    """Nombre saisi à la française : « 250 000 », « 3,45 », « 1 200,50 € »."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(" ", "").replace(" ", "").replace(" ", "").replace("€", "").replace("%", "")
            value = value.replace(",", ".")
        return super().to_python(value)


def montant(label, aide="", requis=True, mini=0, maxi=None, decimales=2, **kw):
    attrs = {"inputmode": "decimal", "autocomplete": "off"}
    attrs.update(kw.pop("attrs", {}))
    return MontantField(label=label, help_text=aide, required=requis, min_value=Decimal(mini),
                        max_value=Decimal(maxi) if maxi is not None else None, decimal_places=decimales,
                        widget=forms.TextInput(attrs=attrs), **kw)


class FraisNotaireForm(forms.Form):
    prix = montant("Prix d'achat (€)", "Prix net vendeur. Dans le neuf : prix TVA comprise.", mini=1000, maxi=100_000_000,
                   attrs={"placeholder": "250 000"})
    type_bien = forms.ChoiceField(label="Type de bien", initial=notaire.ANCIEN, widget=forms.RadioSelect, choices=[
        (notaire.ANCIEN, "Ancien"), (notaire.NEUF, "Neuf (VEFA ou moins de 5 ans, vendu avec TVA)")])
    departement = forms.ChoiceField(label="Département du bien", initial="69",
                                    choices=[(c, f"{c} – {n}") for c, n in notaire.DEPARTEMENTS])
    primo_accedant = forms.BooleanField(label="Premier achat de ma résidence principale (primo-accédant)", required=False,
                                        help_text="Ancien uniquement : la hausse 2025 des droits ne s'applique pas.")
    mobilier = montant("Mobilier inclus dans le prix (€)", "Meubles listés dans l'acte : ils ne sont pas taxés.",
                       requis=False, attrs={"placeholder": "0"})
    formalites = montant("Formalités et débours (€)", "Estimation : environ 1 000 à 1 500 €.", requis=False,
                         initial=notaire.FORMALITES_PAR_DEFAUT, maxi=100_000)

    def clean(self):
        donnees = super().clean()
        prix, mobilier = donnees.get("prix"), donnees.get("mobilier")
        if prix and mobilier and mobilier >= prix:
            self.add_error("mobilier", "Le mobilier doit être inférieur au prix d'achat.")
        if donnees.get("formalites") is None:
            donnees["formalites"] = notaire.FORMALITES_PAR_DEFAUT
        return donnees


class PretForm(forms.Form):
    capital = montant("Montant emprunté (€)", mini=1000, maxi=100_000_000, attrs={"placeholder": "200 000"})
    taux = montant("Taux nominal annuel (%)", "Hors assurance.", mini=0, maxi=20, decimales=3, attrs={"placeholder": "3,40"})
    duree_annees = forms.IntegerField(label="Durée (années)", min_value=1, max_value=35, initial=20,
                                      widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    taux_assurance = montant("Taux d'assurance annuel (%)", "Par exemple 0,30 %. Laissez vide sans assurance.", requis=False,
                             maxi=5, decimales=3, attrs={"placeholder": "0,30"})
    mode_assurance = forms.ChoiceField(label="Assurance calculée sur", initial=pret.ASSURANCE_INITIAL, widget=forms.RadioSelect,
                                       choices=[(pret.ASSURANCE_INITIAL, "le capital initial (montant fixe)"),
                                                (pret.ASSURANCE_RESTANT, "le capital restant dû (dégressive)")])
    frais = montant("Frais de dossier et de garantie (€)", "Caution ou hypothèque, frais de la banque.", requis=False,
                    attrs={"placeholder": "0"})
    revenus = montant("Revenus nets mensuels du foyer (€)", "Facultatif : pour calculer votre taux d'endettement.",
                      requis=False, attrs={"placeholder": "4 000"})
    autres_credits = montant("Autres crédits en cours (€ / mois)", requis=False, attrs={"placeholder": "0"})

    def clean(self):
        donnees = super().clean()
        frais, capital = donnees.get("frais"), donnees.get("capital")
        if frais and capital and frais >= capital:
            self.add_error("frais", "Les frais doivent être inférieurs au montant emprunté.")
        return donnees


# ---------------------------------------------------------------------------------------------------------------------
# Module investisseur : formulaire à nombre de lots variable, lu à la main (lots et associés en lignes numérotées)
# ---------------------------------------------------------------------------------------------------------------------
from .calculs import investissement as inv  # noqa: E402


def nombre(valeur, defaut=0.0):
    """« 190 000 », « 4,2 », « 7 % » → float ; vide ou illisible → défaut."""
    if valeur is None:
        return defaut
    texte = str(valeur)
    for c in (" ", " ", " ", "€", "%"):
        texte = texte.replace(c, "")
    texte = texte.replace(",", ".")
    try:
        return float(texte) if texte else defaut
    except ValueError:
        return defaut


def entier(valeur, defaut=0, mini=0, maxi=10_000):
    return max(mini, min(maxi, int(round(nombre(valeur, defaut)))))


# Champs des paramètres, par bloc : (nom, libellé, aide, genre) ; genre : montant, pct, entier, texte, choix
BLOCS_INVEST = [
    ("achat", "L'achat", "prix et frais", [
        ("prix_affiche", "Prix affiché (€)", "Prix de l'annonce", "montant"),
        ("prix", "Prix proposé (€)", "", "montant"),
        ("frais_agence", "Frais d'agence (€)", "S'ils sont à votre charge", "montant"),
        ("frais_ingenierie", "Frais d'ingénierie, chasseur (€)", "", "montant"),
        ("departement", "Département", "Pour les droits de mutation", "choix"),
        ("frais_bancaires_pct", "Frais bancaires, courtier (% du prix)", "", "pct"),
        ("garantie_pct", "Garantie : hypothèque ou caution (% du prix)", "", "pct"),
    ]),
    ("financement", "Le financement", "apport et prêt", [
        ("apport_complement", "Apport complémentaire (€)", "Ajouté à : notaire + frais bancaires + garantie + meubles", "montant"),
        ("duree", "Durée du prêt (ans)", "", "entier"),
        ("taux", "Taux du prêt (%)", "Hors assurance", "pct"),
        ("taux_assurance", "Taux d'assurance (%)", "Sur le capital emprunté", "pct"),
    ]),
    ("charges", "Les charges annuelles", "", [
        ("taxe_fonciere", "Taxe foncière (€ / an)", "", "montant"),
        ("pno", "Assurance propriétaire PNO (€ / an)", "", "montant"),
        ("gli_pct", "Assurance loyers impayés (% des loyers)", "", "pct"),
        ("gestion_pct", "Gestion par une agence (% des loyers)", "0 si vous gérez vous-même", "pct"),
        ("comptable", "Comptable (€ / an)", "", "montant"),
        ("charges_non_recup", "Charges non récupérables (€ / an)", "Copropriété, parties communes…", "montant"),
        ("frais_banque", "Frais de banque (€ / an)", "Compte de la SCI…", "montant"),
        ("entretien", "Entretien chaudière (€ / an)", "", "montant"),
        ("mise_en_location", "Mise en location (€ / an)", "Annonces, honoraires", "montant"),
        ("crl", "Contribution sur les revenus locatifs (€ / an)", "", "montant"),
        ("autres_charges", "Autres charges (€ / an)", "", "montant"),
        ("vacance", "Vacance locative (%)", "Part de l'année sans loyer", "pct"),
    ]),
    ("fiscalite", "La fiscalité", "votre foyer et la SCI", [
        ("revenu_imposable", "Revenu imposable du foyer (€)", "Celui de l'avis d'impôt, sans cet investissement", "montant"),
        ("situation", "Situation", "", "choix"),
        ("enfants", "Enfants à charge", "", "entier"),
        ("distribution_pct", "SCI à l'IS : bénéfice distribué (%)", "Le reste reste dans la société", "pct"),
    ]),
    ("jeanbrun", "Dispositif Jeanbrun", "location nue à loyer plafonné", [
        ("jeanbrun_etat", "Logement", "Ancien : travaux d'au moins 30 % du prix", "choix"),
        ("jeanbrun_niveau", "Niveau de loyer", "Plus il est bas, plus l'amortissement est fort", "choix"),
        ("jeanbrun_zone", "Zone du logement", "A bis, A, B1, B2 ou C", "choix"),
        ("jeanbrun_plafond", "Plafond de loyer de la commune (€/m²)", "0 : valeur indicative de la zone A ou B1", "montant"),
        ("jeanbrun_autres", "Déjà amorti par d'autres logements Jeanbrun (€ / an)", "Le plafond annuel est commun au foyer", "montant"),
    ]),
    ("avance", "Réglages avancés", "amortissements et taux", [
        ("part_terrain", "Part du terrain, non amortissable (%)", "", "pct"),
        ("duree_travaux", "Amortissement des travaux (ans)", "", "entier"),
        ("duree_meubles", "Amortissement des meubles (ans)", "", "entier"),
        ("duree_frais", "Amortissement des frais d'achat (ans)", "", "entier"),
        ("taux_ps", "Prélèvements sociaux (%)", "Sur les revenus locatifs", "pct"),
        ("taux_pfu", "Flat tax sur les dividendes (%)", "", "pct"),
    ]),
]
CHOIX_INVEST = {
    "departement": [(c, f"{c} – {n}") for c, n in inv.notaire.DEPARTEMENTS],
    "situation": inv.SITUATIONS,
    "jeanbrun_etat": [("ancien", "Ancien avec travaux"), ("neuf", "Neuf ou VEFA")],
    "jeanbrun_niveau": inv.JEANBRUN_NIVEAUX,
    "jeanbrun_zone": inv.JEANBRUN_ZONES,
}


def departement_de(adresse):
    """Département déduit du code postal de l'adresse (« 69600 Oullins » → « 69 »), ou "" ."""
    import re

    m = re.search(r"\b(\d{5})\b", adresse or "")
    if not m:
        return ""
    cp = m.group(1)
    if cp.startswith("97"):
        return cp[:3]
    if cp.startswith("20"):
        return "2A" if int(cp) < 20200 else "2B"
    return cp[:2]


MODES_VALIDES = {m for m, _ in inv.MODES_TRAVAUX}


def lire_chiffrage(texte):
    """Chiffrage détaillé envoyé par la page (JSON) → dict propre, ou None (pas de détail / illisible)."""
    import json

    if not texte:
        return None
    try:
        brut = json.loads(texte)
    except (TypeError, ValueError):
        return None
    if not isinstance(brut, dict) or not isinstance(brut.get("lignes"), list):
        return None
    lignes = []
    for l in brut["lignes"][:40]:
        if not isinstance(l, dict):
            continue
        mode = l.get("mode") if l.get("mode") in MODES_VALIDES else "forfait"
        qte = l.get("quantite")
        piece = l.get("piece")
        lignes.append({
            "poste": str(l.get("poste") or "Autre")[:80], "mode": mode, "unite": str(l.get("unite") or "")[:20],
            "prix": max(0.0, nombre(l.get("prix"))), "quantite": None if qte in (None, "") else max(0.0, nombre(qte)),
            "devis": max(0.0, nombre(l.get("devis"))), "entreprise": str(l.get("entreprise") or "")[:120],
            "agrandissement": bool(l.get("agrandissement")),
            "piece": piece if isinstance(piece, int) else None, "piece_nom": str(l.get("piece_nom") or "")[:120],
            "piece_url": url if (url := str(l.get("piece_url") or "")).startswith("/prive/devis-travaux/") else "",
        })
    return {"imprevus": min(100.0, max(0.0, nombre(brut.get("imprevus"), inv.IMPREVUS_PAR_DEFAUT))), "lignes": lignes}


def lire_lots(data):
    n = entier(data.get("nb_lots"), 1, 1, inv.MAX_LOTS)
    lots = []
    for i in range(n):
        g = lambda k, d="": data.get(f"lot-{i}-{k}", d)
        type_ = g("type", "t2")
        lots.append(inv.Lot(
            type=type_ if type_ in inv.NOMS_TYPES else "t2", surface=nombre(g("surface")), etage=g("etage", "RDC")[:20],
            meuble=g("meuble", "") in ("1", "on", "true"), loue=g("loue", "") in ("1", "on", "true"),
            loyer_actuel=nombre(g("loyer_actuel")), loyer_cible=nombre(g("loyer_cible")), charges=nombre(g("charges")),
            travaux=nombre(g("travaux")), meubles=nombre(g("meubles")), fin_bail=g("fin_bail")[:10], note=g("note")[:200],
            travaux_detail=lire_chiffrage(g("travaux_detail"))))
    return lots


def lire_dpe(brut, nb_lots):
    """DPE saisis (champ caché JSON) : numéro, lot concerné, classe annoncée et dernier résultat de la vérification."""
    from patrimoine import dpe

    try:
        lignes = json.loads(brut) if isinstance(brut, str) else (brut or [])
    except ValueError:
        return []
    res = []
    for l in lignes[:inv.MAX_LOTS + 1] if isinstance(lignes, list) else []:
        if not isinstance(l, dict):
            continue
        numero = dpe.normaliser_numero(l.get("numero"))[:13]
        if not numero:
            continue
        lot = entier(l.get("lot"), 0, 0, nb_lots)
        classe = str(l.get("classe_annoncee") or "").upper()[:1]
        v = l.get("verifie") if isinstance(l.get("verifie"), dict) else {}
        verifie = {k: str(v.get(k) or "")[:20] for k in ("classe", "ges", "date_etablissement", "fin_validite", "statut")}
        verifie["surface"] = nombre(v.get("surface")) or None
        res.append({"numero": numero, "lot": lot, "classe_annoncee": classe if classe in dpe.CLASSES else "",
                    "verifie": verifie if verifie.get("statut") in ("vert", "orange", "rouge") else {}})
    return res


def lire_parametres(data):
    """Paramètres d'une simulation depuis un formulaire envoyé (POST)."""
    defaut = inv.Parametres()
    p = inv.Parametres(nom=(data.get("nom") or "").strip()[:200], adresse=(data.get("adresse") or "").strip()[:255],
                       lien_annonce=(data.get("lien_annonce") or "").strip()[:500], lots=lire_lots(data))
    for _, _, _, champs in BLOCS_INVEST:
        for nom, _, _, genre in champs:
            brut = data.get(nom)
            if genre in ("montant", "pct"):
                setattr(p, nom, max(0.0, nombre(brut, getattr(defaut, nom))))
            elif genre == "entier":
                setattr(p, nom, entier(brut, getattr(defaut, nom), 0, 60))
            elif genre == "choix":
                valides = [c for c, _ in CHOIX_INVEST[nom]]
                setattr(p, nom, brut if brut in valides else getattr(defaut, nom))
    if not data.get("departement"):
        p.departement = departement_de(p.adresse) or defaut.departement
    p.scenario = "actuel" if data.get("scenario") == "actuel" else "cible"
    p.travaux_communs = lire_chiffrage(data.get("travaux_communs"))
    p.niveau_renovation = entier(data.get("niveau_renovation"), 1, 0, 2)
    p.regime = (data.get("regime") or "")[:20]
    p.dpe = lire_dpe(data.get("dpe"), len(p.lots))
    p.associes = []
    for i in range(inv.MAX_ASSOCIES):
        parts = nombre(data.get(f"assoc-{i}-parts"))
        if i == 0 and not (data.get("assoc-0-revenu") or "").strip():
            # associé 1 sans revenu saisi : c'est le foyer décrit plus haut
            if parts > 0:
                p.associes.append(inv.Associe(min(100.0, parts), p.revenu_imposable, p.situation, p.enfants))
            continue
        if parts > 0:
            p.associes.append(inv.Associe(parts=min(100.0, parts), revenu_imposable=nombre(data.get(f"assoc-{i}-revenu")),
                                          situation=data.get(f"assoc-{i}-situation") if data.get(f"assoc-{i}-situation") in dict(inv.SITUATIONS) else "couple",
                                          enfants=entier(data.get(f"assoc-{i}-enfants"), 0, 0, 20)))
    return p
