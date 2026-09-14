window.ClosureTab = {
    props: [
        'triageData',
        'availableRules',
        'selectedRule',
        'closureForm',
        'closureResult',
        'closureGenerating',
        'closureSuggestedRuleName',
        'closureSuggestedDisposition'
    ],
    emits: [
        'update:closure-form',
        'select-case',
        'select-rule',
        'generate-closure-note',
        'copy-closure-note',
        'download-closure-note'
    ],
    template: `
        <div class="space-y-6">
            <div>
                <h2 class="text-3xl font-bold mb-2">Closure Notes Generator</h2>
                <p class="text-gray-400">Generate formatted closure notes for security incidents</p>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div class="lg:col-span-2 space-y-4">
                    <div class="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-4">
                        <div>
                            <label class="text-sm text-gray-400">Select Case</label>
                            <select
                                :value="closureForm.caseId"
                                @change="updateCase($event.target.value)"
                                class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white">
                                <option value="">-- Choose a case --</option>
                                <option v-for="case_ in triageData" :key="case_.case_id" :value="case_.case_id">
                                    {{ case_.case_id }} - {{ case_.rule_name }}
                                </option>
                            </select>
                        </div>

                        <div>
                            <label class="text-sm text-gray-400">Select Rule for Closure</label>
                            <select
                                :value="closureForm.ruleId"
                                @change="updateRule($event.target.value)"
                                class="w-full mt-2 px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white"
                                :disabled="!closureForm.caseId">
                                <option value="">-- Choose a rule --</option>
                                <option v-for="rule in availableRules" :key="rule.rule_id" :value="rule.rule_id">
                                    {{ rule.rule_name || rule.rule_id || 'Unnamed rule' }} ({{ rule.severity || 'medium' }})
                                </option>
                            </select>

                            <p v-if="closureSuggestedRuleName || closureSuggestedDisposition" class="mt-1 text-[11px] text-gray-400">
                                Suggested rule:
                                <span class="font-semibold">{{ closureSuggestedRuleName || 'none' }}</span>
                                • Suggested disposition:
                                <span class="font-semibold">{{ closureSuggestedDisposition || 'Undetermined' }}</span>
                            </p>
                        </div>

                        <div v-if="closureForm.ruleId && selectedRule" class="space-y-3">
                            <p class="text-xs text-gray-400 font-semibold">Required Fields</p>

                            <div v-for="field in selectedRule.required_closure_fields" :key="field">
                                <div v-if="field === 'justification'">
                                    <label class="text-sm text-gray-400">{{ field }}</label>
                                    <select
                                        :value="closureForm.fieldValues[field] || ''"
                                        @change="updateFieldValue(field, $event.target.value)"
                                        class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm">
                                        <option value="">-- Select justification --</option>
                                        <option value="Approved by security team for authorized activity">Approved by security team for authorized activity</option>
                                        <option value="Legitimate business process">Legitimate business process</option>
                                        <option value="Test/training exercise">Test/training exercise</option>
                                        <option value="Known false positive pattern">Known false positive pattern</option>
                                        <option value="Misconfigured alert">Misconfigured alert</option>
                                        <option value="Normal operational behavior">Normal operational behavior</option>
                                    </select>
                                </div>
                                <div v-else>
                                    <label class="text-sm text-gray-400">{{ field }}</label>
                                    <input
                                        :value="closureForm.fieldValues[field] || ''"
                                        @input="updateFieldValue(field, $event.target.value)"
                                        type="text"
                                        class="w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm">
                                </div>
                            </div>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="text-sm text-gray-400">Analyst Notes (Optional)</label>
                                <textarea
                                    :value="closureForm.analystNotes"
                                    @input="updateAnalystNotes($event.target.value)"
                                    rows="6"
                                    placeholder="Add any additional notes..."
                                    class="w-full mt-2 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm placeholder-gray-400"></textarea>
                            </div>
                            <div>
                                <label class="text-sm text-gray-400">Disposition</label>
                                <select
                                    :value="closureForm.disposition"
                                    @change="updateDisposition($event.target.value)"
                                    class="w-full mt-2 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm">
                                    <option value="True Positive">True Positive - Suspicious Activity Confirmed</option>
                                    <option value="Benign Positive">Benign Positive - Suspicious But Expected</option>
                                    <option value="False Positive">False Positive - Incorrect Analytic Logic</option>
                                    <option value="Other">Other</option>
                                    <option value="Undetermined">Undetermined</option>
                                </select>

                                <p class="text-xs text-gray-400 mt-2">
                                    Choose disposition based on final investigative outcome.
                                </p>
                            </div>
                        </div>

                        <div class="flex items-center justify-end mt-4">
                            <button
                                @click="$emit('generate-closure-note')"
                                :disabled="closureGenerating || !closureForm.caseId || !closureForm.ruleId"
                                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded text-sm font-semibold">
                                {{ closureGenerating ? 'Generating Closure Note...' : 'Generate Closure Note' }}
                            </button>
                        </div>
                    </div>
                </div>

                <div class="space-y-4">
                    <div class="bg-gray-800 border border-green-600 rounded-lg p-6 h-full flex flex-col sticky top-24">
                        <h3 class="text-lg font-bold text-green-400 mb-3">Generated Note</h3>
                        <p
                            v-if="closureResult && closureResult.disposition"
                            class="text-xs text-gray-400 mb-2"
                        >
                            Disposition:
                            <span class="text-green-400 font-semibold">{{ closureResult.disposition }}</span>
                        </p>

                        <div
                            v-if="closureResult && closureResult.generated_note"
                            class="flex-1 bg-gray-900 rounded p-3 text-xs font-mono overflow-y-auto max-h-96 mb-4 whitespace-pre-wrap text-gray-300"
                        >
                            {{ closureResult.generated_note }}
                        </div>
                        <div
                            v-else
                            class="flex-1 bg-gray-900 border border-gray-700 rounded p-3 text-sm text-gray-500 flex items-center justify-center min-h-[200px]"
                        >
                            Generate a closure note to preview it here
                        </div>

                        <div class="mt-4 flex flex-col gap-2">
                            <button
                                @click="$emit('copy-closure-note')"
                                :disabled="!closureResult || !closureResult.generated_note"
                                class="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 rounded text-xs font-semibold text-white transition"
                            >
                                Copy to Clipboard
                            </button>
                            <button
                                @click="$emit('download-closure-note')"
                                :disabled="!closureResult || !closureResult.generated_note"
                                class="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 rounded text-xs font-semibold text-white transition"
                            >
                                Download as .txt
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    methods: {
        updateCase(caseId) {
            const next = { ...this.closureForm, caseId };
            this.$emit('update:closure-form', next);
            this.$emit('select-case');
        },
        updateRule(ruleId) {
            const next = { ...this.closureForm, ruleId };
            this.$emit('update:closure-form', next);
            this.$emit('select-rule');
        },
        updateFieldValue(field, value) {
            const next = {
                ...this.closureForm,
                fieldValues: {
                    ...(this.closureForm.fieldValues || {}),
                    [field]: value
                }
            };
            this.$emit('update:closure-form', next);
        },
        updateAnalystNotes(value) {
            const next = { ...this.closureForm, analystNotes: value };
            this.$emit('update:closure-form', next);
        },
        updateDisposition(value) {
            const next = { ...this.closureForm, disposition: value };
            this.$emit('update:closure-form', next);
        }
    }
};
