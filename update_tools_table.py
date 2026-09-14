import re

with open('web/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the Tools Grid section with a compact table
old_grid = '''                <!-- Tools Grid -->
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    <div v-for="tool in filteredTools" :key="tool.name" class="bg-gray-800 border border-gray-700 rounded-lg p-5 hover:border-blue-500 cursor-pointer transition">
                        <h3 class="text-lg font-bold text-blue-400">{{ tool.name }}</h3>
                        <p class="text-xs text-gray-400 mt-1">{{ tool.category }}</p>
                        <p class="text-sm text-gray-300 mt-3">{{ tool.description }}</p>
                        
                        <!-- Arguments -->
                        <div v-if="tool.arguments && tool.arguments.length" class="mt-4 space-y-2">
                            <div v-for="arg in tool.arguments" :key="arg.flag" class="text-xs">
                                <label class="text-gray-400">{{ arg.name }}</label>
                                <input 
                                    v-model="toolArgs[tool.name + '_' + arg.flag]" 
                                    :type="arg.name.includes('file') || arg.name.includes('path') ? 'file' : 'text'"
                                    :placeholder="arg.description"
                                    class="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-xs placeholder-gray-500 focus:outline-none focus:border-blue-400 mt-1">
                            </div>
                        </div>

                        <button 
                            @click="executeTool(tool)"
                            class="mt-4 w-full bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded text-sm font-semibold transition">
                            Execute
                        </button>
                    </div>
                </div>'''

new_grid = '''                <!-- Tools Table -->
                <div class="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
                    <table class="w-full text-sm">
                        <thead class="bg-gray-900 border-b border-gray-700">
                            <tr>
                                <th class="px-4 py-3 text-left font-semibold text-gray-300">Tool Name</th>
                                <th class="px-4 py-3 text-left font-semibold text-gray-300">Category</th>
                                <th class="px-4 py-3 text-left font-semibold text-gray-300">Description</th>
                                <th class="px-4 py-3 text-center font-semibold text-gray-300">Args</th>
                                <th class="px-4 py-3 text-center font-semibold text-gray-300">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="tool in filteredTools" :key="tool.name" class="border-b border-gray-700 hover:bg-gray-750 transition">
                                <td class="px-4 py-3">
                                    <span class="text-blue-400 font-semibold">{{ tool.name }}</span>
                                </td>
                                <td class="px-4 py-3">
                                    <span class="text-xs bg-gray-700 text-gray-300 px-2 py-1 rounded">{{ tool.category }}</span>
                                </td>
                                <td class="px-4 py-3 text-gray-400 max-w-xs truncate">{{ tool.description }}</td>
                                <td class="px-4 py-3 text-center">
                                    <span class="text-xs text-gray-400">{{ tool.arguments ? tool.arguments.length : 0 }}</span>
                                </td>
                                <td class="px-4 py-3 text-center">
                                    <button 
                                        @click="selectToolForExecution(tool)"
                                        class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded text-xs font-semibold transition">
                                        Run
                                    </button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                    <div v-if="filteredTools.length === 0" class="text-center py-8 text-gray-400">
                        No tools match your search
                    </div>
                </div>

                <!-- Tool Execution Modal -->
                <div v-if="selectedToolForExecution" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                    <div class="bg-gray-800 border border-gray-700 rounded-lg p-6 max-w-md w-full mx-4">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-lg font-bold text-blue-400">{{ selectedToolForExecution.name }}</h3>
                            <button @click="selectedToolForExecution = null" class="text-gray-400 hover:text-white text-2xl">&times;</button>
                        </div>
                        
                        <p class="text-sm text-gray-400 mb-4">{{ selectedToolForExecution.description }}</p>
                        
                        <!-- Arguments -->
                        <div v-if="selectedToolForExecution.arguments && selectedToolForExecution.arguments.length" class="space-y-3 mb-4">
                            <div v-for="arg in selectedToolForExecution.arguments" :key="arg.flag">
                                <label class="text-xs text-gray-400">{{ arg.name }}</label>
                                <input 
                                    v-model="toolArgs[selectedToolForExecution.name + '_' + arg.flag]" 
                                    :type="arg.name.includes('file') || arg.name.includes('path') ? 'file' : 'text'"
                                    :placeholder="arg.description"
                                    class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-xs placeholder-gray-500 focus:outline-none focus:border-blue-400">
                            </div>
                        </div>

                        <div class="flex space-x-2">
                            <button 
                                @click="executeTool(selectedToolForExecution)"
                                class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-semibold transition">
                                Execute
                            </button>
                            <button 
                                @click="selectedToolForExecution = null"
                                class="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-semibold transition">
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>'''

content = content.replace(old_grid, new_grid)

# Add selectedToolForExecution to data
old_data = '''                    closureResult: null,
                    closureGenerating: false
                };'''

new_data = '''                    closureResult: null,
                    closureGenerating: false,
                    selectedToolForExecution: null
                };'''

content = content.replace(old_data, new_data)

# Add selectToolForExecution method
old_methods = '''                checkHealth() {
                    axios.get(this.apiUrl + '/health')'''

new_methods = '''                selectToolForExecution(tool) {
                    this.selectedToolForExecution = tool;
                },
                checkHealth() {
                    axios.get(this.apiUrl + '/health')'''

content = content.replace(old_methods, new_methods)

with open('web/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Converted Tools grid to compact table view with modal")
