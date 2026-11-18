/* Recherche globale (barre du haut, sur toutes les pages) : aperçu des résultats pendant la frappe.
   « / » pour y aller, ↑ ↓ pour choisir, Entrée pour ouvrir, Échap pour fermer ; « Tous les résultats » ouvre la page complète. */
(function () {
  "use strict";

  function demarrer() {
    var form = document.getElementById("recherche-globale");
    if (!form) return;
    var champ = form.querySelector("input[name=q]");
    var boite = document.createElement("div");
    boite.className = "apercu-recherche";
    boite.id = "apercu-recherche";
    boite.hidden = true;
    boite.setAttribute("role", "listbox");
    form.appendChild(boite);
    champ.setAttribute("autocomplete", "off");
    champ.setAttribute("aria-controls", "apercu-recherche");
    champ.setAttribute("aria-autocomplete", "list");
    var minuterie, derniere = "", choix = -1, numero = 0;

    function liens() { return Array.prototype.slice.call(boite.querySelectorAll("a.r")); }

    function selection(i) {
      var l = liens();
      choix = Math.max(-1, Math.min(i, l.length - 1));
      l.forEach(function (a, k) { a.classList.toggle("choisi", k === choix); a.setAttribute("aria-selected", k === choix ? "true" : "false"); });
      if (choix >= 0 && l[choix].scrollIntoView) l[choix].scrollIntoView({block: "nearest"});
    }

    function fermer() { boite.hidden = true; choix = -1; }

    function element(balise, classe, texte) {
      var e = document.createElement(balise);
      if (classe) e.className = classe;
      if (texte) e.textContent = texte;
      return e;
    }

    function afficher(donnees, q) {
      boite.innerHTML = "";
      if (!donnees.groupes.length) boite.appendChild(element("p", "vide", "Aucun résultat pour « " + q + " »"));
      donnees.groupes.forEach(function (g) {
        boite.appendChild(element("div", "g", g.titre));
        g.lignes.forEach(function (l) {
          var a = element("a", "r");
          a.href = l.url;
          a.setAttribute("role", "option");
          a.appendChild(element("b", "", l.titre));
          if (l.detail) a.appendChild(element("small", "", l.detail));
          boite.appendChild(a);
        });
      });
      var pied = element("div", "pied");
      pied.appendChild(element("span", "", "Entrée : ouvrir · Échap : fermer"));
      var tout = element("a", "", "Tous les résultats");
      tout.href = donnees.tout;
      pied.appendChild(tout);
      boite.appendChild(pied);
      if (window.innerWidth < 768) boite.style.top = (form.getBoundingClientRect().bottom + 6) + "px";
      boite.hidden = false;
      selection(donnees.groupes.length ? 0 : -1);
    }

    function chercher(forcer) {
      var q = champ.value.trim();
      if (q.length < 2) { fermer(); derniere = q; return; }
      if (q === derniere && !boite.hidden && !forcer) return;
      derniere = q;
      var n = ++numero;
      fetch(form.action + "?apercu=1&q=" + encodeURIComponent(q), {credentials: "same-origin"})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && n === numero && champ.value.trim() === q) afficher(d, q); })
        .catch(function () { /* hors ligne : la touche Entrée ouvre la page de résultats */ });
    }

    champ.addEventListener("input", function () { clearTimeout(minuterie); minuterie = setTimeout(chercher, 180); });
    champ.addEventListener("focus", function () { if (champ.value.trim().length >= 2) chercher(true); });
    champ.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (boite.hidden) chercher(true); else selection(choix + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); selection(choix - 1); }
      else if (e.key === "Escape") { fermer(); champ.blur(); }
      else if (e.key === "Enter" && !boite.hidden && choix >= 0) { e.preventDefault(); location.href = liens()[choix].href; }
    });
    document.addEventListener("click", function (e) { if (!form.contains(e.target)) fermer(); });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      var t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      champ.focus();
      champ.select();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
