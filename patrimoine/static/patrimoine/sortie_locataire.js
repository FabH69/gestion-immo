// Fiche locataire : la date de sortie et la nouvelle adresse n'apparaissent que si « Le locataire quitte le logement » est cochée.
document.addEventListener("DOMContentLoaded", function () {
  var caseSortie = document.getElementById("id_sortie");
  var lignes = document.querySelectorAll(".field-date_sortie, .field-nouvelle_rue");
  if (!caseSortie || !lignes.length) return;
  var cadre = caseSortie.closest("fieldset");
  function maj() {
    lignes.forEach(function (l) { l.style.display = caseSortie.checked ? "" : "none"; });
    if (cadre) cadre.classList.toggle("sortie-active", caseSortie.checked);
    var champ = document.getElementById("id_date_sortie");
    if (caseSortie.checked && champ && !champ.value) champ.value = new Date().toISOString().slice(0, 10);
  }
  caseSortie.addEventListener("change", maj);
  maj();
});
