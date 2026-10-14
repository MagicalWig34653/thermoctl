/* Zuordnungskarte (Entwurf): Linien von Endpunkten zu Zielen ziehen.
 *
 * Dieses Skript entscheidet nichts darüber, was zusammenpasst. Die Seite liefert in
 * `data-payload` für jeden Endpunkt die abgelehnten Ziele samt Begründung (`rejections`), erzeugt
 * von `domain/mapping_map.py`. Was dort fehlt, passt. Der Server prüft beim Speichern noch
 * einmal mit derselben Funktion.
 *
 * Bedienung: ziehen (Zeiger) oder Endpunkt anklicken/Enter und danach das Ziel anklicken/Enter
 * (Tastatur, Touch). Esc bricht ab. Ein Loslassen auf einem unpassenden Ziel oder im Leeren
 * verbindet nichts.
 *
 * Gespeichert wird nur zu Zonen-Stellen (POST an die Domänenfunktionen, danach Neuladen).
 * Ziele mit `data-draft` (Geräte-Eingänge) gibt es im Datenmodell noch nicht: Die Linie wird
 * nur gezeichnet und in der Entwurfsliste geführt.
 */
(function () {
    "use strict";

    // Schon verdrahtete Karten als WeakSet, nicht als Attribut: htmx stellt beim Zurückgehen
    // eine Momentaufnahme des Markups wieder her, aber keine Ereignisbehandler (vgl. assignment.js).
    const wired = new WeakSet();
    let current = null;

    function setUp(root, data) {
    const svg = root.querySelector("svg.tc-map-lines");
    const SVG = "http://www.w3.org/2000/svg";

    // Elemente nach Kennung, ohne Kennungen in CSS-Selektoren zu schreiben.
    const sources = new Map();
    const targets = new Map();
    root.querySelectorAll("[data-endpoint]").forEach(function (b) { sources.set(b.dataset.endpoint, b); });
    root.querySelectorAll("[data-target]").forEach(function (b) { targets.set(b.dataset.target, b); });

    const drafts = [];
    let armed = null;      // Endpunkt-Kennung im Tastatur-/Tipp-Modus
    let dragging = null;   // { id, line, moved }
    let suppressClick = false;

    document.querySelectorAll("[data-map-hint]").forEach(function (e) { e.hidden = false; });
    document.querySelectorAll("[data-disconnect]").forEach(function (e) { e.hidden = false; });
    document.querySelectorAll("[data-no-js]").forEach(function (e) { e.hidden = true; });

    function say(text, isError) {
        const status = document.querySelector("[data-map-status]");
        const error = document.querySelector("[data-map-error]");
        if (status) status.textContent = text || "";
        if (error) {
            error.hidden = !(isError && text);
            error.textContent = isError ? text : "";
        }
    }

    function csrfHeaders() {
        const entry = document.cookie.split("; ").find(function (v) { return v.startsWith("thermoctl_csrf="); });
        return entry ? { "X-CSRF-Token": decodeURIComponent(entry.split("=")[1]) } : {};
    }

    function post(url, fields) {
        const body = new URLSearchParams(fields);
        const headers = Object.assign({ "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" }, csrfHeaders());
        return fetch(url, { method: "POST", headers: headers, body: body, credentials: "same-origin" })
            .then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (json) {
                    return { ok: response.ok && json.ok === true, reason: json.reason || json.detail || "Das Speichern ist fehlgeschlagen." };
                });
            });
    }

    // ---- Linien -------------------------------------------------------------------------

    function centre(button) {
        const box = button.getBoundingClientRect();
        const base = root.getBoundingClientRect();
        return { x: box.left + box.width / 2 - base.left, y: box.top + box.height / 2 - base.top };
    }

    function curve(from, to) {
        const dx = Math.max(40, Math.abs(to.x - from.x) / 2);
        return "M" + from.x + "," + from.y + " C" + (from.x + dx) + "," + from.y + " " + (to.x - dx) + "," + to.y + " " + to.x + "," + to.y;
    }

    function makePath(className) {
        const path = document.createElementNS(SVG, "path");
        path.setAttribute("class", className);
        path.setAttribute("fill", "none");
        svg.appendChild(path);
        return path;
    }

    function redraw() {
        svg.querySelectorAll("path[data-permanent]").forEach(function (p) { p.remove(); });
        const base = root.getBoundingClientRect();
        svg.setAttribute("width", base.width);
        svg.setAttribute("height", base.height);
        const entries = data.connections.map(function (c) { return { a: c.endpoint, b: c.target, draft: false }; })
            .concat(drafts.map(function (d) { return { a: d.endpoint, b: d.target, draft: true }; }));
        entries.forEach(function (entry) {
            const from = sources.get(entry.a);
            const to = targets.get(entry.b);
            if (!from || !to) return;
            const path = makePath(entry.draft ? "tc-map-line tc-map-line-draft" : "tc-map-line");
            path.setAttribute("data-permanent", "");
            path.setAttribute("d", curve(centre(from), centre(to)));
        });
    }

    // ---- Verträglichkeit anzeigen ---------------------------------------------------------

    function mark(endpointId) {
        const rejected = data.rejections[endpointId] || {};
        sources.forEach(function (button, id) { button.classList.toggle("tc-port-active", id === endpointId); });
        targets.forEach(function (button, id) {
            const reason = rejected[id];
            button.classList.toggle("tc-port-unfit", Boolean(reason));
            button.classList.toggle("tc-port-fit", !reason);
            if (reason) {
                button.title = reason;
                button.setAttribute("aria-disabled", "true");
            } else {
                button.removeAttribute("title");
                button.removeAttribute("aria-disabled");
            }
        });
    }

    function unmark() {
        sources.forEach(function (b) { b.classList.remove("tc-port-active"); });
        targets.forEach(function (b) {
            b.classList.remove("tc-port-fit", "tc-port-unfit");
            b.removeAttribute("title");
            b.removeAttribute("aria-disabled");
        });
    }

    // ---- Verbinden ------------------------------------------------------------------------

    function addDraft(endpointId, targetId) {
        if (drafts.some(function (d) { return d.endpoint === endpointId && d.target === targetId; })) {
            say("Diese Verbindung steht schon im Entwurf.", true);
            return;
        }
        drafts.push({ endpoint: endpointId, target: targetId });
        const empty = document.querySelector("[data-draft-empty]");
        if (empty) empty.hidden = true;
        const item = document.createElement("li");
        const from = sources.get(endpointId).getAttribute("aria-label");
        const to = targets.get(targetId).getAttribute("aria-label");
        item.textContent = from.replace(" verbinden", "") + " → " + to + " ";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn btn-sm btn-link p-0";
        remove.textContent = "Entwurf verwerfen";
        remove.addEventListener("click", function () {
            const index = drafts.findIndex(function (d) { return d.endpoint === endpointId && d.target === targetId; });
            if (index >= 0) drafts.splice(index, 1);
            item.remove();
            if (!drafts.length && empty) empty.hidden = false;
            redraw();
        });
        item.appendChild(remove);
        document.querySelector("[data-draft-list]").appendChild(item);
        say("Entwurf gezeichnet, nicht gespeichert.", false);
        redraw();
    }

    function connect(endpointId, targetButton) {
        const targetId = targetButton.dataset.target;
        const reason = (data.rejections[endpointId] || {})[targetId];
        if (reason) {
            say(reason, true);
            return;
        }
        if (!targetButton.hasAttribute("data-editable")) {
            say("Für diese Zone fehlt das Recht, Geräte zuzuordnen.", true);
            return;
        }
        say("", false);
        if (targetButton.hasAttribute("data-draft")) {
            addDraft(endpointId, targetId);
            return;
        }
        post(root.dataset.connectUrl, { endpoint_id: endpointId, target_id: targetId }).then(function (result) {
            if (result.ok) {
                window.location.reload();
            } else {
                say(result.reason, true);
            }
        });
    }

    function cancel() {
        armed = null;
        if (dragging) {
            dragging.line.remove();
            dragging = null;
        }
        unmark();
    }

    // ---- Ereignisse -----------------------------------------------------------------------

    sources.forEach(function (button, id) {
        button.addEventListener("pointerdown", function (event) {
            if (event.button !== 0) return;
            event.preventDefault();
            say("", false);
            mark(id);
            const line = makePath("tc-map-line tc-map-line-live");
            dragging = { id: id, line: line, moved: false };
            button.setPointerCapture(event.pointerId);
            const start = centre(button);
            line.setAttribute("d", curve(start, start));
        });
        button.addEventListener("pointermove", function (event) {
            if (!dragging || dragging.id !== id) return;
            dragging.moved = true;
            const base = root.getBoundingClientRect();
            line_to(dragging.line, button, { x: event.clientX - base.left, y: event.clientY - base.top });
        });
        button.addEventListener("pointerup", function (event) {
            if (!dragging || dragging.id !== id) return;
            const moved = dragging.moved;
            dragging.line.remove();
            dragging = null;
            if (!moved) {
                // Ein Tippen ohne Ziehen: kein Loslassen auf einem Ziel, das `click` unten schaltet um.
                return;
            }
            suppressClick = true;
            setTimeout(function () { suppressClick = false; }, 0);
            const under = document.elementFromPoint(event.clientX, event.clientY);
            const targetButton = under && under.closest ? under.closest("[data-target]") : null;
            unmark();
            if (targetButton) connect(id, targetButton);
        });
        button.addEventListener("pointercancel", cancel);
        button.addEventListener("click", function () {
            if (suppressClick) return;
            if (armed === id) {
                cancel();
                say("Auswahl aufgehoben.", false);
                return;
            }
            armed = id;
            mark(id);
            say("Endpunkt gewählt. Jetzt ein Ziel wählen, Esc bricht ab.", false);
        });
    });

    function line_to(path, button, point) {
        path.setAttribute("d", curve(centre(button), point));
    }

    targets.forEach(function (button) {
        button.addEventListener("click", function () {
            if (!armed) return;
            const id = armed;
            armed = null;
            unmark();
            connect(id, button);
        });
    });

    document.querySelectorAll("[data-disconnect]").forEach(function (button) {
        button.addEventListener("click", function () {
            post(root.dataset.disconnectUrl, { connection_id: button.dataset.disconnect }).then(function (result) {
                if (result.ok) {
                    window.location.reload();
                } else {
                    say(result.reason, true);
                }
            });
        });
    });

    if (window.ResizeObserver) new ResizeObserver(redraw).observe(root);
    redraw();
    current = { cancel: cancel, redraw: redraw, say: say, active: function () { return Boolean(armed || dragging); } };
    }

    function start() {
        const root = document.querySelector("[data-map]");
        if (!root || !root.dataset.payload || wired.has(root)) return;
        wired.add(root);
        setUp(root, JSON.parse(root.dataset.payload));
    }

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && current && current.active()) {
            current.cancel();
            current.say("Abgebrochen.", false);
        }
    });
    window.addEventListener("resize", function () { if (current) current.redraw(); });

    if (document.readyState !== "loading") start();
    document.addEventListener("DOMContentLoaded", start);
    document.addEventListener("htmx:load", start);
    document.addEventListener("htmx:historyRestore", start);
})();
