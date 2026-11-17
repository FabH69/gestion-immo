/* Suppressions : une petite fenêtre « Confirmer / Annuler » à la place de la page de confirmation.
   - liens « Supprimer » d'une fiche (…/delete/) : la suppression part directement après confirmation ;
   - action « Supprimer les … sélectionnés » des listes : idem (voir listes.js).
   Si l'élément est relié à d'autres données protégées, l'appli affiche la page qui explique pourquoi. */
(function () {
  "use strict";

  function jeton() {
    var champ = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (champ) return champ.value;
    var m = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : "";
  }

  // fenêtre de confirmation ; appelle suite() si l'utilisateur confirme
  window.confirmerSuppression = function (message, suite) {
    var d = document.createElement("dialog");
    d.className = "fenetre-confirmation";
    d.innerHTML = '<p class="titre">Confirmer la suppression</p><p class="message"></p>' +
      '<div class="boutons"><button type="button" class="annuler">Annuler</button><button type="button" class="confirmer">Confirmer</button></div>';
    d.querySelector(".message").textContent = message;
    document.body.appendChild(d);
    function fermer() { d.close(); d.remove(); }
    d.querySelector(".annuler").addEventListener("click", fermer);
    d.addEventListener("cancel", function () { d.remove(); });
    d.addEventListener("click", function (e) { if (e.target === d) fermer(); });  // clic à côté = annuler
    d.querySelector(".confirmer").addEventListener("click", function () { fermer(); suite(); });
    if (d.showModal) d.showModal(); else if (window.confirm(message)) suite();
    d.querySelector(".annuler").focus();
  };

  document.addEventListener("click", function (e) {
    var lien = e.target.closest && e.target.closest("a[href]");
    if (!lien || e.ctrlKey || e.metaKey || e.shiftKey || !/\/delete\/(\?.*)?$/.test(lien.getAttribute("href"))) return;
    var t = jeton();
    if (!t || !window.HTMLDialogElement) return;  // à défaut : page de confirmation habituelle
    e.preventDefault();
    var nom = (document.querySelector("#content h1, .fiche-titre h1, h1") || {}).textContent || "";
    nom = nom.replace(/^\s*(Modifier|Afficher)\s+/i, "").trim();
    window.confirmerSuppression("Supprimer définitivement " + (nom ? "« " + nom + " »" : "cet élément") + " ? Cette action est irréversible.", function () {
      var f = document.createElement("form");
      f.method = "post";
      f.action = lien.href;
      [["csrfmiddlewaretoken", t], ["post", "yes"]].forEach(function (c) {
        var i = document.createElement("input");
        i.type = "hidden"; i.name = c[0]; i.value = c[1];
        f.appendChild(i);
      });
      document.body.appendChild(f);
      f.submit();
    });
  });
})();
