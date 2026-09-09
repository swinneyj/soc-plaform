/**
 * modules/analysis.js
 * Domain methods for the Analysis tab (Phase 1 / Phase 2, enrichment,
 * supportive queries, evidence, placeholder resolution).
 *
 * Piece 2 status: structure + pure key / render wrappers.
 * Full method bodies still live in app.js / live index.html.
 * Later: move bodies here and spread into Vue methods.
 */
(function (global) {
    'use strict';

    /**
     * Methods that will eventually live here:
     *
     * checkOllama, onAnalysisCaseChanged, runAnalysis, cancelAnalysis,
     * runPhase2Analysis, analyzeCase, loadAnalysisState, saveAnalysisState,
     * loadSavedSupportiveEvidence, loadSavedEnrichmentEvidence, loadSavedPhase2Evidence,
     * saveSupportiveEvidence, saveEnrichmentEvidence, savePhase2Evidence,
     * getSupportiveKey, getPhase2Key, getEnrichmentKey, *FromTitle variants,
     * getPhase2Template, onPhase2TemplateInput, renderSupportiveQuery, renderPhase2Query,
     * hasUnresolvedPlaceholders, copySupportiveSPL, copyPhase2SPL, copyEnrichmentSPL,
     * promotePhase2Query, goToClosureFromAnalysis, ...
     */

    const AnalysisMethods = {
        // --- Pure key helpers (delegate to utils/keys.js when present) ---

        getSupportiveKey(q) {
            if (global.QueryKeys) return global.QueryKeys.supportive(q);
            if (q && q.id != null) return 'id:' + q.id;
            const title = (q && q.title ? q.title : '').toString().toLowerCase().trim();
            if (!title) return 'title:unknown';
            return 'title:' + title.replace(/\s+/g, '_').slice(0, 64);
        },

        getSupportiveKeyFromTitle(title) {
            if (global.QueryKeys) return global.QueryKeys.supportiveFromTitle(title);
            const normalized = (title || '').toString().toLowerCase().trim();
            if (!normalized) return 'title:unknown';
            return 'title:' + normalized.replace(/\s+/g, '_').slice(0, 64);
        },

        getPhase2Key(q) {
            if (global.QueryKeys) return global.QueryKeys.phase2(q);
            const title = (q && q.title ? q.title : '').toString().toLowerCase().trim();
            if (!title) return 'phase2:title:unknown';
            return 'phase2:' + title.replace(/\s+/g, '_').slice(0, 64);
        },

        getPhase2KeyFromTitle(title) {
            if (global.QueryKeys) return global.QueryKeys.phase2FromTitle(title);
            const normalized = (title || '').toString().toLowerCase().trim();
            if (!normalized) return 'phase2:title:unknown';
            return 'phase2:' + normalized.replace(/\s+/g, '_').slice(0, 64);
        },

        getEnrichmentKey(q) {
            if (global.QueryKeys) return global.QueryKeys.enrichment(q);
            const title = (q && q.title ? q.title : '').toString().toLowerCase().trim();
            if (!title) return 'enrichment:unknown';
            return 'enrichment:' + title.replace(/\s+/g, '_').slice(0, 64);
        },

        getEnrichmentKeyFromTitle(title) {
            if (global.QueryKeys) return global.QueryKeys.enrichmentFromTitle(title);
            const normalized = (title || '').toString().toLowerCase().trim();
            if (!normalized) return 'enrichment:unknown';
            return 'enrichment:' + normalized.replace(/\s+/g, '_').slice(0, 64);
        },

        hasUnresolvedPlaceholders(text) {
            if (global.QueryRender) return global.QueryRender.hasUnresolved(text);
            return /\$[A-Za-z0-9_]+\$/.test((text || '').toString());
        },

        /**
         * Renders a supportive query with placeholder substitution.
         * Uses this.analysisSourceNotable and this.placeholderAliases when present.
         */
        renderSupportiveQuery(q) {
            if (global.QueryRender) {
                return global.QueryRender.renderSupportive(
                    q,
                    this.analysisSourceNotable,
                    this.placeholderAliases
                );
            }
            // Minimal fallback (no substitution)
            return (q && q.spl_query ? q.spl_query : '').toString();
        },

        renderPhase2Query(template) {
            if (global.QueryRender) {
                return global.QueryRender.renderPhase2(
                    template,
                    this.analysisSourceNotable,
                    this.placeholderAliases
                );
            }
            return (template || '').toString();
        }

        // Full async methods (runAnalysis, runPhase2Analysis, evidence save/load, etc.)
        // will be moved here in a later piece.
    };

    global.AnalysisMethods = AnalysisMethods;

})(typeof window !== 'undefined' ? window : globalThis);
