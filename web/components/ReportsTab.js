window.ReportsTab = {
    props: ['reports'],
    template: `
        <div class="space-y-6">
            <div>
                <h2 class="text-3xl font-bold mb-2">Generated Reports</h2>
                <p class="text-gray-400">Download briefings and analysis results</p>
            </div>

            <div
                v-if="!reports || reports.length === 0"
                class="bg-gray-800 border border-gray-700 rounded-lg p-8 text-center text-gray-400"
            >
                No reports generated yet.
            </div>

            <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div
                    v-for="report in reports"
                    :key="report.filename"
                    class="bg-gray-800 border border-gray-700 rounded-lg p-5"
                >
                    <h3 class="text-lg font-bold text-blue-400">{{ report.filename }}</h3>

                    <p class="text-xs text-gray-400 mt-2">
                        {{ ((report.size || 0) / 1024).toFixed(2) }} KB •
                        {{ report.modified ? new Date(report.modified).toLocaleString() : 'n/a' }}
                    </p>

                    <a
                        :href="'/api/reports/' + report.filename"
                        download
                        :filename="report.filename"
                        class="mt-4 inline-block bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-sm font-semibold transition"
                    >
                        Download
                    </a>
                </div>
            </div>
        </div>
    `
};
