/**
 * modules/database.js
 * Domain methods for the Database tab (triage, notables, paste/promote, bulk delete).
 *
 * Piece 2 status: structure only + thin wrappers around pure utils.
 * The full method bodies still live in app.js / the live index.html.
 * Later pieces will move the bodies here and switch app.js to:
 *
 *   methods: {
 *     ...window.DatabaseMethods,
 *     ...
 *   }
 *
 * When that happens, every DB-related method will be in this one file
 * for easier troubleshooting.
 */
(function (global) {
    'use strict';

    /**
     * Methods that will eventually live here (from current app.js):
     *
     * loadHistoricalNotables, loadTriageData, loadAnalysisCases, loadDbStats,
     * loadRecentNotables, savePastedNotable, deletePastedNotable,
     * deleteSelectedPastedNotables, promoteNotableToTriage,
     * promoteAllOpenPastedNotables, jumpToTriageCase, deleteTriageCase,
     * deleteSelectedTriageCases, loadTriageNotableDetails,
     * areAllPastedNotablesSelected, toggleSelectAllPastedNotables,
     * areAllTriageCasesSelected, toggleSelectAllTriageCases,
     * getNotablePocSummary, copyTriageNotableFields, ...
     */

    const DatabaseMethods = {
        // --- Pure / near-pure helpers (already usable) ---

        getNotablePocSummary(notable) {
            if (global.PocSummary && typeof global.PocSummary.fromNotable === 'function') {
                return global.PocSummary.fromNotable(notable);
            }
            // Fallback identical to current app.js behaviour
            const fields = (notable && (notable.fields || notable)) || {};
            const pocEntries = [];
            const addEntry = (label, value) => {
                if (!value) return;
                const cleaned = String(value).trim();
                if (!cleaned) return;
                pocEntries.push({ label, value: cleaned });
            };
            addEntry('Owner', fields.owner);
            return pocEntries;
        },

        areAllPastedNotablesSelected() {
            if (global.Selection) {
                return global.Selection.areAllSelected(
                    this.filteredRecentNotables,
                    this.selectedNotableIds,
                    n => n.id
                );
            }
            const items = this.filteredRecentNotables || [];
            if (!items.length) return false;
            return items.every(n => (this.selectedNotableIds || []).includes(n.id));
        },

        toggleSelectAllPastedNotables(event) {
            const checked = event && event.target ? event.target.checked : false;
            if (global.Selection) {
                this.selectedNotableIds = global.Selection.toggleAll(
                    this.filteredRecentNotables,
                    this.selectedNotableIds,
                    checked,
                    n => n.id
                );
                return;
            }
            if (checked) {
                this.selectedNotableIds = (this.filteredRecentNotables || []).map(n => n.id);
            } else {
                this.selectedNotableIds = [];
            }
        },

        areAllTriageCasesSelected() {
            if (global.Selection) {
                return global.Selection.areAllSelected(
                    this.filteredTriageData,
                    this.selectedTriageCaseIds,
                    c => c.case_id
                );
            }
            const items = this.filteredTriageData || [];
            if (!items.length) return false;
            return items.every(c => (this.selectedTriageCaseIds || []).includes(c.case_id));
        },

        toggleSelectAllTriageCases(event) {
            const checked = event && event.target ? event.target.checked : false;
            if (global.Selection) {
                this.selectedTriageCaseIds = global.Selection.toggleAll(
                    this.filteredTriageData,
                    this.selectedTriageCaseIds,
                    checked,
                    c => c.case_id
                );
                return;
            }
            if (checked) {
                this.selectedTriageCaseIds = (this.filteredTriageData || []).map(c => c.case_id);
            } else {
                this.selectedTriageCaseIds = [];
            }
        }

        // Full async methods (loadTriageData, savePastedNotable, promote..., delete...)
        // will be moved here in a later piece once the thin index is ready.
    };

    global.DatabaseMethods = DatabaseMethods;

})(typeof window !== 'undefined' ? window : globalThis);
