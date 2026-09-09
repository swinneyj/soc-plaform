/**
 * modules/api.js
 * Central API service layer for the SOC Platform.
 *
 * All HTTP calls live here so troubleshooting is limited to one file.
 * Usage (from Vue methods or modules):
 *   const data = await API.tools();
 *   await API.promoteNotable(id);
 *
 * The live site does NOT load this file yet (Piece 1 is additive only).
 * Later pieces will switch app.js / the thin index to use it.
 */
(function (global) {
    'use strict';

    const DEFAULT_BASE = '/api';

    function getBase() {
        // Prefer the Vue instance's apiUrl when available, otherwise default.
        if (global.__SOC_API_BASE__) return global.__SOC_API_BASE__;
        return DEFAULT_BASE;
    }

    function url(path) {
        const base = getBase().replace(/\/$/, '');
        const p = path.startsWith('/') ? path : '/' + path;
        return base + p;
    }

    async function get(path, config) {
        const res = await axios.get(url(path), config);
        return res.data;
    }

    async function post(path, body, config) {
        const res = await axios.post(url(path), body, config);
        return res.data;
    }

    async function put(path, body, config) {
        const res = await axios.put(url(path), body, config);
        return res.data;
    }

    async function del(path, config) {
        const res = await axios.delete(url(path), config);
        return res.data;
    }

    // -------------------------------------------------------------------------
    // Public API surface (grouped by domain)
    // -------------------------------------------------------------------------
    const API = {
        // Allow the app to override the base URL at runtime
        setBase(base) {
            global.__SOC_API_BASE__ = base;
        },

        // ---- Health / platform ------------------------------------------------
        health() {
            return get('/health');
        },
        ollamaHealth() {
            return get('/db/ollama/health');
        },

        // ---- Tools / Jobs / Reports ------------------------------------------
        tools() {
            return get('/tools');
        },
        jobs() {
            return get('/jobs');
        },
        reports() {
            return get('/reports');
        },
        executeTool(payload) {
            return post('/execute', payload);
        },

        // ---- Database – triage -----------------------------------------------
        triage(params) {
            return get('/db/triage', { params });
        },
        triageStats() {
            return get('/db/stats');
        },
        triageNotable(caseId) {
            return get('/db/triage/' + encodeURIComponent(caseId) + '/notable');
        },
        triageEvidence(caseId, params) {
            return get('/db/triage/' + encodeURIComponent(caseId) + '/evidence', { params });
        },
        triageInvestigationState(caseId) {
            return get('/db/triage/' + encodeURIComponent(caseId) + '/investigation-state');
        },
        saveEvidence(caseId, payload) {
            return post('/db/triage/' + encodeURIComponent(caseId) + '/evidence', payload);
        },
        deleteEvidence(caseId, evidenceId) {
            // Prefer batch-delete so single deletes use the same POST route everywhere.
            return post('/db/triage/' + encodeURIComponent(caseId) + '/evidence/batch-delete', {
                ids: [Number(evidenceId)]
            });
        },
        deleteEvidenceBatch(caseId, ids) {
            return post('/db/triage/' + encodeURIComponent(caseId) + '/evidence/batch-delete', { ids: ids || [] });
        },
        deleteAllEvidence(caseId) {
            return post('/db/triage/' + encodeURIComponent(caseId) + '/evidence/delete-all');
        },
        batchDeleteTriage(payload) {
            return post('/db/triage/batch-delete', payload);
        },

        // ---- Database – notables ---------------------------------------------
        notables(params) {
            return get('/db/notables', { params });
        },
        historicalNotables(params) {
            return get('/db/notables/historical', { params: params || { limit: 20 } });
        },
        pasteNotable(payload) {
            return post('/db/notables/paste', payload);
        },
        promoteNotable(id) {
            return post('/db/notables/' + id + '/promote');
        },
        batchDeleteNotables(payload) {
            return post('/db/notables/batch-delete', payload);
        },

        // ---- Rules / supportive queries / aliases ----------------------------
        rules() {
            return get('/db/rules');
        },
        supportiveQueries(params) {
            return get('/db/supportive-queries', { params });
        },
        createSupportiveQuery(payload) {
            return post('/db/supportive-queries', payload);
        },
        updateSupportiveQuery(id, payload) {
            return put('/db/supportive-queries/' + id, payload);
        },
        deleteSupportiveQuery(id) {
            return del('/db/supportive-queries/' + id);
        },
        placeholderAliases() {
            return get('/db/placeholder-aliases');
        },
        placeholderAliasSuggestions(params) {
            return get('/db/placeholder-aliases/suggestions', { params });
        },
        createPlaceholderAlias(payload) {
            return post('/db/placeholder-aliases', payload);
        },
        updatePlaceholderAlias(id, payload) {
            return put('/db/placeholder-aliases/' + id, payload);
        },
        deletePlaceholderAlias(id) {
            return del('/db/placeholder-aliases/' + id);
        },

        // ---- Analysis --------------------------------------------------------
        analyze(payload) {
            return post('/db/analyze', payload);
        },

        // ---- Closure ---------------------------------------------------------
        closureNote(payload) {
            return post('/db/closure-note', payload);
        },

        // ---- Code Review -----------------------------------------------------
        codeReviewSections(payload) {
            return post('/code-review/sections', payload);
        },
        codeReview(payload, isFormData) {
            if (isFormData) {
                // caller supplies the full URL or we use the default endpoint
                return post('/code-review', payload, {
                    headers: { 'Content-Type': 'multipart/form-data' }
                });
            }
            return post('/code-review', payload);
        },
        codeReviewUpload(url, formData) {
            return axios.post(url, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            }).then(r => r.data);
        },
        listCodeReviews(limit) {
            return get('/code-reviews?limit=' + (limit || 10));
        },
        getCodeReview(reviewId) {
            return get('/code-reviews/' + reviewId);
        }
    };

    // Expose globally for the non-module script loading style we use today
    global.API = API;

})(typeof window !== 'undefined' ? window : globalThis);
