/**
 * components/SplunkBoundaryWidget.js
 * Status widget for the Splunk boundary ("the latch").
 *
 * Shows the current boundary mode, recent admitted batches, and offers
 * per-batch purge (double-confirmed). Purge is destructive — it removes the
 * batch's staged file and its ingested DB rows — so it sits behind the
 * API-key gate (the endpoint 403s/401s without the key when configured).
 *
 * Load order: after axios/utils, alongside other components.
 */
window.SplunkBoundaryWidget = {
    data() {
        return {
            status: null,
            loadError: '',
            purgingBatchId: '',
            confirmBatchId: '',
            purgeResult: ''
        };
    },
    computed: {
        mode() {
            return this.status ? this.status.mode : 'unknown';
        },
        modeBadgeClass() {
            return {
                'quarantined': 'bg-red-900 text-red-200 border-red-700',
                'restricted': 'bg-yellow-900 text-yellow-200 border-yellow-700',
                'open': 'bg-green-900 text-green-200 border-green-700',
                'unknown': 'bg-gray-700 text-gray-300 border-gray-600'
            }[this.mode] || 'bg-gray-700 text-gray-300 border-gray-600';
        },
        batches() {
            return this.status ? this.status.batches || [] : [];
        }
    },
    template: `
        <section class="bg-gray-900 border border-gray-700 rounded-lg mb-6" data-testid="splunk-boundary">
            <div class="flex items-center justify-between px-4 py-3 border-b border-gray-700">
                <div class="flex items-center space-x-3">
                    <h2 class="text-sm font-semibold text-gray-200">Splunk Boundary</h2>
                    <span
                        class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide border"
                        :class="modeBadgeClass"
                    >{{ mode }}</span>
                </div>
                <button
                    @click="load"
                    class="text-xs text-gray-400 hover:text-blue-400 transition"
                >refresh</button>
            </div>

            <div v-if="loadError" class="px-4 py-3 text-xs text-red-400">
                {{ loadError }}
            </div>

            <div v-else-if="status" class="px-4 py-3">
                <p class="text-xs text-gray-400 mb-3">
                    {{ mode === 'quarantined'
                        ? 'Latch is shut: HTTP admission disabled; host-local tooling only.'
                        : mode === 'restricted'
                            ? 'Latch open behind auth: HTTP admission allowed with API key.'
                            : 'Latch open: validation warnings only (experimental mode).' }}
                    {{ status.batch_count }} batch(es) in staging.
                </p>

                <div v-if="batches.length === 0" class="text-xs text-gray-500 italic">
                    No admitted batches.
                </div>

                <table v-else class="w-full text-xs text-left">
                    <thead>
                        <tr class="text-gray-500 border-b border-gray-800">
                            <th class="py-1 pr-3 font-medium">Batch</th>
                            <th class="py-1 pr-3 font-medium">File</th>
                            <th class="py-1 pr-3 font-medium">Staged</th>
                            <th class="py-1 pr-3 font-medium">Rows</th>
                            <th class="py-1 font-medium text-right">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="b in batches" :key="b.batch_id" class="border-b border-gray-800/50">
                            <td class="py-1.5 pr-3 font-mono text-gray-300">{{ b.batch_id }}</td>
                            <td class="py-1.5 pr-3 text-gray-400">{{ b.filename || '—' }}</td>
                            <td class="py-1.5 pr-3 text-gray-500">{{ shortTime(b.staged_at) }}</td>
                            <td class="py-1.5 pr-3 text-gray-300">{{ b.ingested_rows ?? '—' }}</td>
                            <td class="py-1.5 text-right">
                                <button
                                    v-if="confirmBatchId !== b.batch_id"
                                    @click="askPurge(b.batch_id)"
                                    class="text-red-400 hover:text-red-300 transition"
                                >purge</button>
                                <span v-else class="inline-flex items-center space-x-2">
                                    <span class="text-red-300">Delete {{ b.ingested_rows ?? '?' }} rows?</span>
                                    <button @click="doPurge(b.batch_id)" class="px-2 py-0.5 rounded bg-red-600 hover:bg-red-500 text-white font-semibold">Yes</button>
                                    <button @click="cancelPurge" class="px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-200">No</button>
                                </span>
                            </td>
                        </tr>
                    </tbody>
                </table>

                <p v-if="purgeResult" class="mt-2 text-xs" :class="purgeResult.ok ? 'text-green-400' : 'text-red-400'">
                    {{ purgeResult.text }}
                </p>
            </div>

            <div v-else class="px-4 py-3 text-xs text-gray-500">Loading…</div>
        </section>
    `,
    methods: {
        async load() {
            this.loadError = '';
            try {
                const res = await axios.get('/api/splunk-boundary/status');
                this.status = res.data;
            } catch (err) {
                this.loadError = 'Boundary status unavailable: ' + (err.response?.status
                    ? 'HTTP ' + err.response.status
                    : 'server unreachable');
            }
        },
        askPurge(batchId) {
            this.confirmBatchId = batchId;
            this.purgeResult = '';
        },
        cancelPurge() {
            this.confirmBatchId = '';
        },
        async doPurge(batchId) {
            this.purgingBatchId = batchId;
            try {
                const res = await axios.delete('/api/splunk-boundary/batches/' + encodeURIComponent(batchId));
                const deleted = res.data && res.data.events_deleted;
                this.purgeResult = {
                    ok: true,
                    text: 'Purged ' + batchId + (typeof deleted === 'number' ? ' — ' + deleted + ' DB row(s) removed' : '')
                };
            } catch (err) {
                const detail = err.response && err.response.data && err.response.data.detail;
                this.purgeResult = {
                    ok: false,
                    text: 'Purge failed for ' + batchId + (detail ? ': ' + detail : ' — check API key / permissions')
                };
            } finally {
                this.purgingBatchId = '';
                this.confirmBatchId = '';
                this.load();
            }
        },
        shortTime(iso) {
            if (!iso) return '—';
            const d = new Date(iso);
            return isNaN(d) ? iso : d.toLocaleString();
        }
    },
    mounted() {
        this.load();
    }
};
