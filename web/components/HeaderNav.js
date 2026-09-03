window.HeaderNav = {
    props: ['currentTab', 'ollamaHealth', 'apiHealthy'],
    emits: ['change-tab'],
    template: `
        <header class="bg-gray-800 border-b border-gray-700 sticky top-0 z-50">
            <div class="max-w-7xl mx-auto px-4 py-4">
                <div class="flex items-center justify-between mb-4">
                    <div class="flex items-center space-x-3">
                        <div class="w-10 h-10 bg-blue-600 rounded flex items-center justify-center font-bold">S</div>
                        <div>
                            <div class="flex items-center space-x-2">
                                <h1 class="text-2xl font-bold">SOC Platform</h1>
                                <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-yellow-500 text-gray-900 tracking-wide">BETA</span>
                            </div>
                            <p class="text-xs text-gray-400">Orchestration & Analysis with Local AI</p>
                        </div>
                    </div>

                    <div class="flex items-center space-x-4">
                        <span class="text-sm text-gray-400">
                            API:
                            <span
                                id="status"
                                :class="apiHealthy ? 'text-green-400' : 'text-red-400'"
                                class="font-semibold"
                            >
                                ●
                            </span>
                        </span>
                        <span class="text-sm text-gray-400">
                            Ollama:
                            <span :class="ollamaHealth.available ? 'text-green-400' : 'text-red-400'" class="font-semibold">●</span>
                        </span>
                    </div>
                </div>

                <div class="flex space-x-4 overflow-x-auto">
                    <button @click="$emit('change-tab', 'tools')" :class="tabClass('tools')" class="px-3 py-2 text-sm hover:text-blue-400 transition">Tools</button>
                    <button @click="$emit('change-tab', 'database')" :class="tabClass('database')" class="px-3 py-2 text-sm hover:text-blue-400 transition">Database</button>
                    <button @click="$emit('change-tab', 'analysis')" :class="tabClass('analysis')" class="px-3 py-2 text-sm hover:text-blue-400 transition">AI Analysis</button>
                    <button @click="$emit('change-tab', 'closure')" :class="tabClass('closure')" class="px-3 py-2 text-sm hover:text-blue-400 transition">Closure Notes</button>
                    <button @click="$emit('change-tab', 'jobs')" :class="tabClass('jobs')" class="px-3 py-2 text-sm hover:text-blue-400 transition">Jobs</button>
                    <button @click="$emit('change-tab', 'reports')" :class="tabClass('reports')" class="px-3 py-2 text-sm hover:text-blue-400 transition">Reports</button>
                    <button @click="$emit('change-tab', 'code-review')" :class="tabClass('code-review')" class="px-3 py-2 text-sm hover:text-blue-400 transition">Code Review</button>
                </div>
            </div>
        </header>
    `,
    methods: {
        tabClass(tab) {
            return this.currentTab === tab
                ? 'border-b-2 border-blue-400'
                : 'border-b-2 border-transparent';
        }
    }
};
