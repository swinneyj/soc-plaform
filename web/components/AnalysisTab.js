/**
 * components/AnalysisTab.js
 * Presentational AI Analysis tab.
 * All business logic stays in the root app (or modules/analysis.js later).
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
        'analysisResult',
        'analysisSourceNotable',
        'analysisRule',
        'showPhase1Analysis',
        'phase2Model',
        'phase2Result',
        'investigationState',
        'evidenceFindingOptions',
        'supportiveManualResults',
        'supportiveFindingTypes',
        'enrichmentManualResults',
        'enrichmentFindingTypes',
        'phase2ManualResults',
        'phase2FindingTypes',
        'phase2EditedQueries',
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
        // pure helpers passed from parent (or component methods below fall back to window utils)
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
        'delete-evidence',
        'delete-evidence-batch',
        'delete-all-evidence'
    ],
    data() {
        return {
            // Stable selection keys (id when known, otherwise title|source|created_at).
            // Must not depend solely on item.id — older timeline rows have no id and
            // would leave every checkbox permanently disabled.
            selectedEvidenceKeys: []
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
        }
    },
    methods: {
        // Prefer parent-provided helpers; fall back to window utils if present
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
            // Fallback for timeline rows that predate id enrichment
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
            // Pass full items so the parent can resolve DB ids when timeline lacks them
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
        <p class="text-gray-400">Run your local Ollama models on security cases</p>
    </div>

    <div v-if="!ollamaHealth.available" class="bg-red-900 bg-opacity-30 border border-red-700 rounded-lg p-6 text-red-200">
        <p class="font-bold">⚠️ Ollama Service Not Available</p>
        <p class="text-sm mt-2">Start Ollama with: <code class="bg-black px-2 py-1 rounded">ollama serve</code></p>
    </div>

    <div v-else class="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-4">
        <div>
            <label class="text-sm text-gray-400">Case to Analyze</label>
            <input
                :value="analysisCaseSearch" @input="$emit('update:analysis-case-search', $event.target.value)"
                type="text"
                placeholder="Filter cases by ID, rule, verdict, or summary"
                class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-400 focus:outline-none focus:border-blue-500">
            <select 
                :value="analysisCaseId" @input="$emit('update:analysis-case-id', $event.target.value)"
                @change="$emit('on-analysis-case-changed')"
                class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500">
                <option value="">-- Choose a case from the database --</option>
                <option v-for="case_ in filteredAnalysisCases" :key="case_.case_id" :value="case_.case_id">
                    {{ case_.case_id }} - {{ case_.rule_name }} ({{ case_.verdict }})
                </option>
            </select>
            <p class="mt-2 text-xs text-gray-400">
                Showing {{ filteredAnalysisCases.length }} of {{ analysisCases.length }} cases for AI analysis.
            </p>
        </div>

        <div>
            <label class="text-sm text-gray-400">Model</label>
            <select :value="analysisModel" @input="$emit('update:analysis-model', $event.target.value)" class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500">
                <option v-for="model in ollamaHealth.models" :key="model" :value="model">
                    {{ model }}
                </option>
            </select>
        </div>

        <div>
            <label class="text-sm text-gray-400">Additional Context (Optional)</label>
            <textarea 
                :value="analysisContext" @input="$emit('update:analysis-context', $event.target.value)" 
                placeholder="Add context for the analysis (freeform notes, summaries, etc.)" 
                class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-400 focus:outline-none focus:border-blue-500 h-24"></textarea>
        </div>

        <div v-if="analysisSourceNotable && analysisSourceNotable.parse_assessment" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3">
            <div class="flex items-center justify-between gap-3">
                <div>
                    <p class="text-sm font-semibold text-blue-300">Parse Quality</p>
                    <p class="text-xs text-gray-400">How complete the pasted notable is before full AI analysis.</p>
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

            <div v-if="analysisSourceNotable.parse_assessment.missing_anchors && analysisSourceNotable.parse_assessment.missing_anchors.length">
                <p class="text-[11px] font-semibold text-gray-300 mb-1">Missing anchors</p>
                <div class="flex flex-wrap gap-2">
                    <span v-for="item in analysisSourceNotable.parse_assessment.missing_anchors" :key="item" class="bg-amber-950 border border-amber-700 text-amber-200 px-2 py-1 rounded text-[11px]">{{ item }}</span>
                </div>
            </div>

            <div v-if="analysisSourceNotable.parse_assessment.mode !== 'normal'" class="bg-blue-950 border border-blue-800 rounded p-3 space-y-3">
                <p class="text-xs text-blue-100">This case needs one or two broad data points before the model can reason confidently. Run one of these generic queries, paste the result, then rerun analysis.</p>
                <div v-if="analysisSourceNotable.parse_assessment.generic_queries && analysisSourceNotable.parse_assessment.generic_queries.length" class="space-y-3">
                    <div v-for="q in analysisSourceNotable.parse_assessment.generic_queries" :key="getEnrichmentKey(q)" class="bg-gray-900 border border-gray-700 rounded p-3 space-y-2">
                        <div class="flex items-start justify-between gap-2">
                            <div>
                                <p class="text-xs font-semibold text-blue-300">{{ q.title }}</p>
                                <p class="text-xs text-gray-400">{{ q.description }}</p>
                            </div>
                            <button type="button" class="px-2 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200" @click="$emit('copy-enrichment-spl', q)">Copy SPL</button>
                        </div>
                        <pre class="text-xs text-gray-200 bg-black bg-opacity-40 rounded p-2 whitespace-pre-wrap">{{ q.spl }}</pre>
                        <div>
                            <label class="text-[11px] text-gray-400">Results / notes for this enrichment query (optional)</label>
                            <textarea
                                :value="enrichmentManualResults[getEnrichmentKey(q)] || ''" @input="emitEnrichmentManual(q, $event.target.value)"
                                rows="3"
                                class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400"
                                placeholder="Paste a short summary or key rows from this generic query..."
                            ></textarea>
                        </div>
                        <div>
                            <label class="text-[11px] text-gray-400">Finding</label>
                            <select
                                :value="enrichmentFindingTypes[getEnrichmentKey(q)] || ''" @change="emitEnrichmentFinding(q, $event.target.value)"
                                class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 focus:outline-none focus:border-blue-400"
                            >
                                <option v-for="option in evidenceFindingOptions" :key="'enrich:' + option" :value="option">{{ option }}</option>
                            </select>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div v-if="investigationState" class="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-4 mt-4">
            <div class="flex items-start justify-between gap-3">
                <div>
                    <h3 class="text-sm font-semibold text-blue-300">Investigation Loop State</h3>
                    <p class="text-[11px] text-gray-400">Track what the case currently suggests, what is still blocking closure, and whether the next loop needs more evidence.</p>
                </div>
                <div class="text-right text-[11px] text-gray-400">
                    <p>Iteration {{ investigationState.iteration_count || 0 }}</p>
                    <p v-if="investigationState.updated_at">Updated {{ new Date(investigationState.updated_at).toLocaleString() }}</p>
                </div>
            </div>

            <div class="flex flex-wrap gap-2 text-xs">
                <span class="px-2 py-1 rounded border border-blue-500 text-blue-200 bg-blue-950">
                    Status: {{ formatLoopStatus(investigationState.loop_status) }}
                </span>
                <span class="px-2 py-1 rounded border border-amber-500 text-amber-200 bg-amber-950">
                    Disposition: {{ formatDispositionLabel(investigationState.provisional_disposition) }}
                </span>
                <span class="px-2 py-1 rounded border border-emerald-500 text-emerald-200 bg-emerald-950">
                    Confidence: {{ ((investigationState.disposition_confidence || 0) * 100).toFixed(0) }}%
                </span>
            </div>

            <div>
                <p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Current Hypothesis</p>
                <p class="text-sm text-gray-200 whitespace-pre-wrap">{{ investigationState.current_hypothesis || 'No persisted hypothesis yet.' }}</p>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                <div class="bg-gray-800 border border-gray-700 rounded p-3">
                    <p class="text-gray-400">Substantive Evidence</p>
                    <p class="text-xl font-bold text-white">{{ investigationState.evidence_summary?.substantive_items ?? investigationState.evidence_summary?.total_items ?? 0 }}</p>
                </div>
                <div class="bg-gray-800 border border-gray-700 rounded p-3">
                    <p class="text-gray-400">Supports / Refutes</p>
                    <p class="text-xl font-bold text-white">{{ investigationState.evidence_summary?.by_finding?.supports || 0 }} / {{ investigationState.evidence_summary?.by_finding?.refutes || 0 }}</p>
                </div>
                <div class="bg-gray-800 border border-gray-700 rounded p-3">
                    <p class="text-gray-400">Pending / Neutral</p>
                    <p class="text-xl font-bold text-white">{{ investigationState.evidence_summary?.pending_items || 0 }} / {{ investigationState.evidence_summary?.by_finding?.neutral || 0 }}</p>
                </div>
            </div>

            <div v-if="investigationState.closure_blockers && investigationState.closure_blockers.length">
                <p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Closure Blockers</p>
                <div class="flex flex-wrap gap-2">
                    <span v-for="item in investigationState.closure_blockers" :key="'blocker:' + item" class="bg-red-950 border border-red-700 text-red-200 px-2 py-1 rounded text-[11px]">{{ item }}</span>
                </div>
            </div>

            <div v-if="investigationState.unresolved_questions && investigationState.unresolved_questions.length">
                <p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Open Questions</p>
                <ul class="list-disc list-inside text-sm text-gray-300 space-y-1">
                    <li v-for="item in investigationState.unresolved_questions" :key="'question:' + item">{{ item }}</li>
                </ul>
            </div>

            <div v-if="investigationState.recommended_next_actions && investigationState.recommended_next_actions.length">
                <p class="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Next Best Actions</p>
                <ul class="list-disc list-inside text-sm text-gray-300 space-y-1">
                    <li v-for="item in investigationState.recommended_next_actions" :key="'action:' + item.title + item.description">
                        <span class="font-semibold text-blue-300">{{ item.title }}</span>
                        <span v-if="item.description">: {{ item.description }}</span>
                    </li>
                </ul>
            </div>

            <div v-if="evidenceTimelineItems.length">
                <details class="mt-2" open>
                    <summary class="text-[11px] uppercase tracking-wide text-gray-500 mb-1 cursor-pointer flex items-center justify-between gap-2">
                        <span>Evidence Timeline</span>
                        <span class="flex items-center gap-2 flex-shrink-0" @click.stop>
                            <button
                                v-if="someEvidenceSelected"
                                type="button"
                                class="text-[10px] px-2 py-0.5 bg-red-900 hover:bg-red-800 border border-red-700 rounded text-red-100"
                                title="Delete selected evidence items"
                                @click="emitDeleteSelectedEvidence"
                            >
                                Delete selected ({{ selectedEvidenceCount }})
                            </button>
                            <button
                                type="button"
                                class="text-[10px] px-2 py-0.5 bg-red-950 hover:bg-red-900 border border-red-800 rounded text-red-200"
                                title="Delete all evidence for this case"
                                @click="emitDeleteAllEvidence"
                            >
                                Delete all
                            </button>
                            <span class="text-[10px] text-gray-400">{{ evidenceTimelineItems.length }} item(s)</span>
                        </span>
                    </summary>
                    <div class="flex items-center gap-2 mt-2 mb-1 px-1">
                        <input
                            type="checkbox"
                            class="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-0 cursor-pointer"
                            :checked="allEvidenceSelected"
                            :indeterminate.prop="someEvidenceSelected && !allEvidenceSelected"
                            @change="toggleSelectAllEvidence($event.target.checked)"
                            title="Select all visible evidence"
                        />
                        <span class="text-[11px] text-gray-500">Select all</span>
                    </div>
                    <div class="space-y-2 mt-1">
                        <div
                            v-for="item in evidenceTimelineItems"
                            :key="'timeline:' + evidenceItemKey(item)"
                            class="bg-gray-800 border border-gray-700 rounded p-3"
                            :class="{ 'border-blue-600': isEvidenceSelected(item) }"
                        >
                            <div class="flex items-start gap-3">
                                <input
                                    type="checkbox"
                                    class="mt-0.5 rounded border-gray-600 bg-gray-900 text-blue-500 focus:ring-blue-500 focus:ring-offset-0 flex-shrink-0 cursor-pointer"
                                    :checked="isEvidenceSelected(item)"
                                    @click.stop
                                    @change="toggleEvidenceSelection(item, $event.target.checked)"
                                    title="Select this evidence item"
                                />
                                <div class="min-w-0 flex-1">
                                    <div class="flex items-start justify-between gap-3">
                                        <div class="min-w-0">
                                            <p class="text-xs font-semibold text-gray-100">{{ item.title }}</p>
                                            <p class="text-[11px] text-gray-400">{{ item.source_system }} • {{ formatFindingLabel(item.finding_type) }}</p>
                                        </div>
                                        <div class="flex items-start gap-2 flex-shrink-0">
                                            <div class="text-right text-[11px] text-gray-500">
                                                <p>{{ item.has_substantive_observation ? 'Observed' : 'Pending' }}</p>
                                                <p v-if="item.created_at">{{ new Date(item.created_at).toLocaleString() }}</p>
                                            </div>
                                            <button
                                                type="button"
                                                class="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-white hover:bg-red-700 border border-gray-600 hover:border-red-500 text-sm font-bold leading-none flex-shrink-0"
                                                title="Delete this evidence item"
                                                @click.stop="$emit('delete-evidence', item)"
                                            >
                                                ×
                                            </button>
                                        </div>
                                    </div>
                                    <p v-if="item.summary" class="text-xs text-gray-300 mt-2 whitespace-pre-wrap">{{ item.summary }}</p>
                                    <p v-else class="text-xs text-amber-300 mt-2">No substantive analyst observation saved yet.</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </details>
            </div>
        </div>

        <div v-if="analysisRule && showPhase1Analysis" class="mt-2">
            <div class="flex items-center justify-between mb-1">
                <p class="text-xs text-gray-400 font-semibold">Supportive SPL queries for this case's rule</p>
                <div class="flex items-center gap-2">
                    <button
                        type="button"
                        class="text-[11px] px-2 py-0.5 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-gray-200"
                        @click="$emit('save-supportive-evidence')"
                    >
                        Save Evidence
                    </button>
                    <button
                        type="button"
                        class="text-[11px] px-2 py-0.5 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-gray-200"
                        @click="$emit('open-supportive-editor', analysisRule)"
                    >
                        Manage
                    </button>
                </div>
            </div>
            <div v-if="analysisRule.supportive_queries && analysisRule.supportive_queries.length" class="space-y-3">
                <div
                    v-for="q in analysisRule.supportive_queries"
                    :key="q.id"
                    class="bg-gray-900 border border-gray-700 rounded p-3 space-y-2"
                >
                    <div class="flex items-start justify-between gap-2">
                        <div>
                            <p class="text-xs font-semibold text-blue-300 mb-0.5">{{ q.title }}</p>
                            <p class="text-xs text-gray-400" v-if="q.description">{{ q.description }}</p>
                        </div>
                        <button
                            type="button"
                            class="ml-2 px-2 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200 flex-shrink-0"
                            @click="$emit('copy-supportive-spl', q)"
                        >
                            Copy SPL
                        </button>
                    </div>
                    <pre class="text-xs text-gray-200 bg-black bg-opacity-40 rounded p-2 whitespace-pre-wrap">{{ renderSupportiveQuery(q) }}</pre>
                    <p v-if="hasUnresolvedPlaceholders(renderSupportiveQuery(q))" class="text-[11px] text-amber-300">This query still has unresolved placeholders. Gather enrichment data or populate more case fields before copying it into Splunk.</p>
                    <div class="mt-1">
                        <label class="text-[11px] text-gray-400">Results / notes for this query (optional)</label>
                        <textarea
                            :value="supportiveManualResults[getSupportiveKey(q)] || ''" @input="emitSupportiveManual(q, $event.target.value)"
                            rows="3"
                            class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400"
                            placeholder="Paste key rows or a short summary from this query's results..."
                        ></textarea>
                    </div>
                    <div class="mt-1">
                        <label class="text-[11px] text-gray-400">Finding</label>
                        <select
                            :value="supportiveFindingTypes[getSupportiveKey(q)] || ''" @change="emitSupportiveFinding(q, $event.target.value)"
                            class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 focus:outline-none focus:border-blue-400"
                        >
                            <option v-for="option in evidenceFindingOptions" :key="'supportive:' + option" :value="option">{{ option }}</option>
                        </select>
                    </div>
                </div>
                <p class="mt-1 text-[11px] text-gray-500">
                    Run these queries in Splunk, paste important results into the fields above, and the platform will include them alongside any additional context when sending the case to the model.
                </p>
            </div>
            <p v-else class="text-[11px] text-amber-300 mt-1">
                No supportive queries defined yet for this rule. Use **Manage** to add some.
            </p>
        </div>

        <button 
            @click="$emit('run-analysis')"
            :disabled="analysisRunning"
            class="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded text-sm font-semibold transition">
            {{ analysisRunning ? 'Running Analysis...' : 'Run Analysis' }}
        </button>

        <div class="mt-2 flex items-center justify-end gap-2">
            <button
                type="button"
                class="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-100"
                @click="$emit('save-analysis-state')"
            >
                Save Analysis
            </button>
            <button
                type="button"
                class="px-3 py-1 bg-red-700 hover:bg-red-800 rounded text-xs font-semibold text-red-100 disabled:bg-gray-700 disabled:text-gray-400"
                @click="$emit('cancel-analysis')"
                :disabled="!analysisRunning"
            >
                Cancel Analysis
            </button>
        </div>
    </div>

    <div v-if="analysisResult" class="bg-gray-800 border border-gray-700 rounded-lg p-6">
        <div class="flex items-center justify-between mb-4">
            <h3 class="text-xl font-bold text-blue-400">Analysis Result (Phase 1)</h3>
            <div class="flex items-center gap-2">
                <button
                    type="button"
                    class="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-100"
                    @click="$emit('copy-analysis-result')"
                >
                    Copy Analysis
                </button>
                <button
                    type="button"
                    class="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-xs font-semibold text-gray-200"
                    @click="$emit('update:show-phase1-analysis', false)"
                >
                    Hide Phase 1
                </button>
            </div>
        </div>
        <div v-if="showPhase1Analysis">
            <p class="text-xs text-gray-400 mb-2" v-if="analysisResult.baseline_notables_count || analysisResult.investigation_evidence_count || analysisResult.supportive_results_count">
                Included
                <span class="font-semibold">{{ analysisResult.baseline_notables_count || 0 }}</span>
                closed baseline notables and
                <span class="font-semibold">{{ analysisResult.investigation_evidence_count || analysisResult.supportive_results_count || 0 }}</span>
                saved evidence item{{ ((analysisResult.investigation_evidence_count || analysisResult.supportive_results_count || 0) === 1) ? '' : 's' }} in this analysis.
            </p>
            <div class="bg-gray-900 rounded p-4 mb-4 max-h-96 overflow-y-auto">
                <p class="text-gray-300 whitespace-pre-wrap">{{ analysisResult.analysis }}</p>
            </div>
        </div>
        <div v-else class="text-xs text-gray-400 mb-2 flex items-center justify-between">
            <span>Phase 1 body is hidden to keep the view compact.</span>
            <button
                type="button"
                class="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-[11px] font-semibold text-gray-100"
                @click="$emit('update:show-phase1-analysis', true)"
            >
                Show Phase 1
            </button>
        </div>
        <div class="text-xs text-gray-400 space-y-1">
            <p>Model: {{ analysisResult.model }}</p>
            <p>Case: {{ analysisResult.case_id }}</p>
        </div>
        <div class="mt-4">
            <button
                @click="$emit('go-to-closure-from-analysis')"
                class="px-3 py-2 bg-green-600 hover:bg-green-700 rounded text-xs font-semibold transition">
                Move to Closure Notes for this Case
            </button>
        </div>
    </div>

    <div
        v-if="analysisResult && analysisResult.phase2_queries && analysisResult.phase2_queries.length"
        class="bg-gray-800 border border-gray-700 rounded-lg p-6 mt-4"
    >
        <details>
            <summary class="flex items-center justify-between mb-3 cursor-pointer">
                <div>
                    <h3 class="text-sm font-semibold text-blue-300">Phase 2 Evidence & Follow-up</h3>
                    <p class="text-[11px] text-gray-400">
                        Run these queries in Splunk, paste results, and save them as durable evidence before Phase 2 analysis.
                    </p>
                </div>
                <div class="flex items-center gap-2">
                    <div class="flex items-center gap-1 text-[11px] text-gray-400">
                        <span>Phase 2 Model:</span>
                        <select
                            :value="phase2Model" @input="$emit('update:phase2-model', $event.target.value)"
                            class="px-2 py-1 bg-gray-800 border border-gray-600 rounded text-[11px] text-gray-100 focus:outline-none focus:border-blue-400"
                        >
                            <option v-for="model in ollamaHealth.models" :key="model" :value="model">
                                {{ model }}
                            </option>
                        </select>
                    </div>
                    <button
                        type="button"
                        class="text-[11px] px-2 py-0.5 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-gray-200"
                        @click="$emit('open-placeholder-alias-editor')"
                    >
                        Manage Placeholder Aliases
                    </button>
                </div>
            </summary>

            <div
            v-for="q in analysisResult.phase2_queries"
            :key="getPhase2Key(q)"
            class="bg-gray-900 border border-gray-700 rounded p-3 space-y-2 mb-2"
        >
            <div class="flex items-start justify-between gap-2">
                <div>
                    <p class="text-xs font-semibold text-blue-300 mb-0.5">{{ q.title }}</p>
                    <p class="text-xs text-gray-400" v-if="q.description">{{ q.description }}</p>
                </div>
                <div class="flex gap-1 ml-2 flex-shrink-0">
                    <button
                        type="button"
                        class="px-2 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded text-[10px] text-gray-200"
                        @click="$emit('copy-phase2-spl', q)"
                    >
                        Copy SPL
                    </button>
                    <button
                        type="button"
                        class="px-2 py-1 bg-gray-800 hover:bg-gray-700 border border-purple-500 rounded text-[10px] text-purple-200"
                        @click="$emit('promote-phase2-query', q)"
                    >
                        Save as Supportive
                    </button>
                </div>
            </div>
            <pre class="text-xs text-gray-200 bg-black bg-opacity-40 rounded p-2 whitespace-pre-wrap mt-1">
                {{ renderPhase2Query(getPhase2Template(q)) }}
            </pre>
            <p
                v-if="hasUnresolvedPlaceholders(renderPhase2Query(getPhase2Template(q)))"
                class="text-[11px] text-amber-300"
            >
                This suggested Phase 2 query still has unresolved placeholders. Use enrichment data first or edit the SPL before copying it into Splunk.
            </p>
            <div class="mt-1">
                <label class="text-[11px] text-gray-400">SPL Query (editable)</label>
                <textarea
                    :value="getPhase2Template(q)"
                    @input="$emit('on-phase2-template-input', q, $event.target.value, $event.target.value)"
                    rows="4"
                    class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400 font-mono"
                    placeholder="Edit the suggested SPL here before running or saving..."
                ></textarea>
            </div>
            <div class="mt-1">
                <label class="text-[11px] text-gray-400">Results / notes for this query</label>
                <textarea
                    :value="phase2ManualResults[getPhase2Key(q)] || ''" @input="emitPhase2Manual(q, $event.target.value)"
                    rows="3"
                    class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-400"
                    placeholder="Paste key rows or a short summary from this query's results. This will be saved as investigation evidence for the case."
                ></textarea>
            </div>
            <div class="mt-1">
                <label class="text-[11px] text-gray-400">Finding</label>
                <select
                    :value="phase2FindingTypes[getPhase2Key(q)] || ''" @change="emitPhase2Finding(q, $event.target.value)"
                    class="w-full mt-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-[11px] text-gray-100 focus:outline-none focus:border-blue-400"
                >
                    <option v-for="option in evidenceFindingOptions" :key="'phase2:' + option" :value="option">{{ option }}</option>
                </select>
            </div>
            </div>
            <div class="mt-3 flex items-center justify-end gap-2">
                <button
                    type="button"
                    class="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-100"
                    :disabled="analysisRunning"
                    @click="$emit('save-phase2-evidence')"
                >
                    Save Phase 2 Evidence
                </button>
                <button
                    type="button"
                    class="px-3 py-2 bg-purple-600 hover:bg-purple-700 rounded text-xs font-semibold text-white"
                    :disabled="analysisRunning"
                    @click="$emit('run-phase2-analysis')"
                >
                    {{ analysisRunning ? 'Saving evidence and re-analyzing...' : 'Save evidence & re-analyze' }}
                </button>
            </div>
        </details>
    </div>

    <div v-if="phase2Result" class="bg-gray-800 border border-purple-700 rounded-lg p-6 mt-6">
        <div class="flex items-center justify-between mb-4">
            <h3 class="text-xl font-bold text-purple-300">Phase 2 Analysis Result</h3>
        </div>
        <p class="text-xs text-gray-400 mb-2" v-if="phase2Result.baseline_notables_count || phase2Result.supportive_results_count">
            Included
            <span class="font-semibold">{{ phase2Result.baseline_notables_count || 0 }}</span>
            closed baseline notables and
            <span class="font-semibold">{{ phase2Result.supportive_results_count || 0 }}</span>
            supportive query result{{ (phase2Result.supportive_results_count || 0) === 1 ? '' : 's' }} in this analysis.
        </p>
        <div class="bg-gray-900 rounded p-4 mb-4 max-h-96 overflow-y-auto">
            <p class="text-gray-300 whitespace-pre-wrap">{{ phase2Result.analysis }}</p>
        </div>
        <div class="text-xs text-gray-400 space-y-1">
            <p>Model: {{ phase2Result.model }}</p>
            <p>Case: {{ phase2Result.case_id }}</p>
        </div>
    </div>

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
                <div class="flex flex-wrap gap-2"><button v-for="candidate in placeholderAliasSuggestions" :key="candidate.field || candidate" type="button" class="text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600" @click="$emit('toggle-alias-field-candidate', candidate.field || candidate)">{{ candidate.field || candidate }}</button></div>
            </div>
            <div class="space-y-2">
                <div v-for="alias in placeholderAliasList" :key="alias.id" class="flex items-center justify-between border border-gray-700 rounded p-3">
                    <div><code class="text-blue-300 text-sm">&dollar;{{ alias.alias }}&dollar;</code><span class="text-xs text-gray-400 ml-2">{{ (alias.fields || []).join(', ') }}</span><p v-if="alias.description" class="text-xs text-gray-500">{{ alias.description }}</p></div>
                    <div class="flex gap-2"><button type="button" class="text-xs text-blue-300" @click="$emit('edit-placeholder-alias', alias)">Edit</button><button type="button" class="text-xs text-red-300" @click="$emit('delete-placeholder-alias', alias.id)">Delete</button></div>
                </div>
                <p v-if="!placeholderAliasList || !placeholderAliasList.length" class="text-xs text-gray-500">No aliases saved yet.</p>
            </div>
        </div>
    </div>
</div>
        </div>
    `
};
