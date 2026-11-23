/* Galerie des biens : déplacer les colonnes propriétaires (glisser l'en-tête, ou flèches ‹ ›) et les biens dans leur
   colonne (glisser la carte, ou flèches) ; l'ordre est enregistré. */
(function () {
  var galerie = document.querySelector(".galerie-biens[data-ordre-url]");
  if (!galerie) return;

  // l'en-tête du propriétaire se fige juste sous la barre du haut (elle-même figée) : on mesure sa hauteur
  function caler() {
    var barre = document.getElementById("header");
    var haut = barre && getComputedStyle(barre).position === "sticky" ? barre.offsetHeight : 0;
    document.documentElement.style.setProperty("--haut-barre", haut + "px");
    document.documentElement.style.setProperty("--haut-barre-tel", haut + "px");
  }
  caler();
  window.addEventListener("resize", caler);

  function jeton() {
    var champ = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (champ) return champ.value;
    var m = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function colonnes() { return Array.prototype.slice.call(galerie.querySelectorAll(".bloc-proprio")); }

  function poster(quoi, ids) {
    var corps = new URLSearchParams();
    corps.append("quoi", quoi);
    corps.append("ids", ids.join(","));
    fetch(galerie.dataset.ordreUrl, { method: "POST", body: corps, credentials: "same-origin",
      headers: { "X-CSRFToken": jeton() } }).catch(function () {});
  }

  function enregistrer() { poster("proprietaire", colonnes().map(function (c) { return c.dataset.proprio; })); }

  function enregistrerBiens(col) {
    poster("bien", Array.prototype.map.call(col.querySelectorAll(".place-carte"), function (p) { return p.dataset.bien; }));
  }

  var deplacee = null, carte = null;
  colonnes().forEach(function (col) {
    var entete = col.querySelector("header");
    entete.addEventListener("dragstart", function (e) {
      deplacee = col;
      col.classList.add("en-deplacement");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", col.dataset.proprio);
    });
    entete.addEventListener("dragend", function () {
      col.classList.remove("en-deplacement");
      colonnes().forEach(function (c) { c.classList.remove("cible"); });
      deplacee = null;
    });
    col.addEventListener("dragover", function (e) {
      if (!deplacee || deplacee === col || carte) return;
      e.preventDefault();
      colonnes().forEach(function (c) { c.classList.toggle("cible", c === col); });
    });
    col.addEventListener("drop", function (e) {
      if (!deplacee || deplacee === col || carte) return;
      e.preventDefault();
      var liste = colonnes();
      // déposée sur une colonne plus loin : elle se place après ; plus tôt : avant
      if (liste.indexOf(deplacee) < liste.indexOf(col)) col.after(deplacee); else col.before(deplacee);
      col.classList.remove("cible");
      enregistrer();
    });
    col.querySelector(".col-avant").addEventListener("click", function (e) {
      e.preventDefault();
      var prec = col.previousElementSibling;
      if (prec && prec.classList.contains("bloc-proprio")) { prec.before(col); enregistrer(); }
    });
    col.querySelector(".col-apres").addEventListener("click", function (e) {
      e.preventDefault();
      var suiv = col.nextElementSibling;
      if (suiv && suiv.classList.contains("bloc-proprio")) { suiv.after(col); enregistrer(); }
    });
  });

  // biens : glisser une carte au-dessus d'une autre de la même colonne
  Array.prototype.forEach.call(galerie.querySelectorAll(".place-carte"), function (place) {
    var col = place.closest(".bloc-proprio");
    var lien = place.querySelector("a.carte-bien");
    lien.addEventListener("dragstart", function (e) {
      carte = place;
      place.classList.add("en-deplacement");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", place.dataset.bien);
    });
    lien.addEventListener("dragend", function () {
      place.classList.remove("en-deplacement");
      Array.prototype.forEach.call(galerie.querySelectorAll(".place-carte.cible"), function (p) { p.classList.remove("cible"); });
      carte = null;
    });
    place.addEventListener("dragover", function (e) {
      if (!carte || carte === place || carte.closest(".bloc-proprio") !== col) return;
      e.preventDefault();
      e.stopPropagation();
      Array.prototype.forEach.call(col.querySelectorAll(".place-carte"), function (p) { p.classList.toggle("cible", p === place); });
    });
    place.addEventListener("drop", function (e) {
      if (!carte || carte === place || carte.closest(".bloc-proprio") !== col) return;
      e.preventDefault();
      e.stopPropagation();
      var liste = Array.prototype.slice.call(col.querySelectorAll(".place-carte"));
      if (liste.indexOf(carte) < liste.indexOf(place)) place.after(carte); else place.before(carte);
      place.classList.remove("cible");
      enregistrerBiens(col);
    });
    place.querySelector(".bien-avant").addEventListener("click", function (e) {
      e.preventDefault();
      var prec = place.previousElementSibling;
      if (prec) { prec.before(place); enregistrerBiens(col); }
    });
    place.querySelector(".bien-apres").addEventListener("click", function (e) {
      e.preventDefault();
      var suiv = place.nextElementSibling;
      if (suiv) { suiv.after(place); enregistrerBiens(col); }
    });
  });
})();
