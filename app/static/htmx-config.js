// Wires this page's per-request CSP nonce into htmx.
//
// The mentor panel (base.html) loads via hx-trigger="load" on every page,
// and its response (_conversation.html) carries an inline <script nonce="...">
// for the optimistic "echo your message immediately" UI. htmx rebuilds that
// script tag rather than relying on innerHTML (which never executes
// scripts), but a nonce satisfies CSP only via the `.nonce` IDL property --
// not the copied attribute -- so without this, every htmx-driven script
// re-execution is silently blocked. htmx.config.inlineScriptNonce is the
// documented bridge: htmx assigns it to `.nonce` on the node it rebuilds.
//
// Deferred and placed right after htmx.min.js's own <script> tag so `htmx`
// already exists and this still runs before htmx's initial DOMContentLoaded
// scan -- which is what fires that first hx-trigger="load".
htmx.config.inlineScriptNonce = document.currentScript.getAttribute("data-nonce");
