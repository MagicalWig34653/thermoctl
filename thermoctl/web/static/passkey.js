/**
 * Passkey-Anmeldung und -Registrierung.
 *
 * Zwei Dinge, die hier leicht falsch gemacht werden und dann wortlos scheitern:
 *
 * 1. WebAuthn spricht Binaerdaten, JSON nicht. Alles, was hin- und hergeht, ist
 *    base64url — ohne Polsterung, mit "-" und "_" statt "+" und "/".
 * 2. Der Browser bietet einen hinterlegten Passkey nur dann im
 *    Autovervollstaendigen-Menue des Benutzernamenfelds an, wenn dort
 *    autocomplete="username webauthn" steht UND hier eine Anfrage mit
 *    mediation: "conditional" laeuft. Fehlt eines von beidem, passiert nichts —
 *    und zwar ohne Fehlermeldung.
 */
(function () {
    "use strict";

    const ANMELDUNG = "/passkey/authentication";
    const REGISTRIERUNG = "/passkey/registration";
    // Die Challenge des Dienstes gilt zwei Minuten. Die bedingte Anfrage laeuft
    // laenger, also wird sie vorher mit einer frischen erneuert.
    const CHALLENGE_ERNEUERN_NACH_MS = 90 * 1000;

    let bedingteAnfrage = null;

    function unterstuetzt() {
        return typeof window.PublicKeyCredential === "function"
            && typeof navigator.credentials === "object";
    }

    function base64urlZuBytes(wert) {
        const gefuellt = wert.replace(/-/g, "+").replace(/_/g, "/")
            + "=".repeat((4 - (wert.length % 4)) % 4);
        const roh = window.atob(gefuellt);
        const bytes = new Uint8Array(roh.length);
        for (let i = 0; i < roh.length; i += 1) {
            bytes[i] = roh.charCodeAt(i);
        }
        return bytes;
    }

    function bytesZuBase64url(puffer) {
        const bytes = new Uint8Array(puffer);
        let roh = "";
        for (let i = 0; i < bytes.length; i += 1) {
            roh += String.fromCharCode(bytes[i]);
        }
        return window.btoa(roh).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    /** Das CSRF-Token aus dem Cookie — die aendernden Wege verlangen es. */
    function csrfKopf() {
        const eintrag = document.cookie.split("; ").find(function (wert) {
            return wert.startsWith("thermoctl_csrf=");
        });
        const kopf = { "Content-Type": "application/json" };
        if (eintrag) {
            kopf["X-CSRF-Token"] = decodeURIComponent(eintrag.split("=")[1]);
        }
        return kopf;
    }

    function holen(pfad, koerper) {
        return fetch(pfad, {
            method: "POST",
            credentials: "same-origin",
            headers: csrfKopf(),
            body: koerper === undefined ? null : JSON.stringify(koerper)
        }).then(function (antwort) {
            return antwort.json().then(function (ergebnis) {
                if (!antwort.ok) {
                    throw new Error(ergebnis.meldung || "Es hat nicht geklappt.");
                }
                return ergebnis;
            });
        });
    }

    /** Wandelt die Argumente des Dienstes in das um, was der Browser erwartet. */
    function argumenteAufbereiten(argumente) {
        const fertig = Object.assign({}, argumente);
        fertig.challenge = base64urlZuBytes(argumente.challenge);
        if (argumente.user) {
            fertig.user = Object.assign({}, argumente.user, {
                id: base64urlZuBytes(argumente.user.id)
            });
        }
        ["allowCredentials", "excludeCredentials"].forEach(function (feld) {
            if (Array.isArray(argumente[feld])) {
                fertig[feld] = argumente[feld].map(function (eintrag) {
                    return Object.assign({}, eintrag, { id: base64urlZuBytes(eintrag.id) });
                });
            }
        });
        return fertig;
    }

    function assertionSenden(credential) {
        return holen(ANMELDUNG + "/verify", {
            id: credential.id,
            rawId: bytesZuBase64url(credential.rawId),
            type: credential.type,
            response: {
                clientDataJSON: bytesZuBase64url(credential.response.clientDataJSON),
                authenticatorData: bytesZuBase64url(credential.response.authenticatorData),
                signature: bytesZuBase64url(credential.response.signature),
                userHandle: credential.response.userHandle
                    ? bytesZuBase64url(credential.response.userHandle)
                    : null
            }
        }).then(function (ergebnis) {
            window.location.href = ergebnis.weiter || "/";
        });
    }

    function bedingteAnfrageAbbrechen() {
        if (bedingteAnfrage === null) {
            return;
        }
        const laufende = bedingteAnfrage;
        bedingteAnfrage = null;
        window.clearTimeout(laufende.erneuerung);
        laufende.abbruch.abort();
    }

    /**
     * Haelt im Hintergrund eine Anfrage offen, damit der Browser einen hinterlegten
     * Passkey im Benutzernamenfeld anbieten kann. Sie wird mit frischer Challenge
     * erneuert, solange die Seite offen ist.
     */
    function bedingteAnmeldungStarten() {
        holen(ANMELDUNG + "/options").then(function (argumente) {
            const abbruch = new AbortController();
            const erneuerung = window.setTimeout(function () {
                bedingteAnfrageAbbrechen();
                bedingteAnmeldungStarten();
            }, CHALLENGE_ERNEUERN_NACH_MS);
            bedingteAnfrage = { abbruch: abbruch, erneuerung: erneuerung };

            return navigator.credentials.get({
                mediation: "conditional",
                signal: abbruch.signal,
                publicKey: argumenteAufbereiten(argumente)
            }).then(function (credential) {
                window.clearTimeout(erneuerung);
                bedingteAnfrage = null;
                if (credential) {
                    return assertionSenden(credential);
                }
            });
        }).catch(function (fehler) {
            // Ein Abbruch ist der Normalfall — entweder die Erneuerung oder ein Klick
            // auf die Schaltflaeche. Die bedingte Anmeldung ist ein Angebot, kein
            // Vorgang, den jemand angestossen hat; sie bleibt deshalb still.
            if (!fehler || fehler.name !== "AbortError") {
                console.debug("Bedingte Passkey-Anmeldung nicht moeglich:", fehler);
            }
        });
    }

    function anmeldungVorbereiten() {
        const knopf = document.getElementById("passkey-login");
        // `dataset.wired`: initialisieren() laeuft bei jedem Inhaltswechsel erneut.
        // Ohne die Marke bekaeme derselbe Knopf mehrere Klickbehandlungen und loeste
        // ebenso viele Authenticator-Anfragen aus.
        if (!knopf || knopf.dataset.wired) {
            return;
        }
        knopf.dataset.wired = "ja";
        const hinweisfeld = document.getElementById("passkey-hint");

        function hinweis(text, istFehler) {
            if (hinweisfeld) {
                hinweisfeld.textContent = text;
                hinweisfeld.className = istFehler
                    ? "form-text text-danger"
                    : "form-text text-body-secondary";
            }
        }

        knopf.addEventListener("click", function () {
            // Der Browser laesst nur EINE Anfrage gleichzeitig zu; die bedingte muss
            // deshalb zuerst weichen, sonst wiese er die zweite ab.
            bedingteAnfrageAbbrechen();
            knopf.disabled = true;
            hinweis("Warte auf den Authenticator …", false);

            holen(ANMELDUNG + "/options")
                .then(function (argumente) {
                    return navigator.credentials.get({
                        publicKey: argumenteAufbereiten(argumente)
                    });
                })
                .then(function (credential) {
                    if (!credential) {
                        throw new Error("Es wurde kein Passkey ausgewählt.");
                    }
                    return assertionSenden(credential);
                })
                .catch(function (fehler) {
                    knopf.disabled = false;
                    hinweis(
                        fehler && fehler.name === "NotAllowedError"
                            ? "Die Anmeldung wurde abgebrochen."
                            : (fehler.message || "Die Anmeldung war nicht erfolgreich."),
                        true
                    );
                    bedingteAnmeldungStarten();
                });
        });
    }

    function registrierungVorbereiten() {
        const knopf = document.getElementById("passkey-register");
        if (!knopf || knopf.dataset.wired) {
            return;
        }
        knopf.dataset.wired = "ja";
        const hinweisfeld = document.getElementById("passkey-registration-hint");

        function hinweis(text, istFehler) {
            if (hinweisfeld) {
                hinweisfeld.textContent = text;
                hinweisfeld.className = istFehler
                    ? "form-text text-danger"
                    : "form-text text-body-secondary";
            }
        }

        knopf.addEventListener("click", function () {
            const feld = document.getElementById("passkey-label");
            const label = feld ? feld.value : "";
            knopf.disabled = true;
            hinweis("Warte auf den Authenticator …", false);

            holen(REGISTRIERUNG + "/options")
                .then(function (argumente) {
                    return navigator.credentials.create({
                        publicKey: argumenteAufbereiten(argumente)
                    });
                })
                .then(function (credential) {
                    if (!credential) {
                        throw new Error("Es wurde kein Passkey angelegt.");
                    }
                    return holen(REGISTRIERUNG + "/verify", {
                        label: label,
                        id: credential.id,
                        rawId: bytesZuBase64url(credential.rawId),
                        type: credential.type,
                        response: {
                            clientDataJSON: bytesZuBase64url(credential.response.clientDataJSON),
                            attestationObject: bytesZuBase64url(
                                credential.response.attestationObject
                            )
                        }
                    });
                })
                .then(function () {
                    window.location.reload();
                })
                .catch(function (fehler) {
                    knopf.disabled = false;
                    hinweis(
                        fehler && fehler.name === "NotAllowedError"
                            ? "Der Vorgang wurde abgebrochen."
                            : (fehler.message || "Der Passkey konnte nicht hinterlegt werden."),
                        true
                    );
                });
        });
    }

    let bedingteGestartet = false;

    function initialisieren() {
        // Ohne WebAuthn gar nichts anbieten, statt eine Schaltflaeche zu zeigen, die
        // nichts tun kann. Die Abschnitte sind im Markup ausgeblendet und werden erst
        // hier sichtbar — so blitzt auch nichts auf, das gleich wieder verschwindet.
        const kann = unterstuetzt();
        document.querySelectorAll("[data-passkey]").forEach(function (element) {
            element.hidden = !kann;
        });
        document.querySelectorAll("[data-without-passkey]").forEach(function (element) {
            element.hidden = kann;
        });
        if (!kann) {
            return;
        }
        anmeldungVorbereiten();
        registrierungVorbereiten();

        // Bedingte Anmeldung nur, wenn der Browser sie kennt: sonst wuerde
        // navigator.credentials.get() sofort einen Dialog aufwerfen, auch bei
        // Besuchern ohne Passkey.
        // `bedingteGestartet`: Auch dieser Teil laeuft bei jedem Inhaltswechsel erneut.
        // Ein zweiter Aufruf wuerde eine zweite Anfrage aufmachen, und der Browser laesst
        // nur eine zu -- er wiese sie ab und beendete dabei die erste.
        if (!bedingteGestartet
            && document.getElementById("passkey-login")
            && typeof window.PublicKeyCredential.isConditionalMediationAvailable === "function") {
            bedingteGestartet = true;
            window.PublicKeyCredential.isConditionalMediationAvailable()
                .then(function (moeglich) {
                    if (moeglich) {
                        bedingteAnmeldungStarten();
                    }
                })
                .catch(function () { /* Die Schaltflaeche bleibt. */ });
        }
    }

    // Zwei Aufhaenger, weil keiner allein beide Wege abdeckt:
    //   * `DOMContentLoaded` fuer den direkten Aufruf einer Adresse,
    //   * `htmx:load` fuer jeden per hx-boost eingetauschten Inhalt.
    // Vorher hing hier nur `DOMContentLoaded`. Wer /passkeys ueber das Menue ansteuerte,
    // loeste keines aus -- der ganze Passkey-Abschnitt blieb ausgeblendet, und die
    // Funktion war fuer jeden unsichtbar, der die Seite nicht direkt neu lud.
    // `initialisieren()` ist deshalb mehrfach ausfuehrbar: Es blendet nur ein und
    // verdrahtet Knoepfe, die noch keine Marke tragen.
    document.addEventListener("DOMContentLoaded", initialisieren);
    document.addEventListener("htmx:load", initialisieren);
})();
