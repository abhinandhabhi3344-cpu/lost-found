/* Shared basic form validations (frontend mirror of app/validators.py).
 * - text-only: letters, spaces, . ' -
 * - email: native type=email + fallback regex
 * - phone: exactly 10 digits
 * Safe: only blocks clearly invalid input, never rewrites values except trimming phone display.
 * Usage: forms use onsubmit="return LFValidateForm(this)" and inputs use data-validate="text|email|phone|title|location|password|username|number|required".
 */
(function () {
  var TEXT_RE = /^[A-Za-z][A-Za-z\s\.'\-]{1,149}$/;
  var USERNAME_RE = /^[A-Za-z0-9_.]{3,30}$/;
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  function digitsOnly(v) {
    v = (v || "").replace(/\D/g, "");
    if (v.length === 12 && v.indexOf("91") === 0) v = v.slice(2);
    else if (v.length === 11 && v.charAt(0) === "0") v = v.slice(1);
    return v;
  }

  function setErr(input, msg) {
    input.setCustomValidity(msg || "");
    if (msg) input.reportValidity();
    return !msg;
  }

  function validateInput(input) {
    var kind = input.getAttribute("data-validate");
    if (!kind || input.disabled) return true;
    var val = (input.value || "").trim();
    var required = input.hasAttribute("required") || kind === "required";

    if (!val) return required ? setErr(input, "This field is required.") : setErr(input, "");

    if (kind === "text") {
      if (val.length < 2) return setErr(input, "Enter at least 2 letters (text only).");
      if (!TEXT_RE.test(val)) return setErr(input, "Text only: letters, spaces and . ' - (no numbers).");
    } else if (kind === "username") {
      if (!USERNAME_RE.test(val)) return setErr(input, "Username: 3-30 chars, letters/numbers/_/ . only.");
    } else if (kind === "email") {
      if (!EMAIL_RE.test(val)) return setErr(input, "Enter a valid email address.");
    } else if (kind === "phone") {
      var d = digitsOnly(val);
      if (!(d.length === 10 && /^\d{10}$/.test(d))) return setErr(input, "Phone must be exactly 10 digits.");
    } else if (kind === "password") {
      if (val.length < 6) return setErr(input, "Password must be at least 6 characters.");
    } else if (kind === "title") {
      if (val.length < 3) return setErr(input, "Title must be at least 3 characters.");
      if (!/[A-Za-z]/.test(val)) return setErr(input, "Title must contain letters.");
    } else if (kind === "location") {
      if (val.length < 3) return setErr(input, "Location must be at least 3 characters.");
      if (!/[A-Za-z]/.test(val)) return setErr(input, "Location must contain letters.");
    } else if (kind === "contact") {
      var dd = digitsOnly(val);
      var isPhone = dd.length === 10 && /^\d{10}$/.test(dd);
      if (!isPhone && !EMAIL_RE.test(val)) return setErr(input, "Enter a 10-digit phone or a valid email.");
    }
    return setErr(input, "");
  }

  window.LFValidateInput = validateInput;

  // AJAX availability check for username/email (frontend only).
  // Shows "already exists" message and disables submit while taken.
  var availabilityState = {};
  window.LFCheckAvailability = function (field, value, msgId, inputId, submitId, formMsgId) {
    var v = (value || "").trim();
    if (!v) return;
    var msgEl = document.getElementById(msgId);
    var inputEl = document.getElementById(inputId);
    var submitBtn = document.getElementById(submitId);
    var formMsg = formMsgId ? document.getElementById(formMsgId) : null;
    // First run local format check so we don't query backend with garbage.
    if (inputEl && !validateInput(inputEl)) {
      if (msgEl) {
        msgEl.textContent = inputEl.validationMessage || "Invalid value.";
        msgEl.style.color = "#dc2626";
        msgEl.style.display = "block";
      }
      return;
    }
    fetch("/api/check-availability/?" + field + "=" + encodeURIComponent(v))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var exists = data[field + "_exists"];
        availabilityState[msgId] = !exists;
        if (!msgEl || !inputEl) return;
        if (exists) {
          msgEl.textContent = (field === "username" ? "Username already exists — please choose another." : "Email already registered — please use another.");
          msgEl.style.color = "#dc2626";
          msgEl.style.display = "block";
          inputEl.style.borderColor = "#dc2626";
        } else {
          msgEl.textContent = "\u2713 Available";
          msgEl.style.color = "#047857";
          msgEl.style.display = "block";
          inputEl.style.borderColor = "#047857";
          setTimeout(function () { msgEl.style.display = "none"; inputEl.style.borderColor = ""; }, 2000);
        }
        if (submitBtn) {
          // Block submit if ANY checked field in this form is taken.
          var form = submitBtn.closest("form");
          var blocked = false;
          if (form) {
            form.querySelectorAll("[data-availability-msg]").forEach(function (m) {
              if (availabilityState[m.id] === false) blocked = true;
            });
          } else if (exists) blocked = true;
          submitBtn.disabled = blocked;
          submitBtn.style.opacity = blocked ? "0.6" : "1";
          submitBtn.style.cursor = blocked ? "not-allowed" : "pointer";
          if (formMsg) {
            if (blocked) {
              formMsg.textContent = "Username/email already exists — please choose another.";
              formMsg.style.color = "#dc2626";
              formMsg.style.display = "block";
            } else {
              formMsg.style.display = "none";
            }
          }
        }
      })
      .catch(function () { /* network error: do not block submit */ });
  };

  function isHiddenForValidation(el) {
    // Skip detective-only fields when citizen is selected (parent has .hidden / display:none).
    if (!el) return true;
    if (el.disabled) return true;
    if (el.closest && el.closest(".hidden")) {
      // The modal itself has .hidden until opened — but on submit the open modal
      // no longer has .hidden, only the detective sub-block does. So this is safe.
      // To avoid skipping everything while modal is closed, only skip if the
      // closest .hidden is NOT a .modal-body (i.e. it is the detective sub-block).
      var h = el.closest(".hidden");
      if (h && !h.classList.contains("modal-body")) return true;
    }
    // display:none inline (citizen/detective toggle uses style.display='none')
    var n = el;
    while (n && n.style) {
      if (n.style.display === "none") return true;
      n = n.parentElement;
    }
    return false;
  }

  window.LFValidateForm = function (form) {
    if (!form) return true;
    var inputs = form.querySelectorAll("[data-validate]");
    for (var i = 0; i < inputs.length; i++) {
      if (isHiddenForValidation(inputs[i])) continue;
      if (!validateInput(inputs[i])) {
        inputs[i].focus();
        return false;
      }
    }
    // Let existing confirm() dialogs still run if present in onsubmit chain.
    return true;
  };

  document.addEventListener("DOMContentLoaded", function () {
    // Live: clear custom errors + auto-strip phone to digits (max 10)
    document.querySelectorAll("input[data-validate='phone']").forEach(function (el) {
      el.setAttribute("inputmode", "numeric");
      el.addEventListener("input", function () {
        var d = digitsOnly(el.value).slice(0, 10);
        // Only rewrite if user typed formatting chars (keep typing smooth)
        if (/[^\d]/.test(el.value)) el.value = d;
        el.setCustomValidity("");
      });
    });
    // Live validation on blur for EVERYTHING (so email/text/phone show errors immediately,
    // not only on submit). Hidden detective fields are skipped.
    document.querySelectorAll("input[data-validate], textarea[data-validate], select[data-validate]").forEach(function (el) {
      el.addEventListener("input", function () { el.setCustomValidity(""); });
      el.addEventListener("blur", function () {
        if (isHiddenForValidation(el)) return;
        validateInput(el);
      });
    });
  });
})();
