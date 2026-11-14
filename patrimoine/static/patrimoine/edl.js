/* État des lieux sur téléphone : envoi des photos dès qu'elles sont prises, saut d'étape, signature au doigt. */
(function () {
  "use strict";

  function demarrer() {
    var form = document.getElementById("form-edl");
    var envoi = document.querySelector(".edl .envoi");
    if (form) {
      var action = form.querySelector("input[name=action]");
      var ancre = form.querySelector("input[name=ancre]");
      // requestSubmit déclenche l'événement « submit » : pas d'alerte « modifications non enregistrées » en quittant la page
      function envoyer() { if (form.requestSubmit) form.requestSubmit(); else form.submit(); }
      form.querySelectorAll("input[type=file]").forEach(function (champ) {
        champ.addEventListener("change", function () {
          if (!champ.files.length) return;
          action.value = "photos";
          ancre.value = champ.dataset.element || "";
          if (envoi) envoi.classList.add("actif");
          envoyer();
        });
      });
      var saut = document.getElementById("saut-etape");
      if (saut) saut.addEventListener("change", function () {
        var aller = form.querySelector("input[name=aller]");
        aller.value = saut.value;
        envoyer();
      });
      form.addEventListener("submit", function (e) {
        var bouton = e.submitter;
        if (bouton && bouton.dataset.confirm && !confirm(bouton.dataset.confirm)) { e.preventDefault(); return; }
        if (bouton && bouton.name === "action") action.value = bouton.value;
        if (bouton && bouton.name === "aller") form.querySelector("input[name=aller]").value = bouton.value;
        if (envoi && form.querySelector("input[type=file]") && Array.prototype.some.call(form.querySelectorAll("input[type=file]"), function (f) { return f.files.length; })) envoi.classList.add("actif");
      });
    }

    document.querySelectorAll("form.form-signature").forEach(function (f) {
      var toile = f.querySelector("canvas");
      var ctx = toile.getContext("2d");
      var trace = false, vide = true, dernier = null;
      function ajuster() {
        var r = toile.getBoundingClientRect(), d = window.devicePixelRatio || 1;
        toile.width = r.width * d; toile.height = r.height * d;
        ctx.setTransform(d, 0, 0, d, 0, 0);
        ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#111";
        vide = true;
      }
      ajuster();
      function point(e) { var r = toile.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top}; }
      toile.addEventListener("pointerdown", function (e) { trace = true; dernier = point(e); toile.setPointerCapture(e.pointerId); e.preventDefault(); });
      toile.addEventListener("pointermove", function (e) {
        if (!trace) return;
        var p = point(e);
        ctx.beginPath(); ctx.moveTo(dernier.x, dernier.y); ctx.lineTo(p.x, p.y); ctx.stroke();
        dernier = p; vide = false;
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach(function (t) { toile.addEventListener(t, function () { trace = false; }); });
      f.querySelector(".effacer").addEventListener("click", function () { ctx.clearRect(0, 0, toile.width, toile.height); vide = true; });
      f.addEventListener("submit", function (e) {
        if (vide) { e.preventDefault(); alert("Signez dans le cadre avant de valider."); return; }
        if (f.dataset.confirm && !confirm(f.dataset.confirm)) { e.preventDefault(); return; }
        // fond blanc pour que la signature reste lisible dans le PDF
        var copie = document.createElement("canvas");
        copie.width = toile.width; copie.height = toile.height;
        var c = copie.getContext("2d");
        c.fillStyle = "#fff"; c.fillRect(0, 0, copie.width, copie.height); c.drawImage(toile, 0, 0);
        f.querySelector("input[name=signature]").value = copie.toDataURL("image/png");
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
