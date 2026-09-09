/**
 * utils/keys.js
 * Pure key-generator helpers used by Analysis / Phase-2 / Enrichment UI.
 *
 * No Vue `this` dependency — easy to unit-test or call from a REPL.
 *
 * Usage (later, from Vue methods or modules):
 *   const key = QueryKeys.supportive(q);
 *   const key = QueryKeys.phase2FromTitle(title);
 */
(function (global) {
    'use strict';

    function normalizeTitle(title) {
        return (title || '').toString().toLowerCase().trim();
    }

    function slug(title, maxLen) {
        const n = normalizeTitle(title);
        if (!n) return null;
        return n.replace(/\s+/g, '_').slice(0, maxLen || 64);
    }

    const QueryKeys = {
        /** Stable key for a supportive-query object (prefers numeric id). */
        supportive(q) {
            if (q && q.id != null) {
                return 'id:' + q.id;
            }
            const s = slug(q && q.title);
            return s ? 'title:' + s : 'title:unknown';
        },

        supportiveFromTitle(title) {
            const s = slug(title);
            return s ? 'title:' + s : 'title:unknown';
        },

        /** Stable key for a Phase-2 suggested query. */
        phase2(q) {
            const s = slug(q && q.title);
            return s ? 'phase2:' + s : 'phase2:title:unknown';
        },

        phase2FromTitle(title) {
            const s = slug(title);
            return s ? 'phase2:' + s : 'phase2:title:unknown';
        },

        /** Stable key for a generic enrichment query card. */
        enrichment(q) {
            const s = slug(q && q.title);
            return s ? 'enrichment:' + s : 'enrichment:unknown';
        },

        enrichmentFromTitle(title) {
            const s = slug(title);
            return s ? 'enrichment:' + s : 'enrichment:unknown';
        }
    };

    global.QueryKeys = QueryKeys;

})(typeof window !== 'undefined' ? window : globalThis);
