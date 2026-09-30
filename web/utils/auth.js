/**
 * utils/auth.js
 * Optional API-key bootstrap for the SOC Platform UI.
 *
 * Reads window.SOC_CONFIG.apiKey (or ?apiKey= for local testing) and, when
 * present, stamps X-API-Key on every axios request via a single interceptor.
 *
 * No build step: a plain IIFE, loaded before any module makes HTTP calls.
 *
 * Config sources, in priority order:
 *   1. window.SOC_CONFIG = { apiKey: "..." }   (set in index HTML; on Vercel
 *      this line can be injected/overridden by a deployment-time transform,
 *      or set by hand locally)
 *   2. URL query parameter ?apiKey=...          (local dev convenience only)
 *
 * If neither is present, nothing happens and the app behaves exactly as
 * before — the backend gate is also a no-op until API_KEY is set.
 */
(function (global) {
    'use strict';

    var cfg = (global.SOC_CONFIG && global.SOC_CONFIG.apiKey) || '';
    if (!cfg) {
        try {
            var qp = new URLSearchParams(global.location.search).get('apiKey');
            if (qp) cfg = qp;
        } catch (e) { /* older browsers: skip query-param path */ }
    }

    if (global.axios && cfg) {
        global.axios.interceptors.request.use(function (config) {
            config.headers = config.headers || {};
            config.headers['X-API-Key'] = cfg;
            return config;
        });
        // Visible confirmation without console spam on every request
        global.console && global.console.info('[auth] X-API-Key attached to axios requests');
    }

    // Session mode (SESSION_AUTH_PLAN.md): stamp X-CSRF-Token on every
    // request when the app has a live session. window.__SOC_CSRF__ is seeded
    // from GET /api/auth/session by app.modular.js and refreshed after login.
    if (global.axios) {
        global.axios.interceptors.request.use(function (config) {
            config.headers = config.headers || {};
            if (global.__SOC_CSRF__) config.headers['X-CSRF-Token'] = global.__SOC_CSRF__;
            return config;
        });
    }
})(typeof window !== 'undefined' ? window : globalThis);
