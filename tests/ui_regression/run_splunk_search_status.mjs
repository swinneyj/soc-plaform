/**
 * UI regression harness: runSplunkSearch inline status chips (no blocking alerts).
 *
 * Loads the real web/modules/analysis.js into a node:vm sandbox with a stubbed
 * axios/window/alert, drives runSplunkSearch through its states, and asserts:
 *   - the per-card splunkRunStatus chip transitions running -> complete | error
 *   - window.alert is NEVER called on any path (modal alerts wedge embedded
 *     webviews and steal focus mid-loop; live regression 2026-09-29)
 *   - successful runs still fill the per-card manual-results textarea payload
 *
 * Usage: node tests/ui_regression/run_splunk_search_status.mjs <scenario>
 * Scenarios: success | failure | no_case | empty_spl
 * Exit code 0 = all assertions held.
 */
import {
    assert,
    makeSandbox,
    makeComponent as makeComponentBase,
    loadModuleMethods,
    runScenarios,
} from './harness_core.mjs';

async function loadAnalysisMethods(ctx) {
    const methods = await loadModuleMethods(ctx, 'web/modules/analysis.js', 'AnalysisMethods');
    if (typeof methods.runSplunkSearch !== 'function') {
        assert(false, 'analysis.js did not expose AnalysisMethods.runSplunkSearch');
    }
    return methods;
}

function makeComponent(methods, overrides = {}) {
    const base = {
        apiUrl: '/api',
        analysisCaseId: 'MOCK-CASE-001',
        analysisRule: { rule_id: 'MOCK-RULE-001', supportive_queries: [] },
        // S7 unified per-card state: run status lives at
        // phase2CardState[key].runStatus; result text at .resultText.
        phase2CardState: {},
        investigationState: null,
        formatSplunkAutoSummary(data) {
            return '[auto-run via /api/splunk/search-one — ' + (data.result_status || '') +
                ' — ' + (data.row_count || 0) + ' rows — saved as splunk_auto evidence]';
        },
        getSupportiveKey: methods.getSupportiveKey
            ? methods.getSupportiveKey.bind(null)
            : (q) => (q && q.id != null) ? 'id:' + q.id
                : 'title:' + String((q && q.title) || '').toLowerCase().replace(/\s+/g, '_').slice(0, 64),
        getPhase2Key: methods.getPhase2Key
            ? methods.getPhase2Key.bind(null)
            : (q) => 'phase2:' + String((q && q.title) || '').toLowerCase().replace(/\s+/g, '_').slice(0, 64),
    };
    return makeComponentBase(methods, {
        base: { ...base, ...overrides },
        bind: ['runSplunkSearch', 'runAllSupportiveSplunk', '_setSplunkRunStatus'],
    });
}

const QUERY = { id: 7, title: 'Failed logins for user', spl_query: 'sourcetype=linux_secure action=failure user=bjones | stats count by host' };

const scenarios = {
    async success() {
        const calls = [];
        const { ctx, alertCalls } = makeSandbox({ post: async (url, body) => {
            calls.push({ url, body });
            return { data: { success: true, result_status: 'success', row_count: 1, rows: [{ host: 'VPN-GW-01', count: 6 }], spl: body.spl } };
        } });
        const methods = await loadAnalysisMethods(ctx);
        const comp = makeComponent(methods);
        await comp.runSplunkSearch({ q: QUERY, kind: 'supportive' });

        assert(calls.length === 1, 'expected exactly one search-one POST, got ' + calls.length);
        assert(calls[0].url.includes('/splunk/search-one'), 'POST url wrong: ' + calls[0].url);
        const st = comp.phase2CardState[comp.getSupportiveKey(QUERY)].runStatus;
        assert(st, 'no chip state written');
        assert(st.state === 'complete', 'expected final state "complete", got ' + st.state);
        assert(st.short === 'Saved ✓', 'expected Saved ✓ chip, got ' + st.short);
        assert(st.message.includes('1 rows') || st.message.includes('1 rows.'), 'message should include row count: ' + st.message);
        const filled = (comp.phase2CardState[comp.getSupportiveKey(QUERY)] || {}).resultText;
        assert(filled && filled.includes('splunk_auto'), 'textarea payload not filled with auto-run summary');
        assert(alertCalls.length === 0, 'window.alert was called: ' + JSON.stringify(alertCalls));
    },

    async failure() {
        const { ctx, alertCalls } = makeSandbox({ post: async () => {
            const err = new Error('Request failed with status code 502');
            err.response = { data: { detail: 'Splunk backend unavailable' } };
            throw err;
        } });
        const methods = await loadAnalysisMethods(ctx);
        const comp = makeComponent(methods);
        await comp.runSplunkSearch({ q: QUERY, kind: 'supportive' });

        const st = (comp.phase2CardState[comp.getSupportiveKey(QUERY)] || {}).runStatus;
        assert(st, 'no chip state written on failure path');
        assert(st.state === 'error', 'expected final state "error", got ' + st.state);
        assert(st.short === 'Run failed', 'expected Run failed chip, got ' + st.short);
        assert(st.message.includes('Splunk backend unavailable'), 'error detail not surfaced: ' + st.message);
        assert(alertCalls.length === 0, 'window.alert was called on failure path: ' + JSON.stringify(alertCalls));
    },

    async no_case() {
        const { ctx, alertCalls } = makeSandbox({ post: async () => {
            throw new Error('should not reach the network');
        } });
        const methods = await loadAnalysisMethods(ctx);
        const comp = makeComponent(methods, { analysisCaseId: '' });
        await comp.runSplunkSearch({ q: QUERY, kind: 'supportive' });

        const st = (comp.phase2CardState[comp.getSupportiveKey(QUERY)] || {}).runStatus;
        assert(st && st.state === 'error', 'no-case path should set an inline error chip');
        assert(alertCalls.length === 0, 'window.alert was called on no-case path: ' + JSON.stringify(alertCalls));
    },

    async empty_spl() {
        const { ctx, alertCalls } = makeSandbox({ post: async () => {
            throw new Error('should not reach the network');
        } });
        const methods = await loadAnalysisMethods(ctx);
        const comp = makeComponent(methods);
        await comp.runSplunkSearch({ q: { id: 8, title: 'Empty SPL card', spl_query: '   ' }, kind: 'supportive' });

        const st = (comp.phase2CardState[comp.getSupportiveKey({ id: 8, title: 'Empty SPL card', spl_query: '   ' })] || {}).runStatus;
        assert(st && st.state === 'error', 'empty-SPL path should set an inline error chip');
        assert(alertCalls.length === 0, 'window.alert was called on empty-SPL path: ' + JSON.stringify(alertCalls));
    },

    async running_to_complete_transitions() {
        // Observe the intermediate "running" state via a deferred axios stub.
        // The stub closes over `comp` (host scope), so it can read the live
        // status map mid-flight without any VM round-trips.
        let release;
        const gate = new Promise((res) => { release = res; });
        let comp;
        const during = [];
        const { ctx, alertCalls } = makeSandbox({ post: async () => {
            const current = (comp.phase2CardState[comp.getSupportiveKey(QUERY)] || {}).runStatus || null;
            during.push(current);
            await gate;
            return { data: { success: true, result_status: 'success', row_count: 0, rows: [] } };
        } });
        const methods = await loadAnalysisMethods(ctx);
        comp = makeComponent(methods);
        const pending = comp.runSplunkSearch({ q: QUERY, kind: 'supportive' });
        await new Promise((r) => setTimeout(r, 20));
        assert(during[0] && during[0].state === 'running' && during[0].short === 'Running…',
            'mid-flight chip should be Running…, got ' + JSON.stringify(during[0]));
        release();
        await pending;
        const after = comp.phase2CardState[comp.getSupportiveKey(QUERY)].runStatus;
        assert(after.state === 'complete' && after.short === 'Saved ✓',
            'final chip should be Saved ✓, got ' + JSON.stringify(after));
        assert(alertCalls.length === 0, 'window.alert was called during transitions: ' + JSON.stringify(alertCalls));
    },
};

runScenarios(scenarios, 'run_splunk_search_status.mjs');
