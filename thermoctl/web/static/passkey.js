/**
 * Passkey sign-in and registration.
 *
 * Two things that are easily got wrong here and then fail without a word:
 *
 * 1. WebAuthn speaks binary, JSON does not. Everything that goes back and forth is
 *    base64url — no padding, with "-" and "_" instead of "+" and "/".
 * 2. The browser only offers a stored passkey in the username field's autocomplete menu
 *    when autocomplete="username webauthn" is set there AND a request with
 *    mediation: "conditional" is running here. If either is missing, nothing happens —
 *    and without any error message.
 */
(function () {
    "use strict";

    const AUTHENTICATION = "/passkey/authentication";
    const REGISTRATION = "/passkey/registration";
    // The service's challenge is valid for two minutes. The conditional request runs
    // longer, so it is renewed with a fresh one before that.
    const RENEW_CHALLENGE_AFTER_MS = 90 * 1000;

    let conditionalRequest = null;

    function isSupported() {
        return typeof window.PublicKeyCredential === "function"
            && typeof navigator.credentials === "object";
    }

    function base64urlToBytes(value) {
        const padded = value.replace(/-/g, "+").replace(/_/g, "/")
            + "=".repeat((4 - (value.length % 4)) % 4);
        const raw = window.atob(padded);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i += 1) {
            bytes[i] = raw.charCodeAt(i);
        }
        return bytes;
    }

    function bytesToBase64url(buffer) {
        const bytes = new Uint8Array(buffer);
        let raw = "";
        for (let i = 0; i < bytes.length; i += 1) {
            raw += String.fromCharCode(bytes[i]);
        }
        return window.btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    /** The CSRF token from the cookie — the changing routes require it. */
    function csrfHeaders() {
        const entry = document.cookie.split("; ").find(function (value) {
            return value.startsWith("thermoctl_csrf=");
        });
        const headers = { "Content-Type": "application/json" };
        if (entry) {
            headers["X-CSRF-Token"] = decodeURIComponent(entry.split("=")[1]);
        }
        return headers;
    }

    function post(path, body) {
        return fetch(path, {
            method: "POST",
            credentials: "same-origin",
            headers: csrfHeaders(),
            body: body === undefined ? null : JSON.stringify(body)
        }).then(function (response) {
            return response.json().then(function (result) {
                if (!response.ok) {
                    throw new Error(result.notice || "Es hat nicht geklappt.");
                }
                return result;
            });
        });
    }

    /** Turns the service's arguments into what the browser expects. */
    function prepareArguments(args) {
        const ready = Object.assign({}, args);
        ready.challenge = base64urlToBytes(args.challenge);
        if (args.user) {
            ready.user = Object.assign({}, args.user, {
                id: base64urlToBytes(args.user.id)
            });
        }
        ["allowCredentials", "excludeCredentials"].forEach(function (field) {
            if (Array.isArray(args[field])) {
                ready[field] = args[field].map(function (entry) {
                    return Object.assign({}, entry, { id: base64urlToBytes(entry.id) });
                });
            }
        });
        return ready;
    }

    function sendAssertion(credential) {
        return post(AUTHENTICATION + "/verify", {
            id: credential.id,
            rawId: bytesToBase64url(credential.rawId),
            type: credential.type,
            response: {
                clientDataJSON: bytesToBase64url(credential.response.clientDataJSON),
                authenticatorData: bytesToBase64url(credential.response.authenticatorData),
                signature: bytesToBase64url(credential.response.signature),
                userHandle: credential.response.userHandle
                    ? bytesToBase64url(credential.response.userHandle)
                    : null
            }
        }).then(function (result) {
            window.location.href = result.redirect || "/";
        });
    }

    function abortConditionalRequest() {
        if (conditionalRequest === null) {
            return;
        }
        const running = conditionalRequest;
        conditionalRequest = null;
        window.clearTimeout(running.renewal);
        running.controller.abort();
    }

    /**
     * Keeps a request open in the background so the browser can offer a stored passkey
     * in the username field. It is renewed with a fresh challenge for as long as the
     * page stays open.
     */
    function startConditionalAuthentication() {
        post(AUTHENTICATION + "/options").then(function (args) {
            const controller = new AbortController();
            const renewal = window.setTimeout(function () {
                abortConditionalRequest();
                startConditionalAuthentication();
            }, RENEW_CHALLENGE_AFTER_MS);
            conditionalRequest = { controller: controller, renewal: renewal };

            return navigator.credentials.get({
                mediation: "conditional",
                signal: controller.signal,
                publicKey: prepareArguments(args)
            }).then(function (credential) {
                window.clearTimeout(renewal);
                conditionalRequest = null;
                if (credential) {
                    return sendAssertion(credential);
                }
            });
        }).catch(function (error) {
            // An abort is the normal case — either the renewal or a click on the button.
            // Conditional sign-in is an offer, not something anyone set in motion; it
            // therefore stays silent.
            if (!error || error.name !== "AbortError") {
                console.debug("Bedingte Passkey-Anmeldung nicht moeglich:", error);
            }
        });
    }

    function wireAuthentication() {
        const button = document.getElementById("passkey-login");
        // `dataset.wired`: setUp() runs again on every content swap. Without the mark the
        // same button would get several click handlers and trigger just as many
        // authenticator requests.
        if (!button || button.dataset.wired) {
            return;
        }
        button.dataset.wired = "yes";
        const hintField = document.getElementById("passkey-hint");

        function hint(text, isError) {
            if (hintField) {
                hintField.textContent = text;
                hintField.className = isError
                    ? "form-text text-danger"
                    : "form-text text-body-secondary";
            }
        }

        button.addEventListener("click", function () {
            // The browser allows only ONE request at a time; the conditional one must
            // therefore give way first, otherwise it would reject the second.
            abortConditionalRequest();
            button.disabled = true;
            hint("Warte auf den Authenticator …", false);

            post(AUTHENTICATION + "/options")
                .then(function (args) {
                    return navigator.credentials.get({
                        publicKey: prepareArguments(args)
                    });
                })
                .then(function (credential) {
                    if (!credential) {
                        throw new Error("Es wurde kein Passkey ausgewählt.");
                    }
                    return sendAssertion(credential);
                })
                .catch(function (error) {
                    button.disabled = false;
                    hint(
                        error && error.name === "NotAllowedError"
                            ? "Die Anmeldung wurde abgebrochen."
                            : (error.message || "Die Anmeldung war nicht erfolgreich."),
                        true
                    );
                    startConditionalAuthentication();
                });
        });
    }

    function wireRegistration() {
        const button = document.getElementById("passkey-register");
        if (!button || button.dataset.wired) {
            return;
        }
        button.dataset.wired = "yes";
        const hintField = document.getElementById("passkey-registration-hint");

        function hint(text, isError) {
            if (hintField) {
                hintField.textContent = text;
                hintField.className = isError
                    ? "form-text text-danger"
                    : "form-text text-body-secondary";
            }
        }

        button.addEventListener("click", function () {
            const field = document.getElementById("passkey-label");
            const label = field ? field.value : "";
            button.disabled = true;
            hint("Warte auf den Authenticator …", false);

            post(REGISTRATION + "/options")
                .then(function (args) {
                    return navigator.credentials.create({
                        publicKey: prepareArguments(args)
                    });
                })
                .then(function (credential) {
                    if (!credential) {
                        throw new Error("Es wurde kein Passkey angelegt.");
                    }
                    return post(REGISTRATION + "/verify", {
                        label: label,
                        id: credential.id,
                        rawId: bytesToBase64url(credential.rawId),
                        type: credential.type,
                        response: {
                            clientDataJSON: bytesToBase64url(credential.response.clientDataJSON),
                            attestationObject: bytesToBase64url(
                                credential.response.attestationObject
                            )
                        }
                    });
                })
                .then(function () {
                    window.location.reload();
                })
                .catch(function (error) {
                    button.disabled = false;
                    hint(
                        error && error.name === "NotAllowedError"
                            ? "Der Vorgang wurde abgebrochen."
                            : (error.message || "Der Passkey konnte nicht hinterlegt werden."),
                        true
                    );
                });
        });
    }

    let conditionalStarted = false;

    function setUp() {
        // Offer nothing at all without WebAuthn, instead of showing a button that cannot
        // do anything. The sections are hidden in the markup and only become visible
        // here — that way nothing flashes up only to disappear again.
        const supported = isSupported();
        document.querySelectorAll("[data-passkey]").forEach(function (element) {
            element.hidden = !supported;
        });
        document.querySelectorAll("[data-without-passkey]").forEach(function (element) {
            element.hidden = supported;
        });
        if (!supported) {
            return;
        }
        wireAuthentication();
        wireRegistration();

        // Conditional sign-in only when the browser knows it: otherwise
        // navigator.credentials.get() would immediately raise a dialog, even for
        // visitors without a passkey.
        // `conditionalStarted`: this part, too, runs again on every content swap. A
        // second call would open a second request, and the browser allows only one --
        // it would reject it and end the first one in doing so.
        if (!conditionalStarted
            && document.getElementById("passkey-login")
            && typeof window.PublicKeyCredential.isConditionalMediationAvailable === "function") {
            conditionalStarted = true;
            window.PublicKeyCredential.isConditionalMediationAvailable()
                .then(function (available) {
                    if (available) {
                        startConditionalAuthentication();
                    }
                })
                .catch(function () { /* The button stays. */ });
        }
    }

    // Two hooks, because neither alone covers both routes:
    //   * `DOMContentLoaded` for a direct call of an address,
    //   * `htmx:load` for every content swapped in by hx-boost.
    // Previously only `DOMContentLoaded` hung here. Anyone reaching /passkeys through the
    // menu triggered neither -- the whole passkey section stayed hidden, and the feature
    // was invisible to everyone who did not reload the page directly. `setUp()` is
    // therefore safe to run repeatedly: it only reveals sections and wires up buttons
    // that carry no mark yet.
    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
})();
