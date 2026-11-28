// Fiche du bien : la zone « Remboursements de l'acquéreur » n'apparaît que quand le bien est vendu
// (case « bien vendu » cochée ou date de vente remplie). Les montants déjà saisis restent visibles.
document.addEventListener("DOMContentLoaded", function () {
  var zone = document.querySelector("fieldset.remboursements-vente"),
      vendu = document.getElementById("id_vendu"),
      date = document.getElementById("id_date_vente");
  if (!zone || !vendu) return;
  var champs = zone.querySelectorAll("input");
  function maj() {
    var rempli = Array.prototype.some.call(champs, function (c) { return c.value; });
    zone.style.display = vendu.checked || (date && date.value) || rempli ? "" : "none";
  }
  vendu.addEventListener("change", maj);
  if (date) date.addEventListener("change", maj);
  maj();
});
