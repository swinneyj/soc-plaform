/**
 * modules/codeReview.js
 * Domain methods for the Code Review tab (full bodies).
 */
(function (global) {
    'use strict';

    const CodeReviewMethods = {
        clearCodeReviewFind() {
            this.codeReviewFindTerm = '';
            this.codeReviewFindMatches = [];
            this.codeReviewFindIndex = -1;
        },
        runCodeReviewFind() {
            // Recompute matches only. Do NOT focus/select the textarea here —
            // that would steal the caret from the Find input on every keystroke.
            const term = (this.codeReviewFindTerm || '');
            const text = (this.codeReviewForm.codeSnippet || '');
            this.codeReviewFindMatches = [];
            this.codeReviewFindIndex = -1;

            if (!term || !text) {
                return;
            }

            const caseSensitive = !!this.codeReviewFindCaseSensitive;
            const haystack = caseSensitive ? text : text.toLowerCase();
            const needle = caseSensitive ? term : term.toLowerCase();
            const matches = [];
            let from = 0;
            while (from < haystack.length) {
                const idx = haystack.indexOf(needle, from);
                if (idx === -1) break;
                matches.push({ start: idx, end: idx + term.length });
                from = idx + Math.max(1, term.length);
            }
            this.codeReviewFindMatches = matches;
            // Leave index at -1 until the user presses Enter / Next / Prev
        },
        selectCodeReviewFindMatch(index) {
            const matches = this.codeReviewFindMatches || [];
            if (!matches.length || index < 0 || index >= matches.length) return;

            this.codeReviewFindIndex = index;
            const m = matches[index];
            const ta = this.$refs.codeReviewTextarea;
            if (!ta) return;

            // Browsers only paint the selection highlight while the textarea
            // has focus. Focus it, select the match, then scroll into view.
            // User can press Ctrl+F (or click the Find box) to return to typing.
            try {
                ta.focus();
                ta.setSelectionRange(m.start, m.end);
            } catch (e) {
                // ignore
            }

            // Accurate scroll: mirror the textarea styles into a hidden div,
            // measure the pixel offset of the match, and center it in view.
            // Fixes cases where the match was selected but still outside the
            // visible area (user had to scroll a little to see the highlight).
            try {
                this.scrollTextareaToOffset(ta, m.start, m.end);
            } catch (e) {
                // ignore scroll errors
            }

            // Re-apply selection on next tick (some browsers clear it during scroll)
            this.$nextTick(() => {
                try {
                    if (document.activeElement === ta) {
                        ta.setSelectionRange(m.start, m.end);
                    }
                } catch (e2) { /* ignore */ }
            });
        },
        scrollTextareaToOffset(ta, start, end) {
            if (!ta) return;
            const text = ta.value || '';
            const style = window.getComputedStyle(ta);

            // Build a mirror that matches the textarea's typography & width
            const mirror = document.createElement('div');
            const props = [
                'boxSizing', 'width', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
                'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
                'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing',
                'textTransform', 'wordSpacing', 'textIndent', 'lineHeight',
                'whiteSpace', 'wordWrap', 'wordBreak', 'overflowWrap'
            ];
            mirror.style.position = 'absolute';
            mirror.style.top = '0';
            mirror.style.left = '-9999px';
            mirror.style.visibility = 'hidden';
            mirror.style.height = 'auto';
            mirror.style.overflow = 'hidden';
            mirror.style.whiteSpace = 'pre-wrap';
            mirror.style.wordWrap = 'break-word';
            // Match content width (clientWidth already excludes scrollbar)
            mirror.style.width = ta.clientWidth + 'px';
            props.forEach((p) => {
                try { mirror.style[p] = style[p]; } catch (e) { /* ignore */ }
            });
            // Force pre-wrap so long lines wrap the same way as the textarea
            if (!mirror.style.whiteSpace || mirror.style.whiteSpace === 'normal') {
                mirror.style.whiteSpace = 'pre-wrap';
            }

            // Text before the match + a marker span at the match position
            const before = text.substring(0, start);
            const matchText = text.substring(start, Math.max(start, end));
            const marker = document.createElement('span');
            marker.textContent = matchText || '.';
            mirror.textContent = before;
            mirror.appendChild(marker);
            document.body.appendChild(mirror);

            const markerTop = marker.offsetTop;
            const markerHeight = marker.offsetHeight || parseFloat(style.lineHeight) || 16;
            document.body.removeChild(mirror);

            // Place the match roughly 1/3 down the visible area so context
            // above and below is visible, and it is never clipped.
            const viewH = ta.clientHeight;
            const target = Math.max(0, markerTop - Math.floor(viewH / 3));
            const maxScroll = Math.max(0, ta.scrollHeight - viewH);
            ta.scrollTop = Math.min(target, maxScroll);

            // If after scrolling the marker would still be below the fold
            // (e.g. very tall wrapped match), nudge so its top is visible.
            if (markerTop + markerHeight > ta.scrollTop + viewH) {
                ta.scrollTop = Math.min(Math.max(0, markerTop - 8), maxScroll);
            }
        },
        codeReviewFindNext() {
            const n = (this.codeReviewFindMatches || []).length;
            if (!n) return;
            // First navigation lands on match 0; later ones advance
            const next = this.codeReviewFindIndex < 0 ? 0 : (this.codeReviewFindIndex + 1) % n;
            this.selectCodeReviewFindMatch(next);
        },
        codeReviewFindPrev() {
            const n = (this.codeReviewFindMatches || []).length;
            if (!n) return;
            const prev = this.codeReviewFindIndex < 0 ? (n - 1) : (this.codeReviewFindIndex - 1 + n) % n;
            this.selectCodeReviewFindMatch(prev);
        },
        onCodeReviewFindKeydown(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                if (event.shiftKey) {
                    this.codeReviewFindPrev();
                } else {
                    this.codeReviewFindNext();
                }
            } else if (event.key === 'Escape') {
                event.preventDefault();
                this.clearCodeReviewFind();
                // return focus to the code box after clearing
                this.$nextTick(() => {
                    const ta = this.$refs.codeReviewTextarea;
                    if (ta) ta.focus();
                });
            } else if (event.key === 'F3') {
                event.preventDefault();
                if (event.shiftKey) this.codeReviewFindPrev();
                else this.codeReviewFindNext();
            }
        },
        onCodeReviewTextareaKeydown(event) {
            // Ctrl+F / Cmd+F focuses the find input instead of browser find
            if ((event.ctrlKey || event.metaKey) && (event.key === 'f' || event.key === 'F')) {
                event.preventDefault();
                const input = this.$refs.codeReviewFindInput;
                if (input) {
                    input.focus();
                    input.select();
                }
                return;
            }
            // While a find is active, Enter / Shift+Enter / F3 navigate matches
            // so you can keep jumping without going back to the Find box.
            const hasMatches = (this.codeReviewFindMatches || []).length > 0;
            if (hasMatches && event.key === 'Enter') {
                event.preventDefault();
                if (event.shiftKey) this.codeReviewFindPrev();
                else this.codeReviewFindNext();
                return;
            }
            if (event.key === 'F3') {
                event.preventDefault();
                if (event.shiftKey) this.codeReviewFindPrev();
                else this.codeReviewFindNext();
                return;
            }
            if (event.key === 'Escape' && (this.codeReviewFindTerm || '').trim()) {
                event.preventDefault();
                this.clearCodeReviewFind();
            }
        },
        // ---- end in-place Find helpers ----

        handleCodeReviewFile(file) {
            if (!file) {
                this.codeReviewForm.uploadedFile = null;
                this.codeReviewSections = [];
                this.codeReviewSectionsSourceText = '';
                this.clearCodeReviewFind();
                return;
            }

            this.codeReviewForm.uploadedFile = file;
            this.codeReviewSections = [];
            this.codeReviewSectionsSourceText = '';
            this.clearCodeReviewFind();
            const name = file.name || '';
            const lowerName = name.toLowerCase();
            const ext = lowerName.includes('.') ? lowerName.substring(lowerName.lastIndexOf('.') + 1) : '';

            // Auto-set language based on extension when reasonable
            if (ext === 'js' || ext === 'ts') {
                this.codeReviewForm.language = 'javascript';
            } else if (ext === 'sh') {
                this.codeReviewForm.language = 'bash';
            } else if (ext === 'sql') {
                this.codeReviewForm.language = 'sql';
            } else if (ext === 'html' || ext === 'htm') {
                this.codeReviewForm.language = 'html';
            }

            // For non-zip files, load contents into the text area
            if (ext !== 'zip') {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const text = (e.target && e.target.result) || '';
                    this.codeReviewForm.codeSnippet = typeof text === 'string' ? text : '';
                    this.clearCodeReviewFind();
                };
                reader.readAsText(file);
            } else {
                // Zip projects can be large; leave textarea empty and route to zip endpoint
                this.codeReviewForm.codeSnippet = '';
                this.clearCodeReviewFind();
            }
        },
        handleFileUpload(event) {
            const file = event.target.files && event.target.files[0];
            this.handleCodeReviewFile(file);
        },
        focusCodeReviewSection() {
            const fullText = this.codeReviewForm.codeSnippet || '';
            const term = (this.codeReviewSearchTerm || '').trim();

            if (!fullText.trim()) {
                alert('No code loaded yet. Upload a file or paste code first.');
                return;
            }
            if (!term) {
                alert('Enter a search term to focus on (e.g., a component name or heading).');
                return;
            }

            const lowerText = fullText.toLowerCase();
            const lowerTerm = term.toLowerCase();
            const idx = lowerText.indexOf(lowerTerm);

            if (idx === -1) {
                alert(`The term "${term}" was not found in the current code.`);
                return;
            }

            const radius = 2000; // characters of context around the match
            const start = Math.max(0, idx - radius);
            const end = Math.min(fullText.length, idx + radius);
            const snippet = fullText.slice(start, end);

            this.codeReviewForm.codeSnippet = snippet;
            this.clearCodeReviewFind();
            alert('Focused on a smaller section around the first match. You can refine further or run the review now.');
        },
        formatSectionKind(kind) {
            const map = {
                'vue-block': 'Major blocks',
                tab: 'Screens',
                class: 'Classes',
                function: 'Actions',
                method: 'Actions',
                computed: 'Computed values',
                data: 'Data',
                hook: 'Startup hooks',
                region: 'Regions',
                section: 'Other'
            };
            return map[(kind || '').toLowerCase()] || (kind || 'Other');
        },
        /** True when a section is something a user would actually want to edit / review. */
        sectionDoesStuff(section) {
            if (!section) return false;
            const kind = (section.kind || '').toLowerCase();
            const name = (section.name || '').toString();
            const nameL = name.toLowerCase();
            const lines = section.line_count
                || (section.start_line && section.end_line
                    ? (section.end_line - section.start_line + 1) : 0);

            // Screens / tab regions always count
            if (kind === 'tab') return true;

            // Whole Vue option blocks that are big enough
            if (kind === 'vue-block' || kind === 'data' || kind === 'computed') {
                return lines >= 8;
            }

            // Classes always (they're structural)
            if (kind === 'class') return lines >= 10;

            // Lifecycle hooks only if substantial
            if (kind === 'hook') return lines >= 15;

            // Methods / functions: prefer ones that act (verbs) or are large
            if (kind === 'method' || kind === 'function' || kind === 'section') {
                if (this.looksLikeActionName(nameL)) return true;
                if (lines >= 20) return true; // large enough to matter even without verb
                return false;
            }

            return lines >= 15;
        },
        looksLikeActionName(nameL) {
            if (!nameL) return false;
            // Common action prefixes / verbs
            const verbs = [
                'handle', 'on', 'submit', 'load', 'save', 'delete', 'remove', 'add',
                'create', 'update', 'send', 'fetch', 'run', 'start', 'stop', 'open',
                'close', 'toggle', 'clear', 'reset', 'detect', 'focus', 'build',
                'parse', 'format', 'copy', 'paste', 'upload', 'download', 'import',
                'export', 'search', 'find', 'filter', 'sort', 'select', 'navigate',
                'switch', 'show', 'hide', 'render', 'process', 'analyze', 'review',
                'aggregate', 'extract', 'scan', 'validate', 'generate', 'refresh'
            ];
            for (const v of verbs) {
                if (nameL.startsWith(v)) return true;
            }
            // CamelCase contains an action word as a segment
            for (const v of verbs) {
                if (nameL.includes(v) && v.length >= 4) return true;
            }
            return false;
        },
        sectionImportanceScore(section) {
            const kind = (section.kind || '').toLowerCase();
            const nameL = (section.name || '').toString().toLowerCase();
            const lines = section.line_count || 0;
            let score = Math.min(lines, 80); // size helps
            if (kind === 'tab') score += 40;
            if (kind === 'vue-block') score += 25;
            if (kind === 'class') score += 20;
            if (this.looksLikeActionName(nameL)) score += 30;
            // Boost well-known feature areas in this app
            if (nameL.includes('codereview') || nameL.includes('code-review')) score += 25;
            if (nameL.includes('find')) score += 15;
            if (nameL.includes('section') || nameL.includes('detect') || nameL.includes('focus')) score += 20;
            if (nameL.includes('upload') || nameL.includes('folder') || nameL.includes('file')) score += 12;
            if (nameL.includes('submit') || nameL.includes('review')) score += 10;
            // Penalize pure event passthroughs
            if (/^on[a-z]+$/.test(nameL) && lines < 12) score -= 20;
            return score;
        },
        inferFeatureArea(section) {
            const kind = (section.kind || '').toLowerCase();
            const nameL = (section.name || '').toString().toLowerCase();
            const labelL = (section.label || '').toString().toLowerCase();
            const blob = nameL + ' ' + labelL;

            if (kind === 'tab') return 'Screens';
            if (kind === 'vue-block' || kind === 'data' || kind === 'computed') return 'Major blocks';

            if (blob.includes('find') && (blob.includes('code') || blob.includes('match') || blob.includes('search'))) {
                return 'Find in code';
            }
            if (blob.includes('codereview') || blob.includes('code-review') || blob.includes('code review')
                || blob.includes('section') || blob.includes('detect') || blob.includes('focus')) {
                return 'Code Review';
            }
            if (blob.includes('upload') || blob.includes('folder') || blob.includes('file')
                || blob.includes('drop') || blob.includes('drag')) {
                return 'Files & upload';
            }
            if (blob.includes('submit') || blob.includes('review') || blob.includes('ollama')
                || blob.includes('model')) {
                return 'AI review';
            }
            return 'Other features';
        },
        /** Turn camelCase / snake_case into a short plain-English title. */
        humanizeName(name) {
            if (!name) return '';
            let s = String(name);
            // tab names like code-review
            s = s.replace(/[-_]+/g, ' ');
            // camelCase / PascalCase → spaces
            s = s.replace(/([a-z0-9])([A-Z])/g, '$1 $2');
            s = s.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
            s = s.replace(/\s+/g, ' ').trim();
            // Lowercase then capitalize first letter of each word for readability
            s = s.split(' ').map(w => {
                if (!w) return w;
                // Keep short acronyms
                if (w.length <= 2) return w.toUpperCase();
                return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
            }).join(' ');
            return s;
        },
        /** Known friendly titles for this app's important pieces. */
        friendlyTitleFor(section) {
            const name = (section.name || '').toString();
            const nameL = name.toLowerCase();
            const kind = (section.kind || '').toLowerCase();

            // Explicit map for the features people actually edit
            const known = {
                'focuscodereviewonsection': 'Focus on a detected section',
                'focuscodereviewsection': 'Focus on section (by search term)',
                'detectcodereviewsections': 'Detect features / sections',
                'buildlocalcodereviewsections': 'Build the section outline',
                'submitcodereview': 'Submit code for AI review',
                'handlecodereviewfile': 'Handle an uploaded code file',
                'handlefolderupload': 'Upload a whole folder',
                'runcodereviewfind': 'Find text in the code box',
                'selectcodereviewfindmatch': 'Highlight a find match',
                'clearcodereviewfind': 'Clear the find bar',
                'codereviewfindnext': 'Go to next find match',
                'codereviewfindprev': 'Go to previous find match',
                'oncodereviewfindkeydown': 'Find bar keyboard shortcuts',
                'oncodereviewtextareakeydown': 'Code box keyboard shortcuts',
                'oncodereviewdragover': 'Drag-over on code box',
                'oncodereviewdragleave': 'Drag-leave on code box',
                'oncodereviewdrop': 'Drop a file onto code box',
                'copycodereviewtoclipboard': 'Copy review result',
                'loadcodereviews': 'Load past reviews',
                'loadfullcodereview': 'Open a past review'
            };
            if (known[nameL]) return known[nameL];

            if (kind === 'tab') {
                const nice = this.humanizeName(name);
                return 'Screen: ' + nice;
            }
            if (kind === 'vue-block' || kind === 'data' || kind === 'computed') {
                return 'Block: ' + this.humanizeName(name);
            }
            if (kind === 'class') {
                return 'Class: ' + this.humanizeName(name);
            }
            if (kind === 'hook') {
                return 'When the page ' + nameL;
            }

            // Generic: strip common noisy prefixes for a cleaner title
            let title = this.humanizeName(name);
            title = title.replace(/^(Handle|On|Do) /i, '');
            return title || (section.label || name || 'Section');
        },
        friendlyDescriptionFor(section) {
            const nameL = (section.name || '').toString().toLowerCase();
            const kind = (section.kind || '').toLowerCase();
            const lines = section.line_count || 0;

            if (kind === 'tab') {
                return 'The UI markup for this screen/tab. Click to edit just this view.';
            }
            if (nameL.includes('detect') && nameL.includes('section')) {
                return 'Builds the list of features you can jump to. Click to edit this logic.';
            }
            if (nameL.includes('focus') && nameL.includes('section')) {
                return 'Loads one section into the editor so you can review or change it.';
            }
            if (nameL.includes('find') && (nameL.includes('code') || nameL.includes('match'))) {
                return 'Powers the Find bar above the code box (search, next, previous).';
            }
            if (nameL.includes('submit') && nameL.includes('review')) {
                return 'Sends the current code to the AI model for review.';
            }
            if (nameL.includes('upload') || nameL.includes('folder') || nameL.includes('file')) {
                return 'Handles files or folders you drop or pick for review.';
            }
            if (kind === 'vue-block' && nameL === 'methods') {
                return 'All interactive actions for this page (large block).';
            }
            if (kind === 'vue-block' || kind === 'data' || kind === 'computed') {
                return 'A major Vue block in this file.';
            }
            if (lines) {
                return lines + ' lines · click to load only this part into “Code to Review”.';
            }
            return 'Click to load only this part into “Code to Review”.';
        },
        /** Attach friendly title / description / feature area onto each section. */
        enrichSectionsWithFriendlyLabels(sections) {
            return (sections || []).map(s => {
                const feature = this.inferFeatureArea(s);
                const title = this.friendlyTitleFor(s);
                const description = this.friendlyDescriptionFor(s);
                return Object.assign({}, s, {
                    feature,
                    title,
                    description,
                    // Keep label for search, but prefer title in the UI
                    label: title || s.label
                });
            });
        },
        /**
         * Smart local outline — Vue/JS/HTML aware.
         * Returns sections with: id, kind, name, label, start_line, end_line,
         * line_count, preview, content (full text of the section).
         */
        buildLocalCodeReviewSections(code, language) {
            const text = code || '';
            if (!text.trim()) return [];

            const lines = text.split(/\r?\n/);
            const sections = [];
            let idSeq = 0;
            const pushSection = (partial) => {
                const start = Math.max(1, partial.start_line || 1);
                const end = Math.max(start, partial.end_line || start);
                const slice = lines.slice(start - 1, end).join('\n');
                const lineCount = end - start + 1;
                const preview = (partial.preview || slice).replace(/\s+/g, ' ').trim().slice(0, 140);
                sections.push({
                    id: 'local-' + (++idSeq),
                    kind: partial.kind || 'section',
                    name: partial.name || '',
                    label: partial.label || '',
                    start_line: start,
                    end_line: end,
                    line_count: lineCount,
                    preview,
                    content: slice
                });
            };

            // --- helpers: rough brace matcher (skips strings / line comments) ---
            const findMatchingBrace = (src, openIdx) => {
                if (openIdx < 0 || openIdx >= src.length || src[openIdx] !== '{') return -1;
                let depth = 0;
                let i = openIdx;
                let inS = false, inD = false, inT = false, inLineComment = false;
                while (i < src.length) {
                    const ch = src[i];
                    const next = src[i + 1];
                    if (inLineComment) {
                        if (ch === '\n') inLineComment = false;
                        i++;
                        continue;
                    }
                    if (inS) {
                        if (ch === '\\') { i += 2; continue; }
                        if (ch === "'") inS = false;
                        i++; continue;
                    }
                    if (inD) {
                        if (ch === '\\') { i += 2; continue; }
                        if (ch === '"') inD = false;
                        i++; continue;
                    }
                    if (inT) {
                        if (ch === '\\') { i += 2; continue; }
                        if (ch === '`') inT = false;
                        i++; continue;
                    }
                    if (ch === '/' && next === '/') { inLineComment = true; i += 2; continue; }
                    if (ch === "'") { inS = true; i++; continue; }
                    if (ch === '"') { inD = true; i++; continue; }
                    if (ch === '`') { inT = true; i++; continue; }
                    if (ch === '{') depth++;
                    else if (ch === '}') {
                        depth--;
                        if (depth === 0) return i;
                    }
                    i++;
                }
                return -1;
            };

            const lineAt = (idx) => {
                // 1-based line number for character index
                let n = 1;
                for (let k = 0; k < idx && k < text.length; k++) {
                    if (text[k] === '\n') n++;
                }
                return n;
            };

            const lang = (language || '').toLowerCase();
            const isJsFamily = !lang || ['javascript', 'js', 'html', 'vue', 'typescript', 'ts'].includes(lang);

            // ---------- Vue major option blocks ----------
            if (isJsFamily) {
                const vueBlockRe = /(?:^|[\s;,])((?:async\s+)?(?:data|computed|methods|watch|components|filters|directives|mixins|setup))\s*(\(|:)/gm;
                let m;
                const seenBlocks = new Set();
                while ((m = vueBlockRe.exec(text)) !== null) {
                    const name = m[1].replace(/^async\s+/, '');
                    if (seenBlocks.has(name)) continue;
                    // Find the opening { after the match
                    let searchFrom = m.index + m[0].length - 1;
                    // data() {  or  data: function() {  or  methods: {
                    let open = -1;
                    for (let j = searchFrom; j < Math.min(text.length, searchFrom + 80); j++) {
                        if (text[j] === '{') { open = j; break; }
                        if (text[j] === ';') break;
                    }
                    if (open < 0) continue;
                    const close = findMatchingBrace(text, open);
                    if (close < 0) continue;
                    seenBlocks.add(name);
                    const startLine = lineAt(open);
                    const endLine = lineAt(close);
                    pushSection({
                        kind: name === 'methods' ? 'vue-block'
                            : name === 'computed' ? 'computed'
                            : name === 'data' ? 'data'
                            : 'vue-block',
                        name,
                        label: 'Vue · ' + name,
                        start_line: startLine,
                        end_line: endLine
                    });

                    // Inside methods: { ... } extract individual methods
                    if (name === 'methods' || name === 'computed') {
                        const body = text.slice(open + 1, close);
                        const bodyOffset = open + 1;
                        // methodName(...) {  or  methodName: function (...) {  or  methodName: (...) =>
                        const methodRe = /(?:^|[\n\r,{])\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*(?:\(|:\s*(?:async\s+)?function\s*\(|:\s*(?:async\s*)?\()/g;
                        let mm;
                        while ((mm = methodRe.exec(body)) !== null) {
                            const methodName = mm[1];
                            if (['function', 'return', 'if', 'for', 'while', 'switch', 'catch', 'try'].includes(methodName)) continue;
                            // Find { after this match within a short window
                            const abs = bodyOffset + mm.index;
                            let mOpen = -1;
                            for (let j = abs; j < Math.min(text.length, abs + 120); j++) {
                                if (text[j] === '{') { mOpen = j; break; }
                                // arrow with concise body — skip
                                if (text[j] === '}' || text[j] === ';') break;
                            }
                            if (mOpen < 0) continue;
                            const mClose = findMatchingBrace(text, mOpen);
                            if (mClose < 0) continue;
                            pushSection({
                                kind: name === 'computed' ? 'computed' : 'method',
                                name: methodName,
                                label: name + '.' + methodName,
                                start_line: lineAt(mOpen),
                                end_line: lineAt(mClose)
                            });
                        }
                    }
                }

                // Lifecycle hooks: mounted() {, created() {, etc.
                const hookRe = /(?:^|[\s;,])((?:async\s+)?(?:beforeCreate|created|beforeMount|mounted|beforeUpdate|updated|beforeDestroy|destroyed|beforeUnmount|unmounted|activated|deactivated|errorCaptured|renderTracked|renderTriggered|serverPrefetch))\s*\(/gm;
                let h;
                const seenHooks = new Set();
                while ((h = hookRe.exec(text)) !== null) {
                    const hookName = h[1].replace(/^async\s+/, '');
                    if (seenHooks.has(hookName)) continue;
                    let open = -1;
                    for (let j = h.index; j < Math.min(text.length, h.index + 100); j++) {
                        if (text[j] === '{') { open = j; break; }
                    }
                    if (open < 0) continue;
                    const close = findMatchingBrace(text, open);
                    if (close < 0) continue;
                    seenHooks.add(hookName);
                    pushSection({
                        kind: 'hook',
                        name: hookName,
                        label: 'hook · ' + hookName,
                        start_line: lineAt(open),
                        end_line: lineAt(close)
                    });
                }

                // Top-level functions & classes (outside the Vue methods we already captured)
                const fnRe = /(?:^|[\n\r])\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(/g;
                let f;
                while ((f = fnRe.exec(text)) !== null) {
                    let open = -1;
                    for (let j = f.index; j < Math.min(text.length, f.index + 200); j++) {
                        if (text[j] === '{') { open = j; break; }
                    }
                    if (open < 0) continue;
                    const close = findMatchingBrace(text, open);
                    if (close < 0) continue;
                    // Skip if this range is already fully inside an existing method section
                    const sLine = lineAt(open);
                    const eLine = lineAt(close);
                    const already = sections.some(s =>
                        (s.kind === 'method' || s.kind === 'function') &&
                        s.start_line <= sLine && s.end_line >= eLine
                    );
                    if (already) continue;
                    pushSection({
                        kind: 'function',
                        name: f[1],
                        label: 'function ' + f[1],
                        start_line: sLine,
                        end_line: eLine
                    });
                }

                const classRe = /(?:^|[\n\r])\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)/g;
                let c;
                while ((c = classRe.exec(text)) !== null) {
                    let open = -1;
                    for (let j = c.index; j < Math.min(text.length, c.index + 200); j++) {
                        if (text[j] === '{') { open = j; break; }
                    }
                    if (open < 0) continue;
                    const close = findMatchingBrace(text, open);
                    if (close < 0) continue;
                    pushSection({
                        kind: 'class',
                        name: c[1],
                        label: 'class ' + c[1],
                        start_line: lineAt(open),
                        end_line: lineAt(close)
                    });
                }

                // HTML tab regions: v-if="currentTab === 'xxx'"
                const tabRe = /v-if\s*=\s*["']currentTab\s*===\s*['"]([^'"]+)['"]["']/g;
                const tabHits = [];
                let t;
                while ((t = tabRe.exec(text)) !== null) {
                    tabHits.push({ name: t[1], index: t.index, line: lineAt(t.index) });
                }
                for (let i = 0; i < tabHits.length; i++) {
                    const startLine = tabHits[i].line;
                    // End at next tab marker or a reasonable window / file end
                    let endLine = i + 1 < tabHits.length
                        ? Math.max(startLine, tabHits[i + 1].line - 1)
                        : Math.min(lines.length, startLine + 400);
                    // Don't let a tab region be absurdly large if it's the last one
                    if (endLine - startLine > 800) endLine = startLine + 800;
                    pushSection({
                        kind: 'tab',
                        name: tabHits[i].name,
                        label: 'tab · ' + tabHits[i].name,
                        start_line: startLine,
                        end_line: endLine
                    });
                }
            }

            // ---------- Python: def / class by indentation ----------
            if (lang === 'python' || lang === 'py') {
                const pyRe = /^( *)(async\s+)?(def|class)\s+([A-Za-z_][\w]*)/gm;
                const hits = [];
                let p;
                while ((p = pyRe.exec(text)) !== null) {
                    hits.push({
                        indent: p[1].length,
                        kind: p[3] === 'class' ? 'class' : 'function',
                        name: p[4],
                        line: lineAt(p.index)
                    });
                }
                for (let i = 0; i < hits.length; i++) {
                    const h = hits[i];
                    let endLine = lines.length;
                    for (let j = i + 1; j < hits.length; j++) {
                        if (hits[j].indent <= h.indent) {
                            endLine = hits[j].line - 1;
                            break;
                        }
                    }
                    // Trim trailing blank lines
                    while (endLine > h.line && !(lines[endLine - 1] || '').trim()) endLine--;
                    pushSection({
                        kind: h.kind,
                        name: h.name,
                        label: (h.kind === 'class' ? 'class ' : 'def ') + h.name,
                        start_line: h.line,
                        end_line: Math.max(h.line, endLine)
                    });
                }
            }

            // Sort by start line, then larger blocks first for same start
            sections.sort((a, b) => {
                if (a.start_line !== b.start_line) return a.start_line - b.start_line;
                return (b.line_count || 0) - (a.line_count || 0);
            });

            return sections;
        },
        async detectCodeReviewSections() {
            const fullText = this.codeReviewForm.codeSnippet || '';
            if (!fullText.trim()) {
                alert('No code loaded yet. Upload a file, paste code, or aggregate a folder first.');
                return;
            }

            this.codeReviewSectionsBusy = true;
            this.codeReviewSectionsSourceText = fullText;
            try {
                let sections = [];

                if (this.codeReviewSectionsUseLocal) {
                    sections = this.buildLocalCodeReviewSections(fullText, this.codeReviewForm.language);
                }

                // Optionally enrich / replace with backend if local found nothing or user disabled local
                if (!this.codeReviewSectionsUseLocal || sections.length === 0) {
                    try {
                        const res = await axios.post(this.apiUrl + '/code-review/sections', {
                            code_snippet: fullText,
                            language: this.codeReviewForm.language
                        });
                        const backendSections = (res.data && res.data.sections) || [];
                        if (backendSections.length) {
                            // Normalize backend shape
                            sections = backendSections.map((s, idx) => ({
                                id: s.id || ('be-' + idx),
                                kind: s.kind || 'section',
                                name: s.name || '',
                                label: s.label || '',
                                start_line: s.start_line,
                                end_line: s.end_line,
                                line_count: s.line_count || (s.start_line && s.end_line ? s.end_line - s.start_line + 1 : 0),
                                preview: s.preview || '',
                                content: s.content || s.preview || ''
                            }));
                        }
                    } catch (beErr) {
                        if (!sections.length) {
                            console.error('Backend section detect failed:', beErr);
                        }
                    }
                }

                // Attach plain-English titles / feature areas for a friendlier list
                this.codeReviewSections = this.enrichSectionsWithFriendlyLabels(sections);
                if (!sections.length) {
                    alert('No discrete sections detected. Try another language setting or use the search-term focus.');
                }
            } catch (err) {
                console.error('Failed to detect code sections:', err);
                alert('Error detecting sections: ' + (err.response?.data?.detail || err.message));
            } finally {
                this.codeReviewSectionsBusy = false;
            }
        },
        focusCodeReviewOnSection(section) {
            if (!section) return;

            const src = this.codeReviewSectionsSourceText || this.codeReviewForm.codeSnippet || '';
            let text = section.content || '';

            // Prefer exact line slice from the snapshot taken at detect time
            if ((!text || text.length < 20) && section.start_line && section.end_line && src) {
                const lines = src.split(/\r?\n/);
                text = lines.slice(section.start_line - 1, section.end_line).join('\n');
            }
            if (!text) {
                text = section.preview || '';
            }
            if (!text) return;

            this.codeReviewForm.codeSnippet = text;
            this.clearCodeReviewFind();
            // Quiet success — the snippet change is visible in the box
            console.info('Focused on: ' + (section.title || section.name || section.kind));
        },
        _focusCodeReviewOnSection_legacyLog(section) {
            // kept only so older references don't break if any
            const kind = section.kind || 'section';
            const name = section.name ? ' "' + section.name + '"' : '';
            const range = (section.start_line && section.end_line)
                ? ' (L' + section.start_line + '–' + section.end_line + ')'
                : '';
            // Quiet success — no blocking alert; the snippet change is visible
            console.info('Focused on ' + kind + name + range);
        },
        async handleFolderUpload(event) {
            const fileList = event.target.files || [];
            const files = Array.from(fileList);
            if (!files.length) {
                return;
            }

            const allowedExts = [
                '.py', '.js', '.ts', '.go', '.sh', '.sql',
                '.java', '.cpp', '.c', '.html', '.htm', '.css', '.json', '.md'
            ];
            const excludedPaths = [
                'node_modules/', 'venv/', 'env/', '__pycache__/', '.git/', 'dist/', 'build/'
            ];
            const maxPerFileChars = 2000;
            const maxTotalChars = 20000;

            let totalChars = 0;
            const chunks = [];

            const sorted = files.slice().sort((a, b) => {
                const pa = (a.webkitRelativePath || a.name || '').toLowerCase();
                const pb = (b.webkitRelativePath || b.name || '').toLowerCase();
                return pa.localeCompare(pb);
            });

            for (const file of sorted) {
                const rel = file.webkitRelativePath || file.name || '';
                const lower = rel.toLowerCase();

                if (excludedPaths.some(excl => lower.includes(excl))) {
                    continue;
                }
                if (!allowedExts.some(ext => lower.endsWith(ext))) {
                    continue;
                }

                const text = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        const result = (e && e.target && e.target.result) || '';
                        resolve(typeof result === 'string' ? result : '');
                    };
                    reader.onerror = reject;
                    reader.readAsText(file);
                }).catch(() => '');

                if (!text || !text.trim()) {
                    continue;
                }

                const snippet = text.slice(0, maxPerFileChars);
                const chunk = `File: ${rel}\n` + snippet.trim() + '\n\n';

                if (totalChars + chunk.length > maxTotalChars) {
                    break;
                }

                chunks.push(chunk);
                totalChars += chunk.length;
            }

            if (!chunks.length) {
                alert('No supported text/code files found in selected folder');
                return;
            }

            this.codeReviewForm.codeSnippet = chunks.join('\n');
            this.codeReviewForm.uploadedFile = null;
            this.codeReviewSections = [];
            this.codeReviewSectionsSourceText = '';
            this.clearCodeReviewFind();
        },
        onCodeReviewDragOver(event) {
            if (!event.dataTransfer) {
                return;
            }
            event.dataTransfer.dropEffect = 'copy';
            this.codeReviewDragActive = true;
        },
        onCodeReviewDragLeave() {
            this.codeReviewDragActive = false;
        },
        onCodeReviewDrop(event) {
            this.codeReviewDragActive = false;
            const dt = event.dataTransfer;
            if (!dt || !dt.files || dt.files.length === 0) {
                return;
            }
            const file = dt.files[0];
            this.handleCodeReviewFile(file);
        },
        async submitCodeReview() {
            const hasText = this.codeReviewForm.codeSnippet && this.codeReviewForm.codeSnippet.trim().length > 0;
            const file = this.codeReviewForm.uploadedFile;
            const isZip = file && file.name && file.name.toLowerCase().endsWith('.zip');

            if (!hasText && !file) {
                alert('Please paste code or upload a file/project to review');
                return;
            }
            this.codeReviewRunning = true;
            try {
                let res;
                if (isZip) {
                    const formData = new FormData();
                    formData.append('file', file);
                    const url = this.apiUrl + `/code-review/zip?language=${encodeURIComponent(this.codeReviewForm.language)}&model=${encodeURIComponent(this.codeReviewForm.model)}&instructions=${encodeURIComponent(this.codeReviewForm.instructions || '')}`;
                    res = await axios.post(url, formData, {
                        headers: { 'Content-Type': 'multipart/form-data' }
                    });
                } else {
                    res = await axios.post(this.apiUrl + '/code-review', {
                        code_snippet: this.codeReviewForm.codeSnippet,
                        language: this.codeReviewForm.language,
                        model: this.codeReviewForm.model,
                        instructions: this.codeReviewForm.instructions
                    });
                }
                this.codeReviewResult = res.data;
                await this.loadCodeReviews();
            } catch (err) {
                alert('Error: ' + (err.response?.data?.detail || err.message));
            } finally {
                this.codeReviewRunning = false;
            }
        },
        async loadCodeReviews() {
            try {
                const res = await axios.get(this.apiUrl + '/code-reviews?limit=10');
                this.codeReviewsList = res.data || [];
            } catch (err) {
                console.error('Failed to load code reviews:', err);
            }
        },
        async loadFullCodeReview(reviewId) {
            try {
                const res = await axios.get(this.apiUrl + '/code-reviews/' + reviewId);
                this.codeReviewResult = res.data;
            } catch (err) {
                alert('Error loading review: ' + (err.response?.data?.detail || err.message));
            }
        },
        copyCodeReviewToClipboard() {
            if (!this.codeReviewResult || !this.codeReviewResult.review) {
                alert('No review to copy');
                return;
            }
            navigator.clipboard.writeText(this.codeReviewResult.review)
                .then(() => alert('Review copied to clipboard!'))
                .catch(err => alert('Failed to copy: ' + err.message));
        },

    };

    global.CodeReviewMethods = CodeReviewMethods;

})(typeof window !== 'undefined' ? window : globalThis);
