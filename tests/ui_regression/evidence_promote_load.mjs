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
 *   draft_autosave_debounce
 * Exit code 0 = all assertions held.
 */
import {
    assert,
    makeSandbox,
    makeComponent,
    loadModuleMethods,
    runScenarios,
} from './harness_core.mjs';

async function loadAnalysis(ctx) {
    const methods = await loadModuleMethods(ctx, 'web/modules/analysis.js', 'AnalysisMethods');
    assert(typeof methods.saveSupportiveEvidence === 'function'
        && typeof methods.savePhase2Evidence === 'function'
        && typeof methods.loadSavedPhase2Evidence === 'function',
        'analysis.js is missing one of the evidence flow methods');
    return methods;
}

async function loadDatabase(ctx) {
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
        supportiveManualResults: {},
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
            supportiveManualResults: {
                'id:1': '6 failures on VPN-GW-01',
                'id:2': '',   // blank + status success -> skipped
                'id:3': '',   // blank + explicit no_results -> real evidence, saved
            },
            phase2CardState: { 'id:3': { status: 'no_results' } },
        }, { loadInvestigationState: async () => { stateLoads += 1; } });

        await comp.saveSupportiveEvidence({ silent: true });

        assert(posts.calls.length === 1, 'expected one evidence POST, got ' + posts.calls.length);
        const [url, body] = posts.calls[0];
        assert(url === '/api/db/triage/MOCK-CASE-001/evidence', 'POST url wrong: ' + url);
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
                'phase2:brute_followup': { resultText: '12 failures', coverage: 'user scope' },
                'phase2:edited_card': { resultText: 'found pivot', editedSpl: 'index=analyst_edit' },
                'phase2:untouched_card': { status: 'no_results' },
            },
        });

        await comp.savePhase2Evidence({ silent: true });

        assert(posts.calls.length === 1, 'expected one evidence POST, got ' + posts.calls.length);
        const [url, body] = posts.calls[0];
        assert(url === '/api/db/triage/MOCK-CASE-001/evidence', 'POST url wrong: ' + url);
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
            supportiveManualResults: { 'id:1': 'text that must never post' },
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
        assert(url === '/api/db/triage/MOCK-CASE-001/evidence', 'GET url wrong: ' + url);
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
        assert(posts.calls[0][0] === '/api/db/notables/42/promote', 'promote url wrong: ' + posts.calls[0][0]);
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
        assert(posts.calls.length === 1 && posts.calls[0][0] === '/api/db/notables/44/promote',
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
            const id = Number(url.split('/db/notables/')[1].split('/')[0]);
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

        assert(gets.calls.length === 1 && gets.calls[0][0] === '/api/db/triage/MOCK-CASE-001/closure-readiness',
            'readiness GET url wrong: ' + JSON.stringify(gets.calls.map(c => c[0])));
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
                supportiveManualResults: {},
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
};

runScenarios(scenarios, 'evidence_promote_load.mjs');
