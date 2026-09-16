/**
 * components/AnalysisTab.js
 * Presentational AI Analysis tab.
 * Implements 5 distinct progressive investigation stages with clean sub-views,
 * formatted visual assessment cards, and Phase 1 context inheritance.
 */
window.AnalysisTab = {
    props: [
        'ollamaHealth',
        'analysisCaseSearch',
        'analysisCaseId',
        'analysisCases',
        'filteredAnalysisCases',
        'analysisModel',
        'analysisContext',
        'analysisRunning',
        'analysisStatus',
        'phase2Status',
        'analysisResult',
        'analysisSourceNotable',
        'analysisRule',
        'showPhase1Analysis',
        'phase2Model',
        'followUpPhase',
        'phase2Result',
        'investigationState',
        'evidenceFindingOptions',
        'supportiveManualResults',
        'supportiveFindingTypes',
        'enrichmentManualResults',
        'enrichmentFindingTypes',
        'phase2ManualResults',
        'phase2FindingTypes',
        'phase2ResolutionTypes',
        'phase2CoverageNotes',
        'phase2ResolutionQuestions',
        'phase2EditedQueries',
        'supportivePlaybookAvailable',
        'supportiveDraftBusy',
        'supportiveDraftError',
        'supportiveImportBusy',
        'supportiveImportError',
        'supportiveEditorOpen',
        'supportiveEditorBusy',
        'supportiveEditorQueries',
        'supportiveEditorRuleId',
        'supportiveEditorError',
        'placeholderAliasEditorOpen',
        'placeholderAliasEditorBusy',
        'placeholderAliasList',
        'placeholderAliasEditorError',
        'placeholderAliasSuggestions',
        'placeholderAliasSuggestionsBusy',
        'newAliasForm',
        'editingAliasId',
        // pure helpers passed from parent
        'renderSupportiveQueryFn',
        'renderPhase2QueryFn',
        'hasUnresolvedPlaceholdersFn',
        'getSupportiveKeyFn',
        'getEnrichmentKeyFn',
        'getPhase2KeyFn',
        'getPhase2TemplateFn',
        'formatLoopStatusFn',
        'formatDispositionLabelFn',
        'formatFindingLabelFn'
    ],
    emits: [
        'update:analysis-case-search',
        'update:analysis-case-id',
        'update:analysis-model',
        'update:analysis-context',
        'update:phase2-model',
        'update:show-phase1-analysis',
        'on-analysis-case-changed',
        'run-analysis',
        'run-phase2-analysis',
        'cancel-analysis',
        'save-analysis-state',
        'copy-analysis-result',
        'go-to-closure-from-analysis',
        'open-placeholder-alias-editor',
        'open-supportive-editor',
        'generate-supportive-playbook-draft',
        'import-supportive-results',
        'close-supportive-editor',
        'add-supportive-query',
        'remove-supportive-query',
        'save-supportive-queries',
        'close-placeholder-alias-editor',
        'save-placeholder-alias',
        'edit-placeholder-alias',
        'delete-placeholder-alias',
        'load-placeholder-alias-suggestions',
        'toggle-alias-field-candidate',
        'save-supportive-evidence',
        'save-supportive-evidence-and-continue',
        'copy-supportive-spl',
        'copy-enrichment-spl',
        'copy-phase2-spl',
        'promote-phase2-query',
        'on-phase2-template-input',
        'update-supportive-manual',
        'update-supportive-finding',
        'update-enrichment-manual',
        'update-enrichment-finding',
        'update-phase2-manual',
        'update-phase2-finding',
        'update-phase2-coverage',
        'delete-evidence',
        'delete-evidence-batch',
        'delete-all-evidence'
    ],
    data() {
        return {
            selectedEvidenceKeys: [],
            currentStage: 1,
            evidenceResultStatuses: {},
            showResolvedFollowUp: false,
            supportiveImportText: '',
            supportiveImportFilename: ''
        };
    },
    watch: {
        'investigationState.evidence_summary.timeline': {
            handler(timeline) {
                const valid = new Set(
                    (timeline || [])
                        .filter((item) => item)
                        .map((item) => this.evidenceItemKey(item))
                        .filter(Boolean)
                );
                this.selectedEvidenceKeys = (this.selectedEvidenceKeys || []).filter((key) => valid.has(String(key)));
            },
            deep: true
        }
    },
    computed: {
        evidenceTimelineItems() {
            return (this.investigationState && this.investigationState.evidence_summary && this.investigationState.evidence_summary.timeline) || [];
        },
        phase2EvidenceItems() {
            return this.evidenceTimelineItems.filter(item => /^phase\d+_manual$/i.test(item.source_system || ''));
        },
        phase2SavedTitles() {
            return new Set(this.phase2EvidenceItems.map(item => (item.title || '').toString().trim().toLowerCase()));
        },
        selectableEvidenceKeys() {
            return this.evidenceTimelineItems
                .map((item) => this.evidenceItemKey(item))
                .filter(Boolean);
        },
        allEvidenceSelected() {
            const keys = this.selectableEvidenceKeys;
            if (!keys.length) return false;
            const selected = new Set((this.selectedEvidenceKeys || []).map(String));
            return keys.every((key) => selected.has(String(key)));
        },
        someEvidenceSelected() {
            return (this.selectedEvidenceKeys || []).length > 0;
        },
        selectedEvidenceCount() {
            return (this.selectedEvidenceKeys || []).length;
        },
        stage1Complete() {
            return Boolean(this.analysisCaseId);
        },
        stage2Complete() {
            const evidenceSummary = this.investigationState && this.investigationState.evidence_summary;
            const hasEvidence = Boolean(
                evidenceSummary &&
                (evidenceSummary.substantive_items >= 1 || evidenceSummary.total_items >= 1)
            );
            // Unsupported rules have no trusted SPL cards to collect yet.
            // Allow the analyst to reach Initial Assessment so the platform
            // can expose the review-gated playbook onboarding flow.
            const noPlaybookDetected = this.supportivePlaybookAvailable === false ||
                (this.supportivePlaybookAvailable == null &&
                    !(this.analysisRule && (this.analysisRule.supportive_queries || []).length));
            const noPlaybookToCollect = noPlaybookDetected &&
                !this.displayPhase2Queries.length &&
                !hasEvidence;
            return hasEvidence || noPlaybookToCollect;
        },
        stage3Complete() {
            return Boolean(this.analysisResult || (this.investigationState && this.investigationState.iteration_count >= 1));
        },
        stage4Complete() {
            return Boolean(
                (this.investigationState && this.investigationState.iteration_count >= 2) ||
                (this.phase2Result && this.phase2Result.analysis)
            );
        },
        stage5Ready() {
            return Boolean(
                this.investigationState &&
                this.investigationState.loop_status === 'ready_for_closure'
            );
        },
        assessmentVerdict() {
            return (this.analysisResult && this.analysisResult.verdict) ||
                (this.investigationState && this.investigationState.provisional_disposition) ||
                'undetermined';
        },
        assessmentConfidence() {
            const value = this.analysisResult && this.analysisResult.confidence !== undefined
                ? this.analysisResult.confidence
                : this.investigationState && this.investigationState.disposition_confidence;
            return Number(value || 0);
        },
        displayPhase2Queries() {
            if (this.phase2Result && this.phase2Result.phase2_queries && this.phase2Result.phase2_queries.length) {
                return this.phase2Result.phase2_queries;
            }
            if (this.analysisResult && this.analysisResult.phase2_queries && this.analysisResult.phase2_queries.length) {
                return this.analysisResult.phase2_queries;
            }
            if (this.analysisRule && this.analysisRule.supportive_queries) {
                const runTitles = new Set((this.evidenceTimelineItems || []).map(i => (i.title || '').toLowerCase().trim()));
                const unrun = this.analysisRule.supportive_queries.filter(q => !runTitles.has((q.title || '').toLowerCase().trim()));
                if (unrun.length) return unrun.slice(0, 3);
            }
            return [];
        },
        resolvedFollowUpQuestions() {
            return new Set((this.investigationState?.evidence_summary?.resolved_questions || []).map(question => String(question).trim().toLowerCase()));
        },
        activePhase2Queries() {
            const resolved = this.resolvedFollowUpQuestions;
            return this.displayPhase2Queries.filter(query => {
                const targets = (query.target_questions || []).map(question => String(question).trim().toLowerCase());
                return !targets.length || targets.some(target => !resolved.has(target));
            });
        },
        resolvedPhase2Queries() {
            const resolved = this.resolvedFollowUpQuestions;
            return this.displayPhase2Queries.filter(query => {
                const targets = (query.target_questions || []).map(question => String(question).trim().toLowerCase());
                return targets.length && targets.every(target => resolved.has(target));
            });
        },
        visiblePhase2Queries() {
            return this.showResolvedFollowUp
                ? this.displayPhase2Queries
                : this.activePhase2Queries;
        }
    },
    methods: {
        async readSupportiveImportFile(event) {
            const file = event && event.target && event.target.files && event.target.files[0];
            if (!file) return;
            this.supportiveImportFilename = file.name || '';
            this.supportiveImportText = await file.text();
            event.target.value = '';
        },
        submitSupportiveImport() {
            const content = (this.supportiveImportText || '').trim();
            if (!content) return;
            this.$emit('import-supportive-results', {
                filename: this.supportiveImportFilename,
                content,
            });
        },
        goToStage(stage) {
            const requestedStage = Number(stage);
            if (requestedStage <= 1) {
                this.currentStage = 1;
                return;
            }
            if (!this.analysisCaseId) {
                return;
            }
            if (requestedStage >= 3 && !this.stage2Complete) {
                this.currentStage = 2;
                return;
            }
            if (requestedStage >= 4 && !this.stage3Complete) {
                this.currentStage = 3;
                return;
            }
            if (requestedStage >= 5 && !this.stage4Complete) {
                this.currentStage = 4;
                return;
            }
            this.currentStage = requestedStage;
        },
        startNextFollowUpPhase() {
            const nextPhase = Math.max(3, Number(this.followUpPhase || 2) + 1);
            this.$emit('update:follow-up-phase', nextPhase);
            this.phase2ManualResults = {};
            this.phase2FindingTypes = {};
            this.phase2ResolutionTypes = {};
            this.phase2EditedQueries = {};
            this.currentStage = 4;
            this.$emit('run-phase2-analysis');
        },
        getSupportiveKey(q) {
            if (typeof this.getSupportiveKeyFn === 'function') return this.getSupportiveKeyFn(q);
            if (window.QueryKeys) return window.QueryKeys.supportive(q);
            return (q && (q.id || q.title || q.spl || '')) + '';
        },
        getEnrichmentKey(q) {
            if (typeof this.getEnrichmentKeyFn === 'function') return this.getEnrichmentKeyFn(q);
            if (window.QueryKeys) return window.QueryKeys.enrichment(q);
            return (q && (q.id || q.title || q.spl || '')) + '';
        },
        getPhase2Key(q) {
            if (typeof this.getPhase2KeyFn === 'function') return this.getPhase2KeyFn(q);
            if (window.QueryKeys) return window.QueryKeys.phase2(q);
            return (q && (q.id || q.title || q.spl || '')) + '';
        },
        getPhase2Template(q) {
            if (typeof this.getPhase2TemplateFn === 'function') return this.getPhase2TemplateFn(q);
            return (q && q.spl) || '';
        },
        renderSupportiveQuery(q) {
            if (typeof this.renderSupportiveQueryFn === 'function') return this.renderSupportiveQueryFn(q);
            if (window.QueryRender) return window.QueryRender.renderSupportive(q);
            return (q && q.spl) || '';
        },
        renderPhase2Query(textOrQ) {
            if (typeof this.renderPhase2QueryFn === 'function') return this.renderPhase2QueryFn(textOrQ);
            if (window.QueryRender) return window.QueryRender.renderPhase2(textOrQ);
            return typeof textOrQ === 'string' ? textOrQ : ((textOrQ && textOrQ.spl) || '');
        },
        hasUnresolvedPlaceholders(text) {
            if (typeof this.hasUnresolvedPlaceholdersFn === 'function') return this.hasUnresolvedPlaceholdersFn(text);
            if (window.QueryRender) return window.QueryRender.hasUnresolved(text);
            return /\{[^}]+\}/.test(text || '');
        },
        formatLoopStatus(v) {
            if (typeof this.formatLoopStatusFn === 'function') return this.formatLoopStatusFn(v);
            return v || '—';
        },
        formatDispositionLabel(v) {
            if (typeof this.formatDispositionLabelFn === 'function') return this.formatDispositionLabelFn(v);
            return v || '—';
        },
        formatFindingLabel(v) {
            if (typeof this.formatFindingLabelFn === 'function') return this.formatFindingLabelFn(v);
            return v || '—';
        },
        getAnalysisSection(sectionKey) {
            if (!this.analysisResult) return '';
            const sec = this.analysisResult.analysis_sections || {};
            if (sec[sectionKey]) return sec[sectionKey].trim();
            const text = this.analysisResult.analysis || '';
            const escapedKey = sectionKey
                .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
                .replace(/\s+/g, '\\s+');
            const pattern = new RegExp('(?:\\*\\*|###\\s+)?' + escapedKey + '[\\s\\S]*?(?=(?:\\*\\*|###\\s+)|$)', 'i');
            const match = text.match(pattern);
            if (match) {
                const headerPattern = new RegExp('^(?:\\*\\*|###\\s+)?' + escapedKey + '[\\s\\S]*?\\n', 'i');
                return match[0].replace(headerPattern, '').trim();
            }
            return '';
        },
        formatAnalysisText(value) {
            const source = (value || '').toString()
                .replace(/\\#/g, '#')
                .replace(/\\\*/g, '*');
            if (!source.trim()) return '';
            const escapeHtml = (text) => text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/\"/g, '&quot;');
            const inline = (text) => escapeHtml(text)
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-black/40 text-purple-200">$1</code>');
            const blocks = [];
            let list = null;
            const flushList = () => {
                if (!list) return;
                blocks.push('<' + list.type + ' class="space-y-1.5 ml-5 ' + (list.type === 'ol' ? 'list-decimal' : 'list-disc') + '">' + list.items.map(item => '<li>' + inline(item) + '</li>').join('') + '</' + list.type + '>');
                list = null;
            };
            source.split(/\r?\n/).forEach(line => {
                const trimmed = line.trim();
                if (!trimmed) { flushList(); return; }
                const heading = trimmed.match(/^#{1,6}\s+(.+)$/);
                const bullet = trimmed.match(/^[-*]\s+(.+)$/);
                const numbered = trimmed.match(/^\d+[.)]\s+(.+)$/);
                if (heading) {
                    flushList();
                    blocks.push('<h4 class="text-sm font-bold text-purple-200 mt-3 first:mt-0">' + inline(heading[1]) + '</h4>');
                } else if (bullet || numbered) {
                    const type = numbered ? 'ol' : 'ul';
                    if (!list || list.type !== type) { flushList(); list = { type, items: [] }; }
                    list.items.push((bullet || numbered)[1]);
                } else {
                    flushList();
                    blocks.push('<p>' + inline(trimmed) + '</p>');
                }
            });
            flushList();
            return blocks.join('');
        },
        getParsedQuestions() {
            const raw = this.getAnalysisSection('key questions');
            if (!raw) return [];
            return raw.split(String.fromCharCode(10))
                .map(l => l.replace(/^\d+[\.\)]\s*|-\s*|\*\s*/, '').trim())
                .filter(l => Boolean(l) && (l.endsWith('?') || l.length > 10));
        },
        emitSupportiveManual(q, value) {
            this.$emit('update-supportive-manual', { key: this.getSupportiveKey(q), value });
        },
        emitSupportiveFinding(q, value) {
            this.$emit('update-supportive-finding', { key: this.getSupportiveKey(q), value });
        },
        emitEnrichmentManual(q, value) {
            this.$emit('update-enrichment-manual', { key: this.getEnrichmentKey(q), value });
        },
        emitEnrichmentFinding(q, value) {
            this.$emit('update-enrichment-finding', { key: this.getEnrichmentKey(q), value });
        },
        emitPhase2Manual(q, value) {
            this.$emit('update-phase2-manual', { key: this.getPhase2Key(q), value });
        },
        emitPhase2Finding(q, value) {
            this.$emit('update-phase2-finding', { key: this.getPhase2Key(q), value });
        },
        evidenceItemKey(item) {
            if (!item) return '';
            if (item.id !== null && item.id !== undefined && item.id !== '') {
                return 'id:' + String(item.id);
            }
            return 'row:'
                + String(item.title || '').trim().toLowerCase()
                + '|'
                + String(item.source_system || '').trim().toLowerCase()
                + '|'
                + String(item.created_at || '');
        },
        isEvidenceSelected(item) {
            const key = this.evidenceItemKey(item);
            if (!key) return false;
            return (this.selectedEvidenceKeys || []).map(String).includes(key);
        },
        toggleEvidenceSelection(item, checked) {
            const key = this.evidenceItemKey(item);
            if (!key) return;
            const current = (this.selectedEvidenceKeys || []).map(String);
            if (checked) {
                if (!current.includes(key)) {
                    this.selectedEvidenceKeys = current.concat([key]);
                }
            } else {
                this.selectedEvidenceKeys = current.filter((k) => k !== key);
            }
        },
        toggleSelectAllEvidence(checked) {
            if (checked) {
                this.selectedEvidenceKeys = this.selectableEvidenceKeys.map(String);
            } else {
                this.selectedEvidenceKeys = [];
            }
        },
        emitDeleteSelectedEvidence() {
            const selected = this.evidenceTimelineItems.filter((item) => this.isEvidenceSelected(item));
            if (!selected.length) return;
            this.$emit('delete-evidence-batch', selected);
        },
        emitDeleteAllEvidence() {
            this.$emit('delete-all-evidence');
        }
    },
    template: `
        <div class="space-y-6">
    <div>
        <h2 class="text-3xl font-bold mb-2">AI Analysis</h2>
        <p class="text-gray-400">Run local Ollama models with progressive 5-stage investigation workflows</p>
    </div>

    <!-- 5-Stage Investigation Workflow Stepper with dynamic progress coloring -->
    <div class="flex items-center space-x-2 bg-gray-900 border border-gray-700 rounded-lg p-2.5 overflow-x-auto text-xs">
        <button
            type="button"
            @click="goToStage(1)"
            :class="[
                currentStage === 1
                    ? 'bg-blue-600 text-white font-semibold shadow-md ring-2 ring-blue-400'
                    : stage1Complete
                        ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-600/80 hover:bg-emerald-900/90'
                        : 'text-gray-400 bg-gray-800/80 border border-gray-700 hover:text-white'
            ]"
            class="px-3 py-1.5 rounded flex items-center gap-1.5 transition"
        >
            <span
                class="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold"
                :class="stage1Complete && currentStage !== 1 ? 'bg-emerald-500 text-black' : 'bg-black/40'"
            >{{ stage1Complete && currentStage !== 1 ? '✓' : '1' }}</span>
            <span>Case & Context</span>
        </button>

        <span :class="stage1Complete ? 'text-emerald-400 font-bold' : 'text-gray-600'">&rarr;</span>

        <button
            type="button"
            @click="goToStage(2)"
            :class="[
                currentStage === 2
                    ? 'bg-blue-600 text-white font-semibold shadow-md ring-2 ring-blue-400'
                    : stage2Complete
                        ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-600/80 hover:bg-emerald-900/90'
                        : 'text-gray-400 bg-gray-800/80 border border-gray-700 hover:text-white'
            ]"
            class="px-3 py-1.5 rounded flex items-center gap-1.5 transition"
        >
            <span
                class="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold"
                :class="stage2Complete && currentStage !== 2 ? 'bg-emerald-500 text-black' : 'bg-black/40'"
            >{{ stage2Complete && currentStage !== 2 ? '✓' : '2' }}</span>
            <span>Evidence Collection</span>
        </button>

        <span :class="stage2Complete ? 'text-emerald-400 font-bold' : 'text-gray-600'">&rarr;</span>

        <button
            type="button"
            @click="goToStage(3)"
            :class="[
                currentStage === 3
                    ? 'bg-blue-600 text-white font-semibold shadow-md ring-2 ring-blue-400'
                    : stage3Complete
                        ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-600/80 hover:bg-emerald-900/90'
                        : 'text-gray-400 bg-gray-800/80 border border-gray-700 hover:text-white'
            ]"
            class="px-3 py-1.5 rounded flex items-center gap-1.5 transition"
        >
            <span
                class="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold"
                :class="stage3Complete && currentStage !== 3 ? 'bg-emerald-500 text-black' : 'bg-black/40'"
            >{{ stage3Complete && currentStage !== 3 ? '✓' : '3' }}</span>
            <span>Initial Assessment</span>
            <span v-if="investigationState && investigationState.evidence_summary && (investigationState.evidence_summary.substantive_items || investigationState.evidence_summary.total_items)" class="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-blue-950 text-blue-200 border border-blue-700">
                {{ investigationState.evidence_summary.substantive_items || investigationState.evidence_summary.total_items }}
            </span>
        </button>

        <span :class="stage3Complete ? 'text-emerald-400 font-bold' : 'text-gray-600'">&rarr;</span>

        <button
            type="button"
            @click="goToStage(4)"
            :class="[
                currentStage === 4
                    ? 'bg-blue-600 text-white font-semibold shadow-md ring-2 ring-blue-400'
                    : stage4Complete
                        ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-600/80 hover:bg-emerald-900/90'
                        : 'text-gray-400 bg-gray-800/80 border border-gray-700 hover:text-white'
            ]"
            class="px-3 py-1.5 rounded flex items-center gap-1.5 transition"
        >
            <span
                class="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold"
                :class="stage4Complete && currentStage !== 4 ? 'bg-emerald-500 text-black' : 'bg-black/40'"
            >{{ stage4Complete && currentStage !== 4 ? '✓' : '4' }}</span>
            <span>Phase {{ followUpPhase }} Follow-Up</span>
        </button>

        <span :class="stage4Complete ? 'text-emerald-400 font-bold' : 'text-gray-600'">&rarr;</span>

        <button
            type="button"
            @click="goToStage(5)"
            :class="[
                currentStage === 5
                    ? 'bg-blue-600 text-white font-semibold shadow-md ring-2 ring-blue-400'
                    : stage5Ready
                        ? 'bg-emerald-600 text-white font-bold animate-pulse shadow-md border border-emerald-400'
                        : 'text-gray-400 bg-gray-800/80 border border-gray-700 hover:text-white'
            ]"
            class="px-3 py-1.5 rounded flex items-center gap-1.5 transition"
        >
            <span
                class="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold"
                :class="stage5Ready && currentStage !== 5 ? 'bg-white text-emerald-700' : 'bg-black/40'"
            >{{ stage5Ready ? '★' : '5' }}</span>
            <span>Verdict & Closure</span>
            <span v-if="stage5Ready" class="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-white/20 text-white font-bold uppercase tracking-wider">
                Ready
            </span>
        </button>
    </div>

    <div v-if="!ollamaHealth.available" class="bg-red-900 bg-opacity-30 border border-red-700 rounded-lg p-6 text-red-200">
        <p class="font-bold">⚠️ Ollama Service Not Available</p>
        <p class="text-sm mt-2">Start Ollama with: <code class="bg-black px-2 py-1 rounded">ollama serve</code></p>
    </div>

    <!-- Active Case Anchor (always visible so user knows case context) -->
    <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <div class="flex items-center justify-between gap-4">
            <div class="min-w-0 flex-1">
                <label class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Active Case</label>
                <div class="flex items-center gap-3 mt-1">
                    <select
                        :value="analysisCaseId"
                        @input="$emit('update:analysis-case-id', $event.target.value)"
                        @change="$emit('on-analysis-case-changed')"
                        class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500 min-w-[280px]"
                    >
                        <option value="">-- Choose a case from the database --</option>
                        <option v-for="case_ in filteredAnalysisCases" :key="case_.case_id" :value="case_.case_id">
                            {{ case_.case_id }} - {{ case_.rule_name }} ({{ case_.verdict }})
                        </option>
                    </select>
                    <span v-if="analysisCaseId" class="text-xs px-2.5 py-1 rounded font-mono font-bold bg-blue-950 border border-blue-700 text-blue-300">
                        {{ analysisCaseId }}
                    </span>
                </div>
            </div>
            <div class="text-right text-xs text-gray-400 flex items-center gap-3">
                <div>
                    <span class="text-gray-500">Stage:</span>
                    <span class="ml-1 text-white font-semibold">Stage {{ currentStage }} of 5</span>
                </div>
            </div>
        </div>
    </div>

    <!-- ============================================================= -->
    <!-- STAGE 1: CASE & RULE CONTEXT -->
    <!-- ============================================================= -->
    <div v-show="currentStage === 1" class="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-6">
        <div>
            <h3 class="text-lg font-bold text-blue-300">Stage 1: Case & Notable Context</h3>
            <p class="text-xs text-gray-400 mt-1">Review the ingested notable telemetry, anchor fields, and detection rule science before running analysis.</p>
        </div>

        <div v-if="analysisSourceNotable && analysisSourceNotable.parse_assessment" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3">
            <div class="flex items-center justify-between gap-3">
                <div>
                    <p class="text-sm font-semibold text-blue-300">Parse Quality Assessment</p>
                    <p class="text-xs text-gray-400">Evaluates completeness of technical fields before AI analysis.</p>
                </div>
                <div class="text-right">
                    <p class="text-xl font-bold text-white">{{ analysisSourceNotable.parse_assessment.score || 0 }}</p>
                    <p class="text-[11px] uppercase tracking-wide" :class="{
                        'text-green-300': analysisSourceNotable.parse_assessment.mode === 'normal',
                        'text-amber-300': analysisSourceNotable.parse_assessment.mode === 'enrichment',
                        'text-red-300': analysisSourceNotable.parse_assessment.mode === 'extraction'
                    }">{{ analysisSourceNotable.parse_assessment.mode || 'unknown' }}</p>
                </div>
            </div>

            <div v-if="analysisSourceNotable.fields" class="mt-3">
                <p class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-2">Extracted Entity Fields</p>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                    <div v-for="(val, k) in analysisSourceNotable.fields" :key="k" class="bg-gray-800 border border-gray-700 rounded p-2 overflow-hidden">
                        <span class="text-gray-400 font-mono text-[11px] block truncate">{{ k }}:</span>
                        <span class="text-white font-mono text-xs font-semibold truncate block mt-0.5" :title="val">{{ val }}</span>
                    </div>
                </div>
            </div>

            <div v-if="analysisSourceNotable.sanitized_text" class="mt-3">
                <details>
                    <summary class="text-xs text-gray-400 font-semibold cursor-pointer hover:text-white">View Raw Sanitized Notable Log</summary>
                    <pre class="mt-2 text-xs text-gray-300 bg-black/50 p-3 rounded overflow-x-auto whitespace-pre-wrap font-mono">{{ analysisSourceNotable.sanitized_text }}</pre>
                </details>
            </div>
        </div>

        <div v-if="analysisRule" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-2">
            <h4 class="text-xs font-bold text-blue-300 uppercase tracking-wider">Detection Rule Science</h4>
            <p class="text-sm font-semibold text-white">{{ analysisRule.rule_name }}</p>
            <p class="text-xs text-gray-300">{{ analysisRule.description }}</p>
            <div class="flex gap-2 pt-1 text-xs">
                <span class="px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700 font-mono">Severity: {{ analysisRule.severity }}</span>
                <span v-if="analysisRule.drilldown_fields" class="px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700 font-mono">Drilldowns: {{ (analysisRule.drilldown_fields || []).join(', ') }}</span>
            </div>
        </div>

        <div class="flex justify-end pt-4 border-t border-gray-700">
            <button
                type="button"
                @click="goToStage(2)"
                class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition shadow flex items-center gap-2"
            >
                <span>Proceed to Evidence Collection</span>
                <span>&rarr;</span>
            </button>
        </div>
    </div>

    <!-- ============================================================= -->
    <!-- STAGE 3: INITIAL AI ASSESSMENT -->
    <!-- ============================================================= -->
    <div v-show="currentStage === 3" class="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-6">
        <div>
            <h3 class="text-lg font-bold text-blue-300">Stage 3: Initial AI Assessment</h3>
            <p class="text-xs text-gray-400 mt-1">Review the collected SPL results and raw notable logs, then run the initial evidence-based assessment.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
                <label class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Model</label>
                <select
                    :value="analysisModel"
                    @input="$emit('update:analysis-model', $event.target.value)"
                    class="w-full mt-1.5 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-xs focus:outline-none focus:border-blue-500"
                >
                    <option v-for="model in ollamaHealth.models" :key="model" :value="model">{{ model }}</option>
                </select>
            </div>
            <div class="md:col-span-2">
                <label class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Analyst Context / Notes (Optional)</label>
                <input
                    :value="analysisContext"
                    @input="$emit('update:analysis-context', $event.target.value)"
                    type="text"
                    placeholder="Add operational context, asset tier, or maintenance window notes..."
                    class="w-full mt-1.5 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-xs placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
            </div>
        </div>

        <div v-if="supportivePlaybookAvailable === false" class="bg-amber-950/40 border border-amber-700/70 rounded-lg p-4 space-y-2">
            <p class="text-sm font-semibold text-amber-200">No supportive playbook exists for this rule yet.</p>
            <p class="text-xs text-gray-300">Before or after the initial assessment, generate reviewable SPL drafts, replace the index placeholder, and save the approved queries to create this rule's playbook.</p>
            <p v-if="supportiveDraftError" class="text-xs text-red-300">{{ supportiveDraftError }}</p>
            <button
                type="button"
                class="px-4 py-2 bg-amber-600 hover:bg-amber-500 rounded text-xs font-bold text-white transition"
                :disabled="analysisRunning || supportiveDraftBusy || !analysisCaseId"
                @click="$emit('generate-supportive-playbook-draft')"
            >
                {{ supportiveDraftBusy ? 'Generating Drafts...' : 'Generate Draft Supportive Playbook' }}
            </button>
        </div>

        <div class="flex items-center justify-between pt-2">
            <button
                type="button"
                class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 rounded text-xs font-bold text-white transition shadow flex items-center gap-2"
                :disabled="analysisRunning || !analysisCaseId"
                @click="$emit('run-analysis')"
            >
                <span v-if="analysisRunning" class="animate-spin">⟳</span>
                <span>{{ analysisRunning ? 'Analyzing with ' + analysisModel + '...' : (analysisResult ? 'Re-run Initial Analysis' : 'Run Initial Analysis') }}</span>
            </button>
            <span v-if="analysisRunning" class="text-xs text-blue-400 animate-pulse">Querying local Ollama instance...</span>
        </div>

        <div
            v-if="analysisStatus && analysisStatus.phase !== 'idle'"
            class="flex items-center justify-between gap-3 rounded border px-3 py-2 text-xs"
            :class="analysisStatus.phase === 'error' || analysisStatus.phase === 'timeout' ? 'bg-red-950/40 border-red-700 text-red-200' : analysisStatus.phase === 'complete' ? 'bg-emerald-950/40 border-emerald-700 text-emerald-200' : 'bg-blue-950/40 border-blue-700 text-blue-200'"
        >
            <div class="flex items-center gap-2 min-w-0">
                <span v-if="analysisRunning && analysisStatus.phase !== 'timeout'" class="animate-pulse">●</span>
                <span v-else-if="analysisStatus.phase === 'complete'">✓</span>
                <span v-else-if="analysisStatus.phase === 'error' || analysisStatus.phase === 'timeout'">!</span>
                <span v-else>○</span>
                <span class="truncate">{{ analysisStatus.message }}</span>
            </div>
            <div class="flex items-center gap-3 flex-shrink-0">
                <span class="font-mono">{{ analysisStatus.elapsedSeconds }}s</span>
                <button
                    v-if="analysisRunning && analysisStatus.phase === 'timeout'"
                    type="button"
                    class="px-2 py-1 rounded bg-red-800 hover:bg-red-700 text-red-100 font-semibold"
                    @click="$emit('cancel-analysis')"
                >Cancel</button>
            </div>
        </div>

        <!-- Formatted Initial Assessment Visual Cards -->
        <div v-if="analysisResult" class="space-y-4 pt-4 border-t border-gray-700">
            <!-- 1. Initial Thoughts / Hypothesis Card -->
            <div class="bg-gray-900 border border-blue-800/80 rounded-lg p-4 space-y-2">
                <div class="flex items-center gap-2 text-blue-300 font-semibold text-xs uppercase tracking-wider">
                    <span>💡</span>
                    <span>Threat Hypothesis & Initial Thoughts</span>
                </div>
                <p class="text-xs text-gray-200 leading-relaxed whitespace-pre-wrap">{{ getAnalysisSection('initial thoughts') || analysisResult.summary || 'Initial evaluation completed.' }}</p>
            </div>

            <!-- 2. Key Questions Card -->
            <div v-if="getParsedQuestions().length" class="bg-gray-900 border border-amber-800/80 rounded-lg p-4 space-y-2">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 text-amber-300 font-semibold text-xs uppercase tracking-wider">
                        <span>❓</span>
                        <span>Key Questions to Answer</span>
                    </div>
                    <span class="text-[10px] text-amber-400 font-semibold uppercase">{{ getParsedQuestions().length }} Inquiries to Resolve</span>
                </div>
                <ul class="space-y-1.5 text-xs text-gray-200">
                    <li v-for="(q, idx) in getParsedQuestions()" :key="idx" class="flex items-start gap-2">
                        <span class="w-4 h-4 rounded-full bg-amber-950 border border-amber-700 text-amber-300 flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5">{{ idx + 1 }}</span>
                        <span>{{ q }}</span>
                    </li>
                </ul>
            </div>

            <!-- 3. Investigative Analysis Narrative Card -->
            <div v-if="getAnalysisSection('investigative analysis')" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-2">
                <div class="flex items-center gap-2 text-gray-300 font-semibold text-xs uppercase tracking-wider">
                    <span>🔍</span>
                    <span>Investigative Analysis</span>
                </div>
                <p class="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap">{{ getAnalysisSection('investigative analysis') }}</p>
            </div>

            <!-- 4. Preliminary Triage Verdict Card -->
            <div
                class="bg-gray-900 border rounded-lg p-4 flex items-center justify-between"
                :class="{
                    'border-red-600/80 bg-red-950/20': assessmentVerdict.toLowerCase() === 'malicious',
                    'border-emerald-600/80 bg-emerald-950/20': ['benign', 'false_positive'].includes(assessmentVerdict.toLowerCase()),
                    'border-gray-700': !assessmentVerdict
                }"
            >
                <div>
                    <p class="text-[11px] uppercase tracking-wider text-gray-400 font-semibold">Preliminary Verdict</p>
                    <p class="text-lg font-bold text-white capitalize mt-0.5">{{ assessmentVerdict.replace('_', ' ') }}</p>
                </div>
                <div class="text-right">
                    <p class="text-[11px] uppercase tracking-wider text-gray-400 font-semibold">Confidence</p>
                    <p class="text-lg font-bold text-emerald-400 mt-0.5">{{ (assessmentConfidence * 100).toFixed(0) }}%</p>
                </div>
            </div>
        </div>

        <div class="flex justify-between pt-4 border-t border-gray-700">
            <button
                type="button"
                @click="goToStage(2)"
                class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-200 transition"
            >
                &larr; Back to Evidence Collection
            </button>
            <button
                type="button"
                @click="goToStage(4)"
                class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition shadow flex items-center gap-2"
            >
                <span>Proceed to Phase 2 Follow-Up</span>
                <span>&rarr;</span>
            </button>
        </div>
    </div>

    <!-- ============================================================= -->
    <!-- STAGE 2: EVIDENCE COLLECTION -->
    <!-- ============================================================= -->
    <div v-show="currentStage === 2" class="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-6">
        <div class="flex items-center justify-between">
            <div>
                <h3 class="text-lg font-bold text-blue-300">Stage 2: Evidence Collection</h3>
                <p class="text-xs text-gray-400 mt-1">Run the available supportive SPL queries, paste raw results, and save the evidence before the initial assessment.</p>
            </div>
            <div class="flex items-center gap-2">
                <button
                    type="button"
                    class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition shadow"
                    :disabled="supportiveSaveBusy"
                    @click="$emit('save-supportive-evidence-and-continue')"
                >
                    {{ supportiveSaveBusy ? 'Saving...' : 'Save & Continue to Initial Assessment' }}
                </button>
                <button
                    type="button"
                    class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded text-xs font-semibold text-gray-200"
                    :disabled="supportiveSaveBusy"
                    @click="$emit('save-supportive-evidence')"
                >
                    Save Only
                </button>
                <button
                    type="button"
                    class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded text-xs font-semibold text-gray-200"
                    @click="$emit('open-supportive-editor', analysisRule)"
                >
                    Manage
                </button>
            </div>
        </div>

        <!-- Supportive Queries Cards -->
        <div v-if="analysisRule && analysisRule.supportive_queries && analysisRule.supportive_queries.length" class="space-y-4">
            <div
                v-for="q in analysisRule.supportive_queries"
                :key="q.id"
                class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3"
            >
                <div class="flex items-start justify-between gap-2">
                    <div>
                        <p class="text-xs font-bold text-blue-300">{{ q.title }}</p>
                        <p class="text-xs text-gray-400 mt-0.5" v-if="q.description">{{ q.description }}</p>
                    </div>
                    <button
                        type="button"
                        class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-[11px] font-semibold text-gray-200 flex-shrink-0"
                        @click="$emit('copy-supportive-spl', q)"
                    >
                        Copy SPL
                    </button>
                </div>
                <pre class="text-xs text-gray-200 bg-black/60 rounded p-2.5 whitespace-pre-wrap font-mono">{{ renderSupportiveQuery(q) }}</pre>

                <div>
                    <label class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Results / Observation Notes</label>
                    <textarea
                        :value="supportiveManualResults[getSupportiveKey(q)] || ''"
                        @input="emitSupportiveManual(q, $event.target.value)"
                        rows="3"
                        class="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400 font-mono"
                        placeholder="Paste key rows or a short summary from this query's results..."
                    ></textarea>
                </div>

                <div>
                    <label class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Collection Status</label>
                    <select
                        v-model="evidenceResultStatuses[getSupportiveKey(q)]"
                        class="w-full mt-1 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-blue-400"
                    >
                        <option value="success">Success (Events Found)</option>
                        <option value="no_results">No Results (0 Events)</option>
                        <option value="data_source_unavailable">Data Source Unavailable</option>
                        <option value="query_failed">Query Failed</option>
                    </select>
                    <p class="text-[10px] text-gray-500 mt-1">Record only what happened when you ran the query — whether the evidence supports or refutes the hypothesis is decided by the AI assessment, not here.</p>
                </div>
            </div>
        </div>

        <!-- Evidence Timeline / Ledger -->
        <div v-if="evidenceTimelineItems.length" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3">
            <div class="flex items-center justify-between">
                <div>
                    <h4 class="text-xs font-bold text-gray-300 uppercase tracking-wider">Evidence Timeline Ledger ({{ evidenceTimelineItems.length }} Saved Items)</h4>
                    <p class="text-[11px] text-gray-400">Durable findings driving confidence scoring and closure gating.</p>
                </div>
                <div class="flex items-center gap-2">
                    <button
                        v-if="someEvidenceSelected"
                        type="button"
                        class="text-[11px] px-2.5 py-1 bg-red-900 hover:bg-red-800 border border-red-700 rounded text-red-100 font-semibold"
                        @click="emitDeleteSelectedEvidence"
                    >
                        Delete Selected ({{ selectedEvidenceCount }})
                    </button>
                    <button
                        type="button"
                        class="text-[11px] px-2.5 py-1 bg-red-950 hover:bg-red-900 border border-red-800 rounded text-red-200 font-semibold"
                        @click="emitDeleteAllEvidence"
                    >
                        Delete All
                    </button>
                </div>
            </div>

            <div class="space-y-2 mt-2">
                <div
                    v-for="item in evidenceTimelineItems"
                    :key="'timeline:' + evidenceItemKey(item)"
                    class="bg-gray-800 border border-gray-700 rounded p-3"
                    :class="{ 'border-blue-600': isEvidenceSelected(item) }"
                >
                    <div class="flex items-start gap-3">
                        <input
                            type="checkbox"
                            class="mt-1 rounded border-gray-600 bg-gray-900 text-blue-500 focus:ring-blue-500 cursor-pointer"
                            :checked="isEvidenceSelected(item)"
                            @change="toggleEvidenceSelection(item, $event.target.checked)"
                        />
                        <div class="min-w-0 flex-1">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <p class="text-xs font-bold text-gray-100">{{ item.title }}</p>
                                    <div class="flex flex-wrap items-center gap-1.5 mt-1">
                                        <span class="text-[11px] text-gray-400 font-mono">{{ item.source_system }}</span>
                                        <span
                                            class="text-[10px] px-1.5 py-0.2 rounded border font-semibold"
                                            :class="{
                                                'bg-red-950 text-red-200 border-red-800': item.finding_type === 'supports',
                                                'bg-emerald-950 text-emerald-200 border-emerald-800': item.finding_type === 'refutes',
                                                'bg-gray-800 text-gray-300 border-gray-700': item.finding_type === 'neutral'
                                            }"
                                        >{{ formatFindingLabel(item.finding_type) }}</span>
                                        <span
                                            v-if="item.ai_verdict_source === 'per_card'"
                                            class="text-[10px] px-1.5 py-0.2 rounded border font-semibold bg-indigo-950 text-indigo-200 border-indigo-800"
                                            :title="item.ai_verdict_rationale || 'Model-assessed verdict for this entry'"
                                        >AI ASSESSMENT</span>
                                        <span
                                            v-if="item.result_status"
                                            class="text-[10px] px-1.5 py-0.2 rounded border font-semibold"
                                            :class="{
                                                'bg-blue-950 text-blue-200 border-blue-800': item.result_status === 'success',
                                                'bg-purple-950 text-purple-200 border-purple-800': item.result_status === 'no_results',
                                                'bg-red-950 text-red-200 border-red-800': item.result_status === 'query_failed' || item.result_status === 'data_source_unavailable',
                                                'bg-emerald-950 text-emerald-200 border-emerald-800': item.result_status === 'benign_result'
                                            }"
                                        >{{ item.result_status.replace('_', ' ').toUpperCase() }}</span>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    class="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-white hover:bg-red-700 border border-gray-600 hover:border-red-500 text-sm font-bold"
                                    title="Delete this evidence item"
                                    @click.stop="$emit('delete-evidence', item)"
                                >
                                    ×
                                </button>
                            </div>
                            <p v-if="item.summary" class="text-xs text-gray-300 mt-2 font-mono whitespace-pre-wrap">{{ item.summary }}</p>
                            <p v-if="item.ai_verdict_source === 'per_card' && item.ai_verdict_rationale" class="text-xs text-indigo-300 mt-1 italic">AI: {{ item.ai_verdict_rationale }}</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="flex justify-between pt-4 border-t border-gray-700">
            <button
                type="button"
                @click="goToStage(1)"
                class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-200 transition"
            >
                &larr; Back to Case Context
            </button>
            <button
                type="button"
                @click="goToStage(3)"
                class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition shadow flex items-center gap-2"
            >
                <span>Proceed to Initial Assessment</span>
                <span>&rarr;</span>
            </button>
        </div>
    </div>

    <!-- ============================================================= -->
    <!-- STAGE 4: PHASE 2 FOLLOW-UP & DEEP INVESTIGATION -->
    <!-- ============================================================= -->
        <div v-show="currentStage === 4" class="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-6">
        <div>
            <h3 class="text-lg font-bold text-blue-300">Stage 4: Phase {{ followUpPhase }} Follow-Up</h3>
            <p class="text-xs text-gray-400 mt-1">Targeted investigation checks generated from the prior phase findings to address remaining questions.</p>
        </div>

        <div
            v-if="phase2Status && phase2Status.phase !== 'idle'"
            class="rounded border px-3 py-2 text-xs flex items-center gap-2"
            :class="phase2Status.phase === 'error' || phase2Status.phase === 'timeout' ? 'bg-red-950/40 border-red-700 text-red-200' : phase2Status.phase === 'complete' ? 'bg-emerald-950/40 border-emerald-700 text-emerald-200' : 'bg-blue-950/40 border-blue-700 text-blue-200'"
        >
            <span v-if="analysisRunning && phase2Status.phase !== 'timeout'" class="animate-pulse">●</span>
            <span v-else-if="phase2Status.phase === 'complete'">✓</span>
            <span v-else-if="phase2Status.phase === 'error' || phase2Status.phase === 'timeout'">!</span>
            <span class="truncate">{{ phase2Status.message }}</span>
            <span class="font-mono ml-auto">{{ phase2Status.elapsedSeconds }}s</span>
            <button
                v-if="analysisRunning && phase2Status.phase === 'timeout'"
                type="button"
                class="px-2 py-1 rounded bg-red-700 hover:bg-red-600 text-white font-semibold"
                @click="$emit('cancel-analysis')"
            >Cancel</button>
        </div>

        <!-- Inherited Context Card from Phase 1 -->
        <div class="bg-gray-900 border border-blue-800/80 rounded-lg p-4 space-y-3">
            <div class="flex items-center justify-between">
                <div>
                    <p class="text-[11px] font-bold uppercase tracking-wider text-blue-400">Context Inherited from Phase 1</p>
                    <p class="text-xs text-gray-200 mt-0.5">Hypothesis: <span class="text-white font-medium">{{ investigationState?.current_hypothesis || 'Active threat investigation in progress' }}</span></p>
                </div>
                <span class="px-2.5 py-1 rounded bg-blue-950 border border-blue-700 text-blue-200 text-xs font-semibold">
                    {{ investigationState?.evidence_summary?.substantive_items || 0 }} Substantive Items Verified
                </span>
            </div>
            <div v-if="investigationState?.unresolved_questions && investigationState.unresolved_questions.length" class="pt-1 border-t border-gray-800">
                <p class="text-[11px] font-semibold text-amber-300">Open Inquiries Being Targeted in Phase {{ followUpPhase }}:</p>
                <ul class="list-disc list-inside text-xs text-gray-300 space-y-1 mt-1">
                    <li v-for="item in investigationState.unresolved_questions" :key="'p2-q:' + item">{{ item }}</li>
                </ul>
            </div>
        </div>

        <!-- Specialized Grounded Query Cards -->
        <div v-if="displayPhase2Queries.length" class="space-y-4">
            <div class="flex items-center justify-between">
                <p class="text-xs font-bold uppercase tracking-wider text-gray-400">Specialized Phase {{ followUpPhase }} Investigative Queries</p>
                <div class="flex items-center gap-3">
                    <button v-if="resolvedPhase2Queries.length" type="button" class="text-[11px] text-emerald-300 hover:text-emerald-200" @click="showResolvedFollowUp = !showResolvedFollowUp">
                        {{ showResolvedFollowUp ? 'Hide Resolved' : 'Show Resolved (' + resolvedPhase2Queries.length + ')' }}
                    </button>
                    <button type="button" class="text-[11px] text-blue-300 hover:text-blue-200" @click="$emit('open-placeholder-alias-editor')">Manage Aliases</button>
                </div>
            </div>

            <div class="bg-blue-950/30 border border-blue-800/70 rounded-lg px-3 py-2 text-xs text-gray-300">
                <span class="font-semibold text-blue-200">Iterative follow-up:</span>
                Run the checks that target the remaining questions, save each result, then re-analyze. A query marked <span class="text-emerald-300 font-semibold">Already saved</span> is retained for the audit trail and does not need to be collected again.
            </div>

            <div v-if="!activePhase2Queries.length && resolvedPhase2Queries.length && !showResolvedFollowUp" class="bg-emerald-950/30 border border-emerald-800/70 rounded-lg px-3 py-2 text-xs text-emerald-200">
                All current inquiries are resolved. Show the resolved queries above if you need to add another piece of evidence.
            </div>

            <div
                v-for="q in visiblePhase2Queries"
                :key="getPhase2Key(q)"
                class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3"
            >
                    <div class="flex items-start justify-between gap-2">
                        <div>
                            <p class="text-xs font-bold text-purple-300">{{ q.title }}</p>
                            <p class="text-xs text-gray-400 mt-0.5" v-if="q.description">{{ q.description }}</p>
                        <p class="text-[11px] text-amber-300 mt-1" v-if="q.target_questions && q.target_questions.length">
                            <span class="font-semibold uppercase tracking-wide">Targets:</span> {{ q.target_questions.join(' • ') }}
                        </p>
                        </div>
                    <div class="flex items-center gap-2">
                        <span v-if="phase2SavedTitles.has((q.title || '').toString().trim().toLowerCase())" class="px-2 py-1 rounded bg-emerald-950 border border-emerald-700 text-[10px] uppercase font-bold text-emerald-300">Already saved</span>
                        <button
                            type="button"
                            class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-[11px] font-semibold text-gray-200"
                            @click="$emit('copy-phase2-spl', q)"
                        >
                            Copy SPL
                        </button>
                    </div>
                </div>

                <div>
                    <label class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Editable SPL Query</label>
                    <textarea
                        :value="getPhase2Template(q)"
                        @input="onPhase2TemplateInput(q, $event.target.value)"
                        rows="3"
                        class="w-full mt-1 px-3 py-2 bg-black/60 border border-gray-700 rounded text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-400 font-mono"
                        spellcheck="false"
                    ></textarea>
                    <p class="text-[10px] text-gray-500 mt-1">Edit this query if needed, then use Copy SPL to send the edited version to Splunk.</p>
                </div>

                <div>
                    <label class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Results / Notes for this Follow-Up Check</label>
                    <textarea
                        :value="phase2ManualResults[getPhase2Key(q)] || ''"
                        @input="emitPhase2Manual(q, $event.target.value)"
                        rows="3"
                        class="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400 font-mono"
                        placeholder="Paste findings from this specialized follow-up query..."
                    ></textarea>
                </div>

                <div>
                    <label class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Collection Status</label>
                    <select
                        v-model="evidenceResultStatuses[getPhase2Key(q)]"
                        class="w-full mt-1 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100 focus:outline-none focus:border-blue-400"
                    >
                        <option value="success">Success (Events Found)</option>
                        <option value="no_results">No Results (0 Events)</option>
                        <option value="data_source_unavailable">Data Source Unavailable</option>
                        <option value="query_failed">Query Failed</option>
                    </select>
                </div>
                <div>
                    <label class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Coverage Note (optional)</label>
                    <input
                        type="text"
                        :value="phase2CoverageNotes[getPhase2Key(q)] || ''"
                        @input="$emit('update-phase2-coverage', {key: getPhase2Key(q), value: $event.target.value})"
                        class="w-full mt-1 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400"
                        placeholder="What time window / scope did this query cover? (The AI decides whether it resolves the inquiry.)"
                    />
                </div>
            </div>

            <div v-if="phase2Result && phase2Result.analysis" class="bg-gray-900 border border-purple-800/80 rounded-lg p-4 space-y-2">
                <p class="text-[11px] font-bold uppercase tracking-wider text-purple-300">Phase {{ followUpPhase }} AI Analysis</p>
                <div class="text-sm text-gray-200 leading-relaxed space-y-2" v-html="formatAnalysisText(phase2Result.analysis)"></div>
            </div>

            <div v-if="phase2EvidenceItems.length" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3">
                <div class="flex items-center justify-between">
                    <p class="text-xs font-bold uppercase tracking-wider text-gray-300">Saved Follow-Up Evidence ({{ phase2EvidenceItems.length }})</p>
                    <span class="text-[10px] uppercase text-emerald-300">Durable</span>
                </div>
                <div v-for="item in phase2EvidenceItems" :key="'phase2-ledger:' + evidenceItemKey(item)" class="border border-gray-800 rounded p-3 space-y-1">
                    <div class="flex items-center justify-between gap-2">
                        <p class="text-xs font-semibold text-gray-100">{{ item.title }}</p>
                        <span class="text-[10px] uppercase text-gray-400">{{ item.result_status || 'success' }}</span>
                    </div>
                    <p class="text-xs text-gray-400">Finding: <span class="capitalize" :class="item.finding_type === 'supports' ? 'text-red-300' : item.finding_type === 'refutes' ? 'text-emerald-300' : 'text-gray-300'">{{ item.finding_type || 'neutral' }}</span> <span v-if="item.ai_verdict_source === 'per_card'" class="ml-1 text-[10px] px-1 rounded bg-indigo-950 text-indigo-200 border border-indigo-800" :title="item.ai_verdict_rationale || 'Model-assessed'">AI</span></p>
                    <p v-if="item.ai_verdict_source === 'per_card' && item.ai_verdict_rationale" class="text-xs text-indigo-300 italic">AI: {{ item.ai_verdict_rationale }}</p>
                    <p class="text-xs text-gray-300 whitespace-pre-wrap">{{ item.summary || 'Saved result; no observation summary recorded.' }}</p>
                </div>
            </div>

            <div class="flex items-center justify-end gap-3 pt-2">
                <button
                    type="button"
                    class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-100 transition shadow"
                    :disabled="analysisRunning"
                    @click="$emit('save-phase2-evidence')"
                >
                    Save Phase {{ followUpPhase }} Evidence
                </button>
                <button
                    type="button"
                    class="px-5 py-2 bg-purple-600 hover:bg-purple-500 rounded text-xs font-bold text-white transition shadow flex items-center gap-2"
                    :disabled="analysisRunning"
                    @click="$emit('run-phase2-analysis')"
                >
                    <span v-if="analysisRunning" class="animate-spin">⟳</span>
                    <span>{{ analysisRunning ? 'Re-analyzing case...' : phase2EvidenceItems.length ? 'Save New Evidence & Re-Analyze' : 'Save Evidence & Re-Analyze Case' }}</span>
                </button>
            </div>
        </div>

        <div v-else class="bg-gray-900 border border-gray-700 rounded-lg p-6 text-center space-y-3">
            <div v-if="supportivePlaybookAvailable === false" class="bg-amber-950/40 border border-amber-700/70 rounded-lg p-4 text-left space-y-2">
                <p class="text-sm font-semibold text-amber-200">No supportive playbook exists for this rule yet.</p>
                <p class="text-xs text-gray-300">Generate draft SPL from this notable, review the index and fields, then explicitly save the approved queries to create the rule playbook.</p>
                <p v-if="supportiveDraftError" class="text-xs text-red-300">{{ supportiveDraftError }}</p>
                <button
                    type="button"
                    class="px-4 py-2 bg-amber-600 hover:bg-amber-500 rounded text-xs font-bold text-white transition"
                    :disabled="analysisRunning || supportiveDraftBusy || !analysisCaseId"
                    @click="$emit('generate-supportive-playbook-draft')"
                >
                    {{ supportiveDraftBusy ? 'Generating Drafts...' : 'Generate Draft Supportive Playbook' }}
                </button>
            </div>
            <p v-if="supportivePlaybookAvailable !== false" class="text-sm font-semibold text-gray-300">No Phase {{ followUpPhase }} recommendations loaded yet.</p>
            <p v-if="supportivePlaybookAvailable !== false" class="text-xs text-gray-400 max-w-md mx-auto">
                Run the initial assessment first in Stage 3, or click below to generate specialized follow-up queries grounded in this rule's detection playbook.
            </p>
            <button v-if="supportivePlaybookAvailable !== false"
                type="button"
                class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition shadow inline-flex items-center gap-2"
                :disabled="analysisRunning || !analysisCaseId"
                @click="$emit('run-phase2-analysis')"
            >
                <span v-if="analysisRunning" class="animate-spin">⟳</span>
                <span>{{ analysisRunning ? 'Running Analysis...' : 'Generate Follow-Up Recommendations' }}</span>
            </button>
        </div>

        <div class="flex justify-between pt-4 border-t border-gray-700">
            <button
                type="button"
                @click="goToStage(3)"
                class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-200 transition"
            >
                &larr; Back to Initial Assessment
            </button>
            <button
                type="button"
                @click="goToStage(5)"
                class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition shadow flex items-center gap-2"
            >
                <span>Proceed to Verdict & Closure</span>
                <span>&rarr;</span>
            </button>
        </div>
    </div>

    <!-- ============================================================= -->
    <!-- STAGE 5: FINAL VERDICT & CLOSURE GATING -->
    <!-- ============================================================= -->
    <div v-show="currentStage === 5" class="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-6">
        <div>
            <h3 class="text-lg font-bold text-blue-300">Stage 5: Final Verdict & Closure</h3>
            <p class="text-xs text-gray-400 mt-1">Review investigation loop confidence, verify active blockers, and transition to structured closure note compilation.</p>
        </div>

        <!-- Investigation Loop State Dashboard -->
        <div v-if="investigationState" class="bg-gray-900 border border-gray-700 rounded-lg p-5 space-y-4">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <span
                        class="px-3 py-1 rounded text-xs font-bold uppercase tracking-wider"
                        :class="stage5Ready ? 'bg-emerald-950 text-emerald-300 border border-emerald-700' : 'bg-amber-950 text-amber-300 border border-amber-700'"
                    >
                        {{ formatLoopStatus(investigationState.loop_status) }}
                    </span>
                    <span class="text-sm font-semibold text-white capitalize">
                        Disposition: {{ formatDispositionLabel(investigationState.provisional_disposition) }}
                    </span>
                </div>
                <div class="text-right">
                    <span class="text-xs text-gray-400 font-semibold uppercase">Confidence</span>
                    <span class="ml-2 text-xl font-bold text-emerald-400">{{ ((investigationState.disposition_confidence || 0) * 100).toFixed(0) }}%</span>
                </div>
            </div>

            <!-- Closure Gating Checklist -->
            <div class="p-4 bg-gray-800/80 rounded border border-gray-700 space-y-2">
                <p class="text-[11px] font-bold uppercase tracking-wider text-gray-400 mb-2">Closure Gating Checklist</p>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center gap-2">
                        <span :class="((investigationState.disposition_confidence || 0) >= 0.8) ? 'text-emerald-400 font-bold' : 'text-red-400'">
                            {{ ((investigationState.disposition_confidence || 0) >= 0.8) ? '✓' : '✗' }}
                        </span>
                        <span class="text-gray-200">High Confidence (&ge; 80%): {{ ((investigationState.disposition_confidence || 0) * 100).toFixed(0) }}%</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <span :class="(investigationState.evidence_summary?.substantive_items >= 2) ? 'text-emerald-400 font-bold' : 'text-red-400'">
                            {{ (investigationState.evidence_summary?.substantive_items >= 2) ? '✓' : '✗' }}
                        </span>
                        <span class="text-gray-200">Substantive Corroborating Findings (&ge; 2): {{ investigationState.evidence_summary?.substantive_items || 0 }}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <span :class="(!investigationState.closure_blockers || !investigationState.closure_blockers.length) ? 'text-emerald-400 font-bold' : 'text-red-400'">
                            {{ (!investigationState.closure_blockers || !investigationState.closure_blockers.length) ? '✓' : '✗' }}
                        </span>
                        <span class="text-gray-200">Active Investigation Blockers: {{ investigationState.closure_blockers?.length || 0 }}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <span :class="(!investigationState.unresolved_questions || !investigationState.unresolved_questions.length) ? 'text-emerald-400 font-bold' : 'text-amber-400'">
                            {{ (!investigationState.unresolved_questions || !investigationState.unresolved_questions.length) ? '✓' : '•' }}
                        </span>
                        <span class="text-gray-200">Unresolved Open Questions: {{ investigationState.unresolved_questions?.length || 0 }}</span>
                    </div>
                </div>
            </div>

            <!-- Active Blockers if any -->
            <div v-if="investigationState.closure_blockers && investigationState.closure_blockers.length" class="space-y-2">
                <p class="text-xs font-bold uppercase tracking-wider text-red-400">Current Closure Blockers</p>
                <div class="flex flex-wrap gap-2">
                    <span v-for="b in investigationState.closure_blockers" :key="'c-block:' + b" class="bg-red-950/80 border border-red-700 text-red-200 px-3 py-1 rounded text-xs">
                        {{ b }}
                    </span>
                </div>
            </div>

            <!-- Guided blocker-resolution workflow -->
            <div
                v-if="(investigationState.closure_blockers && investigationState.closure_blockers.length) || (investigationState.unresolved_questions && investigationState.unresolved_questions.length)"
                class="bg-blue-950/30 border border-blue-800/80 rounded-lg p-4 space-y-3"
            >
                <div class="flex items-start justify-between gap-4">
                    <div>
                        <p class="text-xs font-bold uppercase tracking-wider text-blue-300">Resolve Blockers Before Closure</p>
                        <p class="text-xs text-gray-300 mt-1">Use the current follow-up checks to answer the open questions, save the findings, and start the next investigation round. This screen will update when the blockers are reevaluated.</p>
                    </div>
                    <button
                        type="button"
                        class="flex-shrink-0 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded text-xs font-bold text-white transition"
                        @click="startNextFollowUpPhase()"
                    >Start Phase {{ (Number(followUpPhase || 2) + 1) }} Follow-Up &rarr;</button>
                </div>
                <div v-if="investigationState.recommended_next_actions && investigationState.recommended_next_actions.length" class="space-y-2 pt-2 border-t border-blue-900/70">
                    <p class="text-[11px] font-semibold uppercase tracking-wider text-gray-400">Recommended next actions</p>
                    <div v-for="(action, index) in investigationState.recommended_next_actions" :key="'next-action:' + index + ':' + action.title" class="flex items-start gap-2 text-xs text-gray-300">
                        <span class="w-4 h-4 rounded-full bg-blue-950 border border-blue-700 text-blue-300 flex items-center justify-center text-[10px] font-bold flex-shrink-0">{{ index + 1 }}</span>
                        <span><strong class="text-gray-100">{{ action.title }}</strong><span v-if="action.description"> — {{ action.description }}</span></span>
                    </div>
                </div>
                <div v-else-if="investigationState.unresolved_questions && investigationState.unresolved_questions.length" class="space-y-2 pt-2 border-t border-blue-900/70">
                    <p class="text-[11px] font-semibold uppercase tracking-wider text-gray-400">Open questions to resolve</p>
                    <div v-for="(question, index) in investigationState.unresolved_questions" :key="'open-question:' + index" class="flex items-start gap-2 text-xs text-gray-300">
                        <span class="w-4 h-4 rounded-full bg-blue-950 border border-blue-700 text-blue-300 flex items-center justify-center text-[10px] font-bold flex-shrink-0">{{ index + 1 }}</span>
                        <span>{{ question }}</span>
                    </div>
                </div>
            </div>

            <!-- Evidence Ledger Table Overview -->
            <div v-if="evidenceTimelineItems.length" class="space-y-2 pt-2">
                <p class="text-xs font-bold uppercase tracking-wider text-gray-400">Evidence Ledger Summary</p>
                <div class="overflow-x-auto">
                    <table class="min-w-full text-xs text-left border border-gray-700">
                        <thead class="bg-gray-800 text-gray-400 uppercase tracking-wider font-semibold text-[10px]">
                            <tr>
                                <th class="p-2 border-b border-gray-700">Query Title</th>
                                <th class="p-2 border-b border-gray-700">Source</th>
                                <th class="p-2 border-b border-gray-700">Status</th>
                                <th class="p-2 border-b border-gray-700">Direction</th>
                                <th class="p-2 border-b border-gray-700">Observation</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-800 text-gray-200 font-mono">
                            <tr v-for="item in evidenceTimelineItems" :key="'table:' + evidenceItemKey(item)">
                                <td class="p-2 font-sans font-medium text-gray-100">{{ item.title }}</td>
                                <td class="p-2 text-gray-400">{{ item.source_system }}</td>
                                <td class="p-2">
                                    <span class="px-1.5 py-0.5 rounded text-[10px] uppercase font-sans font-bold"
                                        :class="{
                                            'bg-blue-950 text-blue-200 border border-blue-800': item.result_status === 'success',
                                            'bg-purple-950 text-purple-200 border border-purple-800': item.result_status === 'no_results',
                                            'bg-red-950 text-red-200 border border-red-800': item.result_status === 'query_failed' || item.result_status === 'data_source_unavailable',
                                            'bg-emerald-950 text-emerald-200 border border-emerald-800': item.result_status === 'benign_result'
                                        }"
                                    >{{ item.result_status || 'SUCCESS' }}</span>
                                </td>
                                <td class="p-2 font-sans font-bold capitalize" :class="{
                                    'text-red-400': item.finding_type === 'supports',
                                    'text-emerald-400': item.finding_type === 'refutes',
                                    'text-gray-400': item.finding_type === 'neutral'
                                }">{{ item.finding_type }}</td>
                                <td class="p-2 truncate max-w-xs font-sans text-xs text-gray-300" :title="item.summary">{{ item.summary || 'No observation recorded' }}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div class="flex items-center justify-between pt-4 border-t border-gray-700">
            <button
                type="button"
                @click="goToStage(4)"
                class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-200 transition"
            >
                &larr; Back to Phase {{ followUpPhase }} Follow-Up
            </button>
            <button
                type="button"
                @click="$emit('go-to-closure-from-analysis', analysisCaseId)"
                class="px-6 py-3 bg-emerald-600 hover:bg-emerald-500 rounded text-xs font-bold text-white transition shadow-xl flex items-center gap-2"
            >
                <span>Move to Closure Notes for this Case</span>
                <span>&rarr;</span>
            </button>
        </div>
    </div>

    <!-- Supportive Editor Modal -->
    <div v-if="supportiveEditorOpen" class="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4">
        <div class="bg-gray-800 border border-gray-600 rounded-lg shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto p-5">
            <div class="flex items-center justify-between mb-4">
                <div>
                    <h3 class="text-lg font-bold text-white">Manage Supportive SPL</h3>
                    <p class="text-xs text-gray-400">Rule: {{ supportiveEditorRuleId }}</p>
                </div>
                <div class="flex items-center gap-3">
                    <button type="button" class="text-xs text-blue-300 hover:text-blue-200" @click="$emit('open-placeholder-alias-editor')">Manage placeholder aliases</button>
                    <button type="button" class="text-gray-400 hover:text-white text-xl" @click="$emit('close-supportive-editor')">×</button>
                </div>
            </div>
            <p v-if="supportiveEditorError" class="mb-3 text-sm text-red-300">{{ supportiveEditorError }}</p>
            <div class="mb-4 p-3 bg-blue-950/30 border border-blue-800 rounded space-y-2">
                <p class="text-xs font-bold text-blue-200">Ground drafts with Splunk results</p>
                <p class="text-[11px] text-gray-300">Upload CSV, JSON, or text containing fields such as <span class="font-mono">index</span>, <span class="font-mono">sourcetype</span>, <span class="font-mono">host</span>, and <span class="font-mono">searchtype</span>. Imported results create unsaved drafts for review.</p>
                <div class="flex flex-wrap gap-2 items-center">
                    <input type="file" accept=".csv,.json,.txt,.log,application/json,text/csv,text/plain" @change="readSupportiveImportFile" class="text-xs text-gray-300 file:mr-2 file:px-2 file:py-1 file:rounded file:border-0 file:bg-gray-700 file:text-gray-100">
                    <span v-if="supportiveImportFilename" class="text-[11px] text-gray-400">{{ supportiveImportFilename }}</span>
                </div>
                <textarea v-model="supportiveImportText" rows="3" class="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs font-mono" placeholder="index=linux sourcetype=auditd host=prod-app-07\n_or paste CSV/JSON results_"></textarea>
                <p v-if="supportiveImportError" class="text-xs text-red-300">{{ supportiveImportError }}</p>
                <button type="button" class="px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded text-xs font-semibold" :disabled="supportiveImportBusy || !supportiveImportText.trim()" @click="submitSupportiveImport">
                    {{ supportiveImportBusy ? 'Importing...' : 'Build Drafts from Splunk Results' }}
                </button>
            </div>
            <div v-for="(q, index) in supportiveEditorQueries" :key="q.localKey || q.id || index" class="border border-gray-700 rounded p-3 mb-3 space-y-2">
                <div class="flex gap-2">
                    <input v-model="q.title" class="flex-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-sm" placeholder="Query title">
                    <button type="button" class="text-xs text-red-300 hover:text-red-200 px-2" :disabled="supportiveEditorBusy" @click="$emit('remove-supportive-query', index, q)">Remove</button>
                </div>
                <input v-model="q.description" class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs" placeholder="Why this query matters">
                <textarea v-model="q.spl_query" rows="3" class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs font-mono" placeholder="index=... | ..."></textarea>
            </div>
            <div class="flex justify-between items-center mt-4">
                <button type="button" class="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs" :disabled="supportiveEditorBusy" @click="$emit('add-supportive-query')">+ Add query</button>
                <div class="flex gap-2">
                    <button type="button" class="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs" :disabled="supportiveEditorBusy" @click="$emit('close-supportive-editor')">Cancel</button>
                    <button type="button" class="px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded text-xs font-semibold" :disabled="supportiveEditorBusy" @click="$emit('save-supportive-queries')">{{ supportiveEditorBusy ? 'Saving...' : 'Save queries' }}</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Placeholder Alias Editor Modal -->
    <div v-if="placeholderAliasEditorOpen" class="fixed inset-0 z-[70] bg-black/70 flex items-center justify-center p-4">
        <div class="bg-gray-800 border border-gray-600 rounded-lg shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto p-5">
            <div class="flex items-center justify-between mb-4">
                <div>
                    <h3 class="text-lg font-bold text-white">Placeholder Aliases</h3>
                    <p class="text-xs text-gray-400">Map tokens like <code>$host$</code> to fields found in triaged notables.</p>
                </div>
                <button type="button" class="text-gray-400 hover:text-white text-xl" @click="$emit('close-placeholder-alias-editor')">×</button>
            </div>
            <p v-if="placeholderAliasEditorError" class="mb-3 text-sm text-red-300">{{ placeholderAliasEditorError }}</p>
            <div class="grid md:grid-cols-3 gap-2 mb-4">
                <input v-model="newAliasForm.alias" class="bg-gray-900 border border-gray-600 rounded px-2 py-1 text-sm" placeholder="Alias (host)">
                <input v-model="newAliasForm.fields" class="bg-gray-900 border border-gray-600 rounded px-2 py-1 text-sm" placeholder="Fields: dest, host">
                <input v-model="newAliasForm.description" class="bg-gray-900 border border-gray-600 rounded px-2 py-1 text-sm" placeholder="Description">
            </div>
            <div class="flex items-center justify-between mb-3">
                <button type="button" class="text-xs text-blue-300 hover:text-blue-200" :disabled="placeholderAliasSuggestionsBusy" @click="$emit('load-placeholder-alias-suggestions')">{{ placeholderAliasSuggestionsBusy ? 'Loading…' : 'Suggest fields from triaged notables' }}</button>
                <button type="button" class="px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded text-xs font-semibold" :disabled="placeholderAliasEditorBusy" @click="$emit('save-placeholder-alias')">{{ editingAliasId ? 'Update alias' : 'Save alias' }}</button>
            </div>
            <div v-if="placeholderAliasSuggestions && placeholderAliasSuggestions.length" class="mb-4 p-3 bg-gray-900 rounded border border-gray-700">
                <p class="text-xs text-gray-400 mb-2">Suggested fields from current notable data:</p>
                <div class="flex flex-wrap gap-2">
                    <button v-for="candidate in placeholderAliasSuggestions" :key="candidate.field || candidate" type="button" class="text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600" @click="$emit('toggle-alias-field-candidate', candidate.field || candidate)">{{ candidate.field || candidate }}</button>
                </div>
            </div>
            <div class="space-y-2">
                <div v-for="alias in placeholderAliasList" :key="alias.id" class="flex items-center justify-between border border-gray-700 rounded p-3">
                    <div>
                        <code class="text-blue-300 text-sm">&dollar;{{ alias.alias }}&dollar;</code>
                        <span class="text-xs text-gray-400 ml-2">{{ (alias.fields || []).join(', ') }}</span>
                        <p v-if="alias.description" class="text-xs text-gray-500">{{ alias.description }}</p>
                    </div>
                    <div class="flex gap-2">
                        <button type="button" class="text-xs text-blue-300" @click="$emit('edit-placeholder-alias', alias)">Edit</button>
                        <button type="button" class="text-xs text-red-300" @click="$emit('delete-placeholder-alias', alias.id)">Delete</button>
                    </div>
                </div>
                <p v-if="!placeholderAliasList || !placeholderAliasList.length" class="text-xs text-gray-500">No aliases saved yet.</p>
            </div>
        </div>
    </div>
</div>
    `
};
