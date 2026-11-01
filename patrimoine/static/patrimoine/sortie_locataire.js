// Fiche locataire : la date de sortie n'apparaît que si la case « Le locataire quitte le logement » est cochée.
document.addEventListener("DOMContentLoaded", function () {
  var caseSortie = document.getElementById("id_sortie");
  var ligneDate = document.querySelector(".field-date_sortie");
  if (!caseSortie || !ligneDate) return;
  var cadre = caseSortie.closest("fieldset");
  function maj() {
    ligneDate.style.display = caseSortie.checked ? "" : "none";
    if (cadre) cadre.classList.toggle("sortie-active", caseSortie.checked);
    var champ = document.getElementById("id_date_sortie");
    if (caseSortie.checked && champ && !champ.value) champ.value = new Date().toISOString().slice(0, 10);
  }
  caseSortie.addEventListener("change", maj);
  maj();
});
