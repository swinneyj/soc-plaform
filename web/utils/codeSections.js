/**
 * utils/codeSections.js
 * Pure helpers for Code Review tab section focusing / filtering.
 *
 * The actual section *detection* still calls the backend (see modules/api.js
 * and the Vue method detectCodeReviewSections). These helpers only manipulate
 * already-loaded text or section lists.
 *
 * Usage:
 *   CodeSections.focusAroundTerm(fullText, term, radius)
 *   CodeSections.filterSections(sections, searchTerm, codeOnly)
 */
(function (global) {
    'use strict';

    /**
     * Return a window of text around the first occurrence of `term`.
     * @returns {{ snippet: string, found: boolean, start: number, end: number }}
     */
    function focusAroundTerm(fullText, term, radius) {
        const text = (fullText || '').toString();
        const t = (term || '').toString().trim();
        const r = typeof radius === 'number' ? radius : 2000;

        if (!text.trim() || !t) {
            return { snippet: text, found: false, start: 0, end: text.length };
        }

        const idx = text.toLowerCase().indexOf(t.toLowerCase());
        if (idx === -1) {
            return { snippet: text, found: false, start: 0, end: text.length };
        }

        const start = Math.max(0, idx - r);
        const end = Math.min(text.length, idx + r);
        return {
            snippet: text.slice(start, end),
            found: true,
            start,
            end
        };
    }

    /**
     * Filter a list of detected sections by search term and optional "code only" flag.
     * Mirrors the logic currently in the Vue computed `filteredCodeReviewSections`.
     */
    function filterSections(sections, searchTerm, codeOnly) {
        let list = Array.isArray(sections) ? sections.slice() : [];

        if (codeOnly) {
            const codeNames = new Set(['function', 'method', 'class', 'def', 'code']);
            list = list.filter(s => {
                const name = ((s && s.kind) || '').toLowerCase();
                const label = ((s && s.name) || '').toLowerCase();
                if (codeNames.has(name)) return true;
                if (label.includes('codereview') || name.includes('codereview')) return true;
                if (label.includes('code') && label.includes('review')) return true;
                return false;
            });
        }

        const term = (searchTerm || '').toString().trim().toLowerCase();
        if (term) {
            list = list.filter(s => {
                const name = ((s && s.name) || '').toLowerCase();
                const kind = ((s && s.kind) || '').toLowerCase();
                const preview = ((s && s.preview) || '').toLowerCase();
                return name.includes(term) || kind.includes(term) || preview.includes(term);
            });
        }

        return list;
    }

    const CodeSections = {
        focusAroundTerm,
        filterSections
    };

    global.CodeSections = CodeSections;

})(typeof window !== 'undefined' ? window : globalThis);
