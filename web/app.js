const { createApp } = Vue;

createApp({
    components: {
        'header-nav': window.HeaderNav,
        'tools-tab': window.ToolsTab,
        'database-tab': window.DatabaseTab,
        'closure-tab': window.ClosureTab,
        'jobs-tab': window.JobsTab,
        'reports-tab': window.ReportsTab
    },
    data() {
        return {
            currentTab: 'database',
            tools: [],
            jobs: [],
            reports: [],
            toolSearch: '',
            selectedCategory: '',
            toolArgs: {},
            apiUrl: '/api',
            apiHealthy: false,
            
            // Database
            triageData: [],
            analysisCases: [],
            recentNotables: [],
            dbStats: { triage_cases: 0, verdict_breakdown: {} },
            dbSearch: '',
            dbVerdictFilter: '',
            deleteAnalysisWithCase: false,
            triageNotableDetails: {},
            selectedTriageCaseIds: [],
            showOpenNotablesOnly: true,
            historicalNotablesVisible: false,
            historicalNotables: [],
            notablePasteText: '',
            notableRedactionEnabled: true,
            notableHistorical: false,
            notablePasteSaving: false,
            notablePasteResult: null,
            notablePromotingId: null,
            selectedNotableIds: [],
            bulkPromoteNotablesRunning: false,
            
            // Analysis
            ollamaHealth: { available: false, models: [] },
            evidenceFindingOptions: ['supports', 'refutes', 'neutral'],
            analysisCaseSearch: '',
            analysisCaseId: '',
            analysisModel: '',
            analysisContext: '',
            analysisRunning: false,
            analysisRequestId: 0,
            analysisResult: null,
            showPhase1Analysis: true,
            phase2Model: '',
            phase2Result: null,
            analysisSourceNotable: null,
            enrichmentManualResults: {},
            enrichmentFindingTypes: {},
            placeholderAliases: {},
            supportiveManualResults: {},
            supportiveFindingTypes: {},
            phase2EditedQueries: {},
            phase2ManualResults: {},
            phase2FindingTypes: {},

            // Closure Notes
            availableRules: [],
            selectedRule: null,
            closureSourceNotable: null,
            closureSuggestedRuleName: '',
            closureSuggestedDisposition: '',
            supportiveEditorOpen: false,
            supportiveEditorBusy: false,
            supportiveEditorQueries: [],
            supportiveEditorRuleId: '',
            supportiveEditorError: '',

            // Placeholder Alias Management (dynamic, no-code)
            placeholderAliasEditorOpen: false,
            placeholderAliasEditorBusy: false,
            placeholderAliasList: [],
            placeholderAliasEditorError: '',
            placeholderAliasSuggestions: [],
            placeholderAliasSuggestionsBusy: false,
            newAliasForm: { alias: '', fields: '', description: '' },
            editingAliasId: null,
            closureForm: {
                caseId: '',
                ruleId: '',
                fieldValues: {},
                analystNotes: '',
                disposition: 'Undetermined'
            },
            closureResult: null,
            closureGenerating: false,
            selectedToolForExecution: null,

            // Code Review
            codeReviewForm: { codeSnippet: '', language: 'python', model: 'llama3.1:8b', uploadedFile: null, instructions: '' },
            codeReviewResult: null,
            codeReviewRunning: false,
            codeReviewsList: [],
            codeReviewDragActive: false,
            codeReviewSearchTerm: '',
            codeReviewSections: [],
            codeReviewSectionsBusy: false,
            codeReviewSectionsFilter: '',
            codeReviewSectionsCodeOnly: false
        };
    },
    computed: {
        categories() {
            const cats = new Set(this.tools.map(t => t.category));
            return Array.from(cats).sort();
        },
        filteredTools() {
            return this.tools.filter(tool => {
                const matchSearch = !this.toolSearch || tool.name.toLowerCase().includes(this.toolSearch.toLowerCase()) || 
                                  tool.description.toLowerCase().includes(this.toolSearch.toLowerCase());
                const matchCategory = !this.selectedCategory || tool.category === this.selectedCategory;
                return matchSearch && matchCategory;
            });
        },
        filteredTriageData() {
            const searchTerm = this.dbSearch.trim().toLowerCase();

            return this.triageData.filter(case_ => {
                const verdict = (case_.verdict || '').toLowerCase();
                const matchesVerdict = !this.dbVerdictFilter || verdict === this.dbVerdictFilter.toLowerCase();

                if (!matchesVerdict) {
                    return false;
                }

                if (this.triageFromPastedOnly) {
                    const id = (case_.case_id || '').toUpperCase();
                    if (!id.startsWith('NOTABLE-')) {
                        return false;
                    }
                }

                if (!searchTerm) {
                    return true;
                }

                const haystack = [
                    case_.case_id,
                    case_.rule_name,
                    case_.analysis_summary
                ]
                    .filter(Boolean)
                    .join(' ')
                    .toLowerCase();

                return haystack.includes(searchTerm);
            });
        },
        filteredAnalysisCases() {
            const searchTerm = this.analysisCaseSearch.trim().toLowerCase();

            if (!searchTerm) {
                return this.analysisCases;
            }

            return this.analysisCases.filter(case_ => {
                const haystack = [
                    case_.case_id,
                    case_.rule_name,
                    case_.verdict,
                    case_.analysis_summary
                ]
                    .filter(Boolean)
                    .join(' ')
                    .toLowerCase();

                return haystack.includes(searchTerm);
            });
        },
        codeReviewModels() {
            const models = (this.ollamaHealth && this.ollamaHealth.models) || [];
            if (models.length) {
                return models;
            }
            return ['llama3.1:8b', 'llama2', 'mistral'];
        },
        filteredCodeReviewSections() {
            const sections = this.codeReviewSections || [];
            const term = (this.codeReviewSectionsFilter || '').trim().toLowerCase();
            let filtered = sections;

            if (term) {
                filtered = filtered.filter(section => {
                    const label = (section.label || '').toString().toLowerCase();
                    const name = (section.name || '').toString().toLowerCase();
                    const kind = (section.kind || '').toString().toLowerCase();

                    const haystack = [label, name, kind]
                        .filter(Boolean)
                        .join(' ');

                    return haystack.includes(term);
                });
            }

            if (!this.codeReviewSectionsCodeOnly) {
                return filtered;
            }

            const codeNames = new Set([
                'handlecodereviewfile',
                'focuscodereviewsection',
                'detectcodereviewsections',
                'submitcodereview',
                'loadcodereviews',
                'loadfullcodereview',
                'copycodereviewtoclipboard',
                'oncodereviewdragover',
                'oncodereviewdragleave',
                'oncodereviewdrop',
                'handlefolderupload'
            ]);

            return filtered.filter(section => {
                const label = (section.label || '').toString().toLowerCase();
                const name = (section.name || '').toString().toLowerCase();

                if (codeNames.has(name)) {
                    return true;
                }

                if (label.includes('codereview') || name.includes('codereview')) {
                    return true;
                }

                if (label.includes('code') && label.includes('review')) {
                    return true;
                }

                return false;
            });
        },
        analysisRule() {
            if (!this.analysisCaseId) {
                return null;
            }

            const case_ = this.analysisCases.find(c => c.case_id === this.analysisCaseId);
            if (!case_) {
                return null;
            }

            const caseRuleId = (case_.rule_id || '').trim();
            if (caseRuleId) {
                const byId = this.availableRules.find(r => (r.rule_id || '').trim() === caseRuleId);
                if (byId) {
                    return byId;
                }
            }

            const caseRule = (case_.rule_name || '').toLowerCase().trim();
            if (!caseRule) {
                return null;
            }

            let rule = this.availableRules.find(
                r => (r.rule_name || '').toLowerCase().trim() === caseRule
            );
            if (rule) {
                return rule;
            }

            rule = this.availableRules.find(r => {
                const name = (r.rule_name || '').toLowerCase().trim();
                return !!name && (caseRule.includes(name) || name.includes(caseRule));
            });

            return rule || null;
        },
        filteredRecentNotables() {
            if (!this.showOpenNotablesOnly) {
                return this.recentNotables;
            }

            return this.recentNotables.filter(n => !n.historical);
        },
        notablePasteSegmentEstimate() {
            const text = (this.notablePasteText || '').replace(/\r\n/g, '\n');
            if (!text.trim()) {
                return 0;
            }

            const lines = text.split('\n');
            const notablePattern = /^\s*Notable\s*$/;
            let countNotable = 0;

            for (const line of lines) {
                if (notablePattern.test(line)) {
                    countNotable++;
                }
            }

            if (countNotable >= 2) {
                return countNotable;
            }

            const headingPattern = /^\s*(Title|Correlation Search)\b/i;
            let countHeadings = 0;
            for (const line of lines) {
                if (headingPattern.test(line)) {
                    countHeadings++;
                }
            }

            if (countHeadings <= 2) {
                return 1;
            }
            return countHeadings;
        }
    },
    methods: {
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

            this.$set(this.triageNotableDetails, id, { loading: true, error: null, data: null });

            try {
                const res = await axios.get(this.apiUrl + '/db/notables/' + id);
                this.$set(this.triageNotableDetails, id, { loading: false, error: null, data: res.data });
            } catch (err) {
                console.error('Failed to load closed notable details:', err);
                const detail = err.response?.data?.detail || err.message;
                this.$set(this.triageNotableDetails, id, { loading: false, error: detail, data: null });
            }
        },
        areAllPastedNotablesSelected() {
            const items = this.filteredRecentNotables;
            if (!items.length) {
                return false;
            }
            return items.every(n => this.selectedNotableIds.includes(n.id));
        },
        toggleSelectAllPastedNotables(event) {
            const checked = event.target.checked;
            if (checked) {
                this.selectedNotableIds = this.filteredRecentNotables.map(n => n.id);
            } else {
                this.selectedNotableIds = [];
            }
        },
        areAllTriageCasesSelected() {
            const items = this.filteredTriageData;
            if (!items.length) {
                return false;
            }
            return items.every(c => this.selectedTriageCaseIds.includes(c.case_id));
        },
        toggleSelectAllTriageCases(event) {
            const checked = event.target.checked;
            if (checked) {
                this.selectedTriageCaseIds = this.filteredTriageData.map(c => c.case_id);
            } else {
                this.selectedTriageCaseIds = [];
            }
        },
        getNotablePocSummary(notable) {
            const fields = notable?.fields || notable || {};
            const pocEntries = [];
            const addEntry = (label, value) => {
                if (!value) {
                    return;
                }
                const cleaned = String(value).trim();
                if (!cleaned) {
                    return;
                }
                pocEntries.push({ label, value: cleaned });
            };

            addEntry('Owner', fields.owner);

            return pocEntries;
        },
        async loadTools() {
            try {
                const res = await axios.get(this.apiUrl + '/tools');
                this.tools = res.data;
            } catch (err) {
                console.error('Failed to load tools:', err);
            }
        },
        async loadJobs() {
            try {
                const res = await axios.get(this.apiUrl + '/jobs');
                this.jobs = res.data.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
            } catch (err) {
                console.error('Failed to load jobs:', err);
            }
        },
        async loadReports() {
            try {
                const res = await axios.get(this.apiUrl + '/reports');
                this.reports = res.data;
            } catch (err) {
                console.error('Failed to load reports:', err);
            }
        },
        onAnalysisCaseChanged() {
            this.analysisResult = null;
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
        jumpToTriageCase(caseId) {
            this.dbSearch = caseId;
            this.currentTab = 'database';
            this.loadTriageData();
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
        async deleteClosedNotable(notableSummary) {
            if (!notableSummary || !notableSummary.id) {
                return;
            }

            const id = notableSummary.id;
            try {
                await axios.post(this.apiUrl + '/db/notables/' + id + '/delete');
                await this.loadHistoricalNotables();
                await this.loadDbStats();
            } catch (err) {
                alert('Error deleting closed notable: ' + (err.response?.data?.detail || err.message));
            }
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
                this.supportiveEditorQueries.push({
                    id: null,
                    rule_id: this.supportiveEditorRuleId,
                    title: '',
                    description: '',
                    spl_query: '',
                    localKey: 'new-0',
                });
            } else {
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
            }
        },
        async executeTool(tool) {
            const args = {};
            if (tool.arguments) {
                for (const arg of tool.arguments) {
                    const key = tool.name + '_' + arg.flag;
                    if (this.toolArgs[key]) {
                        args[arg.flag.replace('--', '')] = this.toolArgs[key];
                    }
                }
            }

            try {
                const res = await axios.post(this.apiUrl + '/execute', {
                    tool_name: tool.name,
                    arguments: args,
                    silent: false
                });
                this.currentTab = 'jobs';
                this.loadJobs();
                alert('Tool execution queued: ' + res.data.job_id.slice(0, 8));
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            }
        },
        async analyzeCase(case_) {
            this.analysisCaseId = case_.case_id;
            this.currentTab = 'analysis';
            this.onAnalysisCaseChanged();
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
                    this.analysisResult = newResult;
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
            } catch (err) {
                console.error('Failed to load analysis state:', err);
            }
        },
        cancelAnalysis() {
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

            const aliasMap = Object.keys(this.placeholderAliases || {}).length > 0
                ? this.placeholderAliases
                : {
                    host: ['host', 'destination_nt_hostname', 'destination', 'destination_ip'],
                    dest: ['destination', 'destination_ip', 'host'],
                    user: ['user', 'username', 'user_identity'],
                    account: ['user', 'username', 'user_identity'],
                    process: ['process', 'image', 'file_name', 'value'],
                    src_ip: ['src_ip', 'source_ip'],
                    source_ip: ['source_ip', 'src_ip'],
                };

            const placeholderRegex = /\$([A-Za-z0-9_]+)\$/g;

            const resolveValue = (name) => {
                const lower = name.toLowerCase();
                let value = '';

                if (aliasMap[lower]) {
                    for (const key of aliasMap[lower]) {
                        if (fields[key]) {
                            value = fields[key];
                            break;
                        }
                    }
                }

                if (!value) {
                    if (Object.prototype.hasOwnProperty.call(fields, name)) {
                        value = fields[name];
                    } else {
                        const keys = Object.keys(fields);
                        for (const key of keys) {
                            if (key.toLowerCase() === lower) {
                                value = fields[key];
                                break;
                            }
                        }
                    }
                }

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
        onCaseSelected() {
            this.closureForm.ruleId = '';
            this.closureForm.fieldValues = {};
            this.selectedRule = null;
            this.closureSuggestedRuleName = '';
            this.closureSuggestedDisposition = '';

            const caseId = this.closureForm.caseId;
            this.closureSourceNotable = null;
            if (!caseId) {
                return;
            }

            const triageCase = this.triageData.find(c => c.case_id === caseId);
            if (triageCase) {
                const verdict = (triageCase.verdict || '').toLowerCase();
                if (verdict === 'malicious') {
                    this.closureForm.disposition = 'True Positive';
                    this.closureSuggestedDisposition = 'True Positive';
                } else if (verdict === 'benign') {
                    this.closureForm.disposition = 'Benign Positive';
                    this.closureSuggestedDisposition = 'Benign Positive';
                } else {
                    this.closureForm.disposition = 'Undetermined';
                    this.closureSuggestedDisposition = 'Undetermined';
                }
            } else {
                this.closureForm.disposition = 'Undetermined';
                this.closureSuggestedDisposition = 'Undetermined';
            }

            axios
                .get(this.apiUrl + '/db/triage/' + encodeURIComponent(caseId) + '/notable')
                .then(res => {
                    this.closureSourceNotable = res.data;

                    if (!this.closureForm.ruleId && triageCase && this.availableRules.length) {
                        const triageRuleName = (triageCase.rule_name || '').toLowerCase().trim();
                        let matchingRule = this.availableRules.find(
                            r => (r.rule_name || '').toLowerCase().trim() === triageRuleName
                        );
                        if (!matchingRule && triageRuleName) {
                            matchingRule = this.availableRules.find(r => {
                                const name = (r.rule_name || '').toLowerCase().trim();
                                return !!name && (triageRuleName.includes(name) || name.includes(triageRuleName));
                            });
                        }
                        if (matchingRule) {
                            this.closureForm.ruleId = matchingRule.rule_id;
                            this.closureSuggestedRuleName = matchingRule.rule_name;
                            this.onRuleSelected();
                        }
                    }
                })
                .catch(err => {
                    console.error('Failed to load source notable for closure form:', err);
                    this.closureSourceNotable = null;
                });
        },
        onRuleSelected() {
            this.selectedRule = this.availableRules.find(r => r.rule_id === this.closureForm.ruleId) || null;
            this.closureForm.fieldValues = {};
            if (this.selectedRule) {
                const sourceFields = (this.closureSourceNotable && this.closureSourceNotable.fields) || {};

                const aliasMap = {
                    host: ['host', 'destination_nt_hostname', 'destination'],
                    destination: ['destination', 'destination_ip'],
                    user: ['user', 'username', 'user_identity'],
                    account: ['user', 'username', 'user_identity'],
                };

                for (const field of this.selectedRule.required_closure_fields) {
                    let value = '';

                    if (Object.prototype.hasOwnProperty.call(sourceFields, field)) {
                        value = sourceFields[field];
                    } else {
                        const aliases = aliasMap[field] || [];
                        for (const key of aliases) {
                            if (sourceFields[key]) {
                                value = sourceFields[key];
                                break;
                            }
                        }
                    }

                    this.closureForm.fieldValues[field] = value || '';
                }
            }
        },
        async generateClosureNote() {
            const missingFields = this.selectedRule.required_closure_fields.filter(f => !this.closureForm.fieldValues[f]);
            if (missingFields.length > 0) {
                alert('Please fill in all required fields: ' + missingFields.join(', '));
                return;
            }

            this.closureGenerating = true;
            try {
                const res = await axios.post(this.apiUrl + '/db/closure-note', {
                    rule_id: this.closureForm.ruleId,
                    case_id: this.closureForm.caseId,
                    field_values: this.closureForm.fieldValues,
                    analyst_notes: this.closureForm.analystNotes,
                    disposition: this.closureForm.disposition
                });
                this.closureResult = res.data;
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            } finally {
                this.closureGenerating = false;
            }
        },
        copyToClipboard() {
            navigator.clipboard.writeText(this.closureResult.generated_note);
            alert('Closure note copied to clipboard!');
        },
        downloadClosureNote() {
            const element = document.createElement('a');
            element.setAttribute('href', 'data:text/plain;charset=utf-8,' + encodeURIComponent(this.closureResult.generated_note));
            element.setAttribute('download', this.closureForm.caseId + '_closure_note.txt');
            element.style.display = 'none';
            document.body.appendChild(element);
            element.click();
            document.body.removeChild(element);
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
        selectToolForExecution(tool) {
            this.selectedToolForExecution = tool;
        },
        handleCodeReviewFile(file) {
            if (!file) {
                this.codeReviewForm.uploadedFile = null;
                this.codeReviewSections = [];
                return;
            }

            this.codeReviewForm.uploadedFile = file;
            this.codeReviewSections = [];
            const name = file.name || '';
            const lowerName = name.toLowerCase();
            const ext = lowerName.includes('.') ? lowerName.substring(lowerName.lastIndexOf('.') + 1) : '';

            if (ext === 'js' || ext === 'ts') {
                this.codeReviewForm.language = 'javascript';
            } else if (ext === 'sh') {
                this.codeReviewForm.language = 'bash';
            } else if (ext === 'sql') {
                this.codeReviewForm.language = 'sql';
            } else if (ext === 'html' || ext === 'htm') {
                this.codeReviewForm.language = 'html';
            }

            if (ext !== 'zip') {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const text = (e.target && e.target.result) || '';
                    this.codeReviewForm.codeSnippet = typeof text === 'string' ? text : '';
                };
                reader.readAsText(file);
            } else {
                this.codeReviewForm.codeSnippet = '';
            }
        },
        handleFileUpload(event) {
            const file = event.target.files && event.target.files[0];
            this.handleCodeReviewFile(file);
        },
        focusCodeReviewSection() {
            const fullText = this.codeReviewForm.codeSnippet || '';
            const term = (this.codeReviewSearchTerm || '').trim();

            if (!fullText.trim()) {
                alert('No code loaded yet. Upload a file or paste code first.');
                return;
            }
            if (!term) {
                alert('Enter a search term to focus on (e.g., a component name or heading).');
                return;
            }

            const lowerText = fullText.toLowerCase();
            const lowerTerm = term.toLowerCase();
            const idx = lowerText.indexOf(lowerTerm);

            if (idx === -1) {
                alert(`The term "${term}" was not found in the current code.`);
                return;
            }

            const radius = 2000;
            const start = Math.max(0, idx - radius);
            const end = Math.min(fullText.length, idx + radius);
            const snippet = fullText.slice(start, end);

            this.codeReviewForm.codeSnippet = snippet;
            alert('Focused on a smaller section around the first match. You can refine further or run the review now.');
        },
        async detectCodeReviewSections() {
            const fullText = this.codeReviewForm.codeSnippet || '';
            if (!fullText.trim()) {
                alert('No code loaded yet. Upload a file, paste code, or aggregate a folder first.');
                return;
            }

            this.codeReviewSectionsBusy = true;
            try {
                const res = await axios.post(this.apiUrl + '/code-review/sections', {
                    code_snippet: fullText,
                    language: this.codeReviewForm.language
                });
                const sections = (res.data && res.data.sections) || [];
                this.codeReviewSections = sections;
                if (!sections.length) {
                    alert('No discrete functions/classes were detected. You can still use search-term focus.');
                }
            } catch (err) {
                console.error('Failed to detect code sections:', err);
                alert('Error detecting sections: ' + (err.response?.data?.detail || err.message));
            } finally {
                this.codeReviewSectionsBusy = false;
            }
        },
        focusCodeReviewOnSection(section) {
            if (!section || !section.preview) {
                return;
            }
            this.codeReviewForm.codeSnippet = section.preview;
            const kind = section.kind || 'section';
            const name = section.name ? ' "' + section.name + '"' : '';
            alert('Focused on ' + kind + name + '. You can refine further or run the review now.');
        },
        async handleFolderUpload(event) {
            const fileList = event.target.files || [];
            const files = Array.from(fileList);
            if (!files.length) {
                return;
            }

            const allowedExts = [
                '.py', '.js', '.ts', '.go', '.sh', '.sql',
                '.java', '.cpp', '.c', '.html', '.htm', '.css', '.json', '.md'
            ];
            const excludedPaths = [
                'node_modules/', 'venv/', 'env/', '__pycache__/', '.git/', 'dist/', 'build/'
            ];
            const maxPerFileChars = 2000;
            const maxTotalChars = 20000;

            let totalChars = 0;
            const chunks = [];

            const sorted = files.slice().sort((a, b) => {
                const pa = (a.webkitRelativePath || a.name || '').toLowerCase();
                const pb = (b.webkitRelativePath || b.name || '').toLowerCase();
                return pa.localeCompare(pb);
            });

            for (const file of sorted) {
                const rel = file.webkitRelativePath || file.name || '';
                const lower = rel.toLowerCase();

                if (excludedPaths.some(excl => lower.includes(excl))) {
                    continue;
                }
                if (!allowedExts.some(ext => lower.endsWith(ext))) {
                    continue;
                }

                const text = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        const result = (e && e.target && e.target.result) || '';
                        resolve(typeof result === 'string' ? result : '');
                    };
                    reader.onerror = reject;
                    reader.readAsText(file);
                }).catch(() => '');

                if (!text || !text.trim()) {
                    continue;
                }

                const snippet = text.slice(0, maxPerFileChars);
                const chunk = `File: ${rel}\n` + snippet.trim() + '\n\n';

                if (totalChars + chunk.length > maxTotalChars) {
                    break;
                }

                chunks.push(chunk);
                totalChars += chunk.length;
            }

            if (!chunks.length) {
                alert('No supported text/code files found in selected folder');
                return;
            }

            this.codeReviewForm.codeSnippet = chunks.join('\n');
            this.codeReviewForm.uploadedFile = null;
            this.codeReviewSections = [];
        },
        onCodeReviewDragOver(event) {
            if (!event.dataTransfer) {
                return;
            }
            event.dataTransfer.dropEffect = 'copy';
            this.codeReviewDragActive = true;
        },
        onCodeReviewDragLeave() {
            this.codeReviewDragActive = false;
        },
        onCodeReviewDrop(event) {
            this.codeReviewDragActive = false;
            const dt = event.dataTransfer;
            if (!dt || !dt.files || dt.files.length === 0) {
                return;
            }
            const file = dt.files[0];
            this.handleCodeReviewFile(file);
        },
        async submitCodeReview() {
            const hasText = this.codeReviewForm.codeSnippet && this.codeReviewForm.codeSnippet.trim().length > 0;
            const file = this.codeReviewForm.uploadedFile;
            const isZip = file && file.name && file.name.toLowerCase().endsWith('.zip');

            if (!hasText && !file) {
                alert('Please paste code or upload a file/project to review');
                return;
            }
            this.codeReviewRunning = true;
            try {
                let res;
                if (isZip) {
                    const formData = new FormData();
                    formData.append('file', file);
                    const url = this.apiUrl + `/code-review/zip?language=${encodeURIComponent(this.codeReviewForm.language)}&model=${encodeURIComponent(this.codeReviewForm.model)}&instructions=${encodeURIComponent(this.codeReviewForm.instructions || '')}`;
                    res = await axios.post(url, formData, {
                        headers: { 'Content-Type': 'multipart/form-data' }
                    });
                } else {
                    res = await axios.post(this.apiUrl + '/code-review', {
                        code_snippet: this.codeReviewForm.codeSnippet,
                        language: this.codeReviewForm.language,
                        model: this.codeReviewForm.model,
                        instructions: this.codeReviewForm.instructions
                    });
                }
                this.codeReviewResult = res.data;
                await this.loadCodeReviews();
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            } finally {
                this.codeReviewRunning = false;
            }
        },
        async loadCodeReviews() {
            try {
                const res = await axios.get(this.apiUrl + '/code-reviews?limit=10');
                this.codeReviewsList = res.data || [];
            } catch (err) {
                console.error('Failed to load code reviews:', err);
            }
        },
        async loadFullCodeReview(reviewId) {
            try {
                const res = await axios.get(this.apiUrl + '/code-reviews/' + reviewId);
                this.codeReviewResult = res.data;
            } catch (err) {
                alert('Error loading review: ' + (err.response?.data?.detail || err.message));
            }
        },
        copyCodeReviewToClipboard() {
            if (!this.codeReviewResult || !this.codeReviewResult.review) {
                alert('No review to copy');
                return;
            }
            navigator.clipboard.writeText(this.codeReviewResult.review)
                .then(() => alert('Review copied to clipboard!'))
                .catch(err => alert('Failed to copy: ' + err.message));
        },
        checkHealth() {
            axios.get(this.apiUrl + '/health')
                .then(() => {
                    this.apiHealthy = true;
                })
                .catch(() => {
                    this.apiHealthy = false;
                })
                .finally(() => {
                    const el = document.getElementById('status');
                    if (el) {
                        el.textContent = '●';
                        el.style.color = this.apiHealthy ? '#4ade80' : '#ef4444';
                    }
                });
        }
    },
    mounted() {
        this.loadTools();
        this.loadJobs();
        this.loadReports();
        this.loadTriageData();
        this.loadAnalysisCases();
        this.loadRecentNotables();
        this.loadDbStats();
        this.loadRules();
        this.loadPlaceholderAliases();
        this.checkOllama();
        this.checkHealth();

        setInterval(() => this.loadJobs(), 5000);
        setInterval(() => this.loadReports(), 10000);
        setInterval(() => this.checkHealth(), 15000);
        setInterval(() => this.checkOllama(), 30000);
        setInterval(() => this.loadDbStats(), 30000);
        setInterval(() => this.loadRules(), 30000);
    }
}).mount('#app');
