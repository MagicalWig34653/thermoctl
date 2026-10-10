/* Die Karte bleibt beim HTMX-Tausch bedienbar; iOS nutzt das Overlay. */
(function () {
    "use strict";
    let restoreExpanded = false;
    function close() {
        const card = document.querySelector(".tc-history-expanded");
        if (!card) return;
        card.classList.remove("tc-history-expanded");
        document.body.classList.remove("tc-history-body-open");
        if (document.fullscreenElement) document.exitFullscreen();
    }
    document.addEventListener("htmx:beforeSwap", function (event) {
        restoreExpanded = event.detail.target.id === "zone-history" &&
            event.detail.target.classList.contains("tc-history-expanded");
    });
    document.addEventListener("htmx:afterSettle", function () {
        if (!restoreExpanded) return;
        restoreExpanded = false;
        const card = document.getElementById("zone-history");
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
