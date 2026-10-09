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
        'splunk-boundary-widget': window.SplunkBoundaryWidget,
        'tools-tab': window.ToolsTab,
        'database-tab': window.DatabaseTab,
        'analysis-tab': window.AnalysisTab,
        'closure-tab': window.ClosureTab,
        'jobs-tab': window.JobsTab,
        'reports-tab': window.ReportsTab,
        'code-review-tab': window.CodeReviewTab,
        'login-modal': window.LoginModal
    },
            data() {
                return {
                    currentTab: 'database',
                    tools: [],
                    regressionResult: null,
                    regressionRunning: false,
                    jobs: [],
                    selectedJobIds: [],
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
                    operationsStats: { open_cases: 0, closure_blockers: 0, unresolved_questions: 0, aging_24h: 0, aging_7d: 0, tool_failures: 0, evidence_results: 0, average_closure_hours: null },
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
                    notableAutoPromote: false,
                    notablePasteSaving: false,
                    notablePasteResult: null,
                    notablePromotingId: null,
                    selectedNotableIds: [],
                    bulkPromoteNotablesRunning: false,
                    bulkPromoteProgress: null,
                    bulkPromoteOutcome: {},
                    bulkPromoteSummary: '',
                    evidenceLedgerCaseId: '',
                    evidenceLedgerItems: [],
                    evidenceLedgerLoading: false,
                    evidenceLedgerError: '',
                    evidenceLedgerBusyId: null,
                    
                    // Analysis
                    ollamaHealth: { available: false, models: [] },
                    evidenceFindingOptions: ['supports', 'refutes', 'neutral'],
                    analysisCaseSearch: '',
                    analysisCaseId: '',
                    analysisModel: '',
                    // C3: per-stage model overrides for the analysis wizard
                    // (Stage 3 initial / Stage 4 follow_up / Stage 5 closure).
                    stageModels: { initial: '', follow_up: '', closure: '' },
                    // Session auth (SESSION_AUTH_PLAN.md Piece C): bootstrap
                    // from GET /api/auth/session; mode 'session' shows the
                    // login modal and binds role-aware controls. Flag-off the
                    // endpoint answers mode:'api-key' so nothing changes.
                    auth: { user: '', role: '', csrf: '', mode: '', showLogin: false },
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
                    placeholderAliases: {},
                    runAllBusy: false,
                    runAllSummary: '',
                    supportiveSaveBusy: false,
                    phase2EditedQueries: {},
                    // Unified per-card state (S7): one keyed cell per analysis
                    // card — resultText, findingType, editedSpl, coverage,
                    // status, runStatus — so no field can drift keyings.
                    // Keys are the existing query keys ('id:7', 'phase2:foo');
                    // run-status chips read '<kind>:' + key style composition
                    // via AnalysisTab's runStatusFor helper.
                    phase2CardState: {},
                    supportivePlaybookAvailable: null,
                    supportiveDraftBusy: false,
                    supportiveDraftError: '',
                    supportiveImportBusy: false,
                    supportiveImportError: '',

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
                    closureReadiness: null,
                    selectedToolForExecution: null,

                    // Code Review
                    codeReviewForm: { codeSnippet: '', language: 'python', model: '', uploadedFile: null, instructions: '' },
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
                // Admin controls stay enabled in every mode except a
                // non-admin session (flag-off = pre-C1B behavior).
                isAdmin() {
                    return this.auth.mode !== 'session' || this.auth.role === 'admin';
                },
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
                            case_.verdict,
                            case_.analysis_summary,
                            ...Object.values(case_.key_fields || {})
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
                    return ['llama3.1:latest', 'llama3.1:8b', 'llama2', 'mistral'];
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
                    return window.RuleFamilyResolver
                        ? window.RuleFamilyResolver.resolveCanonicalRule({
                            case_,
                            notable: this.analysisSourceNotable,
                            availableRules: this.availableRules
                        })
                        : null;
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
                async bootstrapAuth() {
                    try {
                        const info = await API.sessionInfo();
                        this.applyAuthInfo(info);
                    } catch (err) {
                        // Unreachable backend behaves like flag-off: keep
                        // controls enabled rather than bricking the UI.
                        this.applyAuthInfo({ mode: 'api-key', role: 'admin' });
                    }
                },
                applyAuthInfo(info) {
                    this.auth.mode = (info && info.mode) || '';
                    this.auth.user = (info && info.user) || '';
                    this.auth.role = (info && info.role) || '';
                    this.auth.csrf = (info && info.csrf_token) || '';
                    window.__SOC_CSRF__ = this.auth.csrf || '';
                    this.auth.showLogin = this.auth.mode === 'session' && !this.auth.user;
                },
                openLogin() {
                    this.auth.showLogin = true;
                },
                async logout() {
                    try { await API.sessionLogout(); } catch (err) { /* cookie may already be gone */ }
                    window.__SOC_CSRF__ = '';
                    await this.bootstrapAuth();
                },
                ...(window.DatabaseMethods || {}),
                ...(window.AnalysisMethods || {}),
                ...(window.ToolsMethods || {}),
                ...(window.ClosureMethods || {}),
                ...(window.CodeReviewMethods || {}),

                // ------------------------------------------------------------------
                // Polling scheduler: ONE ticker for every background refresh.
                // Replaces the nine independent setInterval calls that used to live
                // in mounted(). Each job keeps its own cadence, skips a beat while
                // its previous run is still in flight (in-flight dedupe), and
                // pauses while the tab is hidden (draft autosave opts out so
                // unsaved analysis work keeps landing). Returning to the tab
                // catches every job up immediately.
                // ------------------------------------------------------------------
                startPolling() {
                    const now = Date.now();
                    const jobs = [
                        { name: 'jobs', every: 5000, run: () => this.loadJobs() },
                        { name: 'reports', every: 10000, run: () => this.loadReports() },
                        { name: 'health', every: 15000, run: () => this.checkHealth() },
                        { name: 'ollama', every: 30000, run: () => this.checkOllama() },
                        { name: 'rules', every: 30000, run: () => this.loadRules() },
                        { name: 'triage', every: 30000, run: () => this.loadTriageData() },
                        { name: 'notables', every: 30000, run: () => this.loadRecentNotables() },
                        {
                            name: 'stats',
                            every: 30000,
                            run: async () => {
                                const before = this.dbStats && this.dbStats.triage_cases;
                                await this.loadDbStats();
                                const after = this.dbStats && this.dbStats.triage_cases;
                                // Case count actually changed (external write):
                                // refresh the case lists now. This replaces the old
                                // dbStats watcher, whose `triage_cases !==
                                // triageData.length` guard was wrong whenever a
                                // filter or the 50-row cap applied — i.e. it fired
                                // three redundant reloads on every stats tick.
                                if (typeof after === 'number' && after !== before) {
                                    await Promise.all([this.loadTriageData(), this.loadAnalysisCases()]);
                                }
                            }
                        },
                        {
                            name: 'draft',
                            every: 15000,
                            whenHidden: true,
                            run: () => {
                                if (this.analysisCaseId && !this.analysisRunning && this.persistAnalysisDraft) {
                                    this.persistAnalysisDraft();
                                }
                            }
                        }
                    ].map(job => ({ ...job, lastRun: now, running: false }));

                    this._pollJobs = jobs;
                    this._pollTimer = setInterval(() => this._pollTick(), 1000);
                    this._pollVisibility = () => {
                        if (document.hidden) return;
                        jobs.forEach(job => { job.lastRun = 0; });
                        this._pollTick();
                    };
                    document.addEventListener('visibilitychange', this._pollVisibility);

                    // Introspection hook for smoke tests / debugging.
                    // tick(hiddenOverride) exists because Blink seals
                    // document.hidden (non-configurable) so it cannot be faked.
                    window.__SOC_POLL__ = { jobs: this._pollJobs, tick: (hiddenOverride) => this._pollTick(hiddenOverride) };
                },
                _pollTick(hiddenOverride) {
                    const hidden = hiddenOverride === undefined
                        ? (typeof document !== 'undefined' && document.hidden)
                        : !!hiddenOverride;
                    const now = Date.now();
                    for (const job of this._pollJobs || []) {
                        if (job.running) continue;                                    // in-flight dedupe
                        if (hidden && !job.whenHidden) continue;                      // paused while hidden
                        if (job.lastRun && now - job.lastRun < job.every) continue;    // not due yet
                        job.lastRun = now;                                            // cadence from start
                        job.running = true;
                        Promise.resolve()
                            .then(() => job.run())
                            .catch(err => console.error('poll[' + job.name + '] failed:', err))
                            .finally(() => { job.running = false; });
                    }
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
                this.bootstrapAuth();
                // api.js transport notifies here on any 401 so one place
                // reopens the login modal for every call in the app.
                window.__SOC_ON_SESSION_EXPIRED__ = () => { this.auth.showLogin = true; };

                // Poll for updates — one scheduler (in-flight dedupe +
                // hidden-tab pause) replaces the nine setInterval timers.
                this.startPolling();
            },
            beforeUnmount() {
                clearInterval(this._pollTimer);
                document.removeEventListener('visibilitychange', this._pollVisibility);
            }
        }).mount('#app');
