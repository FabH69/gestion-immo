"""Immobilisations d'un dossier (LMNP ou société) : registre, reprise de l'existant, décomposition du prix d'un bien,
tableau des amortissements en PDF. La fiche de chaque immobilisation est dans l'administration (ImmobilisationAdmin)."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .. import amortissements, dossiers
from ..declarations import annee_fiscale
from ..models import Immobilisation, ReportAmortissements
from .communs import _pdf

ZERO = Decimal("0.00")
Categorie = Immobilisation.Categorie


def _dossier(code):
    d = dossiers.trouver(code)
    if d is None or not dossiers.amortissable(d):
        raise Http404
    return d


def _annees():
    from .compta import _annees as annees

    return annees()


def _contexte(request, dossier, annee):
    from .. import lmnp

    lignes, total = amortissements.registre(dossier, annee)
    biens = list(dossiers.biens(dossier))
    url = reverse("immobilisations", args=[dossier["code"]])
    c = {"d": {**dossier, **dossiers.resume_liens(dossier, annee)}, "annee": annee, "lignes": lignes, "total": total,
         "biens_dossier": biens, "dossiers": [x for x in dossiers.tous() if dossiers.amortissable(x)],
         "douteuses": list(amortissements.a_verifier(biens, annee)), "seuil": amortissements.SEUIL,
         "retour": f"{url}?annee={annee}", "aujourdhui": timezone.localdate()}
    if dossier["formulaire"] == "2031":
        t = lmnp.bilan(annee)["total"]
        c["lmnp"] = t
        c["saisis_main"] = t["amortissements"] - t["dotations"]
        c["charges_douteuses"] = list(amortissements.lignes_alerte_charges_lmnp(lmnp.lots_lmnp(), annee))
    c["nb_douteuses"] = len(c["douteuses"]) + len(c.get("charges_douteuses", []))
    return c


@staff_member_required
def immobilisations(request, code):
    dossier = _dossier(code)
    annee = annee_fiscale(request)
    c = _contexte(request, dossier, annee)
    if request.GET.get("format") == "pdf":
        return _pdf(render_to_string("patrimoine/immobilisations_pdf.html", c), f"amortissements-{dossier['titre']}-{annee}")
    from django.utils.http import urlencode

    c.update({"annees": _annees(), "onglets": dossiers.onglets(dossier, "immobilisations", annee),
              "libelle_regime": dossier["titre"], "title": f"Immobilisations · {dossier['titre']}",
              "lien_ajouter": reverse("admin:patrimoine_immobilisation_add") + "?" + urlencode(
                  {"bien": c["biens_dossier"][0].pk if len(c["biens_dossier"]) == 1 else "", "next": c["retour"]})})
    return render(request, "patrimoine/immobilisations.html", {**admin.site.each_context(request), **c})


# --- reprise de l'existant -----------------------------------------------------------------------------------------------
def _nombre(texte):
    texte = re.sub(r"[\s€  ]", "", str(texte or "")).replace(",", ".")
    if not texte:
        return None
    try:
        return Decimal(texte)
    except InvalidOperation:
        return None


def _date(texte):
    texte = (texte or "").strip()
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texte, f).date()
        except ValueError:
            pass
    return None


def _categorie(texte):
    t = (texte or "").strip().lower()
    for code, libelle in Categorie.choices:
        if t and (t == code or t in libelle.lower() or libelle.lower().split(" (")[0].split(",")[0] in t):
            return code
    for mot, code in (("terrain", "terrain"), ("gros", "gros_oeuvre"), ("construction", "gros_oeuvre"), ("façade", "facade"),
                      ("toit", "facade"), ("install", "installations"), ("agenc", "agencements"), ("cuisine", "agencements"),
                      ("travaux", "travaux"), ("meuble", "mobilier"), ("mobil", "mobilier"), ("électro", "electromenager"),
                      ("electro", "electromenager"), ("informat", "informatique"), ("frais", "frais"), ("notaire", "frais")):
        if mot in t:
            return code
    return "autre"


def lire_collage(texte):
    """Lignes collées depuis Excel (tabulations ou points-virgules) : désignation, catégorie, mise en service, valeur,
    durée, amortissements cumulés. Renvoie (lignes lues, erreurs)."""
    lues, erreurs = [], []
    for n, brute in enumerate((texte or "").splitlines(), 1):
        if not brute.strip():
            continue
        cols = [c.strip() for c in (brute.split("\t") if "\t" in brute else brute.split(";"))]
        cols += [""] * (6 - len(cols))
        mes, valeur, duree, cumul = _date(cols[2]), _nombre(cols[3]), _nombre(re.sub(r"[^\d,.]", "", cols[4])), _nombre(cols[5])
        if not cols[0] or mes is None or valeur is None:
            if n == 1 and valeur is None:  # ligne d'en-têtes
                continue
            erreurs.append(f"Ligne {n} ignorée (désignation, date de mise en service et valeur obligatoires) : {brute[:60]}")
            continue
        categorie = _categorie(cols[1])
        lues.append({"designation": cols[0][:200], "categorie": categorie, "mise_en_service": mes, "valeur": valeur,
                     "duree": 0 if categorie == "terrain" else int(duree) if duree is not None else amortissements.duree_proposee(categorie),
                     "amortissements_anterieurs": cumul or ZERO})
    return lues, erreurs


class LigneRepriseForm(forms.Form):
    designation = forms.CharField(max_length=200, required=False)
    categorie = forms.ChoiceField(choices=Categorie.choices, initial="mobilier", required=False)
    mise_en_service = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    valeur = forms.DecimalField(max_digits=12, decimal_places=2, required=False)
    duree = forms.IntegerField(min_value=0, max_value=100, required=False)
    amortissements_anterieurs = forms.DecimalField(max_digits=12, decimal_places=2, required=False)

    def clean(self):
        d = super().clean()
        if d.get("designation") or d.get("valeur") is not None:
            for champ in ("designation", "mise_en_service", "valeur"):
                if not d.get(champ) and champ not in self.errors:
                    self.add_error(champ, "Obligatoire")
            if d.get("valeur") is not None and (d.get("amortissements_anterieurs") or ZERO) > d["valeur"]:
                self.add_error("amortissements_anterieurs", "Plus que la valeur d'origine")
        return d


LignesReprise = forms.formset_factory(LigneRepriseForm, extra=6)


def _controle(immo):
    """Cumul saisi (comptable) et cumul que l'appli aurait calculé au 31/12 de l'année précédant la reprise."""
    calcule = Immobilisation(valeur=immo.valeur, duree=immo.duree, mise_en_service=immo.mise_en_service)
    lignes = [l for l in amortissements.plan(calcule) if l["annee"] < immo.annee_reprise]
    attendu = lignes[-1]["cumul"] if lignes else ZERO
    return {"immo": immo, "saisi": immo.amortissements_anterieurs, "calcule": attendu, "ecart": immo.amortissements_anterieurs - attendu}


@staff_member_required
def reprise(request, code):
    dossier = _dossier(code)
    biens = list(dossiers.biens(dossier))
    annee_defaut = annee_fiscale(request)
    report = ReportAmortissements.objects.filter(dossier="2031").first() if dossier["formulaire"] == "2031" else None
    url = reverse("immobilisations", args=[code])
    if request.method == "POST":
        formset = LignesReprise(request.POST)
        bien = next((b for b in biens if str(b.pk) == request.POST.get("bien")), None)
        annee = int(request.POST["annee_reprise"]) if request.POST.get("annee_reprise", "").isdigit() else None
        lues, erreurs = lire_collage(request.POST.get("collage"))
        if bien is None or annee is None:
            messages.error(request, "Choisissez le bien et la première année tenue dans l'appli.")
        elif formset.is_valid():
            lignes = [{**f.cleaned_data, "categorie": f.cleaned_data.get("categorie") or "mobilier"}
                      for f in formset if f.cleaned_data.get("designation")] + lues
            for l in lignes:
                duree = 0 if l["categorie"] == "terrain" else (l.get("duree") if l.get("duree") is not None
                                                               else amortissements.duree_proposee(l["categorie"]))
                Immobilisation.objects.create(bien=bien, designation=l["designation"], categorie=l["categorie"],
                                              mise_en_service=l["mise_en_service"], valeur=l["valeur"], duree=duree,
                                              amortissements_anterieurs=l.get("amortissements_anterieurs") or ZERO,
                                              annee_reprise=annee if l["mise_en_service"].year < annee else None,
                                              origine=Immobilisation.Origine.REPRISE)
            if dossier["formulaire"] == "2031" and request.POST.get("report", "").strip():
                ReportAmortissements.objects.update_or_create(dossier="2031", defaults={
                    "annee": annee, "montant": _nombre(request.POST["report"]) or ZERO})
            for e in erreurs:
                messages.warning(request, e)
            messages.success(request, f"{len(lignes)} immobilisation{'s' if len(lignes) > 1 else ''} reprise{'s' if len(lignes) > 1 else ''} "
                                      f"sur {bien.nom}. Les dotations sont calculées à partir de {annee}.")
            return redirect(f"{reverse('reprise_immobilisations', args=[code])}?annee={annee}")
    else:
        formset = LignesReprise()
    reprises = Immobilisation.objects.filter(bien__in=biens, origine=Immobilisation.Origine.REPRISE, annee_reprise__isnull=False) \
        .select_related("bien")
    controles = [_controle(i) for i in reprises if i.duree]
    c = {"d": dossier, "biens_dossier": biens, "formset": formset, "annee": annee_defaut, "report": report,
         "controles": controles, "ecarts": [x for x in controles if x["ecart"]], "retour": f"{url}?annee={annee_defaut}",
         "title": f"Reprendre les immobilisations · {dossier['titre']}", "categories": Categorie.choices,
         "durees": amortissements.DUREES}
    return render(request, "patrimoine/immobilisations_reprise.html", {**admin.site.each_context(request), **c})


# --- décomposition du prix d'achat d'un bien ------------------------------------------------------------------------------
@staff_member_required
def decomposer(request, code):
    dossier = _dossier(code)
    biens = list(dossiers.biens(dossier))
    bien = next((b for b in biens if str(b.pk) == (request.POST.get("bien") or request.GET.get("bien"))), biens[0] if biens else None)
    url = reverse("immobilisations", args=[code])
    composants = [{"categorie": c, "libelle": Categorie(c).label, "part": p, "duree": d} for c, p, d in amortissements.DECOMPOSITION]
    existants = list(Immobilisation.objects.filter(bien=bien, origine=Immobilisation.Origine.DECOMPOSITION)) if bien else []
    if request.method == "POST" and bien:
        prix, frais, mobilier = (_nombre(request.POST.get(k)) or ZERO for k in ("prix", "frais", "mobilier"))
        mes = _date(request.POST.get("mise_en_service")) or bien.date_acquisition
        if request.POST.get("frais_mode") == "prix":
            prix += frais
        parts = [(_nombre(request.POST.get(f"part_{c['categorie']}")) or ZERO,
                  int(_nombre(request.POST.get(f"duree_{c['categorie']}")) or 0), c) for c in composants]
        if prix <= 0 or mes is None:
            messages.error(request, "Indiquez le prix d'achat et la date de mise en location.")
        elif sum(p for p, _, _ in parts) != 100:
            messages.error(request, f"Les parts font {sum(p for p, _, _ in parts)} % : elles doivent faire 100 %.")
        elif existants and not request.POST.get("remplacer"):
            messages.error(request, "Ce bien est déjà décomposé : cochez « Remplacer la décomposition existante » pour la refaire.")
        else:
            Immobilisation.objects.filter(pk__in=[i.pk for i in existants]).delete()
            reste, crees = prix, 0
            for n, (part, duree, c) in enumerate(parts):
                montant = reste if n == len(parts) - 1 else (prix * part / 100).quantize(Decimal("0.01"))
                reste -= montant
                if montant:
                    Immobilisation.objects.create(bien=bien, designation=f"{c['libelle'].split(' (')[0]} ({part:g} %)",
                                                  categorie=c["categorie"], mise_en_service=mes, date_achat=bien.date_acquisition,
                                                  valeur=montant, duree=0 if c["categorie"] == "terrain" else duree,
                                                  origine=Immobilisation.Origine.DECOMPOSITION)
                    crees += 1
            if mobilier:
                Immobilisation.objects.create(bien=bien, designation="Mobilier d'origine", categorie="mobilier", mise_en_service=mes,
                                              valeur=mobilier, duree=int(_nombre(request.POST.get("duree_mobilier")) or 7),
                                              origine=Immobilisation.Origine.DECOMPOSITION)
                crees += 1
            messages.success(request, f"{crees} immobilisations créées pour {bien.nom}. Les dotations sont calculées depuis le "
                                      f"{mes:%d/%m/%Y} ; si le comptable a amorti autrement, corrigez la fiche (amortissements cumulés).")
            return redirect(f"{url}?annee={annee_fiscale(request)}")
    c = {"d": dossier, "biens_dossier": biens, "bien": bien, "composants": composants, "existants": existants,
         "retour": f"{url}?annee={annee_fiscale(request)}", "title": f"Décomposer le prix d'achat · {bien.nom if bien else ''}"}
    return render(request, "patrimoine/immobilisations_decomposer.html", {**admin.site.each_context(request), **c})
