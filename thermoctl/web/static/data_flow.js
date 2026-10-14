/* Zeichnet die Datenflussdiagramme (Seite „Datenfluss“).
 *
 * Der Mermaid-Quelltext steht als Text im <pre data-flow-source>; ohne dieses Skript bleibt er
 * dort lesbar stehen. Mermaid kommt aus dem eigenen Static-Verzeichnis (vendor/mermaid), nie
 * von einem fremden Server. `securityLevel: "strict"`: Mermaid bereinigt Beschriftungen selbst,
 * zusätzlich escapt der Server jeden Namen (domain/data_flow_mermaid.py).
 */
(function () {
    "use strict";
    const source = new URL(document.currentScript.src);
    let loading = null;
    let counter = 0;

    function loadMermaid() {
        if (window.mermaid) return Promise.resolve(window.mermaid);
        if (loading) return loading;
        loading = new Promise(function (resolve, reject) {
            const url = new URL("vendor/mermaid/mermaid.min.js", source);
            url.search = source.search;
            const script = document.createElement("script");
            script.src = url.href;
            script.onload = function () { resolve(window.mermaid); };
            script.onerror = function () { loading = null; reject(new Error("mermaid")); };
            document.head.appendChild(script);
        });
        return loading;
    }

    function render() {
        const hosts = Array.prototype.filter.call(
            document.querySelectorAll("[data-flow-diagram]"),
            function (host) { return !host.hasAttribute("data-flow-done"); }
        );
        if (hosts.length === 0) return;
        hosts.forEach(function (host) { host.setAttribute("data-flow-done", "pending"); });
        loadMermaid().then(function (mermaid) {
            mermaid.initialize({
                startOnLoad: false,
                securityLevel: "strict",
                theme: document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "default"
            });
            return hosts.reduce(function (chain, host) {
                return chain.then(function () {
                    const pre = host.querySelector("[data-flow-source]");
                    const canvas = host.querySelector("[data-flow-canvas]");
                    if (!pre || !canvas || !pre.isConnected) return null;
                    counter += 1;
                    return mermaid.render("flow-svg-" + counter, pre.textContent).then(function (result) {
                        canvas.innerHTML = result.svg;
                        pre.hidden = true;
                        host.setAttribute("data-flow-done", "yes");
                    });
                }).catch(function () {
                    // Bleibt der Quelltext stehen, ist die Seite weiter lesbar.
                    host.setAttribute("data-flow-done", "failed");
                });
            }, Promise.resolve());
        }).catch(function () {
            hosts.forEach(function (host) { host.setAttribute("data-flow-done", "failed"); });
        });
    }

    document.addEventListener("htmx:load", render);
    document.addEventListener("htmx:historyRestore", render);
    render();
})();
