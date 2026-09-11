/**
 * app.modular.js
 * Root Vue app for the modular shell (index.modular.html).
 * Derived from the live index.html script so Analysis / Code Review features stay complete.
 * Live index.html is NOT changed.
 *
 * Load order required:
 *   Vue, axios, utils/*, modules/api.js (+ other modules), components/*, then this file.
 */
const { createApp } = Vue;

const apiOverride = new URLSearchParams(window.location.search).get('api');
const configuredApiUrl = apiOverride || window.SOC_PLATFORM_API_URL || '/api';

        createApp({

    components: {
        'header-nav': window.HeaderNav,
        'tools-tab': window.ToolsTab,
        'database-tab': window.DatabaseTab,
        'analysis-tab': window.AnalysisTab,
        'closure-tab': window.ClosureTab,
        'jobs-tab': window.JobsTab,
        'reports-tab': window.ReportsTab,
        'code-review-tab': window.CodeReviewTab
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
                    apiUrl: configuredApiUrl.replace(/\/$/, ''),
                    apiHealthy: false,
                    
                    // Database
                    triageData: [],
                    analysisCases: [],
                    recentNotables: [],
                    dbStats: { triage_cases: 0, verdict_breakdown: {} },
                    dbSearch: '',
                    dbVerdictFilter: '',
                    triageFromPastedOnly: false,
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
                    analysisAbortController: null,
                    analysisStatus: {
                        phase: 'idle',
                        message: 'Ready',
                        elapsedSeconds: 0,
                        timedOut: false,
                        error: ''
                    },
                    analysisStatusTimer: null,
                    analysisStatusStartedAt: null,
                    phase2Status: {
                        phase: 'idle',
                        message: 'Ready',
                        elapsedSeconds: 0,
                        timedOut: false,
                        error: ''
                    },
                    phase2StatusTimer: null,
                    phase2StatusStartedAt: null,
                    analysisResult: null,
                    showPhase1Analysis: true,
                    phase2Model: '',
                    phase2Result: null,
                    followUpPhase: 2,
                    analysisSourceNotable: null,
                    investigationState: null,
                    enrichmentManualResults: {},
                    enrichmentFindingTypes: {},
                    placeholderAliases: {},
                    supportiveManualResults: {},
                    supportiveFindingTypes: {},
                    phase2EditedQueries: {},
                    phase2ManualResults: {},
                    phase2FindingTypes: {},
                    phase2ResolutionTypes: {},

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
                    codeReviewSectionsFeaturesOnly: true, // only show sections that "do stuff"
                    codeReviewSectionsMinLines: 12,       // hide tiny helpers by default
                    codeReviewSectionsSourceText: '', // snapshot of code at detect time (for exact line slices)
                    codeReviewSectionsUseLocal: true, // prefer smarter local outline
                    codeReviewSectionsShowAdvanced: false, // hide technical filters by default
                    codeReviewSectionsCodeOnly: false, // legacy filter (kept under Advanced)

                    // In-place Find (Ctrl+F style) for the Code to Review textarea
                    codeReviewFindTerm: '',
                    codeReviewFindCaseSensitive: false,
                    codeReviewFindMatches: [],   // array of { start, end }
                    codeReviewFindIndex: -1
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
                    // Fallback list when Ollama health is not available yet.
                    return ['llama3.1:8b', 'llama2', 'mistral'];
                },
                filteredCodeReviewSections() {
                    const sections = this.codeReviewSections || [];
                    const term = (this.codeReviewSectionsFilter || '').trim().toLowerCase();
                    const minLines = Math.max(0, parseInt(this.codeReviewSectionsMinLines, 10) || 0);
                    let filtered = sections.slice();

                    // Size filter — hide tiny helpers by default
                    if (minLines > 0) {
                        filtered = filtered.filter(section => {
                            const lines = section.line_count
                                || (section.start_line && section.end_line
                                    ? (section.end_line - section.start_line + 1)
                                    : 0);
                            if (!lines) return true; // keep unknown size
                            // Always keep high-level screens/tabs even if slightly under threshold
                            const kind = (section.kind || '').toLowerCase();
                            if (kind === 'tab' || kind === 'vue-block') return lines >= Math.min(minLines, 8);
                            return lines >= minLines;
                        });
                    }

                    // Features-only: keep sections that "do stuff" (actions, screens, major blocks)
                    if (this.codeReviewSectionsFeaturesOnly) {
                        filtered = filtered.filter(section => this.sectionDoesStuff(section));
                    }

                    // Legacy "Code Review methods only" (Advanced)
                    if (this.codeReviewSectionsCodeOnly) {
                        filtered = filtered.filter(section => {
                            const label = (section.label || '').toString().toLowerCase();
                            const name = (section.name || '').toString().toLowerCase();
                            const title = (section.title || '').toString().toLowerCase();
                            return label.includes('codereview') || name.includes('codereview')
                                || title.includes('code review') || title.includes('find in code')
                                || (label.includes('code') && label.includes('review'))
                                || (name.includes('find') && name.includes('code'));
                        });
                    }

                    if (term) {
                        filtered = filtered.filter(section => {
                            const haystack = [
                                section.title, section.label, section.name, section.kind,
                                section.feature, section.description, section.preview
                            ].filter(Boolean).join(' ').toLowerCase();
                            return haystack.includes(term);
                        });
                    }

                    // Rank: more important / larger first within the list
                    filtered.sort((a, b) => {
                        const sa = this.sectionImportanceScore(a);
                        const sb = this.sectionImportanceScore(b);
                        if (sb !== sa) return sb - sa;
                        return (a.start_line || 0) - (b.start_line || 0);
                    });

                    return filtered;
                },
                groupedCodeReviewSections() {
                    // Group by friendly feature area (not raw kind)
                    const groups = {};
                    const order = [
                        'Screens',
                        'Code Review',
                        'Find in code',
                        'Files & upload',
                        'AI review',
                        'Major blocks',
                        'Other features'
                    ];
                    for (const section of this.filteredCodeReviewSections) {
                        const area = section.feature || this.inferFeatureArea(section);
                        if (!groups[area]) groups[area] = [];
                        groups[area].push(section);
                    }
                    const keys = Object.keys(groups).sort((a, b) => {
                        const ia = order.indexOf(a);
                        const ib = order.indexOf(b);
                        if (ia === -1 && ib === -1) return a.localeCompare(b);
                        if (ia === -1) return 1;
                        if (ib === -1) return -1;
                        return ia - ib;
                    });
                    return keys.map(area => ({
                        kind: area,
                        label: area,
                        sections: groups[area]
                    }));
                },
                analysisRule() {
                    if (!this.analysisCaseId) {
                        return null;
                    }

                    const case_ = this.analysisCases.find(c => c.case_id === this.analysisCaseId);
                    if (!case_) {
                        return null;
                    }

                    // Prefer stable rule_id mapping when available so
                    // supportive queries stay consistent for the same rule.
                    const caseRuleId = (case_.rule_id || '').trim();
                    if (caseRuleId) {
                        const byId = this.availableRules.find(r => (r.rule_id || '').trim() === caseRuleId);
                        if (byId) {
                            return byId;
                        }
                    }
                    const caseRuleRaw = (case_.rule_name || '');
                    const caseRule = caseRuleRaw.toLowerCase().trim();
                    if (!caseRule) {
                        return null;
                    }

                    // Normalize wrapped ES labels like
                    // "Endpoint - <Rule Name> - Rule" down to a core
                    // so truncated correlation_search values still map
                    // to the canonical rule.
                    const normalizeRuleLabel = (text) => {
                        let s = (text || '').toLowerCase().trim();
                        s = s.replace(/^endpoint\s*-\s*/, '');
                        s = s.replace(/\s*-\s*rule$/, '');
                        return s;
                    };

                    const caseCore = normalizeRuleLabel(caseRuleRaw);

                    // Prefer exact normalized name match first
                    let rule = this.availableRules.find(
                        r => normalizeRuleLabel(r.rule_name || '') === caseCore
                    );
                    if (rule) {
                        return rule;
                    }

                    // Fallback: relaxed contains-based match on
                    // normalized labels.
                    rule = this.availableRules.find(r => {
                        const nameCore = normalizeRuleLabel(r.rule_name || '');
                        return !!nameCore && (caseCore.includes(nameCore) || nameCore.includes(caseCore));
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
                    // Match only the top-of-card "Notable" heading (capital N)
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

                    // Fallback: approximate based on Title/Correlation Search labels
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
                ...(window.DatabaseMethods || {}),
                ...(window.AnalysisMethods || {}),
                ...(window.ToolsMethods || {}),
                ...(window.ClosureMethods || {}),
                ...(window.CodeReviewMethods || {}),
            },
            watch: {
                dbStats: {
                    handler(newVal) {
                        if (newVal && typeof newVal.triage_cases === 'number' && newVal.triage_cases !== this.triageData.length) {
                            this.loadTriageData();
                            this.loadRecentNotables();
                            this.loadAnalysisCases();
                        }
                    },
                    deep: true
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
                
                // Poll for updates
                setInterval(() => this.loadJobs(), 5000);
                setInterval(() => this.loadReports(), 10000);
                setInterval(() => this.checkHealth(), 15000);
                setInterval(() => this.checkOllama(), 30000);
                setInterval(() => this.loadDbStats(), 30000);
                setInterval(() => this.loadRules(), 30000);
                setInterval(() => this.loadTriageData(), 30000);
                setInterval(() => this.loadRecentNotables(), 30000);
            }
        }).mount('#app');
