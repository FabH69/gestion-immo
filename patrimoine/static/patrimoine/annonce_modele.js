// Nouvelle annonce : dès qu'on choisit le lot, son modèle d'annonce par défaut est sélectionné.
document.addEventListener("DOMContentLoaded", function () {
  var lot = document.getElementById("id_lot"), modele = document.getElementById("id_modele");
  if (!lot || !modele) return;
  var url = location.pathname.replace(/\/(add|\d+\/change)\/$/, "/") + "modele-du-lot/";
  lot.addEventListener("change", function () {
    if (!lot.value) return;
    fetch(url + "?lot=" + encodeURIComponent(lot.value), {credentials: "same-origin"})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        modele.value = d.modele ? String(d.modele) : "";
        modele.dispatchEvent(new Event("change"));
        var note = document.getElementById("note-modele");
        if (!note) { note = document.createElement("div"); note.id = "note-modele"; note.className = "help"; modele.parentNode.appendChild(note); }
        note.textContent = d.modele ? "Modèle par défaut du lot : " + d.nom : "Aucun modèle d'annonce rattaché à ce lot : le texte sera généré d'après la fiche du lot.";
      });
  });
});
