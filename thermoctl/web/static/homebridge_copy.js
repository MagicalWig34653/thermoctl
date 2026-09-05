// Copies one zone's mqtt-thing configuration to the clipboard, from the
// Schnittstellen page's Homebridge section.
//
// `navigator.clipboard` is bound to a "secure context" (HTTPS, or localhost) --
// thermoctl runs on the home network over plain HTTP more often than not, and there
// `navigator.clipboard` is simply `undefined`. A button that silently does nothing in
// that case is worse than no button: someone would try it, see nothing happen, and
// assume they mistyped the configuration by hand instead. The fallback below
// (`document.execCommand("copy")` against a temporary, off-screen textarea) still
// works in that situation in every browser this project has to support; only if even
// that fails does the button say so honestly instead of staying quiet.
(function () {
    "use strict";

    function zeigeRueckmeldung(status, text, ok) {
        if (!status) {
            return;
        }
        status.textContent = text;
        status.classList.toggle("text-success", ok);
        status.classList.toggle("text-danger", !ok);
        status.hidden = false;
        window.clearTimeout(status._thermoctlTimeout);
        status._thermoctlTimeout = window.setTimeout(function () {
            status.hidden = true;
        }, 5000);
    }

    // Deprecated, but still the only thing that copies text in an insecure context.
    // `execCommand` itself can throw in a browser that has removed it entirely --
    // caught below, not assumed away.
    function kopiereUeberAuswahl(text) {
        var feld = document.createElement("textarea");
        feld.value = text;
        feld.setAttribute("readonly", "");
        feld.style.position = "fixed";
        feld.style.top = "-1000px";
        feld.style.left = "-1000px";
        document.body.appendChild(feld);
        feld.focus();
        feld.select();
        var geklappt = false;
        try {
            geklappt = document.execCommand("copy");
        } catch (fehler) {
            geklappt = false;
        }
        document.body.removeChild(feld);
        return geklappt;
    }

    function kopiere(text, status) {
        if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(
                function () {
                    zeigeRueckmeldung(status, "In die Zwischenablage kopiert.", true);
                },
                function () {
                    if (kopiereUeberAuswahl(text)) {
                        zeigeRueckmeldung(status, "In die Zwischenablage kopiert.", true);
                    } else {
                        zeigeRueckmeldung(
                            status,
                            "Kopieren nicht möglich — Text bitte markieren und selbst kopieren.",
                            false
                        );
                    }
                }
            );
            return;
        }
        if (kopiereUeberAuswahl(text)) {
            zeigeRueckmeldung(status, "In die Zwischenablage kopiert.", true);
        } else {
            zeigeRueckmeldung(
                status,
                "Kopieren nicht möglich — Text bitte markieren und selbst kopieren.",
                false
            );
        }
    }

    document.addEventListener("click", function (ereignis) {
        var knopf = ereignis.target.closest("[data-homebridge-copy]");
        if (!knopf) {
            return;
        }
        var ziel = document.getElementById(knopf.getAttribute("data-homebridge-copy"));
        if (!ziel) {
            return;
        }
        var status = document.getElementById(knopf.getAttribute("data-homebridge-status"));
        kopiere(ziel.textContent, status);
    });
})();
