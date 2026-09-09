window.ToolsTab = {
    props: ['tools', 'toolSearch', 'selectedCategory', 'toolArgs', 'selectedToolForExecution'],
    emits: [
        'update:tool-search',
        'update:selected-category',
        'select-tool',
        'close-tool-modal',
        'execute-tool',
        'change-tab'
    ],
    computed: {
        categories() {
            return [...new Set((this.tools || []).map(t => t.category))].sort();
        },
        filteredTools() {
            const search = (this.toolSearch || '').trim().toLowerCase();
            return (this.tools || []).filter(tool => {
                const haystack = [
                    tool.name,
                    tool.description,
                    tool.category
                ]
                    .filter(Boolean)
                    .join(' ')
                    .toLowerCase();

                const matchSearch = !search || haystack.includes(search);
                const matchCategory = !this.selectedCategory || tool.category === this.selectedCategory;
                return matchSearch && matchCategory;
            });
        }
    },
    template: `
        <div class="space-y-6">
            <div>
                <h2 class="text-3xl font-bold mb-2">Tools Catalog</h2>
                <p class="text-gray-400">{{ tools.length }} tools available • Filter and execute SOC tasks</p>
            </div>

            <div class="bg-blue-950 border border-blue-800 rounded-lg p-4 flex items-start justify-between gap-4">
                <div>
                    <h3 class="text-sm font-semibold text-blue-300">Looking for host owners or contact details?</h3>
                    <p class="text-sm text-blue-100 mt-1">
                        POC-style fields from pasted notables show up under
                        <span class="font-semibold">Database</span>
                        in Recent Pasted Notables and source notable details.
                    </p>
                </div>
                <button
                    @click="$emit('change-tab', 'database')"
                    class="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition flex-shrink-0">
                    Open Database
                </button>
            </div>

            <div class="bg-gray-800 p-4 rounded-lg border border-gray-700">
                <div class="flex items-center justify-between gap-4 mb-3">
                    <p class="text-sm text-gray-400">Search by tool name, category, or what the tool helps you do.</p>
                    <p class="text-xs text-gray-500">{{ filteredTools.length }} shown</p>
                </div>
                <input
                    :value="toolSearch"
                    @input="$emit('update:tool-search', $event.target.value)"
                    type="text"
                    placeholder="Search tools, for example: MDE, IOC, sanitize, report, CVE..."
                    class="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-400 focus:outline-none focus:border-blue-500">
                <div class="mt-3 flex flex-wrap space-x-2 gap-2">
                    <button
                        v-for="cat in categories"
                        :key="cat"
                        @click="$emit('update:selected-category', selectedCategory === cat ? '' : cat)"
                        :class="selectedCategory === cat ? 'bg-blue-600' : 'bg-gray-700'"
                        class="px-3 py-1 rounded text-sm hover:bg-blue-500 transition">
                        {{ cat }}
                    </button>
                </div>
            </div>

            <div class="space-y-4">
                <details
                    v-for="cat in categories.filter(cat => filteredTools.some(tool => tool.category === cat))"
                    :key="cat"
                    open
                    class="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden"
                >
                    <summary class="list-none cursor-pointer select-none px-4 py-3 bg-gray-900 border-b border-gray-700 flex items-center justify-between">
                        <div>
                            <p class="font-semibold text-gray-100">{{ cat }}</p>
                            <p class="text-xs text-gray-400">
                                {{ filteredTools.filter(tool => tool.category === cat).length }} tool<span v-if="filteredTools.filter(tool => tool.category === cat).length !== 1">s</span>
                            </p>
                        </div>
                        <span class="text-xs text-gray-500">Expand / collapse</span>
                    </summary>

                    <table class="w-full text-sm">
                        <thead class="bg-gray-900/40 border-b border-gray-700">
                            <tr>
                                <th class="px-4 py-3 text-left font-semibold text-gray-300">Tool Name</th>
                                <th class="px-4 py-3 text-left font-semibold text-gray-300">Description</th>
                                <th class="px-4 py-3 text-center font-semibold text-gray-300">Args</th>
                                <th class="px-4 py-3 text-center font-semibold text-gray-300">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr
                                v-for="tool in filteredTools.filter(tool => tool.category === cat)"
                                :key="tool.name"
                                class="border-b border-gray-700 hover:bg-gray-750 transition"
                            >
                                <td class="px-4 py-3 align-top">
                                    <span class="text-blue-400 font-semibold">{{ tool.name }}</span>
                                </td>
                                <td class="px-4 py-3 text-gray-400 max-w-sm leading-snug align-top">{{ tool.description }}</td>
                                <td class="px-4 py-3 text-center align-top">
                                    <span class="text-xs text-gray-400">{{ tool.arguments ? tool.arguments.length : 0 }}</span>
                                </td>
                                <td class="px-4 py-3 text-center align-top">
                                    <button
                                        @click="$emit('select-tool', tool)"
                                        class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition">
                                        Run
                                    </button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </details>
            </div>

            <div v-if="filteredTools.length === 0" class="bg-gray-800 border border-gray-700 rounded-lg text-center py-8 text-gray-400">
                No tools match your search
            </div>

            <div v-if="selectedToolForExecution" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-6 max-w-md w-full mx-4">
                    <div class="flex justify-between items-center mb-4">
                        <h3 class="text-lg font-bold text-blue-400">{{ selectedToolForExecution.name }}</h3>
                        <button @click="$emit('close-tool-modal')" class="text-gray-400 hover:text-white text-2xl">&times;</button>
                    </div>

                    <p class="text-sm text-gray-400 mb-4">{{ selectedToolForExecution.description }}</p>

                    <!-- Arguments -->
                    <div
                        v-if="selectedToolForExecution.arguments && selectedToolForExecution.arguments.length"
                        class="space-y-3 mb-4"
                    >
                        <div v-for="arg in selectedToolForExecution.arguments" :key="arg.flag">
                            <label class="text-xs text-gray-400">{{ arg.name }}</label>
                            <input
                                v-model="toolArgs[selectedToolForExecution.name + '_' + arg.flag]"
                                :type="arg.name.includes('file') || arg.name.includes('path') ? 'file' : 'text'"
                                :placeholder="arg.description"
                                class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-xs placeholder-gray-500 focus:outline-none focus:border-blue-400"
                            >
                        </div>
                    </div>

                    <div class="flex space-x-2">
                        <button
                            @click="$emit('execute-tool', selectedToolForExecution)"
                            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-semibold transition"
                        >
                            Execute
                        </button>
                        <button
                            @click="$emit('close-tool-modal')"
                            class="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-semibold transition"
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `
};
