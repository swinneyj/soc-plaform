/**
 * utils/selection.js
 * Pure selection helpers for multi-select tables (notables, triage cases, etc.).
 *
 * No Vue dependency.
 *
 * Usage:
 *   Selection.areAllSelected(items, selectedIds, idFn)
 *   const next = Selection.toggleAll(items, selectedIds, checked, idFn)
 */
(function (global) {
    'use strict';

    function defaultId(item) {
        return item && (item.id != null ? item.id : item.case_id);
    }

    const Selection = {
        /**
         * @param {Array} items - currently visible / filtered rows
         * @param {Array} selectedIds - currently selected ids
         * @param {Function} [idFn] - extract id from an item (default: item.id || item.case_id)
         */
        areAllSelected(items, selectedIds, idFn) {
            const list = items || [];
            if (!list.length) return false;
            const getId = idFn || defaultId;
            const selected = selectedIds || [];
            return list.every(item => selected.includes(getId(item)));
        },

        /**
         * Returns the new selectedIds array (does not mutate).
         * @param {boolean} checked - true = select all visible, false = clear
         */
        toggleAll(items, selectedIds, checked, idFn) {
            const getId = idFn || defaultId;
            if (checked) {
                return (items || []).map(getId);
            }
            return [];
        },

        /** Add one id if missing. */
        add(selectedIds, id) {
            const set = new Set(selectedIds || []);
            set.add(id);
            return Array.from(set);
        },

        /** Remove one id. */
        remove(selectedIds, id) {
            return (selectedIds || []).filter(x => x !== id);
        },

        /** Toggle one id. */
        toggle(selectedIds, id) {
            const set = new Set(selectedIds || []);
            if (set.has(id)) set.delete(id);
            else set.add(id);
            return Array.from(set);
        }
    };

    global.Selection = Selection;

})(typeof window !== 'undefined' ? window : globalThis);
