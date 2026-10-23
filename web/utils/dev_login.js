/**
 * utils/dev_login.js
 * One-click role switching for local work — no password to type.
 *
 * The API is served by the same process that serves this page, and the page
 * already carries the deployment API key (`window.SOC_CONFIG.apiKey`), which
 * the server treats as the admin machine actor. That means the server can mint
 * a real session for any existing account when the caller presents that key —
 * so switching to `soc-analyst` is a click, and switching back is another.
 *
 * It only ever DOWNGRADES: an analyst session cannot call the mint endpoint
 * (it has no key), and the endpoint itself does not exist on a deployment where
 * SOC_DEV_LOGIN is unset. The widget renders nothing unless `GET
 * /api/auth/session` reports `dev_login: true`, so a production page simply has
 * no control here.
 *
 * No build step, no framework: a plain IIFE like utils/auth.js. Every value is
 * written with textContent, so no markup is ever parsed from data.
 */
(function (global) {
    'use strict';

    var ACCOUNTS = [
        { name: 'soc-admin', label: 'admin' },
        { name: 'soc-analyst', label: 'analyst' }
    ];

    function el(tag, styles, text) {
        var node = global.document.createElement(tag);
        if (styles) node.setAttribute('style', styles);
        if (text) node.textContent = text;
        return node;
    }

    function switchTo(account, statusNode) {
        if (statusNode) statusNode.textContent = 'switching…';
        global.axios.post('/api/auth/dev-login', { account: account })
            .then(function () { global.location.reload(); })
            .catch(function (err) {
                var detail = err && err.response && err.response.data && err.response.data.detail;
                if (statusNode) statusNode.textContent = 'failed: ' + (detail || 'see console');
                global.console && global.console.warn('[dev-login]', detail || err);
            });
    }

    function render(state) {
        var box = el('div', [
            'position:fixed', 'right:12px', 'bottom:12px', 'z-index:2147483000',
            'background:rgba(17,24,39,.95)', 'color:#e5e7eb', 'border:1px solid #374151',
            'border-radius:8px', 'padding:8px 10px', 'font:12px/1.4 ui-monospace,Menlo,monospace',
            'box-shadow:0 6px 20px rgba(0,0,0,.35)'
        ].join(';'));

        var current = (state.role || '?') + (state.user ? ' (' + state.user + ')' : '');
        box.appendChild(el('div', 'margin-bottom:6px;color:#9ca3af', 'role: ' + current));

        var status = el('div', 'margin-top:6px;color:#9ca3af;min-height:1em', '');
        var row = el('div', 'display:flex;gap:6px');
        ACCOUNTS.forEach(function (account) {
            var isCurrent = state.user === account.name;
            row.appendChild(el(
                'button',
                [
                    'cursor:pointer', 'border-radius:6px', 'padding:3px 8px',
                    'border:1px solid ' + (isCurrent ? '#10b981' : '#4b5563'),
                    'background:' + (isCurrent ? '#064e3b' : '#1f2937'),
                    'color:#e5e7eb', 'font:inherit'
                ].join(';'),
                account.label
            )).onclick = function () { switchTo(account.name, status); };
        });
        box.appendChild(row);

        box.appendChild(status);
        global.document.body.appendChild(box);
    }

    function boot() {
        if (!global.axios) return;
        global.axios.get('/api/auth/session')
            .then(function (res) {
                if (res && res.data && res.data.dev_login) render(res.data);
            })
            .catch(function () { /* not enabled, or not signed in: no widget */ });
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})(typeof window !== 'undefined' ? window : globalThis);
