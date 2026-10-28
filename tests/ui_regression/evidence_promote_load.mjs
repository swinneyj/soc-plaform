/**
 * UI regression harness: evidence save, promote, and saved-evidence load flows.
 *
 * Loads the real web/modules/analysis.js and web/modules/database.js into a
 * node:vm sandbox (stubbed axios/window) and drives the production methods.
 * Locked down after two live UI regressions:
 *   - loadSavedPhase2Evidence crashed on removed phase2Resolution* state keys
 *     (fixed in 6942458) — the load path must hydrate surviving keys and never
 *     fall into its catch block, which swallows crashes into console.error;
 *   - promote/save flows must keep their payload contract and refresh fan-out.
 *
 * Canaries: window.alert (blocking modals), console.error (swallowed crashes),
 * console.warn (guarded aborts).
 *
 * S7 note: per-card analysis state is unified under phase2CardState
 * ({ resultText, findingType, editedSpl, coverage, status, runStatus }); the
 * fixtures seed that shape directly.
 *
 * Usage: node tests/ui_regression/evidence_promote_load.mjs <scenario>
 * Scenarios:
 *   save_supportive          save_phase2          save_busy_guard
 *   load_saved_evidence      load_saved_error     load_saved_no_case
 *   promote                  promote_historical_guard
 *   promote_failure          promote_all_open
 *   closure_readiness_punchlist   closure_blocked_generate_punchlist
 *   draft_autosave_debounce  evidence_ledger_view  paste_auto_promote
 *   loop_timeline_panel  snapshot_migration_folds_legacy_maps
 *   api_layer_canonical_fallback  stepper_guards  analyze_stage_models
 * Exit code 0 = all assertions held.
 */
import {
    assert,
    makeSandbox,
    makeComponent,
    makeCard,
    loadModuleMethods,
    runScenarios,
    ROOT,
} from './harness_core.mjs';
import { readFile as fsRead } from 'node:fs/promises';
import path from 'node:path';

async function loadApiLayer(ctx) {
    const api = await loadModuleMethods(ctx, 'web/modules/api.js', 'API');
    assert(typeof api.canonicalFor === 'function' && typeof api.triage === 'function',
        'api.js is missing the canonical-path service layer');
    return api;
}

/**
 * Load api.js into the sandbox and expose it as the `API` global for modules
 * that consume the service layer (M1+). The real api.js transport stays live:
 * scenario axios stubs see canonical-first URLs exactly as production dials.
 */
async function installApiGlobal(ctx) {
    const api = await loadApiLayer(ctx);
    ctx.API = api; // bare `API` lookups resolve against the vm context
    return api;
}

async function loadAnalysis(ctx) {
    await installApiGlobal(ctx); // M1: analysis.js resolves the API global
    const methods = await loadModuleMethods(ctx, 'web/modules/analysis.js', 'AnalysisMethods');
    assert(typeof methods.saveSupportiveEvidence === 'function'
        && typeof methods.savePhase2Evidence === 'function'
        && typeof methods.loadSavedPhase2Evidence === 'function',
        'analysis.js is missing one of the evidence flow methods');
    return methods;
}

async function loadDatabase(ctx) {
    await installApiGlobal(ctx); // M2: database.js resolves the API global
    const methods = await loadModuleMethods(ctx, 'web/modules/database.js', 'DatabaseMethods');
    assert(typeof methods.promoteNotableToTriage === 'function'
        && typeof methods.promoteAllOpenPastedNotables === 'function',
        'database.js is missing the promote flow methods');
    return methods;
}

// --- analysis-side component: post-Phase-4 state ONLY (no phase2Resolution*
// keys — that absence is exactly what the load-path crash regression is about).
// Since S7 all per-card fields live in the unified phase2CardState map:
//   { resultText, findingType, editedSpl, coverage, status, runStatus }.
function analysisComp(methods, overrides = {}, spies = {}) {
    const base = {
        apiUrl: '/api',
        analysisCaseId: 'MOCK-CASE-001',
        followUpPhase: 2,
        analysisRule: { rule_id: 'MOCK-RULE-001', supportive_queries: [] },
        analysisSourceNotable: null,
        placeholderAliases: {},
        supportiveSaveBusy: false,
        phase2CardState: {},
        phase2Result: null,
        analysisResult: null,
        investigationState: null,
        loadInvestigationState: spies.loadInvestigationState || (async () => {}),
    };
    return makeComponent(methods, { base: { ...base, ...overrides } });
}

// --- database-side component with refresh spies.
function databaseComp(methods, overrides = {}) {
    const counters = { refreshes: 0 };
    const base = {
        apiUrl: '/api',
        notablePromotingId: null,
        bulkPromoteNotablesRunning: false,
        filteredRecentNotables: [],
        loadRecentNotables: async () => { counters.refreshes += 1; },
        loadTriageData: async () => { counters.refreshes += 1; },
        loadAnalysisCases: async () => { counters.refreshes += 1; },
        loadDbStats: async () => { counters.refreshes += 1; },
    };
    const comp = makeComponent(methods, { base: { ...base, ...overrides } });
    return { comp, counters };
}

function spy(fn) {
    const calls = [];
    const wrapped = (...args) => {
        calls.push(args);
        return fn ? fn(...args) : undefined;
    };
    wrapped.calls = calls;
    return wrapped;
}

// Swap the sandbox axios implementation mid-scenario. Modules resolve
// `axios` from the sandbox globals on every call, so mutating the sandbox
// object is enough to change what the next request does.
function sandboxPostSwap(sandbox, impl) {
    sandbox.axios.post = impl;
}

function sandboxGetSwap(sandbox, impl) {
    sandbox.axios.get = impl;
}

const SUPPORTIVE_QUERIES = [
    { id: 1, title: 'Failed logins', spl_query: 'index=secure action=failure' },
    { id: 2, title: 'Blank card', spl_query: 'index=x' },
    { id: 3, title: 'Zero results', spl_query: 'index=y' },
];

const scenarios = {
    // Evidence save (supportive): payload contract, skip rule, state handoff.
    async save_supportive() {
        const posts = spy(async () => ({
            data: { investigation_state: { evidence_summary: { total_items: 2 } } },
        }));
        let stateLoads = 0;
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods, {
            analysisRule: { rule_id: 'MOCK-RULE-001', supportive_queries: SUPPORTIVE_QUERIES },
            phase2CardState: {
                'id:1': makeCard({ resultText: '6 failures on VPN-GW-01' }),
                'id:2': makeCard({ resultText: '' }),   // blank + status success -> skipped
                'id:3': makeCard({ resultText: '', status: 'no_results' }),  // explicit no_results -> real evidence, saved
            },
        }, { loadInvestigationState: async () => { stateLoads += 1; } });

        await comp.saveSupportiveEvidence({ silent: true });

        assert(posts.calls.length === 1, 'expected one evidence POST, got ' + posts.calls.length);
        const [url, body] = posts.calls[0];
        assert(url === '/api/evidence/MOCK-CASE-001', 'POST url wrong (canonical evidence family expected): ' + url);
        assert(body.source_system === 'supportive_manual', 'source_system wrong: ' + body.source_system);
        assert(body.replace_existing === true, 'replace_existing must be true');
        assert(body.entries.length === 2, 'expected 2 entries (blank success skipped), got ' + body.entries.length);
        const [first, second] = body.entries;
        assert(first.query_title === 'Failed logins', 'entry title wrong: ' + first.query_title);
        assert(first.query_text === 'index=secure action=failure', 'entry query_text wrong: ' + first.query_text);
        assert(first.result_text === '6 failures on VPN-GW-01', 'entry result_text wrong: ' + first.result_text);
        assert(first.finding_type === 'neutral', 'entry finding_type wrong: ' + first.finding_type);
        assert(first.result_status === 'success', 'entry result_status wrong: ' + first.result_status);
        assert(second.query_title === 'Zero results' && second.result_status === 'no_results'
            && second.result_text === '', 'no_results entry malformed: ' + JSON.stringify(second));
        assert(comp.investigationState && comp.investigationState.evidence_summary.total_items === 2,
            'investigation_state not adopted from save response');
        assert(comp.supportiveSaveBusy === false, 'supportiveSaveBusy left set after save');
        assert(alertCalls.length === 0, 'silent save must not alert: ' + JSON.stringify(alertCalls));
        assert(errorCalls.length === 0, 'save fell into console.error: ' + JSON.stringify(errorCalls));
        assert(stateLoads === 0, 'silent save must not reload investigation state');

        // Non-silent confirm: modal confirmation + state reload exactly once.
        await comp.saveSupportiveEvidence();
        assert(alertCalls.length === 1 && alertCalls[0].includes('Supportive evidence saved'),
            'confirm save must alert once: ' + JSON.stringify(alertCalls));
        assert(stateLoads === 1, 'confirm save must reload investigation state once, got ' + stateLoads);
        assert(errorCalls.length === 0, 'confirm save hit console.error: ' + JSON.stringify(errorCalls));
    },

    // Evidence save (phase 2): advisory labels, coverage notes, edited SPL.
    async save_phase2() {
        const posts = spy(async () => ({ data: { investigation_state: { phase: 2 } } }));
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods, {
            phase2Result: { phase2_queries: [
                { title: 'Brute followup', spl: 'index=auth', target_questions: ['q1', 'q2'] },
                { title: 'Untouched card', spl: 'index=x' },
                { title: 'Edited card', spl: 'index=orig', target_questions: [] },
                { title: 'Blank card', spl: 'index=y' },
            ] },
            phase2CardState: {
                'phase2:brute_followup': makeCard({ resultText: '12 failures', coverage: 'user scope' }),
                'phase2:edited_card': makeCard({ resultText: 'found pivot', editedSpl: 'index=analyst_edit' }),
                'phase2:untouched_card': makeCard({ status: 'no_results' }),
            },
        });

        await comp.savePhase2Evidence({ silent: true });

        assert(posts.calls.length === 1, 'expected one evidence POST, got ' + posts.calls.length);
        const [url, body] = posts.calls[0];
        assert(url === '/api/evidence/MOCK-CASE-001', 'POST url wrong (canonical evidence family expected): ' + url);
        assert(body.source_system === 'phase2_manual', 'source_system wrong: ' + body.source_system);
        assert(body.replace_existing === true, 'replace_existing must be true');
        assert(body.entries.length === 3, 'expected 3 entries (untouched success skipped), got ' + body.entries.length);
        const [brute, untouched, edited] = body.entries;
        assert(brute.query_title === 'Brute followup' && brute.query_text === 'index=auth',
            'phase2 entry malformed: ' + JSON.stringify(brute));
        assert(brute.result_text === '12 failures', 'phase2 result_text wrong: ' + brute.result_text);
        assert(brute.analyst_summary === 'Coverage: user scope', 'coverage note not surfaced: ' + brute.analyst_summary);
        assert(brute.question_resolution === 'not_resolved',
            'analyst label must stay advisory "not_resolved", got ' + brute.question_resolution);
        assert(Array.isArray(brute.target_questions) && brute.target_questions[0] === 'q1',
            'target_questions not forwarded: ' + JSON.stringify(brute.target_questions));
        assert(untouched.result_status === 'no_results' && untouched.result_text === '',
            'explicit no_results entry must save with empty text: ' + JSON.stringify(untouched));
        assert(edited.query_text === 'index=analyst_edit',
            'edited SPL must override card template: ' + edited.query_text);
        assert(comp.investigationState && comp.investigationState.phase === 2,
            'investigation_state not adopted from save response');
        assert(alertCalls.length === 0 && errorCalls.length === 0,
            'silent phase2 save must be quiet: alerts=' + JSON.stringify(alertCalls) + ' errors=' + JSON.stringify(errorCalls));
    },

    // Save re-entrancy: a second save while busy must not double-post.
    async save_busy_guard() {
        const posts = spy(async () => ({ data: {} }));
        const { ctx, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods, {
            supportiveSaveBusy: true,
            analysisRule: { rule_id: 'MOCK-RULE-001', supportive_queries: SUPPORTIVE_QUERIES },
            phase2CardState: { 'id:1': makeCard({ resultText: 'text that must never post' }) },
        });

        await comp.saveSupportiveEvidence();
        // phase2 save with zero phase2 queries also returns before posting.
        await comp.savePhase2Evidence();

        assert(posts.calls.length === 0, 'busy/empty saves must not post, got ' + posts.calls.length + ' posts');
        assert(errorCalls.length === 0, 'busy guard hit console.error: ' + JSON.stringify(errorCalls));
    },

    // Saved-evidence load: THE load-path crash regression (6942458).
    // Legacy raw_result rows still carry question_resolution/target_questions
    // from Phase 4-era saves; rehydration must survive them and hydrate the
    // surviving keys without falling into the swallowing catch block.
    async load_saved_evidence() {
        const gets = spy(async () => ({ data: [
            { query_title: 'Brute followup', raw_result: {
                result_text: '12 failures', finding_type: 'suspicious', query_text: 'index=auth edited',
                question_resolution: 'resolved', target_questions: ['q1'],
            } },
            { query_title: 'Legacy blob', raw_result: 'plain string blob' },
            { raw_result: {} },
        ] }));
        const { ctx, errorCalls } = makeSandbox({ get: gets });
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods);

        await comp.loadSavedPhase2Evidence('MOCK-CASE-001');

        assert(gets.calls.length === 1, 'expected one evidence GET, got ' + gets.calls.length);
        const [url, config] = gets.calls[0];
        assert(url === '/api/evidence/MOCK-CASE-001', 'GET url wrong (canonical evidence family expected): ' + url);
        assert(config && config.params && config.params.source_system === 'phase2_manual',
            'GET params wrong: ' + JSON.stringify(config && config.params));
        const cards = comp.phase2CardState;
        assert(cards['phase2:brute_followup'] && cards['phase2:brute_followup'].resultText === '12 failures',
            'saved result_text not rehydrated: ' + JSON.stringify(cards));
        assert(cards['phase2:brute_followup'].findingType === 'suspicious',
            'finding_type not rehydrated: ' + JSON.stringify(cards));
        assert(cards['phase2:brute_followup'].editedSpl === 'index=auth edited',
            'edited query not rehydrated: ' + JSON.stringify(cards));
        assert(cards['phase2:legacy_blob'] && cards['phase2:legacy_blob'].resultText === '',
            'legacy raw_result row must hydrate to empty text, got ' + JSON.stringify(cards));
        assert(cards['phase2:legacy_blob'].findingType === 'neutral',
            'legacy raw_result row must default to neutral, got ' + JSON.stringify(cards));
        assert(cards['phase2:title:unknown'] && cards['phase2:title:unknown'].resultText === '',
            'title-less row must hydrate under the unknown key: ' + JSON.stringify(cards));
        // The crash regression canary: the try/catch swallows any rehydration
        // crash into console.error and leaves the card state empty, so a
        // healthy load must log nothing AND hydrate (asserted above).
        assert(errorCalls.length === 0,
            'load fell into the swallowing catch block (crash regression): ' + JSON.stringify(errorCalls));
    },

    // Load failure path: swallowed to console.error, never throws at the caller.
    async load_saved_error() {
        const gets = spy(async () => {
            const err = new Error('Request failed with status code 503');
            err.response = { data: { detail: 'evidence backend down' } };
            throw err;
        });
        const { ctx, errorCalls } = makeSandbox({ get: gets });
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods);

        await comp.loadSavedPhase2Evidence('MOCK-CASE-001');

        assert(errorCalls.length === 1 && errorCalls[0].includes('Failed to load saved phase 2 evidence'),
            'load failure must be logged once, got: ' + JSON.stringify(errorCalls));
        assert(Object.keys(comp.phase2CardState).length === 0,
            'failed load must not half-hydrate state: ' + JSON.stringify(comp.phase2CardState));
    },

    // No case selected: the load must not touch the network at all.
    async load_saved_no_case() {
        const gets = spy(async () => ({ data: [] }));
        const { ctx, errorCalls } = makeSandbox({ get: gets });
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods);

        await comp.loadSavedPhase2Evidence('');
        await comp.loadSavedPhase2Evidence(undefined);

        assert(gets.calls.length === 0, 'empty caseId must not hit the network, got ' + gets.calls.length + ' GETs');
        assert(errorCalls.length === 0, 'no-case load logged errors: ' + JSON.stringify(errorCalls));
    },

    // Promote: POST contract, refresh fan-out, busy flag lifecycle.
    async promote() {
        let release;
        const gate = new Promise((res) => { release = res; });
        const posts = spy(async () => {
            await gate;
            return { data: { case_id: 'TRIAGE-42' } };
        });
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadDatabase(ctx);
        const { comp, counters } = databaseComp(methods);

        const pending = comp.promoteNotableToTriage({ id: 42 });
        await new Promise((r) => setTimeout(r, 20));
        assert(comp.notablePromotingId === 42, 'busy flag must hold the notable id mid-flight, got ' + comp.notablePromotingId);
        release();
        await pending;

        assert(posts.calls.length === 1, 'expected one promote POST, got ' + posts.calls.length);
        assert(posts.calls[0][0] === '/api/notables/42/promote', 'promote url wrong (canonical expected): ' + posts.calls[0][0]);
        assert(counters.refreshes === 4, 'promote must refresh notables+triage+analysis+stats (4 calls), got ' + counters.refreshes);
        assert(comp.notablePromotingId === null, 'busy flag must reset to null, got ' + comp.notablePromotingId);
        assert(alertCalls.length === 0, 'promote must not alert: ' + JSON.stringify(alertCalls));
        assert(errorCalls.length === 0, 'promote logged errors: ' + JSON.stringify(errorCalls));
    },

    // Historical guard: unpromoted historical notables never promote.
    async promote_historical_guard() {
        const posts = spy(async () => ({ data: {} }));
        const { ctx, warnCalls } = makeSandbox({ post: posts });
        const methods = await loadDatabase(ctx);
        const { comp, counters } = databaseComp(methods);

        await comp.promoteNotableToTriage({ id: 43, historical: true });
        assert(posts.calls.length === 0, 'historical unpromoted notable must not POST');
        assert(warnCalls.length === 1 && warnCalls[0].includes('Historical pasted notables are not promoted'),
            'guard must warn once, got: ' + JSON.stringify(warnCalls));
        assert(counters.refreshes === 0, 'guarded promote must not refresh, got ' + counters.refreshes);
        assert(comp.notablePromotingId === null, 'guarded promote must not set the busy flag');

        // Already-promoted historical notables pass the guard and re-promote.
        await comp.promoteNotableToTriage({ id: 44, historical: true, promoted_case_id: 'CASE-9' });
        assert(posts.calls.length === 1 && posts.calls[0][0] === '/api/notables/44/promote',
            'promoted historical notable must be allowed to re-promote');
    },

    // Promote failure: logged, never thrown or alerted, state fully reset.
    async promote_failure() {
        const posts = spy(async () => {
            const err = new Error('Request failed with status code 404');
            err.response = { data: { detail: 'notable not found' } };
            throw err;
        });
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadDatabase(ctx);
        const { comp, counters } = databaseComp(methods);

        await comp.promoteNotableToTriage({ id: 45 });

        assert(errorCalls.length === 1 && errorCalls[0].includes('notable not found'),
            'failure detail must be logged, got: ' + JSON.stringify(errorCalls));
        assert(alertCalls.length === 0, 'promote failure must not alert (blocking modal): ' + JSON.stringify(alertCalls));
        assert(comp.notablePromotingId === null, 'busy flag must reset on failure');
        assert(counters.refreshes === 0, 'failed promote must not refresh downstream lists');
    },

    // Bulk promote: filter, survive per-notable failures, single refresh pass.
    async promote_all_open() {
        const attempted = [];
        const posts = spy(async (url) => {
            const id = Number(url.split('/notables/')[1].split('/')[0]);
            attempted.push(id);
            if (id === 1) {
                const err = new Error('boom');
                err.response = { data: { detail: 'boom' } };
                throw err;
            }
            return { data: {} };
        });
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadDatabase(ctx);
        const { comp, counters } = databaseComp(methods, {
            filteredRecentNotables: [
                { id: 1 }, { id: 2, historical: true },
                { id: 3, promoted_case_id: 'CASE-1' }, { id: 4 },
            ],
        });

        await comp.promoteAllOpenPastedNotables();

        assert(attempted.join(',') === '1,4',
            'bulk promote must skip historical + already-promoted and continue past failures, got: ' + attempted.join(','));
        assert(errorCalls.length === 1 && errorCalls[0].includes('Failed to promote notable 1'),
            'per-notable failure must be logged once, got: ' + JSON.stringify(errorCalls));
        assert(counters.refreshes === 4, 'bulk promote must run one refresh pass after the loop, got ' + counters.refreshes);
        assert(comp.bulkPromoteNotablesRunning === false, 'bulk flag must reset');
        assert(alertCalls.length === 0, 'bulk promote must not alert: ' + JSON.stringify(alertCalls));

        // S8: per-item outcome, running summary, and progress lifecycle.
        assert(comp.bulkPromoteOutcome && comp.bulkPromoteOutcome[4] && comp.bulkPromoteOutcome[4].state === 'promoted',
            'per-item outcome must mark successes: ' + JSON.stringify(comp.bulkPromoteOutcome));
        assert(comp.bulkPromoteOutcome[1] && comp.bulkPromoteOutcome[1].state === 'failed'
            && comp.bulkPromoteOutcome[1].detail === 'boom',
            'per-item outcome must carry the failure detail: ' + JSON.stringify(comp.bulkPromoteOutcome));
        assert(comp.bulkPromoteSummary === 'Bulk promote finished: 1 promoted, 1 failed, 2 skipped (historical or already promoted).',
            'summary must count promoted/failed/skipped, got: ' + comp.bulkPromoteSummary);
        assert(comp.bulkPromoteProgress === null, 'progress must clear when the loop finishes');

        // S8: a click with nothing open explains itself instead of no-op'ing.
        comp.filteredRecentNotables = [{ id: 9, historical: true }];
        comp.bulkPromoteSummary = '';
        await comp.promoteAllOpenPastedNotables();
        assert(comp.bulkPromoteSummary.includes('No open pasted notables to promote')
            && comp.bulkPromoteSummary.includes('1 skipped'),
            'empty-candidate run must explain itself, got: ' + comp.bulkPromoteSummary);
        assert(attempted.length === 2, 'no-candidate run must not POST');

        // Re-entrancy guard: a second call while running must be a no-op.
        comp.bulkPromoteNotablesRunning = true;
        await comp.promoteAllOpenPastedNotables();
        assert(attempted.length === 2, 're-entrant bulk promote must not post again');
    },

    // Auto-promote-on-save: the paste response's events[] drive promote
    // calls for newly-added segments only (dedup-skipped segments never
    // promote), historical pastes never promote, and option-off is a
    // plain save.
    async paste_auto_promote() {
        const attempted = [];
        const posts = spy(async (url) => {
            if (url === '/api/notables/paste') {
                return { data: {
                    success: true,
                    added: 2,
                    skipped: 1,
                    segment_count: 3,
                    events: [
                        { event_id: 51, deduplicated: false },
                        { event_id: 52, deduplicated: true },
                        { event_id: 53, deduplicated: false },
                    ],
                } };
            }
            const id = Number(url.split('/notables/')[1].split('/')[0]);
            attempted.push(id);
            return { data: {} };
        });
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        const methods = await loadDatabase(ctx);
        const { comp, counters } = databaseComp(methods, {
            notablePasteText: 'title: autopromote probe',
            notableRedactionEnabled: true,
            notableHistorical: false,
            notableAutoPromote: true,
            notablePasteSaving: false,
            notablePasteResult: null,
            bulkPromoteSummary: '',
            bulkPromoteOutcome: {},
            bulkPromoteProgress: null,
        });

        await comp.savePastedNotable();

        const pasteCall = posts.calls.find(c => c[0] === '/api/notables/paste');
        assert(pasteCall, 'save must POST the paste route');
        assert(pasteCall[1].historical === false && pasteCall[1].redaction_enabled === true,
            'paste payload contract changed: ' + JSON.stringify(pasteCall[1]));
        assert(attempted.join(',') === '51,53',
            'auto-promote must promote added segments only (skip deduplicated), got: ' + attempted.join(','));
        assert(comp.notablePasteText === '', 'paste text must clear after save');
        assert(counters.refreshes === 6,
            'auto-promote must refresh 2x after save + 4x after promotion, got ' + counters.refreshes);
        assert(comp.bulkPromoteSummary === 'Auto-promoted 2 of 2 saved notable(s) to triage.',
            'summary must report the auto-promote outcome, got: ' + comp.bulkPromoteSummary);
        assert(comp.bulkPromoteOutcome[51] && comp.bulkPromoteOutcome[51].state === 'promoted',
            'per-item outcome must light up added ids: ' + JSON.stringify(comp.bulkPromoteOutcome));
        assert(comp.bulkPromoteNotablesRunning === false && comp.bulkPromoteProgress === null,
            'busy/progress state must reset after auto-promote');
        assert(alertCalls.length === 0, 'auto-promote must not alert: ' + JSON.stringify(alertCalls));
        assert(errorCalls.length === 0, 'auto-promote logged errors: ' + JSON.stringify(errorCalls));

        // Historical pastes are closed records: auto-promote explains, never POSTs.
        comp.notableHistorical = true;
        comp.notablePasteText = 'title: closed record';
        comp.notablePasteResult = null;
        comp.bulkPromoteSummary = '';
        await comp.savePastedNotable();
        assert(attempted.length === 2, 'historical paste must not promote, got: ' + attempted.join(','));
        assert(comp.bulkPromoteSummary.includes('historical pastes are closed records'),
            'historical skip must be explained, got: ' + comp.bulkPromoteSummary);

        // Option off: plain save, no promote traffic at all.
        comp.notableHistorical = false;
        comp.notableAutoPromote = false;
        comp.notablePasteText = 'title: plain save';
        await comp.savePastedNotable();
        assert(attempted.length === 2, 'auto-promote off must not promote, got: ' + attempted.join(','));
    },

    // S9: closure readiness punch list loads, survives failure, and its
    // blocker buttons route to the right analysis stage.
    async closure_readiness_punchlist() {
        const gets = spy(async (url) => {
            if (url.includes('/closure-readiness')) {
                return { data: {
                    is_ready: false,
                    status: 'collecting_evidence',
                    blockers: [
                        'No saved investigative evidence exists yet for this case.',
                        '2 investigative question(s) remain unresolved.',
                        "Something entirely novel the UI has never classified.",
                    ],
                    blocker_actions: [
                        { stage: 1, label: 'Run initial analysis' },
                        { stage: 4, label: 'Resolve open questions' },
                        { stage: 4, label: 'Continue investigation' },
                    ],
                } };
            }
            return { data: {} };
        });
        const { ctx, errorCalls } = makeSandbox({ get: gets });
        const methods = await loadAnalysis(ctx);
        // Also load closure methods into the same sandbox namespace.
        const closureMethods = await loadModuleMethods(ctx, 'web/modules/closure.js', 'ClosureMethods');
        assert(typeof closureMethods.loadClosureReadiness === 'function'
            && typeof closureMethods.goResolveBlocker === 'function',
            'closure.js is missing the readiness punch-list methods');
        const goToStageCalls = [];
        const comp = makeComponent({ ...methods, ...closureMethods }, {
            base: {
                apiUrl: '/api',
                closureForm: { caseId: 'MOCK-CASE-001', ruleId: '', fieldValues: {}, analystNotes: '', disposition: '' },
                closureReadiness: null,
                currentTab: 'closure',
                analysisCaseId: '',
                $refs: { analysisTabRef: { goToStage(stage) { goToStageCalls.push(stage); } } },
            },
        });

        await comp.loadClosureReadiness('MOCK-CASE-001');

        assert(gets.calls.length === 1 && gets.calls[0][0] === '/api/cases/MOCK-CASE-001/closure-readiness',
            'readiness GET url wrong (canonical cases family expected): ' + JSON.stringify(gets.calls.map(c => c[0])));
        assert(comp.closureReadiness && comp.closureReadiness.blockers.length === 3,
            'readiness punch list not stored: ' + JSON.stringify(comp.closureReadiness));
        assert(errorCalls.length === 0, 'readiness load logged errors: ' + JSON.stringify(errorCalls));

        // Deep-link routing: evidence blocker -> stage 1, questions -> stage 4.
        comp.goResolveBlocker(0);
        assert(comp.currentTab === 'analysis' && comp.analysisCaseId === 'MOCK-CASE-001',
            'blocker link must jump to the Analysis tab for the same case');
        assert(goToStageCalls.length === 1 && goToStageCalls[0] === 1,
            'evidence blocker must route to stage 1, got: ' + JSON.stringify(goToStageCalls));
        comp.currentTab = 'closure';
        comp.goResolveBlocker(1);
        assert(goToStageCalls[1] === 4, 'unresolved-questions blocker must route to stage 4');
        comp.goResolveBlocker(2);
        assert(goToStageCalls[2] === 4, 'unclassified blocker must fall back to stage 4');

        // Out-of-range index falls back to stage 4 rather than crashing.
        comp.goResolveBlocker(99);
        assert(goToStageCalls[3] === 4, 'unknown blocker index must fall back to stage 4');

        // Empty case clears the punch list.
        await comp.loadClosureReadiness('');
        assert(comp.closureReadiness === null, 'empty caseId must clear readiness');
    },

    // S9: a blocked generate surfaces the punch list (no blind force), and
    // declining the override confirm never posts twice.
    async closure_blocked_generate_punchlist() {
        let postImpl = async () => ({ data: {
            blocked: true,
            readiness: {
                is_ready: false,
                status: 'collecting_evidence',
                blockers: ['Saved evidence is marked neutral only; no supporting or refuting direction established.'],
            },
        } });
        const posts = spy((...args) => postImpl(...args));
        const { ctx, alertCalls, errorCalls, confirmCalls, setConfirmReturn } = makeSandbox({ post: posts });
        setConfirmReturn(false); // analyst declines the force override
        const methods = await loadAnalysis(ctx);
        const closureMethods = await loadModuleMethods(ctx, 'web/modules/closure.js', 'ClosureMethods');
        const comp = makeComponent({ ...methods, ...closureMethods }, {
            base: {
                apiUrl: '/api',
                closureForm: { caseId: 'MOCK-CASE-001', ruleId: 'RULE-1', fieldValues: {}, analystNotes: '', disposition: 'Undetermined' },
                closureReadiness: null,
                closureResult: null,
                closureGenerating: false,
            },
        });

        await comp.generateClosureNote();

        assert(posts.calls.length === 1, 'blocked generate must POST exactly once when the override is declined, got ' + posts.calls.length);
        assert(confirmCalls.length === 1 && confirmCalls[0].includes('closure criteria not yet fully met'),
            'blocked generate must ask before forcing: ' + JSON.stringify(confirmCalls));
        assert(comp.closureReadiness && comp.closureReadiness.blockers.length === 1,
            'blocked generate must surface the punch list: ' + JSON.stringify(comp.closureReadiness));
        assert(comp.closureReadiness.blocker_actions && comp.closureReadiness.blocker_actions[0].stage === 4,
            'punch list from the blocked response must carry default routing: ' + JSON.stringify(comp.closureReadiness.blocker_actions));
        assert(comp.closureResult === null, 'declined override must not adopt a closure result');
        assert(alertCalls.length === 0, 'blocked generate must not alert: ' + JSON.stringify(alertCalls));
        assert(errorCalls.length === 0, 'blocked generate logged errors: ' + JSON.stringify(errorCalls));

        // Force path: confirming the override re-posts and stores the note.
        posts.calls.length = 0;
        setConfirmReturn(true);
        postImpl = async (url, body) => ({ data: { disposition: 'True Positive', generated_note: 'NOTE' } });
        await comp.generateClosureNote(true);

        assert(posts.calls.length === 1, 'forced generate must POST exactly once, got ' + posts.calls.length);
        assert(posts.calls[0][1] && posts.calls[0][1].force_closure === true,
            'forced generate must send force_closure: true');
        assert(comp.closureResult && comp.closureResult.generated_note === 'NOTE',
            'forced generate must adopt the closure result: ' + JSON.stringify(comp.closureResult));
        assert(alertCalls.length === 0, 'forced generate must not alert: ' + JSON.stringify(alertCalls));
    },

    // S10: card edits debounce into a localStorage draft snapshot ~1.2s after
    // the last keystroke; switching cases cancels the pending write.
    async draft_autosave_debounce() {
        const writes = [];
        const firedTimers = [];
        const { ctx, sandbox, warnCalls } = makeSandbox({ get: async () => ({ data: [] }) });
        // Capture timers instead of really waiting, and capture localStorage
        // writes, inside the sandbox globals.
        sandbox.setTimeout = (fn, ms) => {
            const id = firedTimers.length + 1000;
            firedTimers.push({ id, fn, ms, fired: false });
            return id;
        };
        sandbox.clearTimeout = (id) => {
            const t = firedTimers.find((t) => t.id === id);
            if (t) { t.fired = true; }
        };
        sandbox.window.localStorage = {
            setItem(k, v) { writes.push([k, String(v).length]); },
            getItem() { return null; },
        };
        const methods = await loadAnalysis(ctx);
        const comp = makeComponent(methods, {
            base: {
                apiUrl: '/api',
                analysisCaseId: 'MOCK-CASE-001',
                phase2CardState: {},
                _draftSaveTimer: null,
            },
        });

        // Three rapid keystrokes: exactly one trailing timer at ~1.2s.
        comp.updateSupportiveResult('id:1', 'partial paste');
        comp._updateCardField('phase2CardState', 'phase2:foo', 'resultText', 'more');
        comp.onPhase2TemplateInput({ title: 'foo', spl: 'index=x' }, 'index=edited');

        const pending = firedTimers.filter((t) => !t.fired);
        assert(pending.length === 1, 'rapid edits must schedule exactly one trailing timer, got ' + pending.length);
        assert(pending[0].ms === 1200, 'debounce must be trailing ~1.2s, got ' + pending[0].ms);
        assert(writes.length === 0, 'no snapshot write before the debounce elapses');

        // Elapse the timer: exactly one draft write containing both maps.
        pending[0].fired = true;
        pending[0].fn();
        assert(writes.length === 1, 'debounce must produce exactly one snapshot write, got ' + writes.length);
        assert(writes[0][0] === 'analysis_state:MOCK-CASE-001',
            'draft must be keyed by case, got ' + writes[0][0]);
        assert(comp._draftSaveTimer === null, 'timer handle must clear after firing');
        assert(warnCalls.length === 0, 'draft save logged warnings: ' + JSON.stringify(warnCalls));

        // Switching cases cancels a pending draft write (never cross-key).
        comp.updateSupportiveResult('id:1', 'again');
        const before = firedTimers.filter((t) => !t.fired).length;
        assert(before === 1, 'a new edit must schedule a new timer');
        comp.onAnalysisCaseChanged();
        assert(firedTimers.filter((t) => !t.fired).length === 0,
            'case change must cancel the pending draft timer');
        assert(writes.length === 1, 'cancelled draft must never write');

        // No case selected: edits schedule nothing at all.
        comp.analysisCaseId = '';
        comp.updateSupportiveResult('id:1', 'orphan');
        assert(firedTimers.filter((t) => !t.fired).length === 0,
            'no-case edits must not schedule a draft save');
    },

    // S11: case-level evidence ledger - load, empty state, delete fan-in.
    async evidence_ledger_view() {
        const rows = [
            { id: 11, case_id: 'MOCK-CASE-001', query_title: 'Failed logins', source_system: 'supportive_manual', raw_result: { result_text: '6 failures on VPN-GW-01' }, created_at: '2026-09-29T10:00:00' },
            { id: 12, case_id: 'MOCK-CASE-001', query_title: 'Legacy blob', source_system: 'phase2_manual', raw_result: 'plain legacy string', created_at: '2026-09-29T11:00:00' },
        ];
        const gets = spy(async (url) => {
            if (url.includes('/evidence')) {
                return { data: rows };
            }
            return { data: {} };
        });
        const deleted = [];
        const posts = spy(async (url, body) => {
            if (url.includes('/batch-delete')) {
                deleted.push(...(body.ids || []));
                return { data: { deleted: (body.ids || []).length } };
            }
            return { data: {} };
        });
        const { ctx, sandbox, errorCalls, warnCalls } = makeSandbox({ get: gets, post: posts });
        const dbMethods = await loadDatabase(ctx); // installs the API global (M2)
        const comp = makeComponent(dbMethods, {
            base: {
                apiUrl: '/api',
                evidenceLedgerCaseId: '',
                evidenceLedgerItems: [],
                evidenceLedgerLoading: false,
                evidenceLedgerError: '',
                evidenceLedgerBusyId: null,
            },
        });

        // No case: guarded with a message, no network.
        await comp.loadCaseEvidenceLedger();
        assert(gets.calls.length === 0, 'empty case must not hit the network');
        assert(comp.evidenceLedgerError.includes('Select a case'),
            'empty case must explain itself: ' + comp.evidenceLedgerError);

        // Happy path: rows land, loading clears.
        comp.evidenceLedgerCaseId = 'MOCK-CASE-001';
        await comp.loadCaseEvidenceLedger();
        assert(gets.calls.length === 1 && gets.calls[0][0] === '/api/evidence/MOCK-CASE-001',
            'ledger GET url wrong (canonical evidence family expected): ' + gets.calls[0][0]);
        assert(comp.evidenceLedgerItems.length === 2 && comp.evidenceLedgerError === '',
            'ledger rows not stored: ' + JSON.stringify(comp.evidenceLedgerItems));
        assert(comp.evidenceLedgerLoading === false, 'loading flag must clear');

        // Delete one row: batch-delete POST with that id, optimistic removal.
        await comp.deleteEvidenceLedgerItem(comp.evidenceLedgerItems[0]);
        assert(deleted.join(',') === '11', 'delete must POST the row id via batch-delete, got ' + deleted.join(','));
        assert(comp.evidenceLedgerItems.length === 1 && comp.evidenceLedgerItems[0].id === 12,
            'deleted row must leave the local ledger: ' + JSON.stringify(comp.evidenceLedgerItems));
        assert(comp.evidenceLedgerBusyId === null, 'busy flag must reset');

        // Delete failure: logged, row kept, error surfaced, busy reset.
        sandboxPostSwap(sandbox, async () => { const e = new Error('503'); e.response = { data: { detail: 'backend down' } }; throw e; });
        await comp.deleteEvidenceLedgerItem(comp.evidenceLedgerItems[0]);
        assert(comp.evidenceLedgerError.includes('Failed to delete evidence item'),
            'delete failure must surface: ' + comp.evidenceLedgerError);
        assert(comp.evidenceLedgerItems.length === 1, 'failed delete must keep the row');
        assert(comp.evidenceLedgerBusyId === null, 'busy flag must reset on failure');

        // Load failure: logged, ledger cleared, error shown.
        sandboxGetSwap(sandbox, async () => { const e = new Error('500'); e.response = { data: { detail: 'db down' } }; throw e; });
        await comp.loadCaseEvidenceLedger();
        assert(comp.evidenceLedgerError.includes('Failed to load evidence ledger'),
            'load failure must surface: ' + comp.evidenceLedgerError);
        assert(comp.evidenceLedgerItems.length === 0, 'failed load must clear the ledger');
        assert(comp.evidenceLedgerLoading === false, 'loading flag must clear on failure');
    },

    // S12: loop timeline rows derive defensively from the persisted state.
    async loop_timeline_panel() {
        const { ctx } = makeSandbox({});
        const methods = await loadAnalysis(ctx);
        const comp = makeComponent(methods, { base: {} });

        const state = {
            loop_status: 'needs_more_evidence',
            evidence_summary: { timeline: [
                {
                    id: 3,
                    title: 'Failed logins',
                    source_system: 'supportive_manual',
                    result_status: 'success',
                    finding_type: 'supports',
                    confidence_delta_hint: 'increase',
                    ai_verdict_rationale: 'Six failures on the VPN gateway',
                    summary: '6 failures on VPN-GW-01',
                    created_at: '2026-09-29T10:00:00',
                },
                {
                    id: 4,
                    title: 'Data gap check',
                    source_system: 'phase2_manual',
                    result_status: 'data_source_unavailable',
                    finding_type: 'neutral',
                    confidence_delta_hint: 'decrease',
                },
            ] },
        };
        const rows = comp.buildLoopTimeline(state);
        assert(rows.length === 2, 'timeline rows must map 1:1, got ' + rows.length);
        assert(rows[0].title === 'Failed logins' && rows[0].findingType === 'supports'
            && rows[0].deltaHint === 'increase' && rows[0].rationale.includes('VPN gateway'),
            'row fields not mapped: ' + JSON.stringify(rows[0]));
        assert(rows[1].resultStatus === 'data_source_unavailable' && rows[1].deltaHint === 'decrease'
            && rows[1].rationale === '' && rows[1].summary === '',
            'sparse row must fall back defensively: ' + JSON.stringify(rows[1]));
        assert(String(rows[0].key).startsWith('tl:3:'), 'row keys must be stable: ' + rows[0].key);

        // Legacy row without an id still gets a unique, stable key.
        const legacy = comp.buildLoopTimeline({ evidence_summary: { timeline: [{ title: 'old', finding_type: 'refutes' }] } });
        assert(legacy.length === 1 && legacy[0].findingType === 'refutes' && legacy[0].key.startsWith('tl:r0:'),
            'legacy id-less rows must key by position: ' + JSON.stringify(legacy));

        // Empty/absent state yields an empty panel, never a crash.
        assert(comp.buildLoopTimeline(null).length === 0, 'null state must yield no rows');
        assert(comp.buildLoopTimeline({}).length === 0, 'empty state must yield no rows');
    },

    // cardState fold: pre-fold localStorage snapshots (four parallel per-kind
    // maps) migrate into phase2CardState cells on load, and post-S7 snapshots
    // carrying both shapes merge without losing either.
    async snapshot_migration_folds_legacy_maps() {
        const { ctx, sandbox, errorCalls } = makeSandbox({});
        let stored = null;
        sandbox.window.localStorage = {
            setItem(k, v) { stored = v; },
            getItem() { return stored; },
        };
        const methods = await loadAnalysis(ctx);
        const comp = makeComponent(methods, {
            base: {
                apiUrl: '/api',
                analysisCaseId: 'CASE-OLD',
                phase2CardState: {},
                phase2EditedQueries: {},
            },
        });

        // Pre-fold snapshot: legacy maps only, no phase2CardState key.
        stored = JSON.stringify({
            supportiveManualResults: { 'title:failed_logins': 'saved text' },
            supportiveFindingTypes: { 'title:failed_logins': 'suspicious' },
            enrichmentManualResults: { 'enrichment:proc_dump': 'proc data' },
        });
        comp.loadAnalysisState('CASE-OLD');
        let cards = comp.phase2CardState;
        assert(cards['title:failed_logins'] && cards['title:failed_logins'].resultText === 'saved text'
            && cards['title:failed_logins'].findingType === 'suspicious',
            'legacy supportive maps must fold into a card cell: ' + JSON.stringify(cards));
        assert(cards['enrichment:proc_dump'] && cards['enrichment:proc_dump'].resultText === 'proc data',
            'legacy enrichment map must fold into a card cell: ' + JSON.stringify(cards));
        assert(errorCalls.length === 0, 'migration logged errors: ' + JSON.stringify(errorCalls));

        // Post-S7 snapshot: cardState AND legacy maps coexist; both survive.
        comp.phase2CardState = {};
        stored = JSON.stringify({
            phase2CardState: { 'phase2:brute': makeCard({ resultText: 'phase2 draft' }) },
            supportiveManualResults: { 'id:9': 'supportive draft' },
        });
        comp.loadAnalysisState('CASE-OLD');
        cards = comp.phase2CardState;
        assert(cards['phase2:brute'] && cards['phase2:brute'].resultText === 'phase2 draft',
            'cardState key must load directly: ' + JSON.stringify(cards));
        assert(cards['id:9'] && cards['id:9'].resultText === 'supportive draft',
            'legacy maps in a mixed snapshot must still fold: ' + JSON.stringify(cards));

        // Round-trip: the migrated state re-stores as cardState only.
        comp._storeAnalysisStateSnapshot();
        const round = JSON.parse(stored);
        assert(round.phase2CardState && round.phase2CardState['id:9'].resultText === 'supportive draft',
            're-stored snapshot must carry the folded cells');
        assert(!round.supportiveManualResults && !round.enrichmentManualResults,
            're-stored snapshot must drop the legacy maps');
    },

    // S13 client adoption: the api.js service layer speaks canonical paths
    // first and retries the legacy /api/db/* spelling only on canonical 404
    // (older deploys); non-404 errors and non-aliased routes never fall back.
    async api_layer_canonical_fallback() {
        const requests = [];
        let failCanonicalWith = null; // null = canonical works (modern deploy)
        let legacyAllow = () => true;  // legacy routes answer OK
        const { ctx, sandbox } = makeSandbox({});
        const record = (verb) => async (u, ...rest) => {
            requests.push({ verb, url: String(u), body: rest[0] });
            const canonical = !String(u).includes('/db/');
            if (canonical && failCanonicalWith !== null) {
                const err = new Error('Request failed with status code ' + failCanonicalWith);
                err.response = { status: failCanonicalWith, data: { detail: 'fail' } };
                throw err;
            }
            if (!canonical && !legacyAllow(u)) {
                const err = new Error('Request failed with status code 404');
                err.response = { status: 404, data: { detail: 'Not Found' } };
                throw err;
            }
            return { data: { ok: true, url: String(u), body: rest[0] } };
        };
        sandbox.axios.get = record('get');
        sandbox.axios.post = record('post');
        sandbox.axios.put = record('put');
        sandbox.axios.delete = record('delete');
        const api = await loadApiLayer(ctx);

        // Contract fixture is the single source of truth: the client mapping
        // must satisfy every legacy->canonical pair (the server rewriter is
        // checked against the same fixture by TestCanonicalRestPaths).
        const contract = JSON.parse(
            await fsRead(path.join(ROOT, 'tests', 'api_path_contract.json'), 'utf8')
        );
        for (const pair of contract.pairs) {
            const got = api.canonicalFor(pair.legacy);
            assert(got === pair.canonical,
                `contract drift for ${pair.legacy}: got ${got}, expected ${pair.canonical}`);
        }

        // Spot-checks beyond the fixture (behavioral shape).
        assert(api.canonicalFor('/db/triage/CASE-1/evidence/batch-delete') === '/evidence/CASE-1/batch-delete',
            'evidence subroute must win over the generic tails: ' + api.canonicalFor('/db/triage/CASE-1/evidence/batch-delete'));

        // Modern deploy: canonical hits exactly once, legacy never dialed.
        const listed = await api.triage();
        assert(requests.length === 1 && requests[0].url === '/api/cases',
            'modern list must hit /api/cases once, got ' + JSON.stringify(requests));
        assert(listed.ok && listed.url === '/api/cases', 'payload must come from the canonical route');

        await api.saveEvidence('CASE 1', { entries: [1] });
        assert(requests[1].url === '/api/evidence/CASE%201' && requests[1].body.entries,
            'evidence save must hit the canonical evidence family with the body intact: ' + requests[1].url);

        // Older deploy (canonical 404s): exactly one legacy retry, then success.
        requests.length = 0;
        failCanonicalWith = 404;
        const promoted = await api.promoteNotable(42);
        assert(requests.length === 2
            && requests[0].url === '/api/notables/42/promote'
            && requests[1].url === '/api/db/notables/42/promote',
            'canonical 404 must retry the /api/db/* spelling once, got ' + JSON.stringify(requests));
        assert(promoted.url === '/api/db/notables/42/promote', 'payload must come from the legacy route');

        // Canonical 500 (real server error): no fallback, error propagates.
        requests.length = 0;
        failCanonicalWith = 500;
        let threw = false;
        try {
            await api.triageEvidence('CASE-1');
        } catch (err) {
            threw = true;
        }
        assert(threw, 'a canonical 500 must propagate, not fall back');
        assert(requests.length === 1, 'a 500 must not trigger a legacy retry, got ' + JSON.stringify(requests));

        // Double-404 (unknown subresource): both spellings 404, error surfaces.
        requests.length = 0;
        failCanonicalWith = 404;
        legacyAllow = () => false;
        threw = false;
        try {
            await api.triageInvestigationState('X/unknown');
        } catch (err) {
            threw = true;
        }
        assert(threw, 'a double-404 must surface to the caller');
        assert(requests.length === 2, 'double-404 must have tried both spellings, got ' + JSON.stringify(requests));

        // Non-aliased route: one direct call regardless of deploy vintage.
        requests.length = 0;
        failCanonicalWith = null;
        legacyAllow = () => true;
        await api.rules();
        assert(requests.length === 1 && requests[0].url === '/api/db/rules',
            'non-aliased routes must call their only spelling directly, got ' + JSON.stringify(requests));
    },

    // Stepper completion guards (live-UI smoke finding, Sept 30): with no case
    // selected, the stage2Complete "no playbook" fallback marked Evidence
    // Collection ✓ before the analyst picked anything. The guard must key off
    // analysisCaseId first; the legitimate fallback still works with a case.
    async stepper_guards() {
        const { ctx } = makeSandbox({});
        const tab = await loadModuleMethods(ctx, 'web/components/AnalysisTab.js', 'AnalysisTab');
        const computed = tab.computed || {};
        for (const name of ['stage1Complete', 'stage2Complete', 'stage3Complete']) {
            assert(typeof computed[name] === 'function', 'AnalysisTab is missing computed.' + name);
        }
        const stub = (over) => ({
            analysisCaseId: '',
            analysisRule: null,
            analysisResult: null,
            phase2Result: null,
            investigationState: null,
            supportivePlaybookAvailable: null,
            displayPhase2Queries: [],
            ...over,
        });

        // The regression: fresh page, no case chosen — nothing may read ✓.
        const fresh = stub();
        assert(computed.stage1Complete.call(fresh) === false, 'stage1 must be incomplete with no case');
        assert(computed.stage2Complete.call(fresh) === false,
            'stage2 must be incomplete with no case selected (no-playbook fallback ran without a case)');
        assert(computed.stage3Complete.call(fresh) === false, 'stage3 must be incomplete with no results');

        // Legitimate fallback preserved: case active, rule has no playbook,
        // nothing to collect — analyst may proceed to Initial Assessment.
        const noPlaybook = stub({ analysisCaseId: 'MOCK-CASE-001' });
        assert(computed.stage2Complete.call(noPlaybook) === true,
            'unsupported-rule fallback must still allow proceeding with an active case');

        // Playbook present but nothing collected yet: not complete.
        const collecting = stub({
            analysisCaseId: 'MOCK-CASE-001',
            supportivePlaybookAvailable: true,
            displayPhase2Queries: [{ id: 1, title: 'q' }],
        });
        assert(computed.stage2Complete.call(collecting) === false,
            'open playbook with no evidence must not read complete');

        // Evidence collected: complete.
        const collected = stub({
            analysisCaseId: 'MOCK-CASE-001',
            investigationState: { evidence_summary: { total_items: 2, substantive_items: 1 } },
        });
        assert(computed.stage2Complete.call(collected) === true,
            'saved evidence must complete stage 2');
    },

    // C3: the analysis wizard POST must carry per-stage model overrides.
    async analyze_stage_models() {
        const posts = spy(async () => ({
            data: {
                analysis: 'Initial assessment text',
                model: 'fake-model:latest',
                investigation_state: { evidence_summary: { total_items: 0 } },
            },
        }));
        const { ctx, alertCalls, errorCalls } = makeSandbox({ post: posts });
        ctx.AbortController = class { constructor() { this.signal = undefined; } abort() {} };
        const methods = await loadAnalysis(ctx);
        const comp = analysisComp(methods, {
            analysisModel: 'llama3.1:latest',
            stageModels: { initial: 'wizard-a:latest', follow_up: '', closure: '' },
            analysisContext: '',
            _startAnalysisStatus: () => {},
            _setAnalysisStatus: () => {},
            _stopAnalysisStatusTimer: () => {},
            _hydrateTimelineFromEvidence: async () => {},
            _storeAnalysisStateSnapshot: () => {},
        });

        await comp.runAnalysis();

        const analyzeCall = posts.calls.find(([url]) => url === '/api/analyses' || url === '/api/db/analyze');
        assert(analyzeCall, 'no analyze POST captured: ' + JSON.stringify(posts.calls.map(c => c[0])));
        const body = analyzeCall[1];
        assert(body.stage_models && body.stage_models.initial === 'wizard-a:latest',
            'analyze body must carry stage_models.initial: ' + JSON.stringify(body.stage_models));
        assert(body.analysis_stage === 'initial', 'stage must be initial: ' + body.analysis_stage);
        assert(alertCalls.length === 0, 'runAnalysis alerted: ' + JSON.stringify(alertCalls));
        assert(errorCalls.length === 0, 'runAnalysis hit console.error: ' + JSON.stringify(errorCalls));
    },
};

runScenarios(scenarios, 'evidence_promote_load.mjs');
