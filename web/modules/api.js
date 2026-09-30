/**
 * modules/api.js
 * Central API service layer for the SOC Platform.
 *
 * All HTTP calls live here so troubleshooting is limited to one file.
 * Usage (from Vue methods or modules):
 *   const data = await API.tools();
 *   await API.promoteNotable(id);
 *
 * Path policy (S13): methods are written against the historical /api/db/*
 * spellings, and the transport upgrades every aliased route to the canonical
 * resource path (/api/cases, /api/notables, /api/evidence, /api/analyses)
 * before dialing. If the canonical path 404s - an older deploy without the
 * alias rewrite - the legacy spelling is retried once, so the frontend works
 * against both. Routes without a canonical alias (rules, supportive queries,
 * placeholder aliases, closure-note) are called at their only spelling.
 *
 * The modular shell (app.modular.js + domain modules) consumes this layer;
 * domain modules that still call axios directly can migrate call-by-call.
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

    // -------------------------------------------------------------------------
    // Canonical path mapping (mirrors canonical_to_legacy_path in api/main.py,
    // inverted: legacy /api/db/* literal -> canonical spelling).
    // -------------------------------------------------------------------------
    const CANONICAL_RULES = [
        // Dedicated /api/evidence/{case} family (before the generic tails).
        [/^\/db\/triage\/([^/]+)\/evidence$/, '/evidence/$1'],
        [/^\/db\/triage\/([^/]+)\/evidence(\/.+)$/, '/evidence/$1$2'],
        // Cases family: the /api/cases/* tree mirrors /api/db/triage/* 1:1.
        [/^\/db\/triage$/, '/cases'],
        [/^\/db\/triage\/([^/]+)$/, '/cases/$1'],
        [/^\/db\/triage\/([^/]+)\/(.+)$/, '/cases/$1/$2'],
        // Notables family: any tail.
        [/^\/db\/notables$/, '/notables'],
        [/^\/db\/notables(\/.+)$/, '/notables$1'],
        // Analysis.
        [/^\/db\/analyze$/, '/analyses'],
    ];

    function canonicalFor(path) {
        const p = path.startsWith('/') ? path : '/' + path;
        for (const [pattern, template] of CANONICAL_RULES) {
            const m = p.match(pattern);
            if (m) {
                return template.replace(/\$(\d)/g, (_, i) => m[Number(i)]);
            }
        }
        return null;
    }

    function isNotFound(err) {
        return Boolean(err && err.response && err.response.status === 404);
    }

    async function get(path, config) {
        const canonical = canonicalFor(path);
        if (canonical) {
            try {
                const res = await axios.get(url(canonical), config);
                return res.data;
            } catch (err) {
                if (!isNotFound(err)) {
                    noteSessionExpired(err);
                    throw err;
                }
                // Older deploy without the alias rewrite: fall back.
            }
        }
        try {
            const res = await axios.get(url(path), config);
            return res.data;
        } catch (err) {
            noteSessionExpired(err);
            throw err;
        }
    }

    async function send(method, path, body, config) {
        const canonical = canonicalFor(path);
        if (canonical) {
            try {
                const res = await axios[method](url(canonical), body, config);
                return res.data;
            } catch (err) {
                if (!isNotFound(err)) {
                    noteSessionExpired(err);
                    throw err;
                }
            }
        }
        try {
            const res = await axios[method](url(path), body, config);
            return res.data;
        } catch (err) {
            noteSessionExpired(err);
            throw err;
        }
    }

    const post = (path, body, config) => send('post', path, body, config);
    const put = (path, body, config) => send('put', path, body, config);
    const del = (path, config) => send('delete', path, undefined, config);

    // Session auth (SESSION_AUTH_PLAN.md): 401 on an authenticated request
    // means the session is gone — notify so the app can reopen the login
    // modal. One place covers every call in the app.
    function noteSessionExpired(err) {
        if (err && err.response && err.response.status === 401
            && !String(err.config && err.config.url || '').includes('/api/auth/')) {
            if (typeof global.__SOC_ON_SESSION_EXPIRED__ === 'function') {
                try { global.__SOC_ON_SESSION_EXPIRED__(); } catch (e) { /* UI hook only */ }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Public API surface (grouped by domain)
    // -------------------------------------------------------------------------
    const API = {
        // Allow the app to override the base URL at runtime
        setBase(base) {
            global.__SOC_API_BASE__ = base;
        },

        // Exposed for diagnostics/tests: the canonical spelling for a path,
        // or null when the path has no canonical alias.
        canonicalFor,

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
        deleteJob(jobId) {
            return del('/jobs/' + encodeURIComponent(jobId));
        },
        clearAllJobs() {
            return del('/jobs');
        },
        runToolRegression() {
            return post('/tools/regression');
        },
        reports() {
            return get('/reports');
        },
        executeTool(payload) {
            return post('/execute', payload);
        },

        // ---- Session auth (SESSION_AUTH_PLAN.md Piece C) ---------------------
        sessionInfo() {
            return get('/auth/session');
        },
        sessionLogin(payload) {
            return post('/auth/login', payload);
        },
        sessionLogout() {
            return post('/auth/logout');
        },

        // ---- Cases (triage) ---------------------------------------------------
        triage(params) {
            return get('/db/triage', { params });
        },
        triageStats() {
            return get('/db/stats');
        },
        operations() {
            return get('/db/operations');
        },
        notable(id) {
            return get('/db/notables/' + id);
        },
        deleteNotable(id) {
            return post('/db/notables/' + id + '/delete');
        },
        deleteCase(caseId, deleteAnalysis) {
            return post('/db/triage/' + encodeURIComponent(caseId) + '/delete'
                + (deleteAnalysis ? '?delete_analysis=true' : ''));
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

        // ---- Notables ----------------------------------------------------------
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

        // ---- Splunk -------------------------------------------------------------
        splunkSearchOne(payload) {
            return post('/splunk/search-one', payload);
        },
        splunkBoundaryStatus() {
            return get('/splunk-boundary/status');
        },
        releaseSplunkBoundaryBatch(batchId) {
            return del('/splunk-boundary/batches/' + encodeURIComponent(batchId));
        },

        // ---- Rules / supportive queries / aliases (legacy spellings: the
        // server defines no canonical aliases for this family) -----------------
        rules() {
            return get('/db/rules');
        },
        supportiveQueries(params) {
            return get('/db/supportive-queries', { params });
        },
        supportiveQueriesStatus(caseId) {
            return get('/db/supportive-queries/status/' + encodeURIComponent(caseId));
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

        // ---- Analysis -----------------------------------------------------------
        analyze(payload, config) {
            // config carries { signal } for in-flight cancellation.
            return post('/db/analyze', payload, config);
        },

        draftSupportiveQueries(payload) {
            return post('/db/supportive-queries/draft', payload);
        },

        importSupportiveResults(payload) {
            return post('/db/supportive-queries/import-results', payload);
        },

        // ---- Closure (legacy spellings: no canonical aliases server-side) -------
        closureNote(payload) {
            return post('/db/closure-note', payload);
        },
        closureReadiness(caseId) {
            return get('/db/triage/' + encodeURIComponent(caseId) + '/closure-readiness');
        },

        // ---- Code Review --------------------------------------------------------
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
