/**
 * modules/database.js
 * Domain methods for the Database tab (triage, notables, paste/promote, bulk delete).
 *
 * Loaded before app.modular.js. App spreads these into Vue methods:
 *   methods: {
 *     ...window.DatabaseMethods,
 *     ...
 *   }
 *
 * Troubleshooting: Database tab bugs / API failures for triage & notables → this file.
 */
(function (global) {
    'use strict';

    const DatabaseMethods = {
        // ------------------------------------------------------------------
        // Selection helpers
        // ------------------------------------------------------------------

        areAllPastedNotablesSelected() {
            if (global.Selection) {
                return global.Selection.areAllSelected(
                    this.filteredRecentNotables,
                    this.selectedNotableIds,
                    n => n.id
                );
            }
            const items = this.filteredRecentNotables || [];
            if (!items.length) return false;
            return items.every(n => (this.selectedNotableIds || []).includes(n.id));
        },

        toggleSelectAllPastedNotables(event) {
            const checked = event && event.target ? event.target.checked : false;
            if (global.Selection) {
                this.selectedNotableIds = global.Selection.toggleAll(
                    this.filteredRecentNotables,
                    this.selectedNotableIds,
                    checked,
                    n => n.id
                );
                return;
            }
            if (checked) {
                this.selectedNotableIds = (this.filteredRecentNotables || []).map(n => n.id);
            } else {
                this.selectedNotableIds = [];
            }
        },

        areAllTriageCasesSelected() {
            if (global.Selection) {
                return global.Selection.areAllSelected(
                    this.filteredTriageData,
                    this.selectedTriageCaseIds,
                    c => c.case_id
                );
            }
            const items = this.filteredTriageData || [];
            if (!items.length) return false;
            return items.every(c => (this.selectedTriageCaseIds || []).includes(c.case_id));
        },

        toggleSelectAllTriageCases(event) {
            const checked = event && event.target ? event.target.checked : false;
            if (global.Selection) {
                this.selectedTriageCaseIds = global.Selection.toggleAll(
                    this.filteredTriageData,
                    this.selectedTriageCaseIds,
                    checked,
                    c => c.case_id
                );
                return;
            }
            if (checked) {
                this.selectedTriageCaseIds = (this.filteredTriageData || []).map(c => c.case_id);
            } else {
                this.selectedTriageCaseIds = [];
            }
        },

        // ------------------------------------------------------------------
        // Pure / display helpers
        // ------------------------------------------------------------------

        getNotablePocSummary(notable) {
            if (global.PocSummary && typeof global.PocSummary.fromNotable === 'function') {
                return global.PocSummary.fromNotable(notable);
            }
            const fields = (notable && (notable.fields || notable)) || {};
            const pocEntries = [];
            const addEntry = (label, value) => {
                if (!value) return;
                const cleaned = String(value).trim();
                if (!cleaned) return;
                pocEntries.push({ label, value: cleaned });
            };
            // Only surface the primary owner as a Point of Contact
            addEntry('Owner', fields.owner);
            return pocEntries;
        },

        copyTriageNotableFields(case_) {
            const caseId = case_.case_id;
            const details = this.triageNotableDetails[caseId];

            if (!details || !details.data || !details.data.fields) {
                alert('No source-notable fields loaded yet for this case');
                return;
            }

            const fields = details.data.fields;
            const lines = Object.entries(fields).map(([key, value]) => `${key}: ${value}`);
            const text = lines.join('\n');

            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                alert('Clipboard access is not available in this browser context');
                return;
            }

            navigator.clipboard.writeText(text)
                .then(() => {
                    alert('Source notable fields copied to clipboard');
                })
                .catch(err => {
                    console.error('Failed to copy fields:', err);
                    alert('Failed to copy fields to clipboard');
                });
        },

        jumpToTriageCase(caseId) {
            this.dbSearch = caseId;
            this.currentTab = 'database';
            this.loadTriageData();
        },

        // ------------------------------------------------------------------
        // Loaders
        // ------------------------------------------------------------------

        async loadHistoricalNotables() {
            try {
                const res = await axios.get(this.apiUrl + '/db/notables/historical', { params: { limit: 20 } });
                this.historicalNotables = res.data || [];
            } catch (err) {
                console.error('Failed to load historical pasted notables:', err);
                this.historicalNotables = [];
            }
        },

        async loadClosedNotableDetails(notableSummary) {
            if (!notableSummary || !notableSummary.id) {
                return;
            }

            const id = notableSummary.id;
            const existing = this.triageNotableDetails[id];
            if (existing && (existing.loading || existing.data)) {
                return;
            }

            // Vue 3: direct assignment is reactive when the parent object
            // (triageNotableDetails) was declared in data().
            this.triageNotableDetails[id] = { loading: true, error: null, data: null };

            try {
                const res = await axios.get(this.apiUrl + '/db/notables/' + id);
                this.triageNotableDetails[id] = { loading: false, error: null, data: res.data };
            } catch (err) {
                console.error('Failed to load closed notable details:', err);
                const detail = (err.response && err.response.data && err.response.data.detail) || err.message;
                this.triageNotableDetails[id] = { loading: false, error: detail, data: null };
            }
        },

        async deleteClosedNotable(notableSummary) {
            if (!notableSummary || !notableSummary.id) {
                return;
            }

            if (!confirm('Permanently delete this closed notable from the database? It can be re-added later by pasting it again.')) {
                return;
            }

            const id = notableSummary.id;
            try {
                await axios.post(this.apiUrl + '/db/notables/' + id + '/delete');
                // Drop any cached details for this id so the details panel closes cleanly
                if (this.triageNotableDetails && this.triageNotableDetails[id]) {
                    delete this.triageNotableDetails[id];
                }
                await this.loadHistoricalNotables();
                await this.loadDbStats();
            } catch (err) {
                alert('Error deleting closed notable: ' + ((err.response && err.response.data && err.response.data.detail) || err.message));
            }
        },

        async deleteSelectedClosedNotables(ids) {
            const eventIds = Array.isArray(ids) ? ids : [];
            if (!eventIds.length || !confirm('Permanently delete ' + eventIds.length + ' selected closed notable(s)?')) return;
            try {
                await axios.post(this.apiUrl + '/db/notables/batch-delete', { event_ids: eventIds });
                await this.loadHistoricalNotables();
                await this.loadDbStats();
            } catch (err) {
                alert('Error deleting selected closed notables: ' + ((err.response && err.response.data && err.response.data.detail) || err.message));
            }
        },

        async loadTriageData() {
            try {
                const params = { limit: 50 };
                if (this.dbSearch.trim()) {
                    params.search = this.dbSearch.trim();
                }
                if (this.dbVerdictFilter) {
                    params.verdict = this.dbVerdictFilter;
                }

                const res = await axios.get(this.apiUrl + '/db/triage', { params });
                this.triageData = res.data;
            } catch (err) {
                console.error('Failed to load triage data:', err);
                this.triageData = [];
            }
        },

        async loadAnalysisCases() {
            try {
                const res = await axios.get(this.apiUrl + '/db/triage', {
                    params: { limit: 1000 }
                });
                this.analysisCases = res.data;
            } catch (err) {
                console.error('Failed to load analysis cases:', err);
                this.analysisCases = [];
            }
        },

        async loadDbStats() {
            try {
                const res = await axios.get(this.apiUrl + '/db/stats');
                this.dbStats = res.data;
            } catch (err) {
                console.error('Failed to load DB stats:', err);
            }
            try {
                const res = await axios.get(this.apiUrl + '/db/operations');
                this.operationsStats = res.data;
            } catch (err) {
                console.error('Failed to load operations dashboard:', err);
            }
        },

        async loadRecentNotables() {
            try {
                const res = await axios.get(this.apiUrl + '/db/notables', { params: { limit: 20 } });
                this.recentNotables = res.data;
            } catch (err) {
                console.error('Failed to load recent pasted notables:', err);
                this.recentNotables = [];
            }
        },

        async loadTriageNotableDetails(case_) {
            const caseId = case_.case_id;
            const existing = this.triageNotableDetails[caseId];

            if (existing && (existing.loading || existing.data)) {
                return;
            }

            this.triageNotableDetails[caseId] = { loading: true, error: null, data: null };

            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/notable');
                this.triageNotableDetails[caseId] = { loading: false, error: null, data: res.data };
            } catch (err) {
                this.triageNotableDetails[caseId] = {
                    loading: false,
                    error: err.response?.data?.detail || err.message,
                    data: null
                };
            }
        },

        // ------------------------------------------------------------------
        // Mutating actions (paste / promote / delete)
        // ------------------------------------------------------------------

        async savePastedNotable() {
            if (!this.notablePasteText.trim()) {
                alert('Paste a notable before saving');
                return;
            }

            this.notablePasteSaving = true;
            try {
                const res = await axios.post(this.apiUrl + '/db/notables/paste', {
                    raw_text: this.notablePasteText,
                    redaction_enabled: this.notableRedactionEnabled,
                    historical: this.notableHistorical
                });
                this.notablePasteResult = res.data;
                this.notablePasteText = '';
                this.loadRecentNotables();
                this.loadDbStats();
            } catch (err) {
                console.error('Failed to save pasted notable:', err.response?.data?.detail || err.message);
            } finally {
                this.notablePasteSaving = false;
            }
        },

        async deletePastedNotable(notable) {
            if (!confirm('Remove this pasted notable from the recent list? This will not delete any triage cases created from it.')) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/notables', {
                    params: { delete_event_id: notable.id }
                });
                this.recentNotables = res.data;
                await this.loadDbStats();
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            }
        },

        async deleteSelectedPastedNotables() {
            if (!this.selectedNotableIds.length) {
                return;
            }

            if (!confirm('Delete ' + this.selectedNotableIds.length + ' pasted notable(s)? This will not delete any triage cases created from them.')) {
                return;
            }

            try {
                await axios.post(this.apiUrl + '/db/notables/batch-delete', {
                    event_ids: this.selectedNotableIds,
                });
                this.selectedNotableIds = [];
                await this.loadRecentNotables();
                await this.loadDbStats();
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            }
        },

        async promoteNotableToTriage(notable) {
            if (notable && notable.historical && !notable.promoted_case_id) {
                console.warn('Historical pasted notables are not promoted to triage.');
                return;
            }
            this.notablePromotingId = notable.id;
            try {
                await axios.post(this.apiUrl + '/db/notables/' + notable.id + '/promote');
                await this.loadRecentNotables();
                await this.loadTriageData();
                await this.loadAnalysisCases();
                await this.loadDbStats();
            } catch (err) {
                console.error('Failed to promote notable to triage:', err.response?.data?.detail || err.message);
            } finally {
                this.notablePromotingId = null;
            }
        },

        async promoteAllOpenPastedNotables() {
            if (this.bulkPromoteNotablesRunning) {
                return;
            }

            const candidates = this.filteredRecentNotables.filter(n => !n.historical && !n.promoted_case_id);
            if (!candidates.length) {
                return;
            }

            this.bulkPromoteNotablesRunning = true;
            try {
                for (const notable of candidates) {
                    try {
                        await axios.post(this.apiUrl + '/db/notables/' + notable.id + '/promote');
                    } catch (err) {
                        console.error('Failed to promote notable', notable.id, ':', err.response?.data?.detail || err.message);
                    }
                }

                await this.loadRecentNotables();
                await this.loadTriageData();
                await this.loadAnalysisCases();
                await this.loadDbStats();
            } finally {
                this.bulkPromoteNotablesRunning = false;
            }
        },

        async deleteTriageCase(case_) {
            if (!confirm('Delete this triage case from the database?')) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/triage', {
                    params: {
                        delete_case_id: case_.case_id,
                        delete_analysis: this.deleteAnalysisWithCase
                    }
                });
                this.triageData = res.data;
                await this.loadAnalysisCases();
                await this.loadDbStats();
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            }
        },

        async deleteSelectedTriageCases() {
            if (!this.selectedTriageCaseIds.length) {
                return;
            }

            const count = this.selectedTriageCaseIds.length;
            const msg = 'Delete ' + count + ' triage case(s) from the database?' + (this.deleteAnalysisWithCase ? ' This will also delete any AI analysis for these cases.' : '');
            if (!confirm(msg)) {
                return;
            }

            try {
                await axios.post(this.apiUrl + '/db/triage/batch-delete', {
                    case_ids: this.selectedTriageCaseIds,
                    delete_analysis: this.deleteAnalysisWithCase,
                });
                this.selectedTriageCaseIds = [];
                await this.loadTriageData();
                await this.loadAnalysisCases();
                await this.loadDbStats();
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            }
        }
    };

    global.DatabaseMethods = DatabaseMethods;

})(typeof window !== 'undefined' ? window : globalThis);
