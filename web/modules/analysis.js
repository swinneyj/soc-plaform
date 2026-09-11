/**
 * modules/analysis.js
 * Full domain methods for the Analysis tab:
 * case change, rules, supportive editor, placeholder aliases,
 * run analysis / phase2, evidence save/load, keys, renderers, copies.
 *
 * Spread into Vue: ...(window.AnalysisMethods || {})
 * Depends on: this.apiUrl, axios, Analysis data properties.
 * Pure key/render helpers also available via utils/keys.js and utils/queryRender.js.
 */
(function (global) {
    'use strict';

    const AnalysisMethods = {
        onAnalysisCaseChanged() {
            this.analysisResult = null;
            this.investigationState = null;
            this.enrichmentManualResults = {};
            this.enrichmentFindingTypes = {};
            this.supportiveManualResults = {};
            this.supportiveFindingTypes = {};
            this.phase2EditedQueries = {};
            this.phase2ManualResults = {};
            this.phase2FindingTypes = {};
            this.phase2ResolutionTypes = {};
            this.phase2ResolutionQuestions = {};
            this.followUpPhase = 2;
            this.analysisSourceNotable = null;

            const caseId = this.analysisCaseId;
            if (!caseId) {
                return;
            }

            // Resume the last local browser snapshot before refreshing the
            // durable evidence/state from the API.
            this.loadAnalysisState(caseId);

            axios
                .get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/notable')
                .then(res => {
                    this.analysisSourceNotable = res.data;
                })
                .catch(err => {
                    console.error('Failed to load source notable for analysis case:', err);
                    this.analysisSourceNotable = null;
                });

                    this.loadSavedSupportiveEvidence(caseId);
                    this.loadSavedEnrichmentEvidence(caseId);
                    this.loadSavedPhase2Evidence(caseId);
                    this.loadInvestigationState(caseId);
        },

        async loadRules() {
            try {
                const res = await axios.get(this.apiUrl + '/db/rules');
                this.availableRules = res.data;
            } catch (err) {
                console.error('Failed to load rules:', err);
                this.availableRules = [];
            }
        },

        async openSupportiveEditor(rule) {
            if (!rule || !rule.rule_id) {
                return;
            }

            this.supportiveEditorRuleId = rule.rule_id;
            this.supportiveEditorError = '';
            this.supportiveEditorBusy = false;
            this.supportiveEditorQueries = [];

            try {
                const res = await axios.get(this.apiUrl + '/db/supportive-queries', {
                    params: { rule_id: this.supportiveEditorRuleId }
                });
                // Clone the array so edits don't mutate the live rule copy
                this.supportiveEditorQueries = (res.data || []).map(q => ({
                    id: q.id,
                    rule_id: q.rule_id,
                    title: q.title,
                    description: q.description || '',
                    spl_query: q.spl_query,
                }));
            } catch (err) {
                console.error('Failed to load supportive queries for rule:', err);
                this.supportiveEditorError = err.response?.data?.detail || err.message || 'Failed to load supportive queries.';
                this.supportiveEditorQueries = [];
            }

            if (this.supportiveEditorQueries.length === 0) {
                // Seed with an empty row to make the editor less blank
                this.supportiveEditorQueries.push({
                    id: null,
                    rule_id: this.supportiveEditorRuleId,
                    title: '',
                    description: '',
                    spl_query: '',
                    localKey: 'new-0',
                });
            } else {
                // Ensure each row has a stable local key
                this.supportiveEditorQueries = this.supportiveEditorQueries.map((q, index) => ({
                    ...q,
                    localKey: q.id ? `db-${q.id}` : `new-${index}`,
                }));
            }

            this.supportiveEditorOpen = true;
        },

        closeSupportiveEditor() {
            this.supportiveEditorOpen = false;
            this.supportiveEditorQueries = [];
            this.supportiveEditorRuleId = '';
            this.supportiveEditorError = '';
        },

        // === Placeholder Alias Management (dynamic, no-code) ===,

        async openPlaceholderAliasEditor() {
            this.placeholderAliasEditorOpen = true;
            this.placeholderAliasEditorError = '';
            this.newAliasForm = { alias: '', fields: '', description: '' };
            this.editingAliasId = null;
            this.placeholderAliasSuggestions = [];
            await this.loadPlaceholderAliasList();
        },

        closePlaceholderAliasEditor() {
            if (this.placeholderAliasEditorBusy) return;
            this.placeholderAliasEditorOpen = false;
            this.placeholderAliasList = [];
            this.newAliasForm = { alias: '', fields: '', description: '' };
            this.editingAliasId = null;
            this.placeholderAliasEditorError = '';
            this.placeholderAliasSuggestions = [];
        },

        async loadPlaceholderAliasList() {
            this.placeholderAliasEditorBusy = true;
            try {
                const res = await axios.get(this.apiUrl + '/db/placeholder-aliases');
                this.placeholderAliasList = res.data || [];
            } catch (err) {
                console.error('Failed to load placeholder aliases:', err);
                this.placeholderAliasEditorError = 'Failed to load aliases from server.';
            } finally {
                this.placeholderAliasEditorBusy = false;
            }
        },

        async savePlaceholderAlias() {
            if (!this.newAliasForm.alias.trim()) {
                this.placeholderAliasEditorError = 'Alias name is required (e.g. url, signature).';
                return;
            }
            this.placeholderAliasEditorBusy = true;
            this.placeholderAliasEditorError = '';

            const payload = {
                alias: this.newAliasForm.alias.trim().toLowerCase(),
                fields: this.newAliasForm.fields.split(',').map(f => f.trim()).filter(Boolean),
                description: this.newAliasForm.description.trim()
            };

            try {
                if (this.editingAliasId) {
                    await axios.put(`${this.apiUrl}/db/placeholder-aliases/${this.editingAliasId}`, payload);
                } else {
                    await axios.post(this.apiUrl + '/db/placeholder-aliases', payload);
                }
                await this.loadPlaceholderAliasList();
                // Reset form
                this.newAliasForm = { alias: '', fields: '', description: '' };
                this.editingAliasId = null;
            } catch (err) {
                console.error('Failed to save placeholder alias:', err);
                this.placeholderAliasEditorError = err.response?.data?.detail || 'Failed to save alias.';
            } finally {
                this.placeholderAliasEditorBusy = false;
            }
        },

        editPlaceholderAlias(alias) {
            this.editingAliasId = alias.id;
            this.newAliasForm = {
                alias: alias.alias,
                fields: (alias.fields || []).join(', '),
                description: alias.description || ''
            };
            this.placeholderAliasEditorError = '';
        },

        async loadPlaceholderAliasSuggestions() {
            if (this.placeholderAliasSuggestionsBusy) {
                return;
            }
            this.placeholderAliasSuggestionsBusy = true;
            this.placeholderAliasEditorError = '';
            try {
                const res = await axios.get(this.apiUrl + '/db/placeholder-aliases/suggestions', {
                    params: { limit_events: 50 }
                });
                this.placeholderAliasSuggestions = (res.data && res.data.candidates) || [];
            } catch (err) {
                console.error('Failed to load alias field suggestions:', err);
                this.placeholderAliasEditorError = 'Failed to load field suggestions from recent notables.';
                this.placeholderAliasSuggestions = [];
            } finally {
                this.placeholderAliasSuggestionsBusy = false;
            }
        },

        isAliasFieldSelected(fieldName) {
            const raw = this.newAliasForm.fields || '';
            const parts = raw.split(',').map(f => f.trim()).filter(Boolean);
            return parts.some(f => f.toLowerCase() === String(fieldName).toLowerCase());
        },

        toggleAliasFieldCandidate(fieldName) {
            const raw = this.newAliasForm.fields || '';
            let parts = raw.split(',').map(f => f.trim()).filter(Boolean);
            const normalized = String(fieldName).trim();
            const idx = parts.findIndex(f => f.toLowerCase() === normalized.toLowerCase());
            if (idx >= 0) {
                parts.splice(idx, 1);
            } else {
                parts.push(normalized);
            }
            this.newAliasForm.fields = parts.join(', ');
        },

        async deletePlaceholderAlias(id) {
            if (!confirm('Delete this placeholder alias? Supportive queries using it will fall back to direct field matching.')) {
                return;
            }
            this.placeholderAliasEditorBusy = true;
            try {
                await axios.delete(`${this.apiUrl}/db/placeholder-aliases/${id}`);
                await this.loadPlaceholderAliasList();
                if (this.editingAliasId === id) {
                    this.editingAliasId = null;
                    this.newAliasForm = { alias: '', fields: '', description: '' };
                }
            } catch (err) {
                console.error('Failed to delete placeholder alias:', err);
                this.placeholderAliasEditorError = 'Failed to delete alias.';
            } finally {
                this.placeholderAliasEditorBusy = false;
            }
        },

        addSupportiveQueryRow() {
            const index = this.supportiveEditorQueries.length;
            this.supportiveEditorQueries.push({
                id: null,
                rule_id: this.supportiveEditorRuleId,
                title: '',
                description: '',
                spl_query: '',
                localKey: `new-${index}`,
            });
        },

        async removeSupportiveQuery(index, q) {
            if (this.supportiveEditorBusy) {
                return;
            }

            const row = this.supportiveEditorQueries[index];
            if (!row) {
                return;
            }

            // If this row has not been persisted yet, just remove it locally
            if (!row.id) {
                this.supportiveEditorQueries.splice(index, 1);
                return;
            }

            const confirmed = window.confirm('Delete this supportive query from the database?');
            if (!confirmed) {
                return;
            }

            this.supportiveEditorBusy = true;
            this.supportiveEditorError = '';
            try {
                await axios.delete(this.apiUrl + '/db/supportive-queries/' + row.id);
                this.supportiveEditorQueries.splice(index, 1);
                // Refresh rules so Analysis/Closure tabs reflect the change
                await this.loadRules();
            } catch (err) {
                console.error('Failed to delete supportive query:', err);
                this.supportiveEditorError = err.response?.data?.detail || err.message || 'Failed to delete query.';
            } finally {
                this.supportiveEditorBusy = false;
            }
        },

        async saveSupportiveQueries() {
            if (!this.supportiveEditorRuleId) {
                return;
            }

            // Basic validation: every non-empty row needs title and SPL
            const rowsToApply = this.supportiveEditorQueries.filter(q =>
                (q.title && q.title.trim()) || (q.spl_query && q.spl_query.trim())
            );

            for (const q of rowsToApply) {
                if (!q.title || !q.title.trim() || !q.spl_query || !q.spl_query.trim()) {
                    this.supportiveEditorError = 'Each query must have at least a title and SPL query.';
                    return;
                }
            }

            this.supportiveEditorBusy = true;
            this.supportiveEditorError = '';

            try {
                // Persist each row individually; this is fine for the
                // small, analyst-driven counts typical of supportive queries.
                for (const q of rowsToApply) {
                    const payload = {
                        rule_id: this.supportiveEditorRuleId,
                        title: q.title.trim(),
                        description: (q.description || '').trim(),
                        spl_query: q.spl_query.trim(),
                    };

                    if (q.id) {
                        await axios.put(this.apiUrl + '/db/supportive-queries/' + q.id, payload);
                    } else {
                        const res = await axios.post(this.apiUrl + '/db/supportive-queries', payload);
                        q.id = res.data.id;
                    }
                }

                await this.loadRules();

                // If the currently selected rule matches, refresh its
                // reference so the updated queries show up immediately.
                if (this.selectedRule && this.selectedRule.rule_id === this.supportiveEditorRuleId) {
                    this.selectedRule = this.availableRules.find(r => r.rule_id === this.supportiveEditorRuleId) || this.selectedRule;
                }

                this.closeSupportiveEditor();
            } catch (err) {
                console.error('Failed to save supportive queries:', err);
                this.supportiveEditorError = err.response?.data?.detail || err.message || 'Failed to save queries.';
            } finally {
                this.supportiveEditorBusy = false;
            }
        },

        async checkOllama() {
            try {
                const res = await axios.get(this.apiUrl + '/db/ollama/health');
                this.ollamaHealth = res.data;
                if (this.ollamaHealth.models.length > 0) {
                    const preferred = 'llama3.1:8b';
                    const models = this.ollamaHealth.models;
                    let selected = models[0];
                    if (models.includes(preferred)) {
                        selected = preferred;
                    }
                    this.analysisModel = selected;
                    if (!this.phase2Model || !models.includes(this.phase2Model)) {
                        this.phase2Model = selected;
                    }
                    // Keep Code Review model aligned with the same default
                    // when it hasn't been explicitly set yet.
                    if (!this.codeReviewForm.model || !this.codeReviewModels.includes(this.codeReviewForm.model)) {
                        this.codeReviewForm.model = selected;
                    }
                }
            } catch (err) {
                this.ollamaHealth = { available: false, models: [] };
            }
        },

        async loadPlaceholderAliases() {
            try {
                const res = await axios.get(this.apiUrl + '/db/placeholder-aliases');
                const map = {};
                for (const item of res.data || []) {
                    map[item.alias.toLowerCase()] = item.fields || [];
                }
                this.placeholderAliases = map;
            } catch (err) {
                console.warn('Could not load placeholder aliases, using defaults:', err);
                // Keep built-in defaults in renderSupportiveQuery
            }
        },

        async analyzeCase(case_) {
            this.analysisCaseId = case_.case_id;
            this.currentTab = 'analysis';
            this.onAnalysisCaseChanged();
        },

        _emptyInvestigationState(caseId) {
            return {
                case_id: caseId || '',
                rule_id: '',
                current_hypothesis: '',
                provisional_disposition: 'undetermined',
                disposition_confidence: 0,
                loop_status: 'collecting_evidence',
                iteration_count: 0,
                unresolved_questions: [],
                closure_blockers: ['No saved investigative evidence exists yet for this case.'],
                recommended_next_actions: [],
                evidence_summary: {
                    total_items: 0,
                    substantive_items: 0,
                    pending_items: 0,
                    by_finding: { supports: 0, refutes: 0, neutral: 0 },
                    by_source_system: {},
                    recent_titles: [],
                    timeline: []
                },
                last_analysis_stage: 'initial',
                updated_at: null
            };
        },

        async loadInvestigationState(caseId) {
            if (!caseId) {
                return;
            }

            // Always show the Investigation Loop panel for the selected case.
            // If the API is down or returns nothing useful, fall back to an empty
            // shell and hydrate the timeline from GET /evidence.
            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/investigation-state');
                this.investigationState = res.data || this._emptyInvestigationState(caseId);
            } catch (err) {
                console.error('Failed to load investigation state:', err);
                this.investigationState = this._emptyInvestigationState(caseId);
            }
            await this._hydrateTimelineFromEvidence(caseId);
        },

        /**
         * Build / repair Evidence Timeline from the durable evidence ledger.
         * - Attaches missing ids onto existing timeline rows
         * - If timeline is empty but evidence rows exist, builds timeline from them
         *   so the panel is never blank solely because investigation_state is stale
         */
        async _hydrateTimelineFromEvidence(caseId) {
            if (!caseId) return;
            if (!this.investigationState) {
                this.investigationState = this._emptyInvestigationState(caseId);
            }

            let rows = [];
            try {
                const res = await axios.get(
                    this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence'
                );
                rows = Array.isArray(res.data) ? res.data : [];
            } catch (err) {
                console.warn('Could not load evidence ledger for timeline:', err);
                return;
            }

            const state = this.investigationState;
            const summary = Object.assign({}, state.evidence_summary || {});
            let timeline = Array.isArray(summary.timeline) ? summary.timeline.slice() : [];

            // Build from ledger when timeline is empty
            if (!timeline.length && rows.length) {
                timeline = rows.map((row) => {
                    let raw = row.raw_result;
                    if (typeof raw === 'string') {
                        try { raw = JSON.parse(raw); } catch (e) { raw = { result_text: raw }; }
                    }
                    raw = raw || {};
                    const summaryText = (raw.analyst_summary || raw.result_text || '').toString().trim();
                    const finding = (raw.finding_type || 'neutral').toString().toLowerCase();
                    return {
                        id: row.id,
                        title: row.query_title || 'Evidence',
                        source_system: row.source_system || 'unknown',
                        finding_type: finding,
                        summary: summaryText.slice(0, 500),
                        has_substantive_observation: !!summaryText,
                        created_at: row.created_at || null
                    };
                });
            } else if (timeline.length && rows.length) {
                // Attach missing ids
                const byKey = {};
                const byTitle = {};
                for (const row of rows) {
                    const t = String(row.query_title || '').trim().toLowerCase();
                    const s = String(row.source_system || '').trim().toLowerCase();
                    const key = t + '||' + s;
                    if (!byKey[key]) byKey[key] = [];
                    byKey[key].push(row.id);
                    if (!byTitle[t]) byTitle[t] = [];
                    byTitle[t].push(row.id);
                }
                for (const item of timeline) {
                    if (!item || (item.id !== null && item.id !== undefined && item.id !== '')) continue;
                    const key = String(item.title || '').trim().toLowerCase()
                        + '||'
                        + String(item.source_system || '').trim().toLowerCase();
                    let ids = byKey[key];
                    if (!ids || !ids.length) {
                        ids = byTitle[String(item.title || '').trim().toLowerCase()];
                    }
                    if (ids && ids.length) {
                        item.id = ids.shift();
                    }
                }
            }

            // Refresh summary counts from timeline
            const byFinding = { supports: 0, refutes: 0, neutral: 0 };
            let substantive = 0;
            let pending = 0;
            for (const item of timeline) {
                if (item && item.has_substantive_observation) {
                    substantive += 1;
                    const ft = (item.finding_type || 'neutral').toLowerCase();
                    if (byFinding[ft] === undefined) byFinding.neutral += 1;
                    else byFinding[ft] += 1;
                } else {
                    pending += 1;
                }
            }
            summary.timeline = timeline;
            summary.total_items = substantive;
            summary.substantive_items = substantive;
            summary.pending_items = pending;
            summary.by_finding = byFinding;
            summary.saved_entries = rows.length;

            this.investigationState = {
                ...state,
                evidence_summary: summary
            };
        },

        // Back-compat alias used by delete/refresh paths
        async _ensureTimelineEvidenceIds(caseId) {
            return this._hydrateTimelineFromEvidence(caseId);
        },

        async _resolveEvidenceId(item) {
            if (!item) return null;
            if (item.id !== null && item.id !== undefined && item.id !== '') {
                return Number(item.id);
            }
            if (!this.analysisCaseId) return null;
            await this._ensureTimelineEvidenceIds(this.analysisCaseId);
            if (item.id !== null && item.id !== undefined && item.id !== '') {
                return Number(item.id);
            }
            // Final attempt: match live evidence list by title
            try {
                const res = await axios.get(
                    this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence'
                );
                const rows = Array.isArray(res.data) ? res.data : [];
                const title = String(item.title || '').trim().toLowerCase();
                const source = String(item.source_system || '').trim().toLowerCase();
                let match = rows.find((r) =>
                    String(r.query_title || '').trim().toLowerCase() === title
                    && String(r.source_system || '').trim().toLowerCase() === source
                );
                if (!match && title) {
                    match = rows.find((r) =>
                        String(r.query_title || '').trim().toLowerCase() === title
                    );
                }
                if (match && match.id !== null && match.id !== undefined) {
                    item.id = match.id;
                    return Number(match.id);
                }
            } catch (err) {
                console.warn('Evidence id resolve failed:', err);
            }
            return null;
        },

        async _refreshEvidenceAfterDelete(investigationState) {
            if (investigationState) {
                this.investigationState = investigationState;
                await this._ensureTimelineEvidenceIds(this.analysisCaseId);
            } else {
                await this.loadInvestigationState(this.analysisCaseId);
            }
            await this.loadSavedSupportiveEvidence(this.analysisCaseId);
            await this.loadSavedEnrichmentEvidence(this.analysisCaseId);
            await this.loadSavedPhase2Evidence(this.analysisCaseId);
        },

        _evidenceRowToSaveEntry(row) {
            const raw = row && row.raw_result;
            let parsed = raw;
            if (typeof raw === 'string') {
                try {
                    parsed = JSON.parse(raw);
                } catch (e) {
                    parsed = { result_text: raw };
                }
            }
            parsed = parsed || {};
            return {
                query_title: row.query_title || 'Evidence',
                query_text: parsed.query_text || '',
                result_text: parsed.result_text || '',
                analyst_summary: parsed.analyst_summary || '',
                finding_type: parsed.finding_type || 'neutral'
            };
        },

        /**
         * Compatibility delete path for hosts that do not yet expose
         * /evidence/batch-delete or /evidence/delete-all (405/404).
         * Uses the long-standing POST /evidence save endpoint with
         * replace_existing to rewrite remaining rows per source_system.
         */
        async _deleteEvidenceViaRewrite(idsToRemove, deleteAll) {
            const caseId = this.analysisCaseId;
            const listRes = await axios.get(
                this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence'
            );
            const rows = Array.isArray(listRes.data) ? listRes.data : [];
            const removeSet = new Set((idsToRemove || []).map((id) => Number(id)));

            const remaining = deleteAll
                ? []
                : rows.filter((row) => !removeSet.has(Number(row.id)));

            // Group remaining by source_system; also clear source systems that
            // had only deleted rows by including empty groups from original set.
            const sources = new Set();
            rows.forEach((row) => sources.add((row.source_system || 'supportive_manual').trim() || 'supportive_manual'));
            if (!sources.size) {
                sources.add('supportive_manual');
            }

            const bySource = {};
            sources.forEach((src) => { bySource[src] = []; });
            remaining.forEach((row) => {
                const src = (row.source_system || 'supportive_manual').trim() || 'supportive_manual';
                if (!bySource[src]) bySource[src] = [];
                bySource[src].push(this._evidenceRowToSaveEntry(row));
            });

            let lastState = null;
            for (const sourceSystem of Object.keys(bySource)) {
                const res = await axios.post(
                    this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence',
                    {
                        source_system: sourceSystem,
                        replace_existing: true,
                        entries: bySource[sourceSystem]
                    }
                );
                if (res.data && res.data.investigation_state) {
                    lastState = res.data.investigation_state;
                }
            }
            return lastState;
        },

        async _postDeleteIds(ids) {
            const uniqueIds = Array.from(new Set((ids || []).map((id) => Number(id)).filter((id) => !Number.isNaN(id))));
            if (!uniqueIds.length) {
                throw new Error('No evidence ids to delete');
            }
            try {
                return await axios.post(
                    this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence/batch-delete',
                    { ids: uniqueIds }
                );
            } catch (err) {
                const status = err.response && err.response.status;
                if (status === 404 || status === 405) {
                    // API build on this port is missing dedicated delete routes.
                    const state = await this._deleteEvidenceViaRewrite(uniqueIds, false);
                    return { data: { investigation_state: state, success: true, deleted_ids: uniqueIds } };
                }
                throw err;
            }
        },

        async _postDeleteAll() {
            try {
                return await axios.post(
                    this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence/delete-all'
                );
            } catch (err) {
                const status = err.response && err.response.status;
                if (status === 404 || status === 405) {
                    const state = await this._deleteEvidenceViaRewrite([], true);
                    return { data: { investigation_state: state, success: true } };
                }
                throw err;
            }
        },

        async deleteEvidence(item) {
            if (!this.analysisCaseId || !item) {
                return;
            }
            const evidenceId = await this._resolveEvidenceId(item);
            if (evidenceId === null || Number.isNaN(evidenceId)) {
                alert('Could not resolve a database id for this evidence item. Try Save Evidence, then reload the case.');
                return;
            }
            const title = item.title || ('evidence #' + evidenceId);
            if (!confirm('Delete evidence "' + title + '" from this case?')) {
                return;
            }
            try {
                const res = await this._postDeleteIds([evidenceId]);
                await this._refreshEvidenceAfterDelete(res.data && res.data.investigation_state);
            } catch (err) {
                console.error('Failed to delete evidence:', err);
                const detail = err.response && err.response.data && err.response.data.detail
                    ? err.response.data.detail
                    : err.message;
                alert('Failed to delete evidence: ' + detail);
            }
        },

        async deleteEvidenceBatch(itemsOrIds) {
            if (!this.analysisCaseId) {
                return;
            }
            const list = Array.isArray(itemsOrIds) ? itemsOrIds : [];
            const evidenceIds = [];
            for (const entry of list) {
                if (entry && typeof entry === 'object') {
                    const resolved = await this._resolveEvidenceId(entry);
                    if (resolved !== null && !Number.isNaN(resolved)) {
                        evidenceIds.push(resolved);
                    }
                } else {
                    const n = Number(entry);
                    if (!Number.isNaN(n)) {
                        evidenceIds.push(n);
                    }
                }
            }
            const uniqueIds = Array.from(new Set(evidenceIds));
            if (!uniqueIds.length) {
                alert('Could not resolve database ids for the selected evidence. Try Save Evidence, then reload the case.');
                return;
            }
            const label = uniqueIds.length === 1
                ? '1 selected evidence item'
                : (uniqueIds.length + ' selected evidence items');
            if (!confirm('Delete ' + label + ' from this case?')) {
                return;
            }
            try {
                const res = await this._postDeleteIds(uniqueIds);
                await this._refreshEvidenceAfterDelete(res.data && res.data.investigation_state);
            } catch (err) {
                console.error('Failed to delete selected evidence:', err);
                const detail = err.response && err.response.data && err.response.data.detail
                    ? err.response.data.detail
                    : err.message;
                alert('Failed to delete selected evidence: ' + detail);
            }
        },

        async deleteAllEvidence() {
            if (!this.analysisCaseId) {
                return;
            }
            if (!confirm('Delete ALL evidence for case ' + this.analysisCaseId + '? This cannot be undone.')) {
                return;
            }
            try {
                const res = await this._postDeleteAll();
                await this._refreshEvidenceAfterDelete(res.data && res.data.investigation_state);
            } catch (err) {
                console.error('Failed to delete all evidence:', err);
                const detail = err.response && err.response.data && err.response.data.detail
                    ? err.response.data.detail
                    : err.message;
                alert('Failed to delete all evidence: ' + detail);
            }
        },

        _startAnalysisStatus(message) {
            if (this.analysisStatusTimer) {
                clearInterval(this.analysisStatusTimer);
            }
            this.analysisStatusStartedAt = Date.now();
            this.analysisStatus = {
                phase: 'preparing',
                message: message || 'Preparing assessment...',
                elapsedSeconds: 0,
                timedOut: false,
                error: ''
            };
            this.analysisStatusTimer = setInterval(() => {
                const elapsedSeconds = Math.floor((Date.now() - this.analysisStatusStartedAt) / 1000);
                const timedOut = elapsedSeconds >= 30;
                this.analysisStatus = {
                    ...this.analysisStatus,
                    phase: timedOut ? 'timeout' : this.analysisStatus.phase,
                    message: timedOut
                        ? 'Assessment is still processing after 30 seconds. Ollama may be busy or the request may be stuck.'
                        : this.analysisStatus.message,
                    elapsedSeconds,
                    timedOut
                };
            }, 1000);
        },

        _setAnalysisStatus(phase, message, error = '') {
            const elapsedSeconds = this.analysisStatusStartedAt
                ? Math.floor((Date.now() - this.analysisStatusStartedAt) / 1000)
                : 0;
            this.analysisStatus = {
                ...this.analysisStatus,
                phase,
                message,
                elapsedSeconds,
                timedOut: phase === 'timeout' || Boolean(this.analysisStatus && this.analysisStatus.timedOut),
                error
            };
        },

        _stopAnalysisStatusTimer() {
            if (this.analysisStatusTimer) {
                clearInterval(this.analysisStatusTimer);
                this.analysisStatusTimer = null;
            }
        },

        _startPhase2Status(message) {
            if (this.phase2StatusTimer) clearInterval(this.phase2StatusTimer);
            this.phase2StatusStartedAt = Date.now();
            this.phase2Status = {
                phase: 'preparing', message: message || ('Preparing Phase ' + (this.followUpPhase || 2) + '...'),
                elapsedSeconds: 0, timedOut: false, error: ''
            };
            this.phase2StatusTimer = setInterval(() => {
                const elapsedSeconds = Math.floor((Date.now() - this.phase2StatusStartedAt) / 1000);
                const timedOut = elapsedSeconds >= 30;
                this.phase2Status = {
                    ...this.phase2Status,
                    phase: timedOut ? 'timeout' : this.phase2Status.phase,
                    message: timedOut
                        ? 'Phase ' + (this.followUpPhase || 2) + ' analysis is still processing after 30 seconds. Ollama may be busy or the request may be stuck.'
                        : this.phase2Status.message,
                    elapsedSeconds, timedOut
                };
            }, 1000);
        },

        _setPhase2Status(phase, message, error = '') {
            const elapsedSeconds = this.phase2StatusStartedAt
                ? Math.floor((Date.now() - this.phase2StatusStartedAt) / 1000)
                : 0;
            this.phase2Status = {
                ...this.phase2Status, phase, message, elapsedSeconds,
                timedOut: phase === 'timeout' || Boolean(this.phase2Status && this.phase2Status.timedOut), error
            };
        },

        _stopPhase2StatusTimer() {
            if (this.phase2StatusTimer) {
                clearInterval(this.phase2StatusTimer);
                this.phase2StatusTimer = null;
            }
        },

        async runAnalysis() {
            if (!this.analysisCaseId || !this.analysisModel) {
                alert('Please select a case ID and model');
                return;
            }

            const requestId = ++this.analysisRequestId;
            this.analysisRunning = true;
            this.analysisAbortController = new AbortController();
            this._startAnalysisStatus('Saving collected evidence...');
            try {
                await this.saveSupportiveEvidence({ silent: true });
                await this.saveEnrichmentEvidence({ silent: true });
                this._setAnalysisStatus('ollama', 'Sending evidence to Ollama for initial assessment...');

                let combinedContext = this.analysisContext || '';
                // Stage 3 is an independent initial-assessment rerun. Do not
                // feed a prior Phase 2 response back into it; Phase 2 itself
                // remains responsible for using the complete evidence ledger.
                const priorAnalysisText = (this.analysisResult && this.analysisResult.analysis || '').toString().trim();

                const res = await axios.post(this.apiUrl + '/db/analyze', {
                    case_id: this.analysisCaseId,
                    model: this.analysisModel,
                    context: combinedContext,
                    prior_analysis: priorAnalysisText,
                    analysis_stage: 'initial'
                }, { signal: this.analysisAbortController.signal });
                // Ignore stale responses if a newer analysis has been started or cancelled.
                if (requestId === this.analysisRequestId) {
                    const newResult = res.data;
                    // If the new response lacks phase2_queries but we previously had
                    // them, preserve the existing list so phase 2 cards do not vanish.
                    if (
                        (!newResult.phase2_queries || !newResult.phase2_queries.length) &&
                        this.analysisResult &&
                        this.analysisResult.phase2_queries &&
                        this.analysisResult.phase2_queries.length
                    ) {
                        newResult.phase2_queries = this.analysisResult.phase2_queries;
                    }
                    this.analysisResult = newResult;
                    this.investigationState = newResult.investigation_state || this.investigationState;
                    await this._ensureTimelineEvidenceIds(this.analysisCaseId);
                    this._storeAnalysisStateSnapshot();
                }
                if (requestId === this.analysisRequestId) {
                    const metrics = res.data && res.data.ollama_metrics;
                    const detail = metrics && metrics.total_duration_seconds
                        ? ` Ollama generated ${metrics.eval_tokens || 0} tokens in ${metrics.total_duration_seconds}s.`
                        : '';
                    this._setAnalysisStatus('complete', 'Initial assessment completed.' + detail);
                }
            } catch (err) {
                if (requestId === this.analysisRequestId) {
                    this._setAnalysisStatus('error', 'Initial assessment failed.', err.response?.data?.detail || err.message);
                    if (!(err.name === 'CanceledError' || err.code === 'ERR_CANCELED' || err.name === 'AbortError')) {
                        alert('Error: ' + (err.response?.data?.detail || err.message));
                    }
                }
            } finally {
                if (requestId === this.analysisRequestId) {
                    this.analysisRunning = false;
                    this.analysisAbortController = null;
                }
                this._stopAnalysisStatusTimer();
            }
        },

        getPhase2Key(q) {
            const title = (q && q.title ? q.title : '').toString().toLowerCase().trim();
            if (!title) {
                return 'phase2:title:unknown';
            }
            return 'phase2:' + title.replace(/\s+/g, '_').slice(0, 64);
        },

        getPhase2KeyFromTitle(title) {
            const normalized = (title || '').toString().toLowerCase().trim();
            if (!normalized) {
                return 'phase2:title:unknown';
            }
            return 'phase2:' + normalized.replace(/\s+/g, '_').slice(0, 64);
        },

        getPhase2Template(q) {
            const key = this.getPhase2Key(q);
            const existing = this.phase2EditedQueries[key];
            if (existing !== undefined && existing !== null && existing !== '') {
                return existing;
            }
            const raw = (q && q.spl ? q.spl : '').toString();
            // Resolve placeholders once so the editable SPL shows the concrete
            // value (e.g., src_ip) for this case. Subsequent edits operate on
            // the hard-coded version, matching the initial supportive queries.
            const resolved = this.renderPhase2Query(raw);
            this.phase2EditedQueries[key] = resolved;
            return resolved;
        },

        onPhase2TemplateInput(q, value) {
            const key = this.getPhase2Key(q);
            this.phase2EditedQueries[key] = value;
        },

        copyPhase2SPL(q) {
            const key = this.getPhase2Key(q);
            const template = (this.phase2EditedQueries[key] || q.spl || '').toString();
            const text = this.renderPhase2Query(template);
            if (!text) {
                alert('No SPL query text available to copy');
                return;
            }

            if (this.hasUnresolvedPlaceholders(text)) {
                alert('This Phase 2 query still contains unresolved placeholders like $host$ or $user$. Use enrichment data or edit the SPL before copying it into Splunk.');
                return;
            }

            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                alert('Clipboard API not available; copy manually from the card.');
                return;
            }

            navigator.clipboard.writeText(text).catch(err => {
                console.error('Failed to copy SPL:', err);
                alert('Failed to copy SPL to clipboard');
            });
        },

        async runPhase2Analysis() {
            if (!this.analysisCaseId) {
                alert('Please select a case ID');
                return;
            }

            const effectiveModel = this.phase2Model || this.analysisModel;
            if (!effectiveModel) {
                alert('Please select a Phase 2 model (or an Analysis model)');
                return;
            }

            const requestId = ++this.analysisRequestId;
            this.analysisRunning = true;
            this.analysisAbortController = new AbortController();
            const phaseNumber = this.followUpPhase || 2;
            this._startPhase2Status('Saving Phase ' + phaseNumber + ' evidence...');
            try {
                await this.savePhase2Evidence({ silent: true });
                this._setPhase2Status('ollama', 'Sending saved follow-up evidence to Ollama...');

                let combinedContext = this.analysisContext || '';
                const priorAnalysisText = (
                    (this.phase2Result && this.phase2Result.analysis) ||
                    (this.analysisResult && this.analysisResult.analysis) ||
                    ''
                ).toString().trim();

                const res = await axios.post(this.apiUrl + '/db/analyze', {
                    case_id: this.analysisCaseId,
                    model: effectiveModel,
                    context: combinedContext,
                    prior_analysis: priorAnalysisText,
                    analysis_stage: priorAnalysisText ? 'follow_up' : 'initial'
                }, { signal: this.analysisAbortController.signal });
                if (requestId === this.analysisRequestId) {
                    const newResult = res.data;
                    if (
                        (!newResult.phase2_queries || !newResult.phase2_queries.length) &&
                        this.analysisResult &&
                        this.analysisResult.phase2_queries &&
                        this.analysisResult.phase2_queries.length
                    ) {
                        newResult.phase2_queries = this.analysisResult.phase2_queries;
                    }
                    this.phase2Result = newResult;
                    this.investigationState = newResult.investigation_state || this.investigationState;
                    await this._ensureTimelineEvidenceIds(this.analysisCaseId);
                    this._storeAnalysisStateSnapshot();
                }
                if (requestId === this.analysisRequestId) {
                    const metrics = res.data && res.data.ollama_metrics;
                    const detail = metrics && metrics.total_duration_seconds
                        ? ` Ollama generated ${metrics.eval_tokens || 0} tokens in ${metrics.total_duration_seconds}s.`
                        : '';
                    this._setPhase2Status('complete', 'Phase ' + phaseNumber + ' analysis completed.' + detail);
                }
            } catch (err) {
                if (requestId === this.analysisRequestId) {
                    if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED' || err.name === 'AbortError') {
                        this._setPhase2Status('cancelled', 'Phase ' + phaseNumber + ' analysis cancelled.', 'The in-flight response was ignored.');
                    } else {
                        this._setPhase2Status('error', 'Phase ' + phaseNumber + ' analysis failed.', err.response?.data?.detail || err.message);
                        alert('Error: ' + (err.response?.data?.detail || err.message));
                    }
                }
            } finally {
                if (requestId === this.analysisRequestId) {
                    this.analysisRunning = false;
                    this.analysisAbortController = null;
                }
                this._stopPhase2StatusTimer();
            }
        },

        async savePhase2Evidence(options = {}) {
            if (!this.analysisCaseId) {
                return;
            }

            const phase2Queries = (this.phase2Result && this.phase2Result.phase2_queries) ||
                (this.analysisResult && this.analysisResult.phase2_queries) || [];
            const entries = [];
            for (const q of phase2Queries) {
                const key = this.getPhase2Key(q);
                const resultText = (this.phase2ManualResults[key] || '').trim();
                const queryText = (this.phase2EditedQueries[key] || q.spl || '').toString().trim();
                if (!resultText && !queryText) {
                    continue;
                }

                entries.push({
                    query_title: q.title || 'Unnamed phase 2 query',
                    query_text: queryText,
                    result_text: resultText,
                    analyst_summary: '',
                    finding_type: this.phase2FindingTypes[key] || 'neutral',
                    question_resolution: this.phase2ResolutionTypes[key] || 'not_resolved',
                    target_questions: this.phase2ResolutionQuestions[key]
                        ? [this.phase2ResolutionQuestions[key]]
                        : (q.target_questions || []),
                    result_status: (this.evidenceResultStatuses && this.evidenceResultStatuses[key]) || 'success',
                });
            }

            if (!entries.length) {
                return;
            }

            const res = await axios.post(this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence', {
                source_system: 'phase' + (this.followUpPhase || 2) + '_manual',
                replace_existing: true,
                entries,
            });

            if (res.data && res.data.investigation_state) {
                this.investigationState = res.data.investigation_state;
            }

            if (!options.silent) {
                alert('Phase ' + (this.followUpPhase || 2) + ' evidence saved for case ' + this.analysisCaseId);
                await this.loadInvestigationState(this.analysisCaseId);
            }
        },

        async loadSavedPhase2Evidence(caseId) {
            if (!caseId) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence', {
                    params: { source_system: 'phase' + (this.followUpPhase || 2) + '_manual' }
                });
                const saved = {};
                for (const item of (res.data || [])) {
                    const key = this.getPhase2KeyFromTitle(item.query_title || '');
                    const raw = item.raw_result || {};
                    saved[key] = (raw.result_text || '').toString();
                    this.phase2FindingTypes[key] = (raw.finding_type || 'neutral').toString();
                    this.phase2ResolutionTypes[key] = (raw.question_resolution || 'not_resolved').toString();
                    if (raw.target_questions && raw.target_questions.length) {
                        this.phase2ResolutionQuestions[key] = raw.target_questions[0].toString();
                    }
                    if (raw.query_text) {
                        this.phase2EditedQueries[key] = raw.query_text.toString();
                    }
                }
                this.phase2ManualResults = saved;
            } catch (err) {
                console.error('Failed to load saved phase 2 evidence:', err);
            }
        },

        async saveSupportiveEvidence(options = {}) {
            if (!this.analysisCaseId || !this.analysisRule || !this.analysisRule.supportive_queries) {
                return;
            }

            const entries = [];
            for (const q of this.analysisRule.supportive_queries) {
                const key = this.getSupportiveKey(q);
                const resultText = (this.supportiveManualResults[key] || '').trim();
                if (!resultText) {
                    continue;
                }

                entries.push({
                    query_title: q.title || 'Unnamed supportive query',
                    query_text: this.renderSupportiveQuery(q),
                    result_text: resultText,
                    analyst_summary: '',
                    finding_type: this.supportiveFindingTypes[key] || 'neutral',
                    result_status: (this.evidenceResultStatuses && this.evidenceResultStatuses[key]) || 'success',
                });
            }

            if (!entries.length) {
                return;
            }

            const res = await axios.post(this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence', {
                source_system: 'supportive_manual',
                replace_existing: true,
                entries,
            });

            if (res.data && res.data.investigation_state) {
                this.investigationState = res.data.investigation_state;
            }

            if (!options.silent) {
                alert('Supportive evidence saved for case ' + this.analysisCaseId);
                await this.loadInvestigationState(this.analysisCaseId);
            }
        },

        async saveEnrichmentEvidence(options = {}) {
            if (!this.analysisCaseId || !this.analysisSourceNotable || !this.analysisSourceNotable.parse_assessment || !this.analysisSourceNotable.parse_assessment.generic_queries) {
                return;
            }

            const entries = [];
            for (const q of this.analysisSourceNotable.parse_assessment.generic_queries) {
                const key = this.getEnrichmentKey(q);
                const resultText = (this.enrichmentManualResults[key] || '').trim();
                if (!resultText) {
                    continue;
                }

                entries.push({
                    query_title: q.title || 'Unnamed enrichment query',
                    query_text: (q && q.spl ? q.spl : '').toString().trim(),
                    result_text: resultText,
                    analyst_summary: '',
                    finding_type: this.enrichmentFindingTypes[key] || 'neutral',
                });
            }

            if (!entries.length) {
                return;
            }

            await axios.post(this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence', {
                source_system: 'generic_enrichment',
                replace_existing: true,
                entries,
            });

            if (!options.silent) {
                alert('Enrichment evidence saved for case ' + this.analysisCaseId);
                await this.loadInvestigationState(this.analysisCaseId);
            }
        },

        async loadSavedSupportiveEvidence(caseId) {
            if (!caseId) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence', {
                    params: { source_system: 'supportive_manual' }
                });
                const saved = {};
                for (const item of (res.data || [])) {
                    const key = this.getSupportiveKeyFromTitle(item.query_title || '');
                    const raw = item.raw_result || {};
                    saved[key] = (raw.result_text || '').toString();
                    this.supportiveFindingTypes[key] = (raw.finding_type || 'neutral').toString();
                }
                this.supportiveManualResults = saved;
            } catch (err) {
                console.error('Failed to load saved supportive evidence:', err);
            }
        },

        async loadSavedEnrichmentEvidence(caseId) {
            if (!caseId) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence', {
                    params: { source_system: 'generic_enrichment' }
                });
                const saved = {};
                for (const item of (res.data || [])) {
                    const key = this.getEnrichmentKeyFromTitle(item.query_title || '');
                    const raw = item.raw_result || {};
                    saved[key] = (raw.result_text || '').toString();
                    this.enrichmentFindingTypes[key] = (raw.finding_type || 'neutral').toString();
                }
                this.enrichmentManualResults = saved;
            } catch (err) {
                console.error('Failed to load saved enrichment evidence:', err);
            }
        },

        copyAnalysisResult() {
            const text = (this.analysisResult && this.analysisResult.analysis) || '';
            if (!text) {
                alert('No analysis text to copy');
                return;
            }
            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                alert('Clipboard access is not available in this browser context');
                return;
            }
            navigator.clipboard.writeText(text)
                .then(() => {
                    alert('Analysis copied to clipboard');
                })
                .catch(err => {
                    console.error('Failed to copy analysis:', err);
                    alert('Failed to copy analysis to clipboard');
                });
        },

        _storeAnalysisStateSnapshot() {
            if (!this.analysisCaseId) {
                return;
            }

            const key = 'analysis_state:' + this.analysisCaseId;
            const snapshot = {
                analysisContext: this.analysisContext,
                analysisModel: this.analysisModel,
                phase2Model: this.phase2Model,
                followUpPhase: this.followUpPhase,
                supportiveManualResults: this.supportiveManualResults,
                supportiveFindingTypes: this.supportiveFindingTypes,
                enrichmentManualResults: this.enrichmentManualResults,
                enrichmentFindingTypes: this.enrichmentFindingTypes,
                phase2EditedQueries: this.phase2EditedQueries,
                phase2ManualResults: this.phase2ManualResults,
                phase2FindingTypes: this.phase2FindingTypes,
                phase2ResolutionTypes: this.phase2ResolutionTypes,
                phase2ResolutionQuestions: this.phase2ResolutionQuestions,
                analysisResult: this.analysisResult,
                phase2Result: this.phase2Result,
                investigationState: this.investigationState,
            };

            window.localStorage.setItem(key, JSON.stringify(snapshot));
        },

        saveAnalysisState() {
            if (!this.analysisCaseId) {
                alert('Select a case before saving analysis state');
                return;
            }

            try {
                this._storeAnalysisStateSnapshot();
                alert('Analysis state saved for case ' + this.analysisCaseId);
            } catch (err) {
                console.error('Failed to save analysis state:', err);
                alert('Failed to save analysis state: ' + (err.message || err));
            }
        },

        loadAnalysisState(caseId) {
            if (!caseId) {
                return;
            }

            const key = 'analysis_state:' + caseId;
            try {
                const raw = window.localStorage.getItem(key);
                if (!raw) {
                    return;
                }
                const snapshot = JSON.parse(raw);
                if (snapshot.analysisContext !== undefined) {
                    this.analysisContext = snapshot.analysisContext;
                }
                if (snapshot.analysisModel) {
                    this.analysisModel = snapshot.analysisModel;
                }
                if (snapshot.phase2Model) {
                    this.phase2Model = snapshot.phase2Model;
                }
                if (snapshot.followUpPhase) {
                    this.followUpPhase = snapshot.followUpPhase;
                }
                if (snapshot.supportiveManualResults) {
                    this.supportiveManualResults = snapshot.supportiveManualResults;
                }
                if (snapshot.supportiveFindingTypes) {
                    this.supportiveFindingTypes = snapshot.supportiveFindingTypes;
                }
                if (snapshot.enrichmentManualResults) {
                    this.enrichmentManualResults = snapshot.enrichmentManualResults;
                }
                if (snapshot.enrichmentFindingTypes) {
                    this.enrichmentFindingTypes = snapshot.enrichmentFindingTypes;
                }
                if (snapshot.phase2EditedQueries) {
                    this.phase2EditedQueries = snapshot.phase2EditedQueries;
                }
                if (snapshot.phase2ManualResults) {
                    this.phase2ManualResults = snapshot.phase2ManualResults;
                }
                if (snapshot.phase2FindingTypes) {
                    this.phase2FindingTypes = snapshot.phase2FindingTypes;
                }
                if (snapshot.phase2ResolutionTypes) {
                    this.phase2ResolutionTypes = snapshot.phase2ResolutionTypes;
                }
                if (snapshot.phase2ResolutionQuestions) {
                    this.phase2ResolutionQuestions = snapshot.phase2ResolutionQuestions;
                }
                if (snapshot.analysisResult) {
                    this.analysisResult = snapshot.analysisResult;
                }
                if (snapshot.phase2Result) {
                    this.phase2Result = snapshot.phase2Result;
                }
                if (snapshot.investigationState) {
                    this.investigationState = snapshot.investigationState;
                }
            } catch (err) {
                console.error('Failed to load analysis state:', err);
            }
        },

        formatLoopStatus(value) {
            const raw = (value || '').toString().trim().toLowerCase();
            if (!raw) return 'Unknown';
            return raw.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
        },

        formatDispositionLabel(value) {
            const raw = (value || '').toString().trim().toLowerCase();
            if (!raw) return 'Undetermined';
            if (raw === 'false_positive') return 'False Positive';
            return raw.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
        },

        formatFindingLabel(value) {
            const raw = (value || '').toString().trim().toLowerCase();
            if (!raw) return 'Neutral';
            return raw.charAt(0).toUpperCase() + raw.slice(1);
        },

        cancelAnalysis() {
            // Abort the browser request as well as invalidating any late response.
            if (this.analysisAbortController) {
                this.analysisAbortController.abort();
                this.analysisAbortController = null;
            }
            this.analysisRequestId += 1;
            this.analysisRunning = false;
            this._setAnalysisStatus('cancelled', 'Assessment cancelled.', 'The in-flight response was ignored.');
            this._stopAnalysisStatusTimer();
            if (this.phase2Status && this.phase2Status.phase !== 'idle') {
                this._setPhase2Status('cancelled', 'Phase 2 analysis cancelled.', 'The in-flight response was ignored.');
                this._stopPhase2StatusTimer();
            }
        },

        getSupportiveKey(q) {
            if (q && q.id != null) {
                return `id:${q.id}`;
            }
            const title = (q && q.title ? q.title : '').toString().toLowerCase().trim();
            if (!title) {
                return 'title:unknown';
            }
            return 'title:' + title.replace(/\s+/g, '_').slice(0, 64);
        },

        getEnrichmentKey(q) {
            const title = (q && q.title ? q.title : '').toString().toLowerCase().trim();
            if (!title) {
                return 'enrichment:unknown';
            }
            return 'enrichment:' + title.replace(/\s+/g, '_').slice(0, 64);
        },

        getSupportiveKeyFromTitle(title) {
            const normalized = (title || '').toString().toLowerCase().trim();
            if (!normalized) {
                return 'title:unknown';
            }
            return 'title:' + normalized.replace(/\s+/g, '_').slice(0, 64);
        },

        getEnrichmentKeyFromTitle(title) {
            const normalized = (title || '').toString().toLowerCase().trim();
            if (!normalized) {
                return 'enrichment:unknown';
            }
            return 'enrichment:' + normalized.replace(/\s+/g, '_').slice(0, 64);
        },

        hasUnresolvedPlaceholders(text) {
            return /\$[A-Za-z0-9_]+\$/.test((text || '').toString());
        },

        copyEnrichmentSPL(q) {
            const text = (q && q.spl ? q.spl : '').toString();
            if (!text) {
                alert('No SPL query text available to copy');
                return;
            }

            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                alert('Clipboard access is not available in this browser context');
                return;
            }

            navigator.clipboard.writeText(text)
                .catch(err => {
                    console.error('Failed to copy generic enrichment SPL:', err);
                    alert('Failed to copy SPL to clipboard');
                });
        },

        copySupportiveSPL(q) {
            const text = this.renderSupportiveQuery(q);
            if (!text) {
                alert('No SPL query text available to copy');
                return;
            }

            if (this.hasUnresolvedPlaceholders(text)) {
                alert('This query still contains unresolved placeholders like $host$ or $user$. Gather enrichment data or populate more case fields before copying it into Splunk.');
                return;
            }

            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                alert('Clipboard access is not available in this browser context');
                return;
            }

            navigator.clipboard.writeText(text)
                .catch(err => {
                    console.error('Failed to copy SPL:', err);
                    alert('Failed to copy SPL query to clipboard');
                });
        },

        renderPhase2Query(template) {
            const synthetic = { spl_query: template };
            return this.renderSupportiveQuery(synthetic);
        },

        async promotePhase2Query(q) {
            const rule = this.analysisRule;
            if (!rule || !rule.rule_id) {
                alert('Cannot save: no analysis rule selected for this case');
                return;
            }

            const key = this.getPhase2Key(q);
            const template = (this.phase2EditedQueries[key] || q.spl || '').toString().trim();
            if (!template) {
                alert('Cannot save: SPL query is empty');
                return;
            }

            const title = (q.title || '').toString().trim() || 'Phase 2 Query';
            const description = (q.description || '').toString().trim();

            try {
                await axios.post(this.apiUrl + '/db/supportive-queries', {
                    rule_id: rule.rule_id,
                    title,
                    description,
                    spl_query: template,
                });
                await this.loadRules();
                alert('Saved phase 2 query as a supportive query for this rule.');
            } catch (err) {
                console.error('Failed to save phase 2 query as supportive:', err);
                alert('Error saving supportive query: ' + (err.response?.data?.detail || err.message));
            }
        },

        renderSupportiveQuery(q) {
            const text = (q.spl_query || '').toString();
            if (!this.analysisSourceNotable) {
                return text;
            }

            const fields = this.analysisSourceNotable.raw_fields || this.analysisSourceNotable.fields || {};

            // Dynamic alias map loaded from backend (placeholder_aliases table + JSON seed).
            // Falls back to built-in defaults if not yet loaded.
            const aliasMap = Object.keys(this.placeholderAliases || {}).length > 0
                ? this.placeholderAliases
                : {
                    host: ['host', 'destination_nt_hostname', 'destination', 'destination_ip'],
                    dest: ['destination', 'destination_ip', 'host'],
                    user: ['user', 'username', 'user_identity'],
                    account: ['user', 'username', 'user_identity'],
                    process: ['process', 'image', 'file_name', 'value'],
                    // Fallback IP aliases so src_ip/source_ip still resolve even
                    // if the dynamic placeholderAliases map has not yet loaded.
                    src_ip: ['src_ip', 'source_ip'],
                    source_ip: ['source_ip', 'src_ip'],
                };

            const placeholderRegex = /\$([A-Za-z0-9_]+)\$/g;

            const resolveValue = (name) => {
                const lower = name.toLowerCase();
                let value = '';

                // First, try alias mappings like host/dest/user/process.
                if (aliasMap[lower]) {
                    for (const key of aliasMap[lower]) {
                        if (fields[key]) {
                            value = fields[key];
                            break;
                        }
                    }
                }

                // If no alias hit, try direct field name matches.
                if (!value) {
                    if (Object.prototype.hasOwnProperty.call(fields, name)) {
                        value = fields[name];
                    } else {
                        // Case-insensitive field name match.
                        const keys = Object.keys(fields);
                        for (const key of keys) {
                            if (key.toLowerCase() === lower) {
                                value = fields[key];
                                break;
                            }
                        }
                    }
                }

                // Globally escape backslashes so literal Windows paths
                // like c:\windows\system32\certutil.exe render as
                // c\\windows\\system32\\certutil.exe in SPL,
                // which Splunk accepts inside quoted strings.
                if (typeof value === 'string' && value.includes('\\')) {
                    return value.replace(/\\/g, '\\\\');
                }
                return value;
            };

            return text.replace(placeholderRegex, (match, name) => {
                const value = resolveValue(name);
                return value ? value : match;
            });
        },

        goToClosureFromAnalysis() {
            if (!this.analysisCaseId) {
                alert('Select a case to analyze first');
                return;
            }

            this.currentTab = 'closure';
            this.closureForm.caseId = this.analysisCaseId;
            this.onCaseSelected();
        },

    };

    global.AnalysisMethods = AnalysisMethods;

})(typeof window !== 'undefined' ? window : globalThis);
