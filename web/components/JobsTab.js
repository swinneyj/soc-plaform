window.JobsTab = {
    props: ['jobs', 'selectedJobIds'],
    data() {
        return { searchTerm: '', statusFilter: 'all', toolFilter: 'all', page: 1, pageSize: 10, expandedJobIds: [], collapsedGroups: [] };
    },
    computed: {
        filteredJobs() {
            const term = (this.searchTerm || '').trim().toLowerCase();
            return (this.jobs || []).filter(job => {
                const matchesStatus = this.statusFilter === 'all' || job.status === this.statusFilter;
                const matchesTool = this.toolFilter === 'all' || job.tool_name === this.toolFilter;
                const haystack = [job.tool_name, job.job_id, job.stdout, job.stderr]
                    .filter(Boolean).join(' ').toLowerCase();
                return matchesStatus && matchesTool && (!term || haystack.includes(term));
            });
        },
        toolNames() {
            return Array.from(new Set((this.jobs || []).map(job => job.tool_name).filter(Boolean))).sort();
        },
        jobGroups() {
            const groups = new Map();
            for (const job of this.filteredJobs) {
                const key = job.tool_name || 'Unknown tool';
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(job);
            }
            return Array.from(groups.entries()).map(([toolName, jobs]) => ({ toolName, jobs }));
        },
        pageCount() { return Math.max(1, Math.ceil(this.jobGroups.length / this.pageSize)); },
        pagedGroups() {
            const page = Math.min(this.page, this.pageCount);
            const start = (page - 1) * this.pageSize;
            return this.jobGroups.slice(start, start + this.pageSize);
        },
        pagedJobCount() { return this.pagedGroups.reduce((total, group) => total + group.jobs.length, 0); },
        completedCount() { return (this.jobs || []).filter(job => job.status === 'completed').length; },
        failedCount() { return (this.jobs || []).filter(job => job.status === 'failed').length; },
        activeCount() { return (this.jobs || []).filter(job => job.status === 'pending' || job.status === 'running').length; }
    },
    watch: {
        jobs() { if (this.page > this.pageCount) this.page = this.pageCount; },
        collapsedGroups(value) {
            try { localStorage.setItem('soc-platform.jobs.collapsedGroups', JSON.stringify(value)); } catch (err) { /* storage may be unavailable */ }
        }
    },
    mounted() {
        try {
            const saved = JSON.parse(localStorage.getItem('soc-platform.jobs.collapsedGroups') || '[]');
            if (Array.isArray(saved)) this.collapsedGroups = saved;
        } catch (err) {
            this.collapsedGroups = [];
        }
    },
    template: `
        <div class="space-y-6">
            <div class="flex items-start justify-between gap-4">
                <div>
                    <h2 class="text-3xl font-bold mb-2">Job History</h2>
                    <p class="text-gray-400">Track tool executions and view results</p>
                </div>
                <div class="flex gap-2">
                    <button v-if="filteredJobs.length" @click="$emit('update:selected-job-ids', filteredJobs.map(job => job.job_id))" class="bg-blue-700 hover:bg-blue-600 px-3 py-2 rounded text-sm font-semibold">Select All</button>
                    <button v-if="selectedJobIds && selectedJobIds.length" @click="$emit('update:selected-job-ids', [])" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded text-sm font-semibold">Clear Selection</button>
                    <button v-if="selectedJobIds && selectedJobIds.length" @click="$emit('delete-selected-jobs')" class="bg-red-700 hover:bg-red-600 px-3 py-2 rounded text-sm font-semibold">Delete Selected ({{ selectedJobIds.length }})</button>
                    <button v-if="jobs && jobs.length" @click="$emit('clear-jobs')" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded text-sm font-semibold">Clear All</button>
                </div>
            </div>

            <div class="bg-gray-800 border border-gray-700 rounded-lg p-4 flex flex-wrap items-center gap-3">
                <input v-model="searchTerm" @input="page = 1" placeholder="Search jobs, IDs, or output..." class="bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm flex-1 min-w-[220px]">
                <select v-model="statusFilter" @change="page = 1" class="bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm">
                    <option value="all">All statuses ({{ jobs ? jobs.length : 0 }})</option>
                    <option value="completed">Completed ({{ completedCount }})</option>
                    <option value="failed">Failed / interrupted ({{ failedCount }})</option>
                    <option value="running">Running ({{ activeCount }})</option>
                    <option value="pending">Pending</option>
                </select>
                <select v-model="toolFilter" @change="page = 1" class="bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm">
                    <option value="all">All tools</option>
                    <option v-for="toolName in toolNames" :key="toolName" :value="toolName">{{ toolName }}</option>
                </select>
                <span class="text-xs text-gray-400">{{ jobGroups.length }} tool group{{ jobGroups.length === 1 ? '' : 's' }} · {{ filteredJobs.length }} run{{ filteredJobs.length === 1 ? '' : 's' }}</span>
            </div>

            <div
                v-if="!jobs || jobs.length === 0"
                class="bg-gray-800 border border-gray-700 rounded-lg p-8 text-center text-gray-400"
            >
                No jobs yet. Execute a tool to see results here.
            </div>

            <div
                v-for="group in pagedGroups"
                :key="group.toolName"
                class="bg-gray-800 border border-gray-700 rounded-lg p-4"
            >
                <div class="flex items-center justify-between gap-3 mb-3">
                    <div>
                        <h3 class="text-lg font-bold">{{ group.toolName }}</h3>
                        <p class="text-xs text-gray-400">{{ group.jobs.length }} run{{ group.jobs.length === 1 ? '' : 's' }}</p>
                    </div>
                    <button @click="collapsedGroups = collapsedGroups.includes(group.toolName) ? collapsedGroups.filter(name => name !== group.toolName) : [...collapsedGroups, group.toolName]" class="text-sm text-blue-300 hover:text-blue-200">
                        {{ collapsedGroups.includes(group.toolName) ? 'Show runs' : 'Hide runs' }}
                    </button>
                </div>
                <div v-if="!collapsedGroups.includes(group.toolName)" class="space-y-3">
            <div
                v-for="job in group.jobs"
                :key="job.job_id"
                class="bg-gray-900 border border-gray-700 rounded-lg p-4"
            >
                <div class="flex items-center justify-between mb-3 gap-3">
                    <div class="flex items-start gap-3">
                        <input type="checkbox" class="mt-1 h-4 w-4" :checked="(selectedJobIds || []).includes(job.job_id)" @change="$emit('update:selected-job-ids', $event.target.checked ? [...(selectedJobIds || []), job.job_id] : (selectedJobIds || []).filter(id => id !== job.job_id))">
                        <div>
                        <h3 class="text-lg font-bold">{{ job.tool_name }}</h3>
                        <p class="text-xs text-gray-400">ID: {{ (job.job_id || '').slice(0, 8) }}...</p>
                        </div>
                    </div>

                    <div class="flex items-center gap-2">
                        <span
                            :class="{
                            'bg-yellow-900 text-yellow-200': job.status === 'pending' || job.status === 'running',
                            'bg-green-900 text-green-200': job.status === 'completed',
                            'bg-red-900 text-red-200': job.status === 'failed'
                            }"
                            class="px-3 py-1 rounded text-sm font-semibold"
                        >{{ (job.status || 'unknown').toUpperCase() }}</span>
                        <button @click="$emit('delete-job', job.job_id)" class="text-xs text-red-300 hover:text-red-200 border border-red-800 px-2 py-1 rounded">Delete</button>
                    </div>
                </div>

                <div class="text-xs text-gray-400 space-y-1 mb-3">
                    <p>Created: {{ job.created_at ? new Date(job.created_at).toLocaleString() : 'n/a' }}</p>
                    <p v-if="job.completed_at">Completed: {{ new Date(job.completed_at).toLocaleString() }}</p>
                </div>

                <button @click="expandedJobIds = expandedJobIds.includes(job.job_id) ? expandedJobIds.filter(id => id !== job.job_id) : [...expandedJobIds, job.job_id]" class="text-xs text-blue-300 hover:text-blue-200 mb-3">
                    {{ expandedJobIds.includes(job.job_id) ? 'Hide details' : 'Show details' }}
                </button>

                <div v-if="expandedJobIds.includes(job.job_id)">
                <div
                    v-if="job.stdout"
                    class="bg-gray-900 rounded p-3 text-xs font-mono overflow-x-auto mb-3 max-h-40 overflow-y-auto"
                >
                    <pre>{{ job.stdout }}</pre>
                </div>

                <div
                    v-if="job.stderr"
                    class="bg-red-900 bg-opacity-20 border border-red-700 rounded p-3 text-xs font-mono overflow-x-auto text-red-200 mb-3"
                >
                    <pre>{{ job.stderr }}</pre>
                </div>

                <div v-if="job.arguments && Object.keys(job.arguments).length" class="mb-3">
                    <p class="text-xs text-gray-400 mb-1">Inputs</p>
                    <pre class="bg-gray-900 rounded p-3 text-xs font-mono overflow-x-auto">{{ JSON.stringify(job.arguments, null, 2) }}</pre>
                </div>

                <div v-if="job.artifacts && job.artifacts.length" class="mb-3">
                    <p class="text-xs text-gray-400 mb-1">Generated artifacts</p>
                    <div class="space-y-1">
                        <a
                            v-for="artifact in job.artifacts"
                            :key="artifact"
                            :href="'/api/tool-artifacts/' + artifact"
                            target="_blank"
                            class="block text-xs text-blue-400 hover:text-blue-300 underline"
                        >
                            {{ artifact }}
                        </a>
                    </div>
                </div>

                <div class="text-xs text-gray-400">
                    Exit Code:
                    <span
                        :class="job.exit_code === 0 ? 'text-green-400' : 'text-red-400'"
                        class="font-bold"
                    >
                        {{ job.exit_code }}
                    </span>
                </div>
                </div>
            </div>
                </div>

            <div v-if="jobGroups.length" class="flex items-center justify-between">
                <button @click="page = Math.max(1, page - 1)" :disabled="page <= 1" class="bg-gray-700 disabled:opacity-40 px-3 py-2 rounded text-sm">Previous</button>
                <span class="text-sm text-gray-400">Page {{ page }} of {{ pageCount }}</span>
                <button @click="page = Math.min(pageCount, page + 1)" :disabled="page >= pageCount" class="bg-gray-700 disabled:opacity-40 px-3 py-2 rounded text-sm">Next</button>
            </div>
        </div>
    `
};
