/**
 * components/CodeReviewTab.js
 * Presentational Code Review tab.
 * Business logic stays in the root app (or modules/codeReview.js later).
 *
 * IMPORTANT: template must only use props + local methods.
 * Calling root methods by name (e.g. onCodeReviewDragOver) throws and
 * can leave the whole Vue app unable to switch tabs afterward.
 */
window.CodeReviewTab = {
    props: [
        'codeReviewForm',
        'codeReviewModels',
        'codeReviewRunning',
        'codeReviewResult',
        'codeReviewsList',
        'codeReviewDragActive',
        'codeReviewSections',
        'codeReviewSectionsBusy',
        'codeReviewSectionsFilter',
        'codeReviewSectionsCodeOnly',
        'codeReviewSectionsFeaturesOnly',
        'codeReviewSectionsMinLines',
        'codeReviewSectionsUseLocal',
        'codeReviewSectionsShowAdvanced',
        'codeReviewSearchTerm',
        'codeReviewFindTerm',
        'codeReviewFindCaseSensitive',
        'codeReviewFindMatches',
        'codeReviewFindIndex',
        'filteredCodeReviewSections',
        'groupedCodeReviewSections',
        'ollamaHealth'
    ],
    emits: [
        'update:code-review-form',
        'update:code-review-search-term',
        'update:code-review-sections-filter',
        'update:code-review-sections-code-only',
        'update:code-review-sections-features-only',
        'update:code-review-sections-min-lines',
        'update:code-review-sections-use-local',
        'update:code-review-sections-show-advanced',
        'update:code-review-find-term',
        'update:code-review-find-case-sensitive',
        'submit-code-review',
        'detect-code-review-sections',
        'focus-code-review-on-section',
        'focus-code-review-section',
        'handle-file-upload',
        'handle-folder-upload',
        'on-code-review-drag-over',
        'on-code-review-drag-leave',
        'on-code-review-drop',
        'run-code-review-find',
        'clear-code-review-find',
        'code-review-find-next',
        'code-review-find-prev',
        'copy-code-review-to-clipboard',
        'load-full-code-review',
        'on-code-review-find-keydown',
        'on-code-review-textarea-keydown'
    ],
    computed: {
        form() {
            return this.codeReviewForm || {
                codeSnippet: '',
                language: 'python',
                model: '',
                uploadedFile: null,
                instructions: ''
            };
        },
        findMatches() {
            return Array.isArray(this.codeReviewFindMatches) ? this.codeReviewFindMatches : [];
        },
        findIndex() {
            return typeof this.codeReviewFindIndex === 'number' ? this.codeReviewFindIndex : -1;
        },
        sections() {
            return Array.isArray(this.codeReviewSections) ? this.codeReviewSections : [];
        },
        filteredSections() {
            return Array.isArray(this.filteredCodeReviewSections) ? this.filteredCodeReviewSections : [];
        },
        groupedSections() {
            return Array.isArray(this.groupedCodeReviewSections) ? this.groupedCodeReviewSections : [];
        },
        reviewsList() {
            return Array.isArray(this.codeReviewsList) ? this.codeReviewsList : [];
        },
        canSubmit() {
            const snippet = (this.form.codeSnippet || '').trim();
            return !this.codeReviewRunning && !!(snippet || this.form.uploadedFile);
        }
    },
    methods: {
        updateFormField(field, value) {
            const next = Object.assign({}, this.form);
            next[field] = value;
            this.$emit('update:code-review-form', next);
        },
        triggerFileInput() {
            const el = this.$refs && this.$refs.codeReviewFileInput;
            if (el) el.click();
        },
        onDragOver(event) {
            this.$emit('on-code-review-drag-over', event);
        },
        onDragLeave(event) {
            this.$emit('on-code-review-drag-leave', event);
        },
        onDrop(event) {
            this.$emit('on-code-review-drop', event);
        },
        onFindTermInput(event) {
            const value = event && event.target ? event.target.value : '';
            this.$emit('update:code-review-find-term', value);
            this.$emit('run-code-review-find');
        },
        onFindCaseChange(event) {
            const checked = !!(event && event.target && event.target.checked);
            this.$emit('update:code-review-find-case-sensitive', checked);
            this.$emit('run-code-review-find');
        }
    },
    template: `
        <div class="space-y-6">
<div class="bg-gray-900/50 border border-gray-700 rounded-lg p-6">
    <h2 class="text-2xl font-bold text-blue-400 mb-2">Code Review with Local Ollama</h2>
    <p class="text-gray-400 text-sm mb-6">Analyze code quality, security, and performance using local AI models</p>
    
    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Language</label>
        <select :value="form.language" @input="updateFormField('language', $event.target.value)" class="w-full bg-gray-800 text-gray-300 border border-gray-600 rounded px-3 py-2 text-sm">
            <option value="python">Python</option>
            <option value="javascript">JavaScript</option>
            <option value="go">Go</option>
            <option value="bash">Bash</option>
            <option value="sql">SQL</option>
            <option value="html">HTML</option>
        </select>
    </div>
    
    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Model</label>
        <select :value="form.model" @input="updateFormField('model', $event.target.value)" class="w-full bg-gray-800 text-gray-300 border border-gray-600 rounded px-3 py-2 text-sm">
            <option
                v-for="model in (codeReviewModels || [])"
                :key="model"
                :value="model"
            >
                {{ model }}
            </option>
        </select>
    </div>
    
    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Upload File or Project (.zip) (or paste below)</label>
        <div
            class="w-full rounded border-2 border-dashed px-4 py-6 text-xs text-gray-300 bg-gray-900/40 flex flex-col items-center justify-center cursor-pointer"
            :class="codeReviewDragActive ? 'border-blue-400 bg-gray-800/60' : 'border-gray-600 hover:border-blue-500'"
            @dragover.prevent="onDragOver"
            @dragleave.prevent="onDragLeave"
            @drop.prevent="onDrop"
            @click="triggerFileInput"
        >
            <p class="mb-2 text-center">
                Drag & drop a single file or project .zip here,<br>
                or <span class="font-semibold text-blue-400">click to choose</span>
            </p>
            <p v-if="form.uploadedFile" class="text-[11px] text-gray-400 mt-1">
                Selected: <span class="font-mono">{{ form.uploadedFile.name }}</span>
            </p>
            <input
                ref="codeReviewFileInput"
                type="file"
                class="hidden"
                @change="$emit('handle-file-upload', $event)"
                accept=".py,.js,.go,.sh,.sql,.ts,.java,.cpp,.c,.html,.htm,.zip"
            >
        </div>
    </div>

    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Upload Project Folder (optional)</label>
        <input
            type="file"
            webkitdirectory
            @change="$emit('handle-folder-upload', $event)"
            class="w-full bg-gray-800 text-gray-300 border border-gray-600 rounded px-3 py-2 text-xs"
        >
        <p class="mt-1 text-[11px] text-gray-500">
            Select a folder to aggregate its code files into a single review context (no need to zip).
        </p>
    </div>

    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Review Instructions / Question (optional)</label>
        <textarea
            :value="form.instructions" @input="updateFormField('instructions', $event.target.value)"
            class="w-full h-24 bg-gray-800 text-gray-300 border border-gray-600 rounded px-3 py-2 text-xs"
            placeholder="E.g., Focus on security issues in this file, or check performance of this loop..."
        ></textarea>
    </div>

    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Focus on Section (search term)</label>
        <div class="flex gap-2">
            <input
                :value="codeReviewSearchTerm" @input="$emit('update:code-review-search-term', $event.target.value)"
                type="text"
                class="flex-1 bg-gray-800 text-gray-300 border border-gray-600 rounded px-3 py-2 text-xs"
                placeholder="e.g., Analysis Result, Phase 2 SPL, closure-note template"
            >
            <button
                type="button"
                class="px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-xs font-semibold text-gray-100"
                @click="$emit('focus-code-review-section')"
            >
                Extract Section
            </button>
        </div>
        <p class="mt-1 text-[11px] text-gray-500">
            This narrows "Code to Review" to the region around the first match of your search term, so the model works on the exact section you care about.
        </p>
    </div>
    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Jump to a feature</label>
        <p class="text-[11px] text-gray-500 mb-2">
            Finds the parts of this file that actually do work (screens, actions, uploads, find, review…).
            Click one to load <em>only that part</em> into “Code to Review” so you can edit or send it to the AI.
        </p>
        <div class="flex flex-wrap gap-2 items-center mb-2">
            <button
                type="button"
                class="px-3 py-2 bg-indigo-700 hover:bg-indigo-600 rounded text-xs font-semibold text-white disabled:bg-gray-800 disabled:text-gray-500"
                :disabled="codeReviewSectionsBusy || !(form.codeSnippet && form.codeSnippet.trim())"
                @click="$emit('detect-code-review-sections')"
            >
                {{ codeReviewSectionsBusy ? 'Finding features…' : 'Find features in this file' }}
            </button>
            <span v-if="sections.length" class="text-[11px] text-gray-500">
                {{ filteredSections.length }} features
                <span v-if="filteredSections.length !== sections.length">
                    (of {{ sections.length }} total pieces)
                </span>
            </span>
        </div>
        <div v-if="sections.length" class="mb-2 space-y-2">
            <input
                :value="codeReviewSectionsFilter" @input="$emit('update:code-review-sections-filter', $event.target.value)"
                type="text"
                class="w-full bg-gray-800 text-gray-300 border border-gray-600 rounded px-2 py-1 text-[11px]"
                placeholder="Filter… e.g. focus, find, upload, code review"
            >
            <div class="flex flex-wrap items-center gap-3 text-[11px] text-gray-400">
                <label class="flex items-center gap-1.5" title="Hide one-liner helpers and only keep actions, screens, and larger blocks">
                    <input
                        type="checkbox"
                        :checked="codeReviewSectionsFeaturesOnly" @change="$emit('update:code-review-sections-features-only', $event.target.checked)"
                        class="h-3 w-3 bg-gray-800 border border-gray-600 rounded"
                    >
                    <span>Only features that do work</span>
                </label>
                <button
                    type="button"
                    class="text-[11px] text-blue-400 hover:text-blue-300 underline"
                    @click="$emit('update:code-review-sections-show-advanced', !codeReviewSectionsShowAdvanced)"
                >
                    {{ codeReviewSectionsShowAdvanced ? 'Hide options' : 'More options' }}
                </button>
            </div>
            <div v-if="codeReviewSectionsShowAdvanced" class="flex flex-wrap items-center gap-3 text-[11px] text-gray-400 bg-gray-900/50 border border-gray-700 rounded px-2 py-1.5">
                <label class="flex items-center gap-1.5">
                    <span class="text-gray-500">Min lines</span>
                    <input
                        type="number"
                        min="0"
                        max="500"
                        :value="codeReviewSectionsMinLines" @input="$emit('update:code-review-sections-min-lines', Number($event.target.value))"
                        class="w-14 bg-gray-800 border border-gray-600 rounded px-1.5 py-0.5 text-[11px] text-gray-200"
                    >
                </label>
                <label class="flex items-center gap-1.5">
                    <input
                        type="checkbox"
                        :checked="codeReviewSectionsCodeOnly" @change="$emit('update:code-review-sections-code-only', $event.target.checked)"
                        class="h-3 w-3 bg-gray-800 border border-gray-600 rounded"
                    >
                    <span>Code Review related only</span>
                </label>
                <label class="flex items-center gap-1.5" title="Local outline is Vue/JS/HTML-aware. Backend is a flat function list.">
                    <input
                        type="checkbox"
                        :checked="codeReviewSectionsUseLocal" @change="$emit('update:code-review-sections-use-local', $event.target.checked)"
                        class="h-3 w-3 bg-gray-800 border border-gray-600 rounded"
                    >
                    <span>Smart local outline</span>
                </label>
            </div>
        </div>
        <div v-if="groupedSections.length" class="max-h-72 overflow-y-auto bg-gray-900/40 border border-gray-700 rounded">
            <div v-for="group in groupedSections" :key="group.kind" class="border-b border-gray-800 last:border-b-0">
                <div class="sticky top-0 z-10 px-3 py-1.5 bg-gray-900/95 border-b border-gray-800 flex items-center justify-between">
                    <span class="text-[11px] font-semibold tracking-wide text-blue-300">{{ group.label }}</span>
                    <span class="text-[10px] text-gray-500">{{ group.sections.length }}</span>
                </div>
                <button
                    v-for="section in group.sections"
                    :key="section.id"
                    type="button"
                    class="w-full text-left px-3 py-2.5 text-[11px] border-b border-gray-800/80 hover:bg-indigo-900/30 last:border-b-0"
                    @click="$emit('focus-code-review-on-section', section)"
                >
                    <div class="flex justify-between gap-2 items-start">
                        <span class="text-gray-100 font-semibold leading-snug">
                            {{ section.title || section.label || section.name || 'Section' }}
                        </span>
                        <span class="text-gray-500 shrink-0 text-[10px]" v-if="section.start_line && section.end_line">
                            L{{ section.start_line }}–{{ section.end_line }}
                            <span v-if="section.line_count" class="text-gray-600">({{ section.line_count }} lines)</span>
                        </span>
                    </div>
                    <div class="mt-0.5 text-gray-400 leading-snug" v-if="section.description">
                        {{ section.description }}
                    </div>
                    <div class="mt-0.5 text-gray-600 font-mono truncate" v-else-if="section.preview">
                        {{ section.preview }}
                    </div>
                </button>
            </div>
        </div>
        <div v-else-if="sections.length" class="text-[11px] text-gray-500">
            No features match the current filters. Try turning off “Only features that do work” or lowering Min lines under More options.
        </div>
        <p class="mt-1 text-[11px] text-gray-500">
            Tip: for this app’s own UI, look under <strong class="text-gray-400">Code Review</strong> for “Focus on a detected section” and “Detect features / sections”.
        </p>
    </div>
    
    <div class="mb-4">
        <label class="text-xs font-bold text-gray-300 mb-2 block">Code to Review</label>

        <!-- In-place Find bar (Ctrl+F style) -->
        <div class="flex flex-wrap items-center gap-2 mb-2 bg-gray-900/60 border border-gray-700 rounded px-2 py-1.5">
            <span class="text-[11px] text-gray-400 font-semibold shrink-0">Find</span>
            <input
                ref="codeReviewFindInput"
                :value="codeReviewFindTerm"
                type="text"
                class="flex-1 min-w-[140px] bg-gray-800 text-gray-200 border border-gray-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:border-blue-500"
                placeholder="Find in code… (Ctrl+F)"
                @input="onFindTermInput"
                @keydown="$emit('on-code-review-find-keydown', $event)"
            >
            <span class="text-[11px] text-gray-400 tabular-nums shrink-0 min-w-[4.5rem] text-center">
                <span v-if="!(codeReviewFindTerm || '').trim()">—</span>
                <span v-else-if="findMatches.length === 0">0 matches</span>
                <span v-else-if="findIndex < 0">{{ findMatches.length }} matches</span>
                <span v-else>{{ findIndex + 1 }} / {{ findMatches.length }}</span>
            </span>
            <button
                type="button"
                class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-[11px] text-gray-100 disabled:opacity-40"
                :disabled="findMatches.length === 0"
                @click="$emit('code-review-find-prev')"
                title="Previous match (Shift+Enter)"
            >↑</button>
            <button
                type="button"
                class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-[11px] text-gray-100 disabled:opacity-40"
                :disabled="findMatches.length === 0"
                @click="$emit('code-review-find-next')"
                title="Next match (Enter)"
            >↓</button>
            <label class="flex items-center gap-1 text-[11px] text-gray-400 cursor-pointer select-none" title="Case sensitive">
                <input
                    type="checkbox"
                    :checked="codeReviewFindCaseSensitive"
                    class="h-3 w-3 bg-gray-800 border-gray-600 rounded"
                    @change="onFindCaseChange"
                >
                <span>Aa</span>
            </label>
            <button
                type="button"
                class="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-[11px] text-gray-300"
                @click="$emit('clear-code-review-find')"
                title="Clear find (Esc)"
            >✕</button>
        </div>

        <textarea
            ref="codeReviewTextarea"
            :value="form.codeSnippet" @input="updateFormField('codeSnippet', $event.target.value)"
            class="w-full h-64 bg-gray-800 text-gray-300 border border-gray-600 rounded px-3 py-2 font-mono text-xs"
            placeholder="Paste your code here..."
            @keydown="$emit('on-code-review-textarea-keydown', $event)"
        ></textarea>
        <p class="mt-1 text-[11px] text-gray-500">
            Type in Find → press Enter to jump to a match (highlighted). Enter / Shift+Enter / F3 keep navigating. Ctrl+F returns to the Find box. Esc clears.
        </p>
    </div>
    
    <button @click="$emit('submit-code-review')" :disabled="!canSubmit" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-white rounded text-sm font-bold">
        {{ codeReviewRunning ? 'Reviewing...' : 'Submit for Review' }}
    </button>
</div>

<!-- Code Review Result -->
<div v-if="codeReviewResult" class="bg-gray-900/50 border border-gray-700 rounded-lg p-6">
    <h3 class="text-xl font-bold text-blue-400 mb-2">
        Review Result (ID: {{ codeReviewResult.id || codeReviewResult.review_id }})
    </h3>
    <p class="text-xs text-gray-400 mb-4">
        <strong>Language:</strong> {{ codeReviewResult.language }}
        &nbsp;|&nbsp;
        <strong>Model:</strong> {{ codeReviewResult.model }}
        &nbsp;|&nbsp;
        <strong>Created:</strong> {{ codeReviewResult.created_at }}
    </p>
    <div class="bg-black/40 border border-gray-700 rounded p-3 max-h-96 overflow-y-auto text-xs font-mono text-gray-200 whitespace-pre-wrap">
        {{ codeReviewResult.review }}
    </div>
    <button
        @click="$emit('copy-code-review-to-clipboard')"
        class="mt-3 px-3 py-1 bg-emerald-600 hover:bg-emerald-700 text-white text-xs rounded"
    >
        Copy Review to Clipboard
    </button>
</div>

<!-- Previous Code Reviews -->
<div v-if="reviewsList.length > 0" class="bg-gray-900/50 border border-gray-700 rounded-lg p-6">
    <h3 class="text-lg font-bold text-blue-400 mb-3">Previous Reviews</h3>
    <div class="overflow-x-auto">
        <table class="min-w-full text-xs text-gray-200 border border-gray-700 rounded">
            <thead class="bg-gray-800">
                <tr>
                    <th class="px-2 py-1 text-left border-b border-gray-700">ID</th>
                    <th class="px-2 py-1 text-left border-b border-gray-700">Language</th>
                    <th class="px-2 py-1 text-left border-b border-gray-700">Model</th>
                    <th class="px-2 py-1 text-left border-b border-gray-700">Code (preview)</th>
                    <th class="px-2 py-1 text-left border-b border-gray-700">Review (preview)</th>
                    <th class="px-2 py-1 text-left border-b border-gray-700">Created</th>
                    <th class="px-2 py-1 text-left border-b border-gray-700">Action</th>
                </tr>
            </thead>
            <tbody>
                <tr
                    v-for="review in reviewsList"
                    :key="review.id"
                    class="border-b border-gray-800 hover:bg-gray-800/70"
                >
                    <td class="px-2 py-1">{{ review.id }}</td>
                    <td class="px-2 py-1">{{ review.language }}</td>
                    <td class="px-2 py-1">{{ review.model }}</td>
                    <td class="px-2 py-1 font-mono text-[11px] max-w-[200px] truncate">
                        {{ review.code_snippet }}
                    </td>
                    <td class="px-2 py-1 font-mono text-[11px] max-w-[260px] truncate">
                        {{ review.review }}
                    </td>
                    <td class="px-2 py-1 text-[11px]">{{ review.created_at }}</td>
                    <td class="px-2 py-1">
                        <button
                            @click="$emit('load-full-code-review', review.id)"
                            class="px-2 py-1 bg-violet-600 hover:bg-violet-700 text-white text-[11px] rounded"
                        >
                            View Full
                        </button>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
</div>
        </div>
    `
};
