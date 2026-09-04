window.DatabaseTab = {
    props: [
        'dbStats',
        'notablePasteText',
        'notablePasteSegmentEstimate',
        'notableRedactionEnabled',
        'notableHistorical',
        'notablePasteSaving',
        'notablePasteResult',
        'showOpenNotablesOnly',
        'filteredRecentNotables',
        'historicalNotablesVisible',
        'historicalNotables',
        'filteredTriageData',
        'dbSearch',
        'dbVerdictFilter',
        'triageFromPastedOnly',
        'deleteAnalysisWithCase',
        'selectedNotableIds',
        'selectedTriageCaseIds',
        'triageNotableDetails'
    ],
    emits: [
        'change-tab',
        'update:notable-paste-text',
        'update:notable-redaction-enabled',
        'update:notable-historical',
        'save-pasted-notable',
        'load-historical-notables',
        'update:historical-notables-visible',
        'load-recent-notables',
        'delete-pasted-notable',
        'delete-selected-pasted-notables',
        'promote-notable-to-triage',
        'promote-all-open-pasted-notables',
        'update:selected-notable-ids',
        'update:show-open-notables-only',
        'update:db-search',
        'update:db-verdict-filter',
        'update:triage-from-pasted-only',
        'update:selected-triage-case-ids',
        'update:delete-analysis-with-case',
        'load-triage-data',
        'delete-triage-case',
        'delete-selected-triage-cases',
        'load-triage-notable-details',
        'copy-triage-notable-fields',
        'analyze-case'
    ],
    computed: {
        allPastedSelected() {
            const items = this.filteredRecentNotables || [];
            if (!items.length) return false;
            const ids = this.selectedNotableIds || [];
            return items.every(n => ids.includes(n.id));
        },
        allTriageSelected() {
            const items = this.filteredTriageData || [];
            if (!items.length) return false;
            const ids = this.selectedTriageCaseIds || [];
            return items.every(c => ids.includes(c.case_id));
        }
    },
    methods: {
        toggleNotableSelection(id, checked) {
            const current = Array.isArray(this.selectedNotableIds) ? this.selectedNotableIds.slice() : [];
            const idx = current.indexOf(id);
            if (checked && idx === -1) {
                current.push(id);
            } else if (!checked && idx !== -1) {
                current.splice(idx, 1);
            }
            this.$emit('update:selected-notable-ids', current);
        },
        toggleTriageSelection(id, checked) {
            const current = Array.isArray(this.selectedTriageCaseIds) ? this.selectedTriageCaseIds.slice() : [];
            const idx = current.indexOf(id);
            if (checked && idx === -1) {
                current.push(id);
            } else if (!checked && idx !== -1) {
                current.splice(idx, 1);
            }
            this.$emit('update:selected-triage-case-ids', current);
        },
        toggleSelectAllPasted(event) {
            const checked = event.target.checked;
            if (!checked) {
                this.$emit('update:selected-notable-ids', []);
                return;
            }
            const ids = (this.filteredRecentNotables || []).map(n => n.id);
            this.$emit('update:selected-notable-ids', ids);
        },
        toggleSelectAllTriage(event) {
            const checked = event.target.checked;
            if (!checked) {
                this.$emit('update:selected-triage-case-ids', []);
                return;
            }
            const ids = (this.filteredTriageData || []).map(c => c.case_id);
            this.$emit('update:selected-triage-case-ids', ids);
        }
    },
    template: `
        <div class="space-y-6">
            <div>
                <h2 class="text-3xl font-bold mb-2">Triage Database</h2>
                <p class="text-gray-400">Browse and query cases from the active database backend</p>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
                <button @click="$emit('change-tab', 'database')" class="text-left bg-gray-800 border border-gray-700 rounded-lg p-4 hover:border-blue-500 transition">
                    <p class="text-sm font-semibold text-blue-300">1. Paste a Live Notable</p>
                    <p class="text-xs text-gray-400 mt-1">Start by saving Incident Review text into the platform.</p>
                </button>
                <button @click="$emit('change-tab', 'analysis')" class="text-left bg-gray-800 border border-gray-700 rounded-lg p-4 hover:border-blue-500 transition">
                    <p class="text-sm font-semibold text-blue-300">2. Analyze a Case</p>
                    <p class="text-xs text-gray-400 mt-1">Run local AI, review follow-up queries, and gather more evidence.</p>
                </button>
                <button @click="$emit('change-tab', 'closure')" class="text-left bg-gray-800 border border-gray-700 rounded-lg p-4 hover:border-blue-500 transition">
                    <p class="text-sm font-semibold text-blue-300">3. Draft Closure Notes</p>
                    <p class="text-xs text-gray-400 mt-1">Turn a triaged case into an operator-ready closure note.</p>
                </button>
                <button @click="$emit('change-tab', 'tools')" class="text-left bg-gray-800 border border-gray-700 rounded-lg p-4 hover:border-blue-500 transition">
                    <p class="text-sm font-semibold text-blue-300">Browse Utility Tools</p>
                    <p class="text-xs text-gray-400 mt-1">Use the wider tool catalog for one-off parsing, intel, and reporting work.</p>
                </button>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4">
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
                    <p class="text-gray-400 text-sm">Total Cases</p>
                    <p class="text-3xl font-bold text-blue-400">{{ dbStats.triage_cases }}</p>
                </div>
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
                    <p class="text-gray-400 text-sm">Malicious</p>
                    <p class="text-3xl font-bold text-red-400">{{ dbStats.verdict_breakdown?.malicious || 0 }}</p>
                </div>
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
                    <p class="text-gray-400 text-sm">Suspicious</p>
                    <p class="text-3xl font-bold text-yellow-400">{{ dbStats.verdict_breakdown?.suspicious || 0 }}</p>
                </div>
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
                    <p class="text-gray-400 text-sm">Benign</p>
                    <p class="text-3xl font-bold text-green-400">{{ dbStats.verdict_breakdown?.benign || 0 }}</p>
                </div>
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
                    <p class="text-gray-400 text-sm">Closed Notables</p>
                    <p class="text-3xl font-bold text-amber-300">{{ dbStats.pasted_notables_historical || 0 }}</p>
                </div>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-4">
                    <div>
                        <h3 class="text-xl font-bold text-blue-400">Paste Splunk Notable</h3>
                        <p class="text-sm text-gray-400">Paste Incident Review notable text here. The platform will sanitize it with the existing tokenizer and store the sanitized notable in the database.</p>
                    </div>
                    <textarea
                        :value="notablePasteText"
                        @input="$emit('update:notable-paste-text', $event.target.value)"
                        placeholder="Paste the notable block here..."
                        class="w-full h-56 px-4 py-3 bg-gray-900 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"></textarea>
                    <p
                        v-if="notablePasteSegmentEstimate > 1"
                        class="text-xs text-amber-300 mt-1"
                    >
                        Detected {{ notablePasteSegmentEstimate }} notables in this paste; each will be saved separately.
                    </p>
                    <label class="flex items-center text-xs text-gray-300 space-x-2">
                        <input
                            type="checkbox"
                            :checked="notableRedactionEnabled"
                            @change="$emit('update:notable-redaction-enabled', $event.target.checked)"
                            class="form-checkbox h-3 w-3 text-blue-500 bg-gray-900 border-gray-600 rounded" />
                        <span>Apply tokenizer redaction when saving</span>
                    </label>
                    <label class="flex items-center text-xs text-gray-300 space-x-2">
                        <input
                            type="checkbox"
                            :checked="notableHistorical"
                            @change="$emit('update:notable-historical', $event.target.checked)"
                            class="form-checkbox h-3 w-3 text-amber-500 bg-gray-900 border-gray-600 rounded" />
                        <span>Mark as closed historical notable</span>
                    </label>
                    <div class="flex items-center justify-between">
                        <p v-if="notablePasteResult" class="text-xs text-gray-400">
                            <span v-if="(notablePasteResult.segment_count || 1) === 1">
                                Saved event {{ notablePasteResult.event_id }} with {{ notablePasteResult.mapping_entries }} token mappings.
                            </span>
                            <span v-else>
                                Saved {{ notablePasteResult.segment_count }} notables (first event {{ notablePasteResult.event_id }}) with {{ notablePasteResult.mapping_entries }} total token mappings.
                            </span>
                        </p>
                        <p v-else class="text-xs text-gray-500">Redaction stays local-first: sanitized text goes to the DB, mapping stays in Active_Workspace.</p>
                        <button
                            @click="$emit('save-pasted-notable')"
                            :disabled="notablePasteSaving || !(notablePasteText || '').trim()"
                            class="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-600 rounded text-sm font-semibold transition">
                            {{ notablePasteSaving ? 'Saving...' : 'Save Pasted Notable' }}
                        </button>
                    </div>
                </div>

                <div class="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-4">
                    <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-2 md:gap-4">
                        <div class="space-y-1">
                            <h3 class="text-xl font-bold text-blue-400">Recent Pasted Notables</h3>
                            <p class="text-xs text-gray-400 max-w-xl">
                                Sanitized notable records saved from copy/paste workflow. Owner and contact fields are surfaced here when present.
                            </p>
                        </div>
                        <div class="flex flex-wrap items-center gap-2">
                            <label class="flex items-center text-[11px] text-gray-300 space-x-1">
                                <input
                                    type="checkbox"
                                    :checked="allPastedSelected"
                                    @change="toggleSelectAllPasted($event)"
                                    class="form-checkbox h-3 w-3 text-blue-500 bg-gray-900 border-gray-600 rounded" />
                                <span class="whitespace-nowrap">Select all</span>
                            </label>
                            <label class="flex items-center text-[11px] text-gray-300 space-x-1">
                                <input
                                    type="checkbox"
                                    :checked="showOpenNotablesOnly"
                                    @change="$emit('update:show-open-notables-only', $event.target.checked)"
                                    class="form-checkbox h-3 w-3 text-blue-500 bg-gray-900 border-gray-600 rounded" />
                                <span class="whitespace-nowrap">Show open pasted notables only</span>
                            </label>
                            <button @click="$emit('load-recent-notables')" class="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition">Refresh</button>
                            <button
                                @click="$emit('delete-selected-pasted-notables')"
                                :disabled="!selectedNotableIds || !selectedNotableIds.length"
                                class="px-3 py-2 bg-red-700 hover:bg-red-800 disabled:bg-gray-700 rounded text-xs font-semibold transition">
                                Delete selected
                            </button>
                            <button
                                @click="$emit('promote-all-open-pasted-notables')"
                                class="px-3 py-2 bg-purple-700 hover:bg-purple-800 rounded text-xs font-semibold transition">
                                Promote all open to triage
                            </button>
                        </div>
                    </div>

                    <div v-if="!filteredRecentNotables || filteredRecentNotables.length === 0" class="text-gray-400 text-sm text-center py-8">
                        No pasted notables saved yet.
                    </div>

                    <div v-for="notable in filteredRecentNotables" :key="notable.id" class="bg-gray-900 border border-gray-700 rounded p-4 space-y-2">
                        <div class="flex items-start justify-between gap-4">
                            <div class="flex items-start gap-2">
                                <div class="pt-1">
                                    <input
                                        type="checkbox"
                                        class="form-checkbox h-3 w-3 text-blue-500 bg-gray-900 border-gray-600 rounded"
                                        :checked="selectedNotableIds && selectedNotableIds.includes(notable.id)"
                                        @change="toggleNotableSelection(notable.id, $event.target.checked)" />
                                </div>
                                <div>
                                    <h4 class="font-bold text-blue-300">{{ notable.title || 'Untitled notable' }}</h4>
                                    <p class="text-xs text-gray-400">{{ notable.correlation_search || 'No correlation search parsed' }}</p>
                                </div>
                            </div>
                            <div class="text-right text-xs text-gray-400">
                                <button
                                    @click="$emit('delete-pasted-notable', notable)"
                                    class="text-gray-500 hover:text-red-400 mb-1 ml-2"
                                    title="Remove this pasted notable from the recent list">
                                    ✕
                                </button>
                                <p>{{ notable.urgency || 'n/a' }} urgency</p>
                                <p>{{ notable.status || 'n/a' }} status</p>
                            </div>
                        </div>
                        <p v-if="notable.promoted_case_id" class="text-xs text-green-400">Promoted to triage as {{ notable.promoted_case_id }}</p>
                        <div class="flex flex-wrap gap-2 text-xs text-gray-300">
                            <span v-if="notable.historical" class="bg-gray-800 px-2 py-1 rounded border border-amber-500 text-amber-300">Historical (closed)</span>
                            <span v-if="notable.disposition" class="bg-gray-800 px-2 py-1 rounded">Disposition: {{ notable.disposition }}</span>
                            <span v-if="notable.host" class="bg-gray-800 px-2 py-1 rounded">Host: {{ notable.host }}</span>
                            <span v-if="notable.destination" class="bg-gray-800 px-2 py-1 rounded">Destination: {{ notable.destination }}</span>
                            <span v-if="notable.username || notable.user" class="bg-gray-800 px-2 py-1 rounded">User: {{ notable.username || notable.user }}</span>
                        </div>
                        <p class="text-xs text-gray-500">Saved: {{ new Date(notable.saved_at || notable.time).toLocaleString() }}</p>
                        <div class="flex items-center justify-between gap-3">
                            <button
                                v-if="notable.historical"
                                class="px-3 py-2 bg-gray-700 rounded text-xs font-semibold text-gray-300 cursor-default"
                                disabled
                            >
                                {{ notable.promoted_case_id ? 'Already in Triage' : 'Saved to Database' }}
                            </button>
                            <button
                                v-else
                                @click="$emit('promote-notable-to-triage', notable)"
                                class="px-3 py-2 bg-purple-600 hover:bg-purple-700 rounded text-xs font-semibold transition">
                                {{ notable.promoted_case_id ? 'Already in Triage' : 'Promote to Triage' }}
                            </button>
                            <button
                                v-if="notable.promoted_case_id"
                                @click="$emit('analyze-case', { case_id: notable.promoted_case_id })"
                                class="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition">
                                Open in Triage
                            </button>
                        </div>
                        <details class="text-xs text-gray-300">
                            <summary class="cursor-pointer text-blue-400">Show details</summary>
                            <div class="mt-2 space-y-2">
                                <div v-if="notable.fields">
                                    <p class="text-gray-400 font-semibold mb-1">Parsed fields</p>
                                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
                                        <div v-for="(value, key) in notable.fields" :key="key" class="flex flex-col">
                                            <span class="text-gray-500 mr-1">{{ key }}:</span>
                                            <span class="text-gray-200 break-words">{{ value }}</span>
                                        </div>
                                    </div>
                                </div>
                                <div>
                                    <p class="text-gray-400 font-semibold mb-1">Sanitized text</p>
                                    <pre class="mt-1 whitespace-pre-wrap bg-black bg-opacity-40 rounded p-3 max-h-40 overflow-y-auto">{{ notable.sanitized_text }}</pre>
                                </div>
                                <div v-if="notable.history">
                                    <p class="text-gray-400 font-semibold mb-1">History / closure notes</p>
                                    <pre class="mt-1 whitespace-pre-wrap bg-black bg-opacity-40 rounded p-3 max-h-40 overflow-y-auto">{{ notable.history }}</pre>
                                </div>
                            </div>
                        </details>
                    </div>
                </div>
            </div>

            <div class="flex items-center justify-between mt-6">
                <div>
                    <h3 class="text-sm font-semibold text-gray-300">Closed Notables Summary</h3>
                    <p class="text-xs text-gray-500">High-level view of closed historical notables for reference.</p>
                </div>
                <button
                    @click="$emit('update:historical-notables-visible', !historicalNotablesVisible); if (!historicalNotablesVisible) { $emit('load-historical-notables'); }"
                    class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold transition">
                    {{ historicalNotablesVisible ? 'Hide closed notables' : 'Show closed notables' }}
                </button>
            </div>

            <div v-if="historicalNotablesVisible" class="bg-gray-800 border border-gray-700 rounded-lg p-4 mt-3">
                <div class="flex items-center justify-between mb-3">
                    <div>
                        <h3 class="text-lg font-bold text-blue-400">Closed Notables</h3>
                        <p class="text-xs text-gray-400">Compact summary of closed notables for quick baseline reference.</p>
                    </div>
                    <button @click="$emit('load-historical-notables')" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition">Refresh</button>
                </div>
                <div v-if="!historicalNotables || !historicalNotables.length" class="text-gray-500 text-xs">No historical pasted notables loaded yet.</div>
                <div v-else class="overflow-x-auto">
                    <table class="min-w-full text-xs text-gray-200">
                        <thead>
                            <tr class="border-b border-gray-700 text-gray-400">
                                <th class="px-2 py-1 text-left">ID</th>
                                <th class="px-2 py-1 text-left">Title / Rule</th>
                                <th class="px-2 py-1 text-left">Host</th>
                                <th class="px-2 py-1 text-left">User</th>
                                <th class="px-2 py-1 text-left">Urgency</th>
                                <th class="px-2 py-1 text-left">Disposition</th>
                                <th class="px-2 py-1 text-left">Saved</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="item in historicalNotables" :key="item.id" class="border-b border-gray-800">
                                <td class="px-2 py-1 align-top">{{ item.id }}</td>
                                <td class="px-2 py-1 align-top">
                                    <div class="font-semibold text-blue-300">{{ item.title || 'Untitled notable' }}</div>
                                    <div v-if="item.correlation_search" class="text-[11px] text-gray-400">{{ item.correlation_search }}</div>
                                </td>
                                <td class="px-2 py-1 align-top">{{ item.host || 'n/a' }}</td>
                                <td class="px-2 py-1 align-top">{{ item.user || 'n/a' }}</td>
                                <td class="px-2 py-1 align-top">{{ item.urgency || 'n/a' }}</td>
                                <td class="px-2 py-1 align-top">{{ item.disposition || 'n/a' }}</td>
                                <td class="px-2 py-1 align-top">{{ item.saved_at ? new Date(item.saved_at).toLocaleString() : 'n/a' }}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="bg-gray-800 border border-gray-700 rounded-lg p-4">
                <div class="flex space-x-2 mb-4">
                    <input
                        :value="dbSearch"
                        @input="$emit('update:db-search', $event.target.value)"
                        type="text"
                        placeholder="Search case ID or rule..."
                        class="flex-1 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-400 focus:outline-none focus:border-blue-500">
                    <select
                        :value="dbVerdictFilter"
                        @change="$emit('update:db-verdict-filter', $event.target.value)"
                        class="px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500">
                        <option value="">All Verdicts</option>
                        <option value="benign">Benign</option>
                        <option value="suspicious">Suspicious</option>
                        <option value="malicious">Malicious</option>
                    </select>
                    <label class="flex items-center text-xs text-gray-300 space-x-1">
                        <input
                            type="checkbox"
                            :checked="allTriageSelected"
                            @change="toggleSelectAllTriage($event)"
                            class="form-checkbox h-3 w-3 text-blue-500 bg-gray-800 border-gray-600 rounded" />
                        <span>Select all</span>
                    </label>
                    <label class="flex items-center text-xs text-gray-300 space-x-1">
                        <input
                            type="checkbox"
                            :checked="triageFromPastedOnly"
                            @change="$emit('update:triage-from-pasted-only', $event.target.checked)"
                            class="form-checkbox h-3 w-3 text-blue-500 bg-gray-800 border-gray-600 rounded" />
                        <span>From pasted notables only</span>
                    </label>
                    <label class="flex items-center text-xs text-gray-300 space-x-1">
                        <input
                            type="checkbox"
                            :checked="deleteAnalysisWithCase"
                            @change="$emit('update:delete-analysis-with-case', $event.target.checked)"
                            class="form-checkbox h-3 w-3 text-blue-500 bg-gray-800 border-gray-600 rounded" />
                        <span>Delete AI analysis with cases</span>
                    </label>
                    <button @click="$emit('load-triage-data')" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-semibold transition">Refresh</button>
                    <button
                        @click="$emit('delete-selected-triage-cases')"
                        :disabled="!selectedTriageCaseIds || !selectedTriageCaseIds.length"
                        class="px-4 py-2 bg-red-700 hover:bg-red-800 disabled:bg-gray-700 rounded text-sm font-semibold transition">
                        Delete selected
                    </button>
                </div>

                <div v-if="!filteredTriageData || filteredTriageData.length === 0" class="text-gray-400 text-center py-8">
                    No triage cases found. Run analysis or ingest Splunk data.
                </div>

                <div v-for="case_ in filteredTriageData" :key="case_.case_id" class="bg-gray-900 border border-gray-700 rounded p-4 mb-3">
                    <div class="flex items-start justify-between mb-2">
                        <div class="flex items-start gap-2">
                            <div class="pt-1">
                                <input
                                    type="checkbox"
                                    class="form-checkbox h-3 w-3 text-blue-500 bg-gray-900 border-gray-600 rounded"
                                    :checked="selectedTriageCaseIds && selectedTriageCaseIds.includes(case_.case_id)"
                                    @change="toggleTriageSelection(case_.case_id, $event.target.checked)" />
                            </div>
                            <div>
                                <h4 class="font-bold text-blue-400">{{ case_.rule_name || case_.case_id }}</h4>
                                <p class="text-xs text-gray-400">Case ID: {{ case_.case_id }}</p>
                            </div>
                        </div>
                        <div class="flex flex-col items-end space-y-1">
                            <span :class="{
                                'bg-red-900 text-red-200': case_.verdict === 'malicious',
                                'bg-yellow-900 text-yellow-200': case_.verdict === 'suspicious',
                                'bg-green-900 text-green-200': case_.verdict === 'benign'
                            }" class="px-3 py-1 rounded text-xs font-semibold">
                                {{ case_.verdict.toUpperCase() }}
                            </span>
                            <span
                                v-if="triageNotableDetails && triageNotableDetails[case_.case_id]?.data"
                                class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-blue-200 border border-blue-500"
                            >
                                From pasted notable
                            </span>
                        </div>
                    </div>
                    <p class="text-sm text-gray-300 mb-2">{{ case_.analysis_summary }}</p>
                    <div class="text-xs text-gray-400 flex space-x-4">
                        <span>Confidence: {{ (case_.confidence_score * 100).toFixed(1) }}%</span>
                        <span>Triaged: {{ new Date(case_.triaged_at).toLocaleString() }}</span>
                    </div>
                    <div class="mt-2 flex space-x-2">
                        <button
                            @click="$emit('analyze-case', case_)"
                            class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition">
                            Analyze with AI
                        </button>
                        <button
                            @click="$emit('delete-triage-case', case_)"
                            class="px-3 py-1 bg-red-700 hover:bg-red-800 rounded text-xs font-semibold transition">
                            Delete case
                        </button>
                    </div>
                    <details class="mt-2 text-xs text-gray-300">
                        <summary
                            class="cursor-pointer text-blue-400"
                            @click.stop="$emit('load-triage-notable-details', case_)"
                        >
                            Show source notable details
                        </summary>
                        <div class="mt-2">
                            <p v-if="triageNotableDetails && triageNotableDetails[case_.case_id]?.loading">Loading...</p>
                            <p v-else-if="triageNotableDetails && triageNotableDetails[case_.case_id]?.error" class="text-red-400">
                                {{ triageNotableDetails[case_.case_id].error }}
                            </p>
                            <div
                                v-else-if="triageNotableDetails && triageNotableDetails[case_.case_id]?.data"
                                class="space-y-2"
                            >
                                <div class="flex items-center justify-between">
                                    <div v-if="triageNotableDetails[case_.case_id].data.historical" class="text-amber-300">
                                        Historical (closed pasted notable)
                                    </div>
                                    <button
                                        @click="$emit('copy-triage-notable-fields', case_)"
                                        class="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-[10px] text-gray-200 border border-gray-600"
                                    >
                                        Copy fields
                                    </button>
                                </div>
                                <div v-if="triageNotableDetails[case_.case_id].data.fields">
                                    <p class="text-gray-400 font-semibold mb-1">Parsed fields</p>
                                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
                                        <div
                                            v-for="(value, key) in triageNotableDetails[case_.case_id].data.fields"
                                            :key="key"
                                            class="flex flex-col"
                                        >
                                            <span class="text-gray-500 mr-1">{{ key }}:</span>
                                            <span class="text-gray-200 break-words">{{ value }}</span>
                                        </div>
                                    </div>
                                </div>
                                <div>
                                    <p class="text-gray-400 font-semibold mb-1">Sanitized text</p>
                                    <pre class="mt-1 whitespace-pre-wrap bg-black bg-opacity-40 rounded p-3 max-h-40 overflow-y-auto">
{{ triageNotableDetails[case_.case_id].data.sanitized_text }}
                                    </pre>
                                </div>
                                <div v-if="triageNotableDetails[case_.case_id].data.history">
                                    <p class="text-gray-400 font-semibold mb-1">History / closure notes</p>
                                    <pre class="mt-1 whitespace-pre-wrap bg-black bg-opacity-40 rounded p-3 max-h-40 overflow-y-auto">
{{ triageNotableDetails[case_.case_id].data.history }}
                                    </pre>
                                </div>
                            </div>
                        </div>
                    </details>
                </div>
            </div>
        </div>
    `
};
