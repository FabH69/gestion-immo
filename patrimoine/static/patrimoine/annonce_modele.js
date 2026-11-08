// Annonce : quand on choisit le lot, son modèle d'annonce par défaut est sélectionné,
// et le dépôt de garantie est calculé : (loyer CC - charges) x 1 mois en location nue, x 2 en meublé.
document.addEventListener("DOMContentLoaded", function () {
  var lot = document.getElementById("id_lot"), modele = document.getElementById("id_modele");
  var loyer = document.getElementById("id_loyer_cc"), charges = document.getElementById("id_charges"),
      depot = document.getElementById("id_depot_garantie");
  if (!lot) return;
  var url = location.pathname.replace(/\/(add|\d+\/change)\/$/, "/") + "modele-du-lot/";
  var meuble = null, dernierCalcul = null;
  var nombre = function (champ) { return parseFloat(String(champ && champ.value || "0").replace(",", ".")) || 0; };

  function calculerDepot() {
    if (!depot || !loyer || meuble === null) return;
    var actuel = nombre(depot);
    // on ne remplace pas un montant saisi à la main
    if (actuel && dernierCalcul !== null && Math.abs(actuel - dernierCalcul) > 0.005) return;
    if (actuel && dernierCalcul === null) return;
    var hc = nombre(loyer) - nombre(charges);
    if (hc <= 0) return;
    dernierCalcul = Math.round(hc * (meuble ? 2 : 1) * 100) / 100;
    depot.value = dernierCalcul.toFixed(2);
  }

  function chargerLot(avecModele) {
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
        if (avecModele && dernierCalcul !== null) dernierCalcul = nombre(depot);  // le lot change : on recalcule
        calculerDepot();
      });
  }

  lot.addEventListener("change", function () { chargerLot(true); });
  [loyer, charges].forEach(function (c) { if (c) c.addEventListener("input", calculerDepot); });
  chargerLot(false);
});
