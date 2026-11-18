// Listes : barre d'actions visible seulement quand une ligne est cochée, une action = un bouton ; tri à une seule flèche.
// initListe est rappelée quand la liste est rechargée par la recherche pendant la frappe (recherche_liste.js).
window.initListe = function () {
  var form = document.getElementById("changelist-form");
  if (form) {
    form.querySelectorAll(".actions").forEach(function (barre) {
      var choix = barre.querySelector("select[name=action]"), envoyer = barre.querySelector("button[name=index]");
      if (!choix || !envoyer) return;
      var boutons = document.createElement("span");
      boutons.className = "actions-boutons";
      Array.prototype.forEach.call(choix.options, function (option) {
        if (!option.value) return;
        var b = document.createElement("button");
        b.type = "button";
        b.className = "button" + (option.value === "delete_selected" ? " danger" : "");
        b.textContent = option.textContent.replace(/\s+/g, " ").trim();
        b.addEventListener("click", function () {
          if (option.value === "delete_selected" && window.confirmerSuppression && window.HTMLDialogElement) {
            // petite fenêtre au lieu de la page de confirmation ; « post » fait supprimer directement
            var n = form.querySelectorAll("input.action-select:checked").length;
            var tout = form.querySelector("input[name=select_across]");
            var texte = tout && tout.value === "1" ? "tous les éléments de la liste" : n + " élément" + (n > 1 ? "s" : "") + " sélectionné" + (n > 1 ? "s" : "");
            window.confirmerSuppression("Supprimer définitivement " + texte + " ? Cette action est irréversible.", function () {
              choix.value = option.value;
              var post = form.querySelector("input[name=post]") || form.appendChild(Object.assign(document.createElement("input"), {type: "hidden", name: "post"}));
              post.value = "yes";
              if (form.requestSubmit) form.requestSubmit(envoyer); else envoyer.click();
            });
            return;
          }
          choix.value = option.value;
          // factures : les saisies du tableau sont enregistrées avant l'action, pas de message « modifications non sauvegardées »
          if (document.body.classList.contains("model-piececomptable") && form.requestSubmit) form.requestSubmit(envoyer);
          else envoyer.click();
        });
        boutons.appendChild(b);
      });
      // la suppression en dernier, la première autre action en bouton plein
      var suppr = boutons.querySelector(".danger");
      if (suppr) boutons.appendChild(suppr);
      var premier = boutons.querySelector(".button:not(.danger)");
      if (premier) premier.classList.add("principal");
      barre.classList.add("actions-en-boutons");
      barre.insertBefore(boutons, barre.firstChild);
    });
    var maj = function () { form.classList.toggle("selection-active", !!form.querySelector("input.action-select:checked")); };
    form.addEventListener("change", maj);
    form.addEventListener("click", function () { setTimeout(maj, 0); });
    maj();
  }
  document.querySelectorAll("#result_list thead th.sorted").forEach(function (th) {
    var rang = th.querySelector(".sortpriority");
    if (rang && rang.textContent.trim() !== "1") th.classList.add("tri-secondaire");
  });
};
document.addEventListener("DOMContentLoaded", window.initListe);
