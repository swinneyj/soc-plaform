/**
 * utils/pocSummary.js
 * Pure helper to build a short "proof of concept" summary from a notable's fields.
 *
 * Usage:
 *   const entries = PocSummary.fromNotable(notable);
 *   // entries = [{ label: 'Owner', value: '...' }, ...]
 */
(function (global) {
    'use strict';

    function fromNotable(notable) {
        const fields = (notable && (notable.fields || notable)) || {};
        const pocEntries = [];

        function addEntry(label, value) {
            if (!value) return;
            const cleaned = String(value).trim();
            if (!cleaned) return;
            pocEntries.push({ label, value: cleaned });
        }

        // Currently only Owner is extracted; expand here as needed.
        addEntry('Owner', fields.owner);

        return pocEntries;
    }

    const PocSummary = {
        fromNotable
    };

    global.PocSummary = PocSummary;

})(typeof window !== 'undefined' ? window : globalThis);
