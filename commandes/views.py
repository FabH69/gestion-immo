from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import CommandeForm, JoueurForm, LigneForm
from .models import Commande, Joueur, Ligne


@login_required
def accueil(request):
    commandes = Commande.objects.prefetch_related("lignes")
    en_cours = [c for c in commandes if c.statut != Commande.Statut.TERMINEE]
    terminees = [c for c in commandes if c.statut == Commande.Statut.TERMINEE]
    return render(request, "commandes/accueil.html", {
        "en_cours": en_cours,
        "terminees": terminees,
        "commission_totale": sum(c.commission for c in commandes),
    })


# ---------- Joueurs ----------

@login_required
def joueurs(request):
    return render(request, "commandes/joueurs.html", {"joueurs": Joueur.objects.all()})


@login_required
def joueur_form(request, pk=None):
    joueur = get_object_or_404(Joueur, pk=pk) if pk else None
    form = JoueurForm(request.POST or None, instance=joueur)
    if form.is_valid():
        joueur = form.save()
        messages.success(request, f"Joueur « {joueur} » enregistré.")
        suivant = request.GET.get("next")
        if suivant and suivant.startswith("/"):
            return redirect(suivant)
        return redirect("balles:joueurs")
    return render(request, "commandes/form.html", {
        "form": form,
        "titre": f"Modifier {joueur}" if joueur else "Nouveau joueur",
        "retour": "balles:joueurs",
    })


# ---------- Commandes ----------

@login_required
def commande_form(request, pk=None):
    commande = get_object_or_404(Commande, pk=pk) if pk else None
    form = CommandeForm(request.POST or None, instance=commande)
    if form.is_valid():
        commande = form.save()
        messages.success(request, "Commande enregistrée.")
        return redirect(commande)
    return render(request, "commandes/form.html", {
        "form": form,
        "titre": f"Modifier {commande}" if commande else "Nouvelle commande",
        "retour": "balles:accueil",
    })


@login_required
def commande_detail(request, pk):
    commande = get_object_or_404(Commande, pk=pk)
    form = LigneForm(request.POST or None, commande=commande)
    if request.method == "POST" and form.is_valid():
        ligne = form.save(commit=False)
        ligne.commande = commande
        ligne.save()
        messages.success(request, f"{ligne.joueur} prend {ligne.nb_boites} boîte(s).")
        return redirect(commande)
    lignes = commande.lignes.select_related("joueur")
    return render(request, "commandes/commande_detail.html", {
        "commande": commande,
        "lignes": lignes,
        "form": form,
        "nb_payes": sum(1 for l in lignes if l.paye),
        "nb_remis": sum(1 for l in lignes if l.remis),
        "statuts": Commande.Statut.choices,
    })


@login_required
@require_POST
def commande_statut(request, pk):
    commande = get_object_or_404(Commande, pk=pk)
    statut = request.POST.get("statut")
    if statut in Commande.Statut.values:
        commande.statut = statut
        if statut == Commande.Statut.RECUE and not commande.date_reception:
            commande.date_reception = timezone.localdate()
        commande.save()
        messages.success(request, f"Statut : {commande.get_statut_display()}.")
        if statut == Commande.Statut.RECUE:
            return redirect("balles:commande_messages", pk=pk)
    return redirect(commande)


@login_required
@require_POST
def ligne_action(request, pk):
    ligne = get_object_or_404(Ligne, pk=pk)
    action = request.POST.get("action")
    if action == "paye":
        ligne.paye = not ligne.paye
        ligne.save()
    elif action == "remis":
        ligne.remis = not ligne.remis
        ligne.save()
    elif action == "prevenu":
        ligne.prevenu_le = None if ligne.prevenu_le else timezone.now()
        ligne.save()
    elif action == "boites":
        try:
            nb = int(request.POST.get("nb_boites", ""))
        except ValueError:
            nb = 0
        if nb >= 1:
            ligne.nb_boites = nb
            ligne.save()
    elif action == "supprimer":
        ligne.delete()
        messages.info(request, f"{ligne.joueur} retiré de la commande.")
    suivant = request.POST.get("next")
    if suivant and suivant.startswith("/"):
        return redirect(suivant)
    return redirect(ligne.commande)


@login_required
def commande_messages(request, pk):
    commande = get_object_or_404(Commande, pk=pk)
    if request.method == "POST" and request.POST.get("action") == "tous_prevenus":
        commande.lignes.filter(prevenu_le__isnull=True).update(prevenu_le=timezone.now())
        messages.success(request, "Tous les joueurs sont marqués comme prévenus.")
        return redirect("balles:commande_messages", pk=pk)

    envois = []
    for ligne in commande.lignes.select_related("joueur"):
        texte = ligne.message()
        j = ligne.joueur
        envois.append({
            "ligne": ligne,
            "texte": texte,
            "whatsapp": f"https://wa.me/{j.telephone_international}?text={quote(texte)}" if j.telephone else "",
            "sms": f"sms:{j.telephone.replace(' ', '')}?&body={quote(texte)}" if j.telephone else "",
            "email": (
                f"mailto:{j.email}?subject={quote('Balles de tennis : ' + commande.nom)}&body={quote(texte)}"
                if j.email else ""
            ),
        })

    recap = [f"🎾 Les balles de la commande « {commande.nom} » sont arrivées !"]
    if commande.lieu_remise:
        recap.append(f"Remise : {commande.lieu_remise}")
    recap.append("")
    for e in envois:
        l = e["ligne"]
        recap.append(f"- {l.joueur} : {l.nb_boites} boîte(s), {l.montant:.2f} €".replace(".", ","))
    message_groupe = "\n".join(recap)

    return render(request, "commandes/messages.html", {
        "commande": commande,
        "envois": envois,
        "message_groupe": message_groupe,
        "whatsapp_groupe": f"https://wa.me/?text={quote(message_groupe)}",
    })
