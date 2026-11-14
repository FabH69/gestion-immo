// Bail : la zone des plages d'heures creuses n'apparaît que si la case « heures pleines / heures creuses » est cochée.
document.addEventListener("DOMContentLoaded", function () {
  var caseHc = document.getElementById("id_heures_creuses"), plages = document.getElementById("id_plages_heures_creuses");
  if (!caseHc || !plages) return;
  plages.rows = 3;
  plages.placeholder = "22h00 – 06h00\n12h30 – 14h30";
  var bloc = plages.closest(".fieldBox") || plages.parentNode;
  function maj() { bloc.style.display = caseHc.checked ? "" : "none"; }
  caseHc.addEventListener("change", function () { maj(); if (caseHc.checked) plages.focus(); });
  maj();
});
