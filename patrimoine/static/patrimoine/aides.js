/* Aides des champs en infobulle.
   Le texte d'aide affiché sous un champ (help_text) est remplacé par un petit « ? » collé au libellé.
   Le texte apparaît au survol, au focus clavier, ou au toucher sur téléphone (un 2e toucher, Échap
   ou un toucher ailleurs le referme). Sans JavaScript, l'aide reste affichée sous le champ comme avant.
   Les messages d'erreur ne sont pas concernés. */
(function () {
  "use strict";
  var SELECTEUR = ".form-row .help, .champ .aide, .champ-court .help, .champ-aide .help";
  var ouverte = null;  // bulle épinglée par un clic ou un toucher

  function libelleDe(aide) {
    // 1) aide Django : id « id_<champ>_helptext » → label[for=id_<champ>]
    if (aide.id && /_helptext$/.test(aide.id)) {
      var champ = aide.id.replace(/_helptext$/, "");
      var l = document.querySelector('label[for="' + champ + '"]');
      if (l) return l;
    }
    // 2) gabarits maison : le libellé du même bloc de champ
    var bloc = aide.closest(".fieldBox, .champ, .champ-court, .champ-aide, .form-row");
    if (!bloc) return null;
    var labels = bloc.querySelectorAll("label, .titre");
    return labels.length === 1 ? labels[0] : null;
  }

  function placer(bouton, bulle) {
    var r = bouton.getBoundingClientRect(), marge = 8;
    bulle.style.left = "0px"; bulle.style.top = "0px";
    var b = bulle.getBoundingClientRect();
    var gauche = Math.min(Math.max(marge, r.left + r.width / 2 - 24), window.innerWidth - b.width - marge);
    var haut = r.bottom + 6;
    if (haut + b.height > window.innerHeight - marge && r.top - b.height - 6 > marge) haut = r.top - b.height - 6;
    bulle.style.left = Math.max(marge, gauche) + "px";
    bulle.style.top = haut + "px";
  }

  function montrer(bouton, bulle) {
    bulle.classList.add("visible");
    bouton.setAttribute("aria-expanded", "true");
    placer(bouton, bulle);
  }
  function cacher(bouton, bulle) {
    bulle.classList.remove("visible");
    bouton.setAttribute("aria-expanded", "false");
  }

  function transformer(aide) {
    if (aide.dataset.infobulle || aide.classList.contains("timezonewarning")) return;
    if (!aide.textContent.trim()) return;
    var libelle = libelleDe(aide);
    if (!libelle) return;  // pas de libellé sûr : on laisse l'aide visible
    if (!aide.id) aide.id = "aide-" + Math.random().toString(36).slice(2, 9);
    aide.dataset.infobulle = "1";
    aide.classList.add("infobulle");
    aide.setAttribute("role", "tooltip");

    var bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "bouton-aide";
    bouton.textContent = "?";
    bouton.setAttribute("aria-label", "Aide");
    bouton.setAttribute("aria-describedby", aide.id);
    bouton.setAttribute("aria-expanded", "false");
    // le « ? » ne passe jamais seul à la ligne : il reste collé au dernier mot du libellé
    var dernier = libelle.lastChild;
    while (dernier && dernier.nodeType === 3 && !dernier.textContent.trim()) dernier = dernier.previousSibling;
    if (dernier && dernier.nodeType === 3) {
      var texte = dernier.textContent.replace(/\s+$/, ""), coupe = texte.search(/\S+$/);
      var colle = document.createElement("span");
      colle.className = "aide-insecable";
      colle.textContent = texte.slice(coupe);
      dernier.textContent = texte.slice(0, coupe);
      dernier.parentNode.insertBefore(colle, dernier.nextSibling);
      colle.appendChild(bouton);
    } else {
      libelle.appendChild(bouton);
    }
    libelle.classList.add("avec-aide");

    // le bouton est dans le <label> : ne pas cocher la case ni donner le focus au champ
    bouton.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      if (ouverte && ouverte.bulle === aide) { cacher(bouton, aide); ouverte = null; return; }
      if (ouverte) cacher(ouverte.bouton, ouverte.bulle);
      montrer(bouton, aide); ouverte = { bouton: bouton, bulle: aide };
    });
    bouton.addEventListener("mouseenter", function () { montrer(bouton, aide); });
    bouton.addEventListener("mouseleave", function () { if (!ouverte || ouverte.bulle !== aide) cacher(bouton, aide); });
    bouton.addEventListener("focus", function () { montrer(bouton, aide); });
    bouton.addEventListener("blur", function () { if (!ouverte || ouverte.bulle !== aide) cacher(bouton, aide); });
  }

  function tout() { document.querySelectorAll(SELECTEUR).forEach(transformer); }

  document.addEventListener("click", function (e) {
    if (ouverte && !ouverte.bulle.contains(e.target)) { cacher(ouverte.bouton, ouverte.bulle); ouverte = null; }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var actif = document.activeElement;
    if (actif && actif.classList && actif.classList.contains("bouton-aide")) {
      cacher(actif, document.getElementById(actif.getAttribute("aria-describedby")));
    }
    if (ouverte) { cacher(ouverte.bouton, ouverte.bulle); ouverte = null; }
  });
  window.addEventListener("scroll", function () {
    document.querySelectorAll(".infobulle.visible").forEach(function (b) {
      var bt = document.querySelector('.bouton-aide[aria-describedby="' + b.id + '"]');
      if (bt) placer(bt, b);
    });
  }, true);
  window.addEventListener("resize", function () { if (ouverte) placer(ouverte.bouton, ouverte.bulle); });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tout); else tout();
  // lignes ajoutées dynamiquement (« Ajouter un autre … » des formulaires en ligne)
  document.addEventListener("formset:added", tout);
})();
