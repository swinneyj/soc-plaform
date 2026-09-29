/**
 * Shared core for the zero-dependency UI regression harnesses.
 *
 * Each harness loads a real web/modules/*.js into a node:vm sandbox with a
 * stubbed axios/window and drives the production methods directly. This core
 * owns the plumbing so every harness asserts the same way:
 *   - assert/fail + scenario dispatcher (exit 0 = scenario held, 1 = fail, 2 = usage)
 *   - makeSandbox: vm context with canned axios and canary capture of
 *     window.alert (blocking modals wedge embedded webviews mid-loop),
 *     console.error (swallowed crashes in catch blocks), and console.warn
 *   - loadModuleMethods: evaluate a real module file and grab its UMD export
 *   - makeComponent: bind real methods onto a state stub so `this` is faithful
 */
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

export function fail(msg) {
    console.error('FAIL: ' + msg);
    process.exit(1);
}

export function assert(cond, msg) {
    if (!cond) fail(msg);
}

/**
 * Build a vm sandbox with canned axios handlers and canary collections.
 * axiosImpls: { get?, post? } — omitted verbs fail loudly if touched.
 * Returns { ctx, alertCalls, errorCalls, warnCalls }.
 */
export function makeSandbox(axiosImpls = {}) {
    const alertCalls = [];
    const errorCalls = [];
    const warnCalls = [];
    const unexpected = (verb) => async () => {
        throw new Error('unexpected axios.' + verb + ' (no network expected in this scenario)');
    };
    const sandboxWindow = {
        location: { origin: 'http://localhost:8001' },
        // Canary: any alert() call is a regression (blocking modal).
        alert(msg) { alertCalls.push(String(msg)); },
    };
    sandboxWindow.window = sandboxWindow;
    const sandboxConsole = {
        log() {}, info() {}, debug() {},
        // Canaries: console.error marks a swallowed crash or surfaced failure;
        // console.warn marks a guarded/aborted action worth asserting on.
        warn(...args) { warnCalls.push(args.map(String).join(' ')); },
        error(...args) { errorCalls.push(args.map(String).join(' ')); },
    };
    const sandbox = {
        window: sandboxWindow,
        alert: sandboxWindow.alert,
        axios: {
            get: axiosImpls.get || unexpected('get'),
            post: axiosImpls.post || unexpected('post'),
        },
        setTimeout, clearTimeout,
        console: sandboxConsole,
        URL, URLSearchParams,
    };
    sandbox.globalThis = sandbox;
    return { ctx: vm.createContext(sandbox), alertCalls, errorCalls, warnCalls };
}

/**
 * Evaluate a real web module file in the sandbox and return its UMD export
 * (the modules attach to `window` when present, else `globalThis`).
 */
export async function loadModuleMethods(ctx, relPath, exportName) {
    const source = await readFile(path.join(ROOT, relPath), 'utf8');
    vm.runInContext(source, ctx, { filename: relPath });
    const methods = vm.runInContext('(window.' + exportName + ' || globalThis.' + exportName + ')', ctx);
    if (!methods || typeof methods !== 'object') {
        fail(relPath + ' did not expose ' + exportName);
    }
    return methods;
}

/**
 * Assemble a component stub: `base` state/spies win over real methods of the
 * same name, every other production method is attached so `this.method()`
 * calls work, and `bind` entries are explicitly bound to the component.
 */
export function makeComponent(methods, { base = {}, bind = [] } = {}) {
    const comp = { ...base };
    for (const [name, fn] of Object.entries(methods)) {
        if (typeof fn === 'function' && !(name in comp)) {
            comp[name] = fn;
        }
    }
    for (const name of bind) {
        if (typeof methods[name] === 'function') {
            comp[name] = methods[name].bind(comp);
        }
    }
    return comp;
}

/** Scenario dispatcher shared by every harness entrypoint. */
export function runScenarios(scenarios, usageName) {
    const scenario = process.argv[2];
    if (!scenario || !scenarios[scenario]) {
        console.error('usage: node ' + usageName + ' <' + Object.keys(scenarios).join('|') + '>');
        process.exit(2);
    }
    scenarios[scenario]().then(
        () => {
            console.log('OK ' + scenario);
            process.exit(0);
        },
        (err) => {
            console.error('FAIL: ' + (err && err.stack || err));
            process.exit(1);
        }
    );
}
