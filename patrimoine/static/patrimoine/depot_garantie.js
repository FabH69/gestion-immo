// Dépôt de garantie proposé automatiquement : 1 mois de loyer HC (nu), 2 mois (meublé, étudiant), 0 (bail mobilité).
document.addEventListener("DOMContentLoaded", () => {
  const depot = document.getElementById("id_depot_garantie");
  const type = document.getElementById("id_type");
  const loyers = ["id_loyer_initial", "id_loyer_hc"].map(id => document.getElementById(id)).filter(Boolean);
  if (!depot || !type || !loyers.length) return;
  const mois = { nu: 1, meuble: 2, etudiant: 2, mobilite: 0 };
  const nombre = v => parseFloat(String(v).replace(/\s/g, "").replace(",", "."));
  const calcul = () => {
    const loyer = loyers.map(e => nombre(e.value)).find(v => !isNaN(v));
    const m = mois[type.value];
    return loyer === undefined || m === undefined ? null : (loyer * m).toFixed(2);
  };
  // on ne remplace que si le champ est vide, à zéro, ou encore égal au dernier calcul
  let dernier = location.pathname.endsWith("/add/") && nombre(depot.value) ? calcul() : null;
  const maj = () => {
    const v = calcul();
    if (v === null) return;
    if (!nombre(depot.value) || (dernier !== null && nombre(depot.value) === nombre(dernier))) {
      depot.value = v;
      dernier = v;
    }
  };
  [...loyers, type].forEach(e => { e.addEventListener("input", maj); e.addEventListener("change", maj); });
});
