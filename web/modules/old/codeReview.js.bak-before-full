/**
 * modules/codeReview.js
 * Domain methods for the Code Review tab.
 *
 * Piece 2 status: structure + pure focus/filter helpers.
 * Full method bodies (submit, upload, detect sections via API) still in app.js.
 */
(function (global) {
    'use strict';

    /**
     * Methods that will eventually live here:
     *
     * handleCodeReviewFile, handleFolderUpload, onCodeReviewDrop/Drag*,
     * submitCodeReview, loadCodeReviews, loadFullCodeReview,
     * detectCodeReviewSections, focusCodeReviewSection, focusCodeReviewOnSection,
     * copyCodeReviewToClipboard, ...
     */

    const CodeReviewMethods = {
        /**
         * Focus the code snippet around a search term (pure util + state write).
         */
        focusCodeReviewSection() {
            const fullText = (this.codeReviewForm && this.codeReviewForm.codeSnippet) || '';
            const term = (this.codeReviewSearchTerm || '').trim();

            if (!fullText.trim()) {
                alert('No code loaded yet. Upload a file or paste code first.');
                return;
            }
            if (!term) {
                alert('Enter a search term to focus on (e.g., a component name or heading).');
                return;
            }

            if (global.CodeSections) {
                const result = global.CodeSections.focusAroundTerm(fullText, term, 2000);
                if (!result.found) {
                    alert('The term "' + term + '" was not found in the current code.');
                    return;
                }
                this.codeReviewForm.codeSnippet = result.snippet;
                alert('Focused on a smaller section around the first match. You can refine further or run the review now.');
                return;
            }

            // Fallback (same logic as current app.js)
            const lowerText = fullText.toLowerCase();
            const lowerTerm = term.toLowerCase();
            const idx = lowerText.indexOf(lowerTerm);
            if (idx === -1) {
                alert('The term "' + term + '" was not found in the current code.');
                return;
            }
            const radius = 2000;
            const start = Math.max(0, idx - radius);
            const end = Math.min(fullText.length, idx + radius);
            this.codeReviewForm.codeSnippet = fullText.slice(start, end);
            alert('Focused on a smaller section around the first match. You can refine further or run the review now.');
        },

        focusCodeReviewOnSection(section) {
            if (!section || !section.preview) return;
            this.codeReviewForm.codeSnippet = section.preview;
            const kind = section.kind || 'section';
            const name = section.name ? ' "' + section.name + '"' : '';
            alert('Focused on ' + kind + name + '. You can refine further or run the review now.');
        }

        // detectCodeReviewSections, submitCodeReview, handleFolderUpload, etc.
        // will be moved here in a later piece (they call API).
    };

    global.CodeReviewMethods = CodeReviewMethods;

})(typeof window !== 'undefined' ? window : globalThis);
