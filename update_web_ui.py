import re

with open('web/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update closureForm data
old_form = '''closureForm: {
                        caseId: '',
                        ruleId: '',
                        fieldValues: {},
                        analystNotes: ''
                    },'''

new_form = '''closureForm: {
                        caseId: '',
                        ruleId: '',
                        fieldValues: {},
                        analystNotes: '',
                        disposition: 'Undetermined'
                    },'''

content = content.replace(old_form, new_form)

# 2. Update the field inputs section
old_fields = '''<!-- Dynamic Field Inputs -->
                            <div v-if="closureForm.ruleId && selectedRule" class="space-y-3">
                                <p class="text-xs text-gray-400 font-semibold">Required Fields</p>
                                <div v-for="field in selectedRule.required_closure_fields" :key="field">
                                    <label class="text-sm text-gray-400">{{ field }}</label>
                                    <input 
                                        v-model="closureForm.fieldValues[field]"
                                        type="text"
                                        :placeholder="'Enter ' + field"
                                        class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-blue-400 text-sm">
                                </div>
                            </div>

                            <!-- Analyst Notes -->'''

new_fields = '''<!-- Dynamic Field Inputs -->
                            <div v-if="closureForm.ruleId && selectedRule" class="space-y-3">
                                <p class="text-xs text-gray-400 font-semibold">Required Fields</p>
                                <div v-for="field in selectedRule.required_closure_fields" :key="field">
                                    <!-- Dropdown for justification -->
                                    <div v-if="field === 'justification'">
                                        <label class="text-sm text-gray-400">{{ field }}</label>
                                        <select 
                                            v-model="closureForm.fieldValues[field]"
                                            class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-400 text-sm">
                                            <option value="">-- Select justification --</option>
                                            <option value="Approved by security team for authorized activity">Approved by security team for authorized activity</option>
                                            <option value="Legitimate business process">Legitimate business process</option>
                                            <option value="Test/training exercise">Test/training exercise</option>
                                            <option value="Known false positive pattern">Known false positive pattern</option>
                                            <option value="Misconfigured alert">Misconfigured alert</option>
                                            <option value="Normal operational behavior">Normal operational behavior</option>
                                        </select>
                                    </div>
                                    <!-- Regular text input for other fields -->
                                    <div v-else>
                                        <label class="text-sm text-gray-400">{{ field }}</label>
                                        <input 
                                            v-model="closureForm.fieldValues[field]"
                                            type="text"
                                            :placeholder="'Enter ' + field"
                                            class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-blue-400 text-sm">
                                    </div>
                                </div>
                            </div>

                            <!-- Disposition/Status Dropdown -->
                            <div>
                                <label class="text-sm text-gray-400">Disposition</label>
                                <select v-model="closureForm.disposition" class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-blue-500">
                                    <option value="True Positive">True Positive - Suspicious Activity Confirmed</option>
                                    <option value="Benign Positive">Benign Positive - Suspicious But Expected</option>
                                    <option value="False Positive">False Positive - Incorrect Analytic Logic</option>
                                    <option value="Other">Other</option>
                                    <option value="Undetermined">Undetermined</option>
                                </select>
                            </div>

                            <!-- Analyst Notes -->'''

content = content.replace(old_fields, new_fields)

# 3. Update generateClosureNote to pass disposition
old_generate = '''const res = await axios.post(this.apiUrl + '/db/closure-note', {
                            rule_id: this.closureForm.ruleId,
                            case_id: this.closureForm.caseId,
                            field_values: this.closureForm.fieldValues,
                            analyst_notes: this.closureForm.analystNotes
                        });'''

new_generate = '''const res = await axios.post(this.apiUrl + '/db/closure-note', {
                            rule_id: this.closureForm.ruleId,
                            case_id: this.closureForm.caseId,
                            field_values: this.closureForm.fieldValues,
                            analyst_notes: this.closureForm.analystNotes,
                            disposition: this.closureForm.disposition
                        });'''

content = content.replace(old_generate, new_generate)

# 4. Update result display
old_result = '''<div v-if="closureResult" class="bg-gray-800 border border-green-600 rounded-lg p-6 sticky top-24">
                            <h3 class="text-lg font-bold text-green-400 mb-3">Generated Note</h3>
                            <div class="bg-gray-900 rounded p-3 text-xs font-mono overflow-y-auto max-h-96 mb-4 whitespace-pre-wrap text-gray-300">'''

new_result = '''<div v-if="closureResult" class="bg-gray-800 border border-green-600 rounded-lg p-6 sticky top-24">
                            <h3 class="text-lg font-bold text-green-400 mb-3">Generated Note</h3>
                            <p class="text-xs text-gray-400 mb-2">Disposition: <span class="text-green-400 font-semibold">{{ closureResult.disposition }}</span></p>
                            <div class="bg-gray-900 rounded p-3 text-xs font-mono overflow-y-auto max-h-96 mb-4 whitespace-pre-wrap text-gray-300">'''

content = content.replace(old_result, new_result)

with open('web/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated web/index.html with disposition and justification dropdown")
