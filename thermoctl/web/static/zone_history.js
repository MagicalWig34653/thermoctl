/* Die Karte bleibt beim HTMX-Tausch bedienbar; iOS nutzt das Overlay. */
(function () {
    "use strict";
    let restoreExpandedId = null;
    function close() {
        const card = document.querySelector(".tc-history-expanded");
        if (!card) return;
        card.classList.remove("tc-history-expanded");
        document.body.classList.remove("tc-history-body-open");
        if (document.fullscreenElement) document.exitFullscreen();
    }
    document.addEventListener("htmx:beforeSwap", function (event) {
        const target = event.detail.target;
        restoreExpandedId = target.classList.contains("tc-history") &&
            target.classList.contains("tc-history-expanded") ? target.id : null;
    });
    document.addEventListener("htmx:afterSettle", function () {
        if (restoreExpandedId === null) return;
        const card = document.getElementById(restoreExpandedId);
        restoreExpandedId = null;
        if (card) {
            card.classList.add("tc-history-expanded");
            document.body.classList.add("tc-history-body-open");
        }
    });
    document.addEventListener("click", function (event) {
        const open = event.target.closest("[data-history-open]");
        if (open) {
            const card = open.closest(".tc-history");
            card.classList.add("tc-history-expanded");
            document.body.classList.add("tc-history-body-open");
            // Das Dokument bleibt beim HTMX-Tausch bestehen, die Karte selbst nicht.
            if (document.documentElement.requestFullscreen) {
                document.documentElement.requestFullscreen().catch(function () {});
            }
        }
        if (event.target.closest("[data-history-close]")) close();
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") close();
    });
    document.addEventListener("fullscreenchange", function () {
        if (!document.fullscreenElement) close();
    });
})();

/* Anzeige am Mauszeiger (Maus, Finger, Pfeiltasten).
 *
 * Gezeigt wird nur, was die Daten der Karte belegen (`data-history-data`, Format siehe
 * `hover_payload` in thermoctl/domain/zone_history_chart.py): das Stück, in dem der Zeitpunkt
 * liegt -- oder "keine Daten", wenn dort keine Zeile liegt. Nichts wird zwischen Stücken
 * interpoliert, nichts über eine Lücke hinweg angezeigt. Texte nur über textContent.
 */
(function () {
    "use strict";
    const SVG = "http://www.w3.org/2000/svg";
    const STEP_FRACTION = 0.01;
    const models = new WeakMap();
    // Beschriftungen der Anlagensicht. Die Wohnungssicht schickt abweichende Texte als `l` in den
    // Daten (`Wording.hover` in der Domäne); was dort fehlt, bleibt bei diesen Vorgaben.
    const DEFAULT_LABELS = {
        measured: "Ist", scheduled: "Zeitplan-Soll", effective: "Wirksamer Soll",
        mean: "Mittelwert aus {n} Messungen (Abschnitt bis zu {bucket})",
        lowest: "kleinster", highest: "größter", unit: "K", unknown: "unbekannt",
        partly: "(teils unbekannt)", solar_unknown: "Absenkung unbekannt"
    };

    function parse(text) {
        let raw;
        try { raw = JSON.parse(text); } catch (error) { return null; }
        if (!raw || raw.v !== 1 || !Array.isArray(raw.c)) return null;
        const model = {
            start: raw.s, span: raw.e, bucket: raw.b, formatter: null,
            labels: Object.assign({}, DEFAULT_LABELS, raw.l || {}),
            from: [], to: [], count: [], mean: [], low: [], high: [], heat: [],
            effective: [], scheduled: [], solar: []
        };
        let at = 0;
        for (let i = 0; i + 6 < raw.c.length; i += 7) {
            const from = at + raw.c[i], to = from + raw.c[i + 1];
            at = to;
            model.from.push(from);
            model.to.push(to);
            model.count.push(raw.c[i + 2]);
            model.mean.push(raw.c[i + 3]);
            model.low.push(raw.c[i + 3] + raw.c[i + 4]);
            model.high.push(raw.c[i + 3] + raw.c[i + 5]);
            model.heat.push(raw.c[i + 6]);
        }
        [["ef", "effective"], ["sc", "scheduled"], ["so", "solar"]].forEach(function (pair) {
            (raw[pair[0]] || []).forEach(function (run) {
                for (let k = 0; k < run[0]; k++) model[pair[1]].push(run);
            });
        });
        try {
            model.formatter = new Intl.DateTimeFormat("de-DE", {
                timeZone: raw.tz, weekday: "short", day: "2-digit", month: "2-digit",
                hour: "2-digit", minute: "2-digit", hourCycle: "h23"
            });
        } catch (error) {
            model.formatter = new Intl.DateTimeFormat("de-DE", {
                timeZone: "UTC", weekday: "short", day: "2-digit", month: "2-digit",
                hour: "2-digit", minute: "2-digit", hourCycle: "h23"
            });
        }
        return model;
    }

    function modelOf(plot) {
        const text = plot.dataset.historyData;
        if (!text) return null;
        // Neu lesen, sobald der Text ein anderer ist; ein HTMX-Tausch bringt ohnehin neue Knoten.
        const cached = models.get(plot);
        if (!cached || cached.text !== text) models.set(plot, { text: text, model: parse(text) });
        return models.get(plot).model;
    }

    // Ortszeit in der eingestellten Zeitzone, auch über die Zeitumstellung hinweg (Intl).
    function when(model, seconds) {
        const parts = {};
        model.formatter.formatToParts(new Date((model.start + seconds) * 1000)).forEach(function (p) {
            parts[p.type] = p.value;
        });
        return {
            day: parts.day + "." + parts.month + ".",
            weekday: parts.weekday.replace(".", ""),
            clock: parts.hour + ":" + parts.minute
        };
    }

    const number = function (tenths) { return (tenths / 10).toFixed(1).replace(".", ","); };
    const degrees = function (tenths) { return number(tenths) + " °C"; };

    function durationText(seconds) {
        if (seconds < 120) return Math.round(seconds) + " Sekunden";
        if (seconds < 7200) return Math.round(seconds / 60) + " Minuten";
        return number(Math.round(seconds / 360)) + " Stunden";
    }

    function spanText(model, run, unit) {
        if (run[1] === null) return model.labels.unknown;
        const text = run[1] === run[2] ? number(run[1]) + " " + unit
            : number(run[1]) + " bis " + number(run[2]) + " " + unit;
        return run[3] ? text + " " + model.labels.partly : text;
    }

    function solarText(model, run) {
        const unit = model.labels.unit;
        if (run[1] === null) return model.labels.solar_unknown;
        let text;
        if (run[2] === 0) text = "keine Sonnenabsenkung";
        else if (run[1] === run[2]) text = "Sonnenabsenkung " + number(run[2]) + " " + unit;
        else text = "Sonnenabsenkung " + number(run[1]) + " bis " + number(run[2]) + " " + unit;
        return run[3] ? text + " " + model.labels.partly : text;
    }

    function find(model, seconds) {
        // Das letzte Stück, das vor oder bei `seconds` beginnt.
        let low = 0, high = model.from.length - 1, found = -1;
        while (low <= high) {
            const mid = (low + high) >> 1;
            if (model.from[mid] <= seconds) { found = mid; low = mid + 1; } else { high = mid - 1; }
        }
        return found;
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function describe(model, seconds) {
        const now = when(model, seconds);
        const tip = element("div");
        tip.appendChild(element("div", "tc-history-tip-head",
            now.weekday + " " + now.day + " " + now.clock + " Uhr"));
        // `before`: das letzte Stück, das vor dem Zeitpunkt beginnt (-1: keines). Liegt der
        // Zeitpunkt hinter dessen Ende, ist er in einer Lücke und `index` bleibt -1. Das Ende
        // des allerletzten Stücks zählt noch dazu, sonst wäre der rechte Rand stets "keine Daten".
        const before = find(model, seconds);
        const inside = before >= 0 && (seconds < model.to[before] ||
            (before === model.from.length - 1 && seconds === model.to[before]));
        const index = inside ? before : -1;
        const grid = element("dl", "tc-history-tip-grid");
        const row = function (key, value) {
            grid.appendChild(element("dt", "", key));
            grid.appendChild(element("dd", "", value));
        };
        const note = function (text) {
            grid.appendChild(element("dd", "tc-history-tip-note tc-history-tip-wide", text));
        };
        if (index >= 0) {
            const n = model.count[index];
            const labels = model.labels;
            row(labels.measured, n === 0 ? "kein Messwert" : degrees(model.mean[index]));
            if (n === 1) {
                note("eine Messung");
            } else if (n > 1) {
                note(labels.mean.replace("{n}", n).replace("{bucket}", model.bucket) +
                    (model.low[index] === model.high[index] ? "" :
                        "; " + labels.lowest + " " + degrees(model.low[index]) +
                        ", " + labels.highest + " " + degrees(model.high[index])));
            }
            row(labels.scheduled, spanText(model, model.scheduled[index], "°C"));
            row(labels.effective, spanText(model, model.effective[index], "°C"));
            row("Heizanforderung", model.heat[index] === 100 ? "ja" : model.heat[index] === 0 ? "nein" :
                model.heat[index] + " % der Zeit");
            const solar = element("dd", "tc-history-tip-wide", solarText(model, model.solar[index]));
            grid.appendChild(solar);
        } else {
            row(model.labels.measured, "keine Daten");
            const after = before + 1 < model.from.length ? before + 1 : -1;
            if (before < 0) {
                note("Vor dem ersten Eintrag dieses Zeitraums.");
            } else if (after < 0) {
                note("Nach dem letzten Eintrag um " + when(model, model.to[before]).clock + " Uhr.");
            } else {
                const gap = model.from[after] - model.to[before];
                note("Kein Eintrag von " + when(model, model.to[before]).clock + " bis " +
                    when(model, model.from[after]).clock + " Uhr (" + durationText(gap) + ").");
            }
        }
        tip.appendChild(grid);
        return { index: index, node: tip };
    }

    function visibleSvg(plot) {
        return Array.from(plot.querySelectorAll("svg")).find(function (svg) {
            return svg.getClientRects().length > 0;
        }) || null;
    }

    function markerOf(svg) {
        let marker = svg.querySelector(".tc-history-marker");
        if (marker) return marker;
        marker = document.createElementNS(SVG, "g");
        marker.setAttribute("class", "tc-history-marker");
        marker.setAttribute("aria-hidden", "true");
        marker.setAttribute("display", "none");
        const line = document.createElementNS(SVG, "line");
        line.setAttribute("class", "tc-history-marker-line");
        marker.appendChild(line);
        ["actual", "scheduled", "effective"].forEach(function (kind) {
            const dot = document.createElementNS(SVG, "circle");
            dot.setAttribute("class", "tc-history-marker-dot tc-history-marker-dot-" + kind);
            dot.setAttribute("r", "3.5");
            dot.dataset.kind = kind;
            marker.appendChild(dot);
        });
        svg.appendChild(marker);
        return marker;
    }

    function tipOf(plot) {
        let tip = plot.querySelector(".tc-history-tip");
        if (!tip) {
            tip = element("div", "tc-history-tip");
            tip.setAttribute("role", "status");
            tip.hidden = true;
            plot.appendChild(tip);
        }
        return tip;
    }

    function hide(plot) {
        plot.querySelectorAll(".tc-history-marker").forEach(function (marker) {
            marker.setAttribute("display", "none");
        });
        const tip = plot.querySelector(".tc-history-tip");
        if (tip) tip.hidden = true;
        delete plot.dataset.historyAt;
    }

    function hideAll() {
        document.querySelectorAll("[data-history-plot]").forEach(hide);
    }

    // `seconds`: Zeitpunkt ab Fensterbeginn; `spoken`: Tastatur, Ansage erwünscht.
    function show(plot, seconds, spoken) {
        const model = modelOf(plot);
        const svg = visibleSvg(plot);
        if (!model || !svg || model.span <= 0) return;
        seconds = Math.min(model.span, Math.max(0, seconds));
        const left = Number(svg.dataset.left), right = Number(svg.dataset.right);
        const top = Number(svg.dataset.top), bottom = Number(svg.dataset.bottom);
        const x = left + (right - left) * seconds / model.span;
        plot.querySelectorAll("svg").forEach(function (other) {
            if (other !== svg) {
                const m = other.querySelector(".tc-history-marker");
                if (m) m.setAttribute("display", "none");
            }
        });
        const marker = markerOf(svg);
        marker.removeAttribute("display");
        const line = marker.querySelector("line");
        line.setAttribute("x1", x); line.setAttribute("x2", x);
        line.setAttribute("y1", top); line.setAttribute("y2", svg.dataset.laneBottom);

        const described = describe(model, seconds);
        const index = described.index;
        const low = Number(plot.dataset.min), high = Number(plot.dataset.max);
        const place = function (kind, tenths) {
            const dot = marker.querySelector('[data-kind="' + kind + '"]');
            if (tenths === null || tenths === undefined) { dot.setAttribute("display", "none"); return; }
            dot.removeAttribute("display");
            dot.setAttribute("cx", x);
            dot.setAttribute("cy", bottom - (bottom - top) * (tenths / 10 - low) / (high - low));
        };
        // Punkte nur auf einem einzelnen Wert; ein Bereich (Wechsel im Abschnitt) bekommt keinen.
        const single = function (run) { return run[1] !== null && run[1] === run[2] ? run[1] : null; };
        if (index >= 0) {
            place("actual", model.count[index] > 0 ? model.mean[index] : null);
            place("scheduled", single(model.scheduled[index]));
            place("effective", single(model.effective[index]));
        } else {
            place("actual", null); place("scheduled", null); place("effective", null);
        }

        const tip = tipOf(plot);
        tip.replaceChildren(described.node);
        if (spoken) tip.removeAttribute("aria-hidden"); else tip.setAttribute("aria-hidden", "true");
        tip.hidden = false;
        const anchor = svg.createSVGPoint();
        anchor.x = x; anchor.y = top;
        const screen = anchor.matrixTransform(svg.getScreenCTM());
        const box = plot.getBoundingClientRect();
        const width = tip.offsetWidth, gap = 12;
        let tipLeft = screen.x - box.left + gap;
        if (tipLeft + width > box.width) tipLeft = screen.x - box.left - gap - width;
        tipLeft = Math.max(0, Math.min(tipLeft, box.width - width));
        tip.style.left = tipLeft + "px";
        tip.style.top = Math.max(0, screen.y - box.top + 4) + "px";
        plot.dataset.historyAt = String(seconds);
    }

    function fromPointer(plot, event) {
        const svg = visibleSvg(plot);
        const model = modelOf(plot);
        if (!svg || !model) return;
        const point = svg.createSVGPoint();
        point.x = event.clientX; point.y = event.clientY;
        const inside = point.matrixTransform(svg.getScreenCTM().inverse());
        const left = Number(svg.dataset.left), right = Number(svg.dataset.right);
        const fraction = (inside.x - left) / (right - left);
        // Außerhalb der Zeichenfläche (Achsenbeschriftung) bleibt es beim zuletzt gezeigten Stand.
        if (fraction < 0 || fraction > 1) { hide(plot); return; }
        show(plot, fraction * model.span, false);
    }

    function plotOf(event) {
        return event.target.closest ? event.target.closest("[data-history-plot]") : null;
    }

    function prepare() {
        document.querySelectorAll("[data-history-plot]").forEach(function (plot) {
            if (plot.hasAttribute("tabindex") || !modelOf(plot)) return;
            plot.setAttribute("tabindex", "0");
            plot.setAttribute("role", "group");
            plot.setAttribute("aria-label",
                "Verlaufsdiagramm. Mit den Pfeiltasten links und rechts die Werte an einer Stelle ablesen.");
        });
    }

    document.addEventListener("pointermove", function (event) {
        const plot = plotOf(event);
        if (plot) fromPointer(plot, event);
    });
    document.addEventListener("pointerdown", function (event) {
        const plot = plotOf(event);
        hideAll();
        if (plot) fromPointer(plot, event);
    });
    document.addEventListener("pointerleave", function (event) {
        if (event.pointerType === "mouse" && event.target.matches &&
            event.target.matches("[data-history-plot]")) hide(event.target);
    }, true);
    document.addEventListener("keydown", function (event) {
        const plot = event.target.matches && event.target.matches("[data-history-plot]") ? event.target : null;
        const model = plot && modelOf(plot);
        if (!model) return;
        if (event.key === "Escape") { hide(plot); return; }
        const current = plot.dataset.historyAt === undefined ? null : Number(plot.dataset.historyAt);
        const step = model.span * STEP_FRACTION * (event.shiftKey ? 10 : 1);
        let next = null;
        if (event.key === "ArrowLeft") next = (current === null ? model.span : current) - step;
        else if (event.key === "ArrowRight") next = (current === null ? 0 : current) + step;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = model.span;
        if (next === null) return;
        event.preventDefault();
        show(plot, next, true);
    });
    document.addEventListener("focusout", function (event) {
        const plot = plotOf(event);
        if (plot && !plot.contains(event.relatedTarget)) hide(plot);
    });
    window.addEventListener("resize", hideAll);
    document.addEventListener("click", function (event) {
        // Vollbild an- oder abschalten wechselt die Zeichnung; ein alter Marker passte nicht mehr.
        if (event.target.closest("[data-history-open], [data-history-close]")) hideAll();
    });
    document.addEventListener("fullscreenchange", hideAll);
    document.addEventListener("htmx:load", prepare);
    prepare();
})();
