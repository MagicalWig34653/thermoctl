/* Zuordnungskarte (Entwurf): frei verschiebbare Knoten, Linien folgen live.
 *
 * Dieses Skript entscheidet nichts darüber, was zusammenpasst. Die Seite liefert in
 * `data-payload` für jeden Anschluss die abgelehnten Gegenstücke samt Begründung
 * (`rejections`, `reasons`), erzeugt von `domain/mapping_map.py`. Was dort fehlt, passt. Der Server
 * prüft beim Speichern noch einmal mit derselben Funktion. Das Skript kennt nur Anschluss-
 * Kennungen und die Richtung (`data-dir`: "out" liefert, "in" nimmt an), weil es die Seite
 * zeichnet.
 *
 * Fläche: Knoten stehen in Weltkoordinaten; die ganze Welt wird verschoben (Hintergrund ziehen)
 * und gezoomt (Mausrad, Knöpfe, Zwei-Finger-Geste). Positionen, Ansicht, eingeklappte Karten
 * und der Schalter „technische Werte" werden pro Browser in localStorage gemerkt; ohne
 * localStorage startet die Seite mit der automatischen Anordnung.
 *
 * Bedienung: von einem Anschluss zu einem anderen ziehen (von Ausgang zu Eingang oder
 * umgekehrt), oder Anschluss anklicken/Enter und danach den zweiten (Tastatur, Touch). Esc
 * bricht ab. Ein Loslassen auf der falschen Seite oder auf einem unpassenden Anschluss
 * verbindet nichts und nennt den Grund.
 *
 * Gespeichert wird nur zwischen Zone und Gerät (POST an die Domänenfunktionen, danach
 * Neuladen). Linien zwischen zwei Geräten gibt es im Datenmodell nicht: Sie werden nur
 * gezeichnet und in der Entwurfsliste geführt.
 *
 * Entfernen: Eine Linie anklicken, antippen oder mit der Tastatur anwählen (Tab, Enter) markiert sie
 * und zeigt in ihrer Mitte „× Entfernen". Entf/Backspace auf der fokussierten Linie entfernt sie
 * ebenso. Gespeicherte Verbindungen löst der Server (POST /devices/map/disconnect); schlägt das fehl,
 * bleibt die Linie und die Meldung erscheint unten. Entwürfe werden nur lokal entfernt.
 */
(function () {
    "use strict";

    // Schon verdrahtete Karten als WeakSet, nicht als Attribut: htmx stellt beim Zurückgehen
    // eine Momentaufnahme des Markups wieder her, aber keine Ereignisbehandler (vgl. assignment.js).
    const wired = new WeakSet();
    let current = null;

    const STORAGE_KEY = "thermoctl.mappingmap.v1";
    const MIN_ZOOM = 0.25;
    const MAX_ZOOM = 2;
    const DRAG_THRESHOLD = 4;

    function setUp(root, data) {
        const SVG = "http://www.w3.org/2000/svg";
        const viewport = root.querySelector("[data-map-viewport]");
        const world = root.querySelector("[data-map-world]");
        const svg = world.querySelector("svg.tc-map-lines");
        const nodes = new Map();   // Knotenkennung -> Element
        const ports = new Map();   // Anschlusskennung -> Element
        root.querySelectorAll("[data-node].tc-node").forEach(function (n) { nodes.set(n.dataset.node, n); });
        root.querySelectorAll("[data-port]").forEach(function (p) { ports.set(p.dataset.port, p); });

        const state = { view: { x: 0, y: 0, z: 1 }, pos: {}, collapsed: {}, technical: false };
        const drafts = [];
        let armed = null;       // Anschlusskennung im Tastatur-/Tipp-Modus
        let dragging = null;    // { id, line, moved, x0, y0 }
        let suppressClick = false;
        let frame = 0;
        let selected = null;    // Kennung der markierten Linie (gespeichert: Verbindung, Entwurf: "draft:...")
        let removing = false;   // eine Löschanfrage läuft
        const lineInfo = new Map();   // Linienkennung -> { entry, mid }

        // ---- Browser-Speicher (optional) -----------------------------------------------------

        function load() {
            try {
                const raw = window.localStorage.getItem(STORAGE_KEY);
                if (!raw) return false;
                const saved = JSON.parse(raw);
                if (saved && typeof saved === "object") {
                    if (saved.view && isFinite(saved.view.x) && isFinite(saved.view.y) && isFinite(saved.view.z)) state.view = saved.view;
                    state.pos = saved.pos && typeof saved.pos === "object" ? saved.pos : {};
                    state.collapsed = saved.collapsed && typeof saved.collapsed === "object" ? saved.collapsed : {};
                    state.technical = Boolean(saved.technical);
                    return true;
                }
            } catch (e) { /* kein Speicher: Standardansicht */ }
            return false;
        }

        function save() {
            try {
                window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
            } catch (e) { /* ohne Speicher funktioniert die Seite trotzdem */ }
        }

        // ---- Ausgabe ---------------------------------------------------------------------------

        const FLASH_KEY = "thermoctl.mappingmap.flash";

        // Kurze Bestätigung unten, nicht modal; verschwindet von selbst.
        let noticeTimer = 0;
        function notice(text) {
            say(text, false);
            const box = document.querySelector("[data-map-notice]");
            if (!box) return;
            box.textContent = text;
            box.hidden = false;
            window.clearTimeout(noticeTimer);
            noticeTimer = window.setTimeout(function () { box.hidden = true; }, 8000);
        }

        function say(text, isError) {
            const status = document.querySelector("[data-map-status]");
            const error = document.querySelector("[data-map-error]");
            if (status) status.textContent = text || "";
            if (error) {
                error.hidden = !(isError && text);
                error.textContent = isError ? text : "";
            }
            const box = document.querySelector("[data-map-notice]");
            if (box && isError) box.hidden = true;
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
                        return { ok: response.ok && json.ok === true, reason: json.reason || json.detail || "Das Speichern ist fehlgeschlagen.", message: json.message || "" };
                    });
                }, function () {
                    return { ok: false, reason: "Der Server ist nicht erreichbar. Es wurde nichts geändert.", message: "" };
                });
        }

        // ---- Ansicht und Koordinaten -----------------------------------------------------------

        function clampZoom(z) { return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z)); }

        function applyView() {
            const v = state.view;
            world.style.transform = "translate(" + v.x + "px," + v.y + "px) scale(" + v.z + ")";
            root.dataset.view = JSON.stringify({ x: Math.round(v.x * 100) / 100, y: Math.round(v.y * 100) / 100, z: Math.round(v.z * 1000) / 1000 });
        }

        // Bildschirm -> Welt (unabhängig davon, was gerade gezeichnet ist).
        function toWorld(clientX, clientY) {
            const box = viewport.getBoundingClientRect();
            return { x: (clientX - box.left - state.view.x) / state.view.z, y: (clientY - box.top - state.view.y) / state.view.z };
        }

        function zoomAt(clientX, clientY, factor) {
            const box = viewport.getBoundingClientRect();
            const z = clampZoom(state.view.z * factor);
            const px = clientX - box.left;
            const py = clientY - box.top;
            state.view.x = px - (px - state.view.x) * (z / state.view.z);
            state.view.y = py - (py - state.view.y) * (z / state.view.z);
            state.view.z = z;
            applyView();
            save();
        }

        function isRendered(node) { return node.getClientRects().length > 0; }

        function nodePos(node) {
            return { x: parseFloat(node.style.left) || 0, y: parseFloat(node.style.top) || 0 };
        }

        function place(node, x, y) {
            node.style.left = x + "px";
            node.style.top = y + "px";
            state.pos[node.dataset.node] = { x: x, y: y };
        }

        function fit() {
            const list = Array.from(nodes.values()).filter(isRendered);
            if (!list.length) return;
            let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
            list.forEach(function (n) {
                const p = nodePos(n);
                x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y);
                x1 = Math.max(x1, p.x + n.offsetWidth); y1 = Math.max(y1, p.y + n.offsetHeight);
            });
            const box = viewport.getBoundingClientRect();
            const z = clampZoom(Math.min((box.width - 40) / (x1 - x0), (box.height - 40) / (y1 - y0), 1.2));
            state.view.z = z;
            state.view.x = (box.width - (x1 - x0) * z) / 2 - x0 * z;
            state.view.y = (box.height - (y1 - y0) * z) / 2 - y0 * z;
            applyView();
            save();
        }

        // Anordnung nach Datenfluss: Geräte mit Ausgängen (Fühler, Kontakte) links, Zonen in der
        // Mitte, reine Empfänger (Ventile, Schalter) rechts -- so läuft fast jede Linie nach rechts.
        function group(node) {
            if (node.dataset.kind === "zone") return "mid";
            const hasOut = node.querySelector('[data-dir="out"]:not([data-technical])');
            const hasIn = node.querySelector('[data-dir="in"]:not([data-technical])');
            return hasIn && !hasOut ? "right" : "left";
        }

        function autoLayout() {
            const sample = Array.from(nodes.values()).find(isRendered);
            const width = sample ? sample.offsetWidth : 320;
            const gapX = 150, gapSub = 40, gapY = 16, columnHeight = 900;
            const groups = { left: [], mid: [], right: [] };
            nodes.forEach(function (n) {
                if (isRendered(n)) groups[group(n)].push(n);
            });
            function fill(list, baseX, step) {
                let y = 0, sub = 0;
                list.forEach(function (n) {
                    const h = n.offsetHeight;
                    if (y > 0 && y + h > columnHeight) { sub += 1; y = 0; }
                    place(n, baseX + step * sub * (width + gapSub), y);
                    y += h + gapY;
                });
                return sub + 1;
            }
            fill(groups.left, 0, -1);
            const midCols = fill(groups.mid, width + gapX, 1);
            fill(groups.right, width + gapX + (midCols - 1) * (width + gapSub) + width + gapX, 1);
        }

        // Karten ohne gemerkte Position (neu, oder eben erst sichtbar): unter alles Vorhandene.
        function layoutMissing() {
            const missing = [];
            const placed = [];
            nodes.forEach(function (n) {
                if (!isRendered(n)) return;
                (state.pos[n.dataset.node] ? placed : missing).push(n);
            });
            if (!missing.length) return;
            if (!placed.length) { autoLayout(); return; }
            let bottom = -Infinity, left = Infinity;
            placed.forEach(function (n) {
                const p = nodePos(n);
                bottom = Math.max(bottom, p.y + n.offsetHeight);
                left = Math.min(left, p.x);
            });
            let x = left;
            missing.forEach(function (n) {
                place(n, x, bottom + 40);
                x += n.offsetWidth + 40;
            });
        }

        // ---- Linien ----------------------------------------------------------------------------

        // Mittelpunkt eines Anschlusses in Weltkoordinaten. Ist der Anschluss verborgen (Karte
        // eingeklappt), endet die Linie stattdessen an der Kopfzeile auf der Seite des Anschlusses.
        function anchor(portId) {
            const el = ports.get(portId);
            if (!el) return null;
            const node = nodes.get(el.dataset.owner);
            if (!node || !isRendered(node)) return null;
            let box = el.getBoundingClientRect();
            let cx = box.left + box.width / 2;
            let cy = box.top + box.height / 2;
            if (!box.width) {
                box = node.querySelector("[data-node-handle]").getBoundingClientRect();
                cx = el.dataset.dir === "out" ? box.right : box.left;
                cy = box.top + box.height / 2;
            }
            return toWorld(cx, cy);
        }

        function curve(from, to) {
            const dx = Math.max(40, Math.abs(to.x - from.x) / 2);
            return "M" + from.x + "," + from.y + " C" + (from.x + dx) + "," + from.y + " " + (to.x - dx) + "," + to.y + " " + to.x + "," + to.y;
        }

        function kindClass(portId) {
            const el = ports.get(portId);
            const match = el ? Array.from(el.classList).find(function (c) { return c.startsWith("tc-kind-"); }) : null;
            return match || "tc-kind-none";
        }

        function makePath(className) {
            const path = document.createElementNS(SVG, "path");
            path.setAttribute("class", className);
            svg.appendChild(path);
            return path;
        }

        function redrawNow() {
            frame = 0;
            svg.querySelectorAll("path[data-permanent]").forEach(function (p) { p.remove(); });
            lineInfo.clear();
            const entries = data.connections.map(function (c) { return { id: c.id, out: c.out, in: c.in, draft: false, editable: c.editable, title: c.title }; })
                .concat(drafts.map(function (d) { return { id: d.id, out: d.out, in: d.in, draft: true, editable: true, title: d.title }; }));
            entries.forEach(function (entry) {
                if (!entry.out || !entry.in) return;
                const from = anchor(entry.out);
                const to = anchor(entry.in);
                if (!from || !to) return;
                const path = makePath("tc-map-line " + kindClass(entry.out) + (entry.draft ? " tc-map-line-draft" : ""));
                path.setAttribute("data-permanent", "");
                path.setAttribute("data-line", entry.id);
                path.setAttribute("data-out", entry.out);
                path.setAttribute("data-in", entry.in);
                path.setAttribute("d", curve(from, to));
                // Mittelpunkt einer Bézierkurve mit diesen Stützpunkten (t = 0,5) ist der der Endpunkte.
                lineInfo.set(entry.id, { entry: entry, mid: { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 } });
                const hit = makePath("tc-map-hit");
                hit.setAttribute("data-permanent", "");
                hit.setAttribute("data-hit", entry.id);
                hit.setAttribute("d", curve(from, to));
                hit.setAttribute("tabindex", "0");
                hit.setAttribute("role", "button");
                hit.setAttribute("aria-label", "Verbindung " + entry.title + (entry.draft ? " (Entwurf)" : "") + ". Markieren; Entf entfernt sie.");
                hit.addEventListener("pointerdown", function (event) { event.stopPropagation(); });
                hit.addEventListener("click", function (event) { event.stopPropagation(); select(entry.id); });
                // Tastaturfokus markiert; ein Mausklick markiert über `click` (der Fokus durch die Maus tut es nicht,
                // damit die Markierung nicht schon beim Drücken erscheint).
                hit.addEventListener("focus", function () { if (hit.matches(":focus-visible")) select(entry.id); });
                hit.addEventListener("keydown", function (event) {
                    if (event.key === "Delete" || event.key === "Backspace") {
                        event.preventDefault();
                        removeLine(entry.id);
                    } else if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        select(entry.id);
                    }
                });
            });
            if (selected && !lineInfo.has(selected)) selected = null;
            applySelection();
        }

        // ---- Linie markieren und entfernen -----------------------------------------------------

        const remover = document.createElement("button");
        remover.type = "button";
        remover.className = "btn btn-sm btn-danger tc-line-remove";
        remover.textContent = "× Entfernen";
        remover.hidden = true;
        remover.addEventListener("pointerdown", function (event) { event.stopPropagation(); });
        remover.addEventListener("click", function (event) {
            event.stopPropagation();
            if (selected) removeLine(selected);
        });
        world.appendChild(remover);

        function applySelection() {
            svg.querySelectorAll("path.tc-map-line").forEach(function (p) {
                p.classList.toggle("tc-map-line-selected", p.getAttribute("data-line") === selected);
            });
            const info = selected ? lineInfo.get(selected) : null;
            if (!info || !info.entry.editable) {
                remover.hidden = true;
                return;
            }
            remover.hidden = false;
            remover.style.left = info.mid.x + "px";
            remover.style.top = info.mid.y + "px";
            remover.setAttribute("aria-label", "Verbindung " + info.entry.title + " entfernen");
        }

        function select(id) {
            if (selected === id) return;
            selected = id;
            applySelection();
        }

        function removeDraft(entry) {
            const index = drafts.findIndex(function (d) { return d.id === entry.id; });
            if (index < 0) return;
            drafts[index].item.remove();
            drafts.splice(index, 1);
            const empty = document.querySelector("[data-draft-empty]");
            if (!drafts.length && empty) empty.hidden = false;
            selected = null;
            redraw();
            notice("Entwurf entfernt: " + entry.title + ".");
        }

        // Gespeichert: der Server löst die Verbindung; erst bei Erfolg verschwindet die Linie (Neuladen).
        // Schlägt es fehl, bleibt sie stehen und der Grund erscheint unten.
        function removeStored(entry) {
            if (!entry.editable) {
                say("Für diese Zone fehlt das Recht, Geräte zuzuordnen.", true);
                return;
            }
            if (removing) return;
            removing = true;
            post(root.dataset.disconnectUrl, { connection_id: entry.id }).then(function (result) {
                removing = false;
                if (result.ok) {
                    try { window.sessionStorage.setItem(FLASH_KEY, result.message || "Verbindung gelöst."); } catch (e) { /* ohne Speicher keine Meldung nach dem Neuladen */ }
                    window.location.reload();
                } else {
                    say(result.reason, true);
                }
            });
        }

        function removeLine(id) {
            const info = lineInfo.get(id);
            if (!info) return;
            if (info.entry.draft) removeDraft(info.entry); else removeStored(info.entry);
        }

        function redraw() {
            if (!frame) frame = window.requestAnimationFrame(redrawNow);
        }

        // ---- Verträglichkeit anzeigen ----------------------------------------------------------

        function dirOf(id) { return ports.get(id).dataset.dir; }

        // Die Begründung, warum `a` nicht mit `b` verbunden werden darf, oder null.
        function reasonFor(a, b) {
            if (ports.get(b).hasAttribute("data-technical")) return data.technical_reason;
            if (dirOf(a) === dirOf(b)) return data.same_side[dirOf(a)];
            const index = (data.rejections[a] || {})[b];
            return index === undefined ? null : data.reasons[index];
        }

        function mark(id) {
            ports.forEach(function (el, other) {
                el.classList.toggle("tc-port-active", other === id);
                if (other === id) return;
                const reason = reasonFor(id, other);
                el.classList.toggle("tc-port-unfit", Boolean(reason));
                el.classList.toggle("tc-port-fit", !reason);
                if (reason) {
                    el.title = reason;
                    el.setAttribute("aria-disabled", "true");
                } else {
                    el.title = el.dataset.tip;
                    el.removeAttribute("aria-disabled");
                }
            });
        }

        function unmark() {
            ports.forEach(function (el) {
                el.classList.remove("tc-port-active", "tc-port-fit", "tc-port-unfit");
                el.title = el.dataset.tip;
                el.removeAttribute("aria-disabled");
            });
        }

        // ---- Verbinden -------------------------------------------------------------------------

        function addDraft(a, b) {
            const outId = dirOf(a) === "out" ? a : b;
            const inId = outId === a ? b : a;
            if (drafts.some(function (d) { return d.out === outId && d.in === inId; })) {
                say("Diese Verbindung steht schon im Entwurf.", true);
                return;
            }
            if (data.single[inId] && drafts.some(function (d) { return d.in === inId; })) {
                say(data.single[inId], true);
                return;
            }
            const empty = document.querySelector("[data-draft-empty]");
            if (empty) empty.hidden = true;
            const item = document.createElement("li");
            const title = ports.get(outId).getAttribute("aria-label").split(". ")[0] + " → " + ports.get(inId).getAttribute("aria-label").split(". ")[0];
            const draft = { id: "draft:" + outId + ">" + inId, out: outId, in: inId, title: title, item: item };
            drafts.push(draft);
            item.textContent = title + " ";
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "btn btn-sm btn-link p-0";
            remove.textContent = "Entwurf verwerfen";
            remove.addEventListener("click", function () { removeDraft(draft); });
            item.appendChild(remove);
            document.querySelector("[data-draft-list]").appendChild(item);
            say("Entwurf gezeichnet, nicht gespeichert.", false);
            redraw();
        }

        function connect(a, b) {
            if (a === b) return;
            const reason = reasonFor(a, b);
            if (reason) {
                say(reason, true);
                return;
            }
            const stored = [ports.get(a), ports.get(b)].find(function (el) { return el.hasAttribute("data-stored"); });
            if (!stored) {
                say("", false);
                addDraft(a, b);
                return;
            }
            if (!stored.hasAttribute("data-editable")) {
                say("Für diese Zone fehlt das Recht, Geräte zuzuordnen.", true);
                return;
            }
            say("", false);
            post(root.dataset.connectUrl, { from_id: a, to_id: b }).then(function (result) {
                if (result.ok) {
                    window.location.reload();
                } else {
                    say(result.reason, true);
                }
            });
        }

        function cancel() {
            armed = null;
            select(null);
            if (dragging) {
                dragging.line.remove();
                dragging = null;
            }
            unmark();
        }

        // ---- Anschlüsse: ziehen oder nacheinander wählen ----------------------------------------

        function liveCurve(id, point) {
            const here = anchor(id);
            if (!here) return "";
            return dirOf(id) === "out" ? curve(here, point) : curve(point, here);
        }

        ports.forEach(function (button, id) {
            button.addEventListener("pointerdown", function (event) {
                if (event.button !== 0) return;
                event.preventDefault();
                event.stopPropagation();
                if (button.hasAttribute("data-technical")) {
                    say(data.technical_reason, true);
                    return;
                }
                say("", false);
                mark(id);
                const line = makePath("tc-map-line tc-map-line-live " + kindClass(id));
                dragging = { id: id, line: line, moved: false, x0: event.clientX, y0: event.clientY };
                button.setPointerCapture(event.pointerId);
                const start = anchor(id);
                if (start) line.setAttribute("d", curve(start, start));
            });
            button.addEventListener("pointermove", function (event) {
                if (!dragging || dragging.id !== id) return;
                if (!dragging.moved && Math.hypot(event.clientX - dragging.x0, event.clientY - dragging.y0) < DRAG_THRESHOLD) return;
                dragging.moved = true;
                dragging.line.setAttribute("d", liveCurve(id, toWorld(event.clientX, event.clientY)));
            });
            button.addEventListener("pointerup", function (event) {
                if (!dragging || dragging.id !== id) return;
                const moved = dragging.moved;
                dragging.line.remove();
                dragging = null;
                if (!moved) {
                    // Ein Tippen ohne Ziehen: das `click` unten schaltet um.
                    unmark();
                    return;
                }
                suppressClick = true;
                setTimeout(function () { suppressClick = false; }, 0);
                const under = document.elementFromPoint(event.clientX, event.clientY);
                const other = under && under.closest ? under.closest("[data-port]") : null;
                unmark();
                if (other && other !== button) connect(id, other.dataset.port);
            });
            button.addEventListener("pointercancel", cancel);
            button.addEventListener("click", function () {
                if (suppressClick || button.hasAttribute("data-technical")) return;
                if (armed === id) {
                    cancel();
                    say("Auswahl aufgehoben.", false);
                    return;
                }
                if (armed) {
                    const first = armed;
                    armed = null;
                    unmark();
                    connect(first, id);
                    return;
                }
                armed = id;
                mark(id);
                say("Anschluss gewählt. Jetzt den zweiten wählen, Esc bricht ab.", false);
            });
        });

        // ---- Knoten verschieben, einklappen -----------------------------------------------------

        nodes.forEach(function (node, id) {
            const head = node.querySelector("[data-node-handle]");
            let drag = null;
            head.addEventListener("pointerdown", function (event) {
                if (event.button !== 0 || (event.target.closest && event.target.closest("button"))) return;
                event.stopPropagation();
                const p = nodePos(node);
                drag = { x: event.clientX, y: event.clientY, nx: p.x, ny: p.y };
                head.setPointerCapture(event.pointerId);
                node.classList.add("tc-node-dragging");
            });
            head.addEventListener("pointermove", function (event) {
                if (!drag) return;
                place(node, drag.nx + (event.clientX - drag.x) / state.view.z, drag.ny + (event.clientY - drag.y) / state.view.z);
                redraw();
            });
            function stop() {
                if (!drag) return;
                drag = null;
                node.classList.remove("tc-node-dragging");
                save();
            }
            head.addEventListener("pointerup", stop);
            head.addEventListener("pointercancel", stop);
            head.addEventListener("keydown", function (event) {
                const step = event.shiftKey ? 50 : 10;
                const move = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
                if (!move) return;
                event.preventDefault();
                const p = nodePos(node);
                place(node, p.x + move[0], p.y + move[1]);
                redraw();
                save();
            });
            const fold = node.querySelector("[data-fold]");
            fold.hidden = false;
            fold.addEventListener("click", function () { setCollapsed(node, !node.hasAttribute("data-collapsed")); redraw(); save(); });
            fold.addEventListener("pointerdown", function (event) { event.stopPropagation(); });
        });

        function setCollapsed(node, collapsed) {
            const id = node.dataset.node;
            node.toggleAttribute("data-collapsed", collapsed);
            const fold = node.querySelector("[data-fold]");
            fold.textContent = collapsed ? "+" : "−";
            fold.setAttribute("aria-expanded", collapsed ? "false" : "true");
            fold.setAttribute("aria-label", "Karte " + node.dataset.name + (collapsed ? " ausklappen" : " einklappen"));
            if (collapsed) state.collapsed[id] = true; else delete state.collapsed[id];
        }

        // ---- Hintergrund: schieben und zoomen (Maus, Touch, Zwei-Finger-Geste) ---------------------

        const touches = new Map();
        let panning = null;
        let pinch = 0;
        viewport.addEventListener("pointerdown", function (event) {
            if (event.button !== 0 || (event.target.closest && event.target.closest(".tc-node, .tc-line-remove, .tc-map-hit"))) return;
            touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
            viewport.setPointerCapture(event.pointerId);
            if (touches.size === 1) {
                panning = { x: event.clientX, y: event.clientY, vx: state.view.x, vy: state.view.y };
                viewport.classList.add("tc-map-panning");
            } else if (touches.size === 2) {
                panning = null;
                const pts = Array.from(touches.values());
                pinch = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
            }
        });
        viewport.addEventListener("pointermove", function (event) {
            if (!touches.has(event.pointerId)) return;
            touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
            if (touches.size === 2 && pinch) {
                const pts = Array.from(touches.values());
                const distance = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
                zoomAt((pts[0].x + pts[1].x) / 2, (pts[0].y + pts[1].y) / 2, distance / pinch);
                pinch = distance;
                redraw();
            } else if (panning && touches.size === 1) {
                state.view.x = panning.vx + (event.clientX - panning.x);
                state.view.y = panning.vy + (event.clientY - panning.y);
                applyView();
            }
        });
        function endPan(event) {
            if (!touches.has(event.pointerId)) return;
            touches.delete(event.pointerId);
            pinch = 0;
            if (!touches.size) {
                panning = null;
                viewport.classList.remove("tc-map-panning");
                save();
            }
        }
        viewport.addEventListener("click", function (event) {
            if (event.target.closest && event.target.closest(".tc-node, .tc-line-remove, .tc-map-hit")) return;
            select(null);
        });
        viewport.addEventListener("pointerup", endPan);
        viewport.addEventListener("pointercancel", endPan);
        viewport.addEventListener("wheel", function (event) {
            event.preventDefault();
            zoomAt(event.clientX, event.clientY, Math.exp(-event.deltaY * 0.0015));
            redraw();
        }, { passive: false });

        // ---- Werkzeugleiste -----------------------------------------------------------------------

        function centreZoom(factor) {
            const box = viewport.getBoundingClientRect();
            zoomAt(box.left + box.width / 2, box.top + box.height / 2, factor);
            redraw();
        }
        const toolbar = document.querySelector("[data-map-toolbar]");
        toolbar.hidden = false;
        toolbar.querySelectorAll("[data-map-zoom]").forEach(function (b) {
            b.addEventListener("click", function () { centreZoom(b.dataset.mapZoom === "in" ? 1.25 : 0.8); });
        });
        toolbar.querySelector("[data-map-fit]").addEventListener("click", function () { fit(); redraw(); });
        toolbar.querySelector("[data-map-layout]").addEventListener("click", function () {
            autoLayout();
            fit();
            redraw();
            save();
            say("Karten automatisch angeordnet.", false);
        });
        toolbar.querySelectorAll("[data-map-fold]").forEach(function (b) {
            b.addEventListener("click", function () {
                nodes.forEach(function (n) { setCollapsed(n, b.dataset.mapFold === "collapse"); });
                redraw();
                save();
            });
        });
        const technical = toolbar.querySelector("[data-map-technical]");
        function setTechnical(on) {
            state.technical = on;
            technical.checked = on;
            root.classList.toggle("tc-map-show-technical", on);
            layoutMissing();
            redraw();
            save();
        }
        technical.addEventListener("change", function () { setTechnical(technical.checked); });
        const filter = toolbar.querySelector("[data-map-filter]");
        filter.addEventListener("input", function () {
            const q = filter.value.trim().toLowerCase();
            nodes.forEach(function (n) {
                const text = (n.dataset.name + " " + Array.from(n.querySelectorAll(".tc-plabel")).map(function (l) { return l.textContent; }).join(" ")).toLowerCase();
                n.hidden = Boolean(q) && text.indexOf(q) < 0;
            });
            layoutMissing();
            redraw();
        });

        document.querySelectorAll("[data-map-hint]").forEach(function (e) { e.hidden = false; });
        document.querySelectorAll("[data-disconnect]").forEach(function (e) { e.hidden = false; });
        document.querySelectorAll("[data-no-js]").forEach(function (e) { e.hidden = true; });
        document.querySelectorAll("[data-disconnect]").forEach(function (button) {
            button.addEventListener("click", function () {
                const stored = data.connections.find(function (c) { return c.id === button.dataset.disconnect; });
                removeStored({ id: button.dataset.disconnect, editable: true, title: stored ? stored.title : "" });
            });
        });

        // ---- Start ---------------------------------------------------------------------------------

        root.classList.add("tc-map-js");
        const restored = load();
        nodes.forEach(function (node, id) {
            const saved = state.pos[id];
            if (saved && isFinite(saved.x) && isFinite(saved.y)) {
                node.style.left = saved.x + "px";
                node.style.top = saved.y + "px";
            } else {
                delete state.pos[id];
            }
            setCollapsed(node, Boolean(state.collapsed[id]));
        });
        root.classList.toggle("tc-map-show-technical", state.technical);
        technical.checked = state.technical;
        if (restored && Object.keys(state.pos).length) {
            layoutMissing();
            applyView();
        } else {
            autoLayout();
            fit();
        }
        save();
        window.addEventListener("resize", redraw);
        redraw();
        try {
            const flash = window.sessionStorage.getItem(FLASH_KEY);
            if (flash) {
                window.sessionStorage.removeItem(FLASH_KEY);
                notice(flash);
            }
        } catch (e) { /* ohne Speicher keine Meldung nach dem Neuladen */ }
        current = {
            cancel: cancel,
            redraw: redraw,
            say: say,
            active: function () { return Boolean(armed || dragging || selected); }
        };
        root.dataset.ready = "1";
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

    if (document.readyState !== "loading") start();
    document.addEventListener("DOMContentLoaded", start);
    document.addEventListener("htmx:load", start);
    document.addEventListener("htmx:historyRestore", start);
})();
