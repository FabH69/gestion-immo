/* Champs montant : on peut taper un calcul (« 120+238 », « =120+238 », « 3*45,50 ») ; il est calculé en sortant du champ.
   Les champs nombre à virgule deviennent des champs texte (le navigateur refusait le « + ») avec le clavier numérique. */
(function () {
  "use strict";

  // petit calculateur sans eval : + − × ÷ et parenthèses
  function calculer(texte) {
    var s = texte.replace(/^\s*=/, "").replace(/\s| | /g, "").replace(/,/g, ".")
      .replace(/[x×X]/g, "*").replace(/÷|:/g, "/").replace(/−/g, "-").replace(/€/g, "");
    if (!s || /[^0-9.+\-*/()]/.test(s)) return null;
    var i = 0;
    function nombre() {
      var m = /^\d*\.?\d+|^\d+\.?/.exec(s.slice(i));
      if (!m) throw 0;
      i += m[0].length;
      return parseFloat(m[0]);
    }
    function facteur() {
      if (s[i] === "-") { i++; return -facteur(); }
      if (s[i] === "+") { i++; return facteur(); }
      if (s[i] === "(") { i++; var v = somme(); if (s[i] !== ")") throw 0; i++; return v; }
      return nombre();
    }
    function produit() {
      var v = facteur();
      while (s[i] === "*" || s[i] === "/") { var op = s[i++], d = facteur(); v = op === "*" ? v * d : v / d; }
      return v;
    }
    function somme() {
      var v = produit();
      while (s[i] === "+" || s[i] === "-") { var op = s[i++], d = produit(); v = op === "+" ? v + d : v - d; }
      return v;
    }
    try {
      var v = somme();
      if (i !== s.length || !isFinite(v)) return null;
      return Math.round(v * 100) / 100;
    } catch (e) { return null; }
  }
  window.calculerMontant = calculer;

  function estMontant(champ) {
    if (!champ || champ.tagName !== "INPUT") return false;
    if (champ.type === "number") return /\./.test(champ.step || "") || champ.step === "any";
    return champ.type === "text" && (champ.dataset.calcul === "1" || /(^|[-_])montant/.test(champ.name || ""));
  }

  function preparer(champ) {
    if (champ.dataset.calcul === "1") return;
    if (champ.type === "number") {
      champ.type = "text";
      champ.removeAttribute("step");
    }
    champ.dataset.calcul = "1";
    champ.setAttribute("inputmode", "decimal");
    if (!champ.title) champ.title = "Vous pouvez taper un calcul, ex. 120+238";
  }

  function tout() { document.querySelectorAll("input[type=number], input[type=text]").forEach(function (c) { if (estMontant(c)) preparer(c); }); }

  document.addEventListener("focusin", function (e) { if (estMontant(e.target)) preparer(e.target); });
  document.addEventListener("change", function (e) {
    var champ = e.target;
    if (!champ || champ.dataset.calcul !== "1") return;
    var brut = champ.value.trim();
    if (!brut) return;
    var v = calculer(brut);
    if (v === null) { champ.setCustomValidity("Montant ou calcul illisible : « " + brut + " »"); champ.reportValidity(); return; }
    champ.setCustomValidity("");
    var texte = String(v);
    if (texte !== brut) {
      champ.value = texte;
      champ.dispatchEvent(new Event("input", {bubbles: true}));
    }
  }, true);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tout); else tout();
  document.addEventListener("liste-rechargee", tout);
})();
