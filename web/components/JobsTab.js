window.JobsTab = {
    props: ['jobs'],
    template: `
        <div class="space-y-6">
            <div>
                <h2 class="text-3xl font-bold mb-2">Job History</h2>
                <p class="text-gray-400">Track tool executions and view results</p>
            </div>

            <div
                v-if="!jobs || jobs.length === 0"
                class="bg-gray-800 border border-gray-700 rounded-lg p-8 text-center text-gray-400"
            >
                No jobs yet. Execute a tool to see results here.
            </div>

            <div
                v-for="job in jobs"
                :key="job.job_id"
                class="bg-gray-800 border border-gray-700 rounded-lg p-5"
            >
                <div class="flex items-center justify-between mb-3">
                    <div>
                        <h3 class="text-lg font-bold">{{ job.tool_name }}</h3>
                        <p class="text-xs text-gray-400">ID: {{ (job.job_id || '').slice(0, 8) }}...</p>
                    </div>

                    <span
                        :class="{
                            'bg-yellow-900 text-yellow-200': job.status === 'pending' || job.status === 'running',
                            'bg-green-900 text-green-200': job.status === 'completed',
                            'bg-red-900 text-red-200': job.status === 'failed'
                        }"
                        class="px-3 py-1 rounded text-sm font-semibold"
                    >
                        {{ (job.status || 'unknown').toUpperCase() }}
                    </span>
                </div>

                <div class="text-xs text-gray-400 space-y-1 mb-3">
                    <p>Created: {{ job.created_at ? new Date(job.created_at).toLocaleString() : 'n/a' }}</p>
                    <p v-if="job.completed_at">Completed: {{ new Date(job.completed_at).toLocaleString() }}</p>
                </div>

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
    `
};
