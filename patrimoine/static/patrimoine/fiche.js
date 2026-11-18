/* Fiches (bail, locataire, lot) : un onglet visible à la fois, menus d'actions, modifications non enregistrées. */
(function () {
  "use strict";

  function onglets() {
    var nav = document.querySelector(".onglets-fiche");
    if (!nav) return;
    var corps = document.querySelector(".fiche-corps");
    var donnees = document.getElementById("onglets-inlines");
    var sousListes = donnees ? JSON.parse(donnees.textContent) : {};
    Object.keys(sousListes).forEach(function (prefixe) {
      var groupe = document.getElementById(prefixe + "-group");
      if (groupe) groupe.classList.add("onglet", "onglet-" + sousListes[prefixe]);
    });
    var liens = Array.prototype.slice.call(nav.querySelectorAll("a[data-onglet]"));
    var codes = liens.map(function (a) { return a.dataset.onglet; });
    var cle = "onglet:" + location.pathname;

    function activer(code, memoriser) {
      if (codes.indexOf(code) < 0) code = codes[0];
      liens.forEach(function (a) { a.classList.toggle("actif", a.dataset.onglet === code); a.setAttribute("aria-current", a.dataset.onglet === code ? "page" : "false"); });
      corps.querySelectorAll(".onglet").forEach(function (el) { el.classList.toggle("visible", el.classList.contains("onglet-" + code)); });
      if (memoriser !== false) {
        try { sessionStorage.setItem(cle, code); } catch (e) { /* navigation privée */ }
      }
      var actif = nav.querySelector("a.actif");
      if (actif && actif.scrollIntoView && nav.scrollWidth > nav.clientWidth) actif.scrollIntoView({block: "nearest", inline: "nearest"});
    }

    function ongletDe(el) {
      var bloc = el && el.closest(".onglet");
      if (!bloc) return null;
      var classe = Array.prototype.find.call(bloc.classList, function (c) { return c.indexOf("onglet-") === 0; });
      return classe ? classe.slice(7) : null;
    }

    liens.forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        activer(a.dataset.onglet);
        history.replaceState(null, "", "#" + a.dataset.onglet);
      });
    });

    // onglet de départ : la première erreur, sinon l'adresse (#argent), sinon le dernier ouvert sur cette fiche
    var erreur = ongletDe(corps.querySelector(".errorlist, .errors"));
    var depart = erreur || location.hash.slice(1);
    if (codes.indexOf(depart) < 0) {
      try { depart = sessionStorage.getItem(cle); } catch (e) { depart = null; }
    }
    activer(depart, false);
    document.body.classList.add("onglets-prets");
    window.addEventListener("hashchange", function () { if (codes.indexOf(location.hash.slice(1)) >= 0) activer(location.hash.slice(1)); });
    // un lien interne vers un onglet (#argent) depuis l'encart « En bref »
    document.querySelectorAll('.fiche-bref a[href^="#"]').forEach(function (a) {
      a.addEventListener("click", function (e) {
        var code = a.getAttribute("href").slice(1);
        if (codes.indexOf(code) >= 0) { e.preventDefault(); activer(code); history.replaceState(null, "", "#" + code); window.scrollTo({top: 0, behavior: "smooth"}); }
      });
    });
  }

  function menus() {
    var menus = document.querySelectorAll(".barre-actions details.menu-action");
    menus.forEach(function (m) {
      m.addEventListener("toggle", function () {
        if (m.open) menus.forEach(function (autre) { if (autre !== m) autre.open = false; });
      });
    });
    document.addEventListener("click", function (e) {
      menus.forEach(function (m) { if (m.open && !m.contains(e.target)) m.open = false; });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") menus.forEach(function (m) { m.open = false; });
    });
  }

  function modifications() {
    var barre = document.querySelector(".barre-enregistrer");
    var form = barre && barre.closest("form");
    if (!form) return;
    var etat = barre.querySelector(".etat-modif");
    function modifie() {
      if (barre.classList.contains("modifiee")) return;
      barre.classList.add("modifiee");
      if (etat) etat.textContent = "Modifications non enregistrées";
    }
    form.addEventListener("input", modifie);
    form.addEventListener("change", modifie);
    form.addEventListener("submit", function () { barre.classList.remove("modifiee"); window.onbeforeunload = null; });
    window.addEventListener("beforeunload", function (e) {
      if (barre.classList.contains("modifiee")) { e.preventDefault(); e.returnValue = ""; }
    });
  }

  function demarrer() { onglets(); menus(); modifications(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
