// Fiche du bien : quand le propriétaire est une SCI à l'IR (ou à l'IS), le régime fiscal et l'option sont ceux de la SCI.
// Les deux listes sont réglées et verrouillées ; le serveur applique la même règle à l'enregistrement.
document.addEventListener("DOMContentLoaded", function () {
  var proprio = document.getElementById("id_proprietaire"),
      regime = document.getElementById("id_regime_fiscal"),
      option = document.getElementById("id_option_fiscale");
  if (!proprio || !regime) return;
  var imposes = {};
  try { imposes = JSON.parse(proprio.getAttribute("data-regimes-imposes") || "{}"); } catch (e) { imposes = {}; }
  var note = document.createElement("div");
  note.className = "help regime-impose";
  note.style.cssText = "margin-top:6px;color:#8a5a00;font-weight:600";
  (regime.closest(".fieldBox") || regime.parentNode).appendChild(note);

  function verrouiller(champ, oui) {
    if (!champ) return;
    // pas de « disabled » : la valeur doit rester envoyée avec le formulaire
    champ.style.pointerEvents = oui ? "none" : "";
    champ.style.background = oui ? "#eee" : "";
    champ.tabIndex = oui ? -1 : 0;
    champ.setAttribute("aria-disabled", oui ? "true" : "false");
  }

  function maj() {
    var impose = imposes[proprio.value] || "";
    if (impose) {
      regime.value = impose;
      if (option) option.value = "reel";
      var nom = proprio.options[proprio.selectedIndex] ? proprio.options[proprio.selectedIndex].text : "la SCI";
      note.textContent = "Régime imposé par « " + nom + " » : " +
        (impose === "sci_ir" ? "revenus fonciers au réel, déclaration 2072 de la SCI." : "impôt sur les sociétés, au réel.");
    }
    note.style.display = impose ? "" : "none";
    verrouiller(regime, !!impose);
    verrouiller(option, !!impose);
  }
  proprio.addEventListener("change", maj);
  maj();
});
