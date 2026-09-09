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
            this.analysisSourceNotable = null;

            const caseId = this.analysisCaseId;
            if (!caseId) {
                return;
            }

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

        async loadInvestigationState(caseId) {
            if (!caseId) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/investigation-state');
                this.investigationState = res.data;
            } catch (err) {
                console.error('Failed to load investigation state:', err);
                this.investigationState = null;
            }
        },

        async deleteEvidence(item) {
            if (!this.analysisCaseId || !item) {
                return;
            }
            if (!item.id) {
                alert('This evidence item is missing an id. Click Save Evidence once to refresh the timeline, then try delete again.');
                return;
            }
            const title = item.title || ('evidence #' + item.id);
            if (!confirm('Delete evidence "' + title + '" from this case?')) {
                return;
            }
            try {
                const res = await axios.post(
                    this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence/' + item.id + '/delete'
                );
                if (res.data && res.data.investigation_state) {
                    this.investigationState = res.data.investigation_state;
                } else {
                    await this.loadInvestigationState(this.analysisCaseId);
                }
                await this.loadSavedSupportiveEvidence(this.analysisCaseId);
                await this.loadSavedEnrichmentEvidence(this.analysisCaseId);
                await this.loadSavedPhase2Evidence(this.analysisCaseId);
            } catch (err) {
                console.error('Failed to delete evidence:', err);
                alert('Failed to delete evidence: ' + (err.response && err.response.data && err.response.data.detail ? err.response.data.detail : err.message));
            }
        },

        async runAnalysis() {
            if (!this.analysisCaseId || !this.analysisModel) {
                alert('Please select a case ID and model');
                return;
            }

            const requestId = ++this.analysisRequestId;
            this.analysisRunning = true;
            try {
                await this.saveSupportiveEvidence({ silent: true });
                await this.saveEnrichmentEvidence({ silent: true });

                let combinedContext = this.analysisContext || '';
                const priorAnalysisText = (
                    (this.phase2Result && this.phase2Result.analysis) ||
                    (this.analysisResult && this.analysisResult.analysis) ||
                    ''
                ).toString().trim();

                const res = await axios.post(this.apiUrl + '/db/analyze', {
                    case_id: this.analysisCaseId,
                    model: this.analysisModel,
                    context: combinedContext,
                    prior_analysis: priorAnalysisText,
                    analysis_stage: priorAnalysisText ? 'follow_up' : 'initial'
                });
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
                }
            } catch (err) {
                if (requestId === this.analysisRequestId) {
                    alert('Error: ' + (err.response?.data?.detail || err.message));
                }
            } finally {
                if (requestId === this.analysisRequestId) {
                    this.analysisRunning = false;
                }
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
            try {
                await this.savePhase2Evidence({ silent: true });

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
                });
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
                }
            } catch (err) {
                if (requestId === this.analysisRequestId) {
                    alert('Error: ' + (err.response?.data?.detail || err.message));
                }
            } finally {
                if (requestId === this.analysisRequestId) {
                    this.analysisRunning = false;
                }
            }
        },

        async savePhase2Evidence(options = {}) {
            if (!this.analysisCaseId) {
                return;
            }

            const phase2Queries = (this.analysisResult && this.analysisResult.phase2_queries) || [];
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
                });
            }

            await axios.post(this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence', {
                source_system: 'phase2_manual',
                replace_existing: true,
                entries,
            });

            if (!options.silent) {
                alert('Phase 2 evidence saved for case ' + this.analysisCaseId);
                await this.loadInvestigationState(this.analysisCaseId);
            }
        },

        async loadSavedPhase2Evidence(caseId) {
            if (!caseId) {
                return;
            }

            try {
                const res = await axios.get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/evidence', {
                    params: { source_system: 'phase2_manual' }
                });
                const saved = {};
                for (const item of (res.data || [])) {
                    const key = this.getPhase2KeyFromTitle(item.query_title || '');
                    const raw = item.raw_result || {};
                    saved[key] = (raw.result_text || '').toString();
                    this.phase2FindingTypes[key] = (raw.finding_type || 'neutral').toString();
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
                });
            }

            await axios.post(this.apiUrl + '/db/triage/' + encodeURIComponent(this.analysisCaseId) + '/evidence', {
                source_system: 'supportive_manual',
                replace_existing: true,
                entries,
            });

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

        saveAnalysisState() {
            if (!this.analysisCaseId) {
                alert('Select a case before saving analysis state');
                return;
            }

            const key = 'analysis_state:' + this.analysisCaseId;
            const snapshot = {
                analysisContext: this.analysisContext,
                analysisModel: this.analysisModel,
                phase2Model: this.phase2Model,
                supportiveManualResults: this.supportiveManualResults,
                supportiveFindingTypes: this.supportiveFindingTypes,
                enrichmentManualResults: this.enrichmentManualResults,
                enrichmentFindingTypes: this.enrichmentFindingTypes,
                phase2EditedQueries: this.phase2EditedQueries,
                phase2ManualResults: this.phase2ManualResults,
                phase2FindingTypes: this.phase2FindingTypes,
                analysisResult: this.analysisResult,
                phase2Result: this.phase2Result,
                investigationState: this.investigationState,
            };

            try {
                window.localStorage.setItem(key, JSON.stringify(snapshot));
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
            // Invalidate any in-flight analysis responses and clear the running flag.
            this.analysisRequestId += 1;
            this.analysisRunning = false;
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
