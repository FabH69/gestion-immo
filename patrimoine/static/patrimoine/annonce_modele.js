// Annonce : quand on choisit le lot, son modèle d'annonce par défaut est sélectionné,
// et le dépôt de garantie est calculé : (loyer CC - charges) x 1 mois en location nue, x 2 en meublé.
// Le dépôt suit chaque correction du loyer ou des charges, sauf si on l'a tapé à la main sur la page.
// Les montants sont affichés avec 2 décimales.
document.addEventListener("DOMContentLoaded", function () {
  var lot = document.getElementById("id_lot"), modele = document.getElementById("id_modele");
  var loyer = document.getElementById("id_loyer_cc"), charges = document.getElementById("id_charges"),
      depot = document.getElementById("id_depot_garantie");
  if (!lot) return;
  var url = location.pathname.replace(/\/(add|\d+\/change)\/$/, "/") + "modele-du-lot/";
  var meuble = null, depotSaisi = false;
  // accepte aussi un calcul tapé dans le champ (« 600+10 »), comme les autres montants
  var nombre = function (champ) {
    var brut = String(champ && champ.value || "").trim();
    if (!brut) return NaN;
    var v = window.calculerMontant ? window.calculerMontant(brut) : parseFloat(brut.replace(/\s/g, "").replace(",", "."));
    return v === null ? NaN : v;
  };
  function deuxDecimales(champ) {
    var v = nombre(champ);
    if (champ && !isNaN(v)) champ.value = v.toFixed(2);
  }

  function calculerDepot() {
    if (!depot || !loyer || meuble === null || depotSaisi) return;
    var hc = (nombre(loyer) || 0) - (nombre(charges) || 0);
    if (hc <= 0) return;
    depot.value = (Math.round(hc * (meuble ? 2 : 1) * 100) / 100).toFixed(2);
  }

  function chargerLot(avecModele, puis) {
    if (!lot.value) return;
    fetch(url + "?lot=" + encodeURIComponent(lot.value), {credentials: "same-origin"})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        meuble = !!d.meuble;
        if (avecModele && modele) {
          modele.value = d.modele ? String(d.modele) : "";
          modele.dispatchEvent(new Event("change"));
          var note = document.getElementById("note-modele");
          if (!note) { note = document.createElement("div"); note.id = "note-modele"; note.className = "help"; modele.parentNode.appendChild(note); }
          note.textContent = d.modele ? "Modèle par défaut du lot : " + d.nom : "Aucun modèle d'annonce rattaché à ce lot : le texte sera généré d'après la fiche du lot.";
        }
        if (puis) puis();
      });
  }

  // au chargement : dépôt calculé seulement s'il est vide (sinon on garde le montant enregistré ou proposé)
  [loyer, charges, depot].forEach(deuxDecimales);
  chargerLot(false, function () { if (!(nombre(depot) > 0)) calculerDepot(); });

  lot.addEventListener("change", function () { chargerLot(true, calculerDepot); });
  [loyer, charges].forEach(function (c) {
    if (!c) return;
    c.addEventListener("input", calculerDepot);
    c.addEventListener("change", function () { deuxDecimales(c); calculerDepot(); });
  });
  if (depot) {
    // une saisie à la main est conservée ; vider le champ relance le calcul automatique
    depot.addEventListener("input", function (e) {
      if (!e.isTrusted) return;  // reformatage d'un calcul (calcul_montants.js) : pas une saisie
      depotSaisi = depot.value.trim() !== ""; if (!depotSaisi) calculerDepot(); });
    depot.addEventListener("change", function () { deuxDecimales(depot); });
  }
});
