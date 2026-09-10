/* One loader in the persistent head, including across boosted body swaps.
 * Detect controls, not routes: partial swaps and history restoration need the
 * same enhancements. Mark in-flight loads too, so rapid swaps cannot load twice.
 */
(function () {
    "use strict";
    const source = new URL(document.currentScript.src);
    const loaded = new Set();
    const features = [
        ["passkey.js", "[data-passkey], #passkey-login, #passkey-register"],
        ["schedule.js", "#schedule-grid"],
        ["permissions.js", "[data-permission]"],
        ["assignment.js", "#device-pool, [data-submit-on-change]"],
        ["device_filter.js", "#device-search"],
        ["homebridge_copy.js", "[data-homebridge-copy]"]
    ];

    function load() {
        features.forEach(function (feature) {
            const [file, selector] = feature;
            if (loaded.has(file) || !document.querySelector(selector)) {
                return;
            }
            loaded.add(file);
            const url = new URL(file, source);
            url.search = source.search;
            const script = document.createElement("script");
            script.src = url.href;
            script.onerror = function () {
                loaded.delete(file);
                script.remove();
            };
            document.head.appendChild(script);
        });
    }

    // An open tab can outlive a server update. Never swap new markup into the
    // old head: a full navigation replaces CSS, loader and feature scripts together.
    document.addEventListener("htmx:beforeSwap", function (event) {
        const xhr = event.detail.xhr;
        const version = xhr.getResponseHeader("X-Thermoctl-Assets");
        if (version && version !== source.searchParams.get("v")) {
            event.detail.shouldSwap = false;
            if (event.detail.requestConfig.boosted) {
                window.location.assign(xhr.responseURL);
            } else {
                window.location.reload();
            }
        }
    });

    document.addEventListener("DOMContentLoaded", load);
    document.addEventListener("htmx:load", load);
    document.addEventListener("htmx:historyRestore", load);
})();
