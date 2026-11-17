/* Champ « Filtrer ces … » des listes : les résultats se mettent à jour pendant la frappe, sans appuyer sur Entrée.
   La liste est rechargée en arrière-plan puis remplacée sur place (le curseur reste dans le champ). */
(function () {
  "use strict";
  var ZONES = [".barre-liste", ".navigation-periode", "#changelist-form", "#changelist-search .small", ".bouton-filtres"];

  function relancerScripts(noeud) {
    noeud.querySelectorAll("script").forEach(function (ancien) {
      var s = document.createElement("script");
      Array.prototype.forEach.call(ancien.attributes, function (a) { s.setAttribute(a.name, a.value); });
      s.textContent = ancien.textContent;
      ancien.replaceWith(s);
    });
  }

  function demarrer() {
    var champ = document.getElementById("searchbar");
    var form = champ && champ.form;
    if (!champ || !form || form.id !== "changelist-search") return;
    champ.setAttribute("autocomplete", "off");
    var minuterie, numero = 0, derniere = champ.value.trim();

    function charger() {
      var q = champ.value.trim();
      if (q === derniere) return;
      derniere = q;
      var url = new URL(location.href);
      if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
      url.searchParams.delete("p");  // on repart de la première page
      var n = ++numero;
      document.body.classList.add("liste-en-cours");
      fetch(url.toString(), {credentials: "same-origin"})
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) {
          if (!html || n !== numero) return;
          var page = new DOMParser().parseFromString(html, "text/html");
          ZONES.forEach(function (sel) {
            var ancien = document.querySelector(sel), nouveau = page.querySelector(sel);
            if (ancien && nouveau) {
              var copie = document.importNode(nouveau, true);
              ancien.replaceWith(copie);
              relancerScripts(copie);
            } else if (ancien) {
              ancien.remove();
            }
          });
          history.replaceState(null, "", url.toString());
          var cases = document.querySelectorAll("tr input.action-select");
          if (cases.length && window.Actions) window.Actions(cases);
          if (window.initListe) window.initListe();
          document.dispatchEvent(new CustomEvent("liste-rechargee"));
        })
        .catch(function () { /* hors ligne : la touche Entrée lance toujours la recherche */ })
        .then(function () { if (n === numero) document.body.classList.remove("liste-en-cours"); });
    }

    champ.addEventListener("input", function () { clearTimeout(minuterie); minuterie = setTimeout(charger, 300); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
