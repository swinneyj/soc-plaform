#!/usr/bin/env node
/**
 * check_web_option_keys — fail on duplicate keys inside a single object
 * literal in the shipped web/ JavaScript.
 *
 * Why this gate exists (C1B.3 regression, commit c795bdb): web/app.modular.js
 * gained a SECOND `computed:` key inside createApp({ ... }). Object literals
 * keep only the last duplicate, so the entire first 225-line computed block
 * was silently discarded at parse time: node --check passed, pytest passed,
 * the harness passed — while every case table/list rendered empty. Vue's
 * dev-build warning was the only runtime trace, and a production build
 * swallows even that. A duplicate key inside one object literal is always a
 * shadowing bug, so this scans every object literal in web/ — not just the
 * root-app options — and exits 1 with file:line for each collision.
 *
 * How it works: a small state machine walks the source tracking strings,
 * template literals (incl. ${} nesting), comments, and regex literals so
 * brace depth stays honest, collects property keys per object-literal frame,
 * and reports keys seen twice in the same frame. Same-named keys in SIBLING
 * objects are legitimate and are not flagged.
 *
 * Usage:
 *   node scripts/check_web_option_keys.mjs            # default web/ list
 *   node scripts/check_web_option_keys.mjs <file...>  # explicit files
 *
 * Exit: 0 = clean, 1 = duplicate key(s), 2 = usage/read error.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function defaultFiles() {
    const files = [];
    const topLevel = path.join(ROOT, 'web');
    for (const name of readdirSync(topLevel).sort()) {
        if (name.endsWith('.js')) files.push(path.join(topLevel, name));
    }
    for (const dir of ['modules', 'components', 'utils']) {
        const full = path.join(topLevel, dir);
        try {
            if (!statSync(full).isDirectory()) continue;
        } catch { continue; }
        for (const name of readdirSync(full).sort()) {
            if (name.endsWith('.js')) files.push(path.join(full, name));
        }
    }
    return files;
}

// `{` opens an object literal only in value positions (prev char is one of
// these, or the previous word is `return`); every other `{` is a block
// statement / function body, whose "keys" (labels, keywords) must NOT be
// collected or `if (x) { ... }` repeated twice would false-positive.
const OBJ_PREV = new Set(['(', '[', '=', ':', ',', '?', '!', '&', '|', '+',
    '-', '*', '/', '%', '^', '<', '>', '{', '', ';']);
const BLOCK_WORDS = new Set(['else', 'do', 'try', 'finally']);
// `/` starts a regex literal (skip it whole) only in these positions;
// elsewhere it is division and must not be swallowed.
const REGEX_PREV = new Set(['(', '[', '=', ':', ',', '?', '!', '&', '|', '+',
    '-', '*', '/', '%', '^', '<', '{', '}', ';', '']);
const REGEX_WORDS = new Set(['return', 'typeof', 'instanceof', 'in', 'of',
    'new', 'delete', 'void', 'case', 'do', 'else', 'yield', 'await']);

/**
 * Scan one source string. Returns [{ name, line, firstLine }] per duplicate.
 */
export function findDuplicateKeys(src, file = '<input>') {
    const violations = [];
    let i = 0;
    let line = 1;
    const n = src.length;
    const stack = [{ type: 'root', keys: [] }];
    let expectKey = false;      // only meaningful when top frame is 'obj'
    let prevSig = '';           // last significant code character
    let lastWord = '';

    const adv = (count) => {
        for (let k = 0; k < count && i < n; k++) {
            if (src[i] === '\n') line++;
            i++;
        }
    };
    const top = () => stack[stack.length - 1];
    const inCode = (t) => t === 'root' || t === 'obj' || t === 'block'
        || t === 'arr' || t === 'expr';

    const isIdentStart = (c) => /[A-Za-z_$]/.test(c);
    const isIdentPart = (c) => /[A-Za-z0-9_$]/.test(c);

    const recordKey = (name, atLine) => {
        const frame = stack[stack.length - 1];
        const seen = frame.keys.find((k) => k.name === name);
        if (seen) {
            violations.push({ file, name, line: atLine, firstLine: seen.line });
        } else {
            frame.keys.push({ name, line: atLine });
        }
    };

    while (i < n) {
        const t = top().type;

        // ---- string / comment / template frames --------------------------
        if (t === 'lc') {
            if (src[i] === '\n') { adv(1); stack.pop(); continue; }
            adv(1); continue;
        }
        if (t === 'bc') {
            if (src[i] === '*' && src[i + 1] === '/') { adv(2); stack.pop(); continue; }
            adv(1); continue;
        }
        if (t === 'sq' || t === 'dq') {
            const q = t === 'sq' ? "'" : '"';
            if (src[i] === '\\') { adv(2); continue; }
            if (src[i] === q) { adv(1); stack.pop(); prevSig = q; continue; }
            if (src[i] === '\n') { adv(1); continue; } // tolerate unterminated
            adv(1); continue;
        }
        if (t === 'tpl') {
            if (src[i] === '\\') { adv(2); continue; }
            if (src[i] === '$' && src[i + 1] === '{') {
                stack.push({ type: 'expr' }); adv(2);
                prevSig = ''; lastWord = ''; continue;
            }
            if (src[i] === '`') { adv(1); stack.pop(); prevSig = '`'; continue; }
            adv(1); continue;
        }

        // ---- code frames --------------------------------------------------
        if (!inCode(t)) { adv(1); continue; } // defensive; never expected
        const c = src[i];

        // comments before regex check
        if (c === '/' && src[i + 1] === '/') { stack.push({ type: 'lc' }); adv(2); continue; }
        if (c === '/' && src[i + 1] === '*') { stack.push({ type: 'bc' }); adv(2); continue; }

        if (c === '`') { stack.push({ type: 'tpl' }); adv(1); prevSig = '`'; lastWord = ''; continue; }

        if (c === "'" || c === '"') {
            // A quoted property key: `'name' :` — record it inline.
            if (t === 'obj' && expectKey) {
                const q = c;
                let j = i + 1;
                let name = '';
                let closed = false;
                while (j < n) {
                    if (src[j] === '\\') { name += src[j] + (src[j + 1] || ''); j += 2; continue; }
                    if (src[j] === q) { closed = true; break; }
                    if (src[j] === '\n') break;
                    name += src[j]; j++;
                }
                let k = j + (closed ? 1 : 0);
                while (k < n && /\s/.test(src[k])) k++;
                if (closed && (src[k] === ':' || src[k] === '(')) {
                    const keyLine = line;
                    recordKey(name, keyLine);
                    expectKey = false;
                    adv(j + 1 - i);
                    prevSig = q; lastWord = '';
                    continue;
                }
            }
            stack.push({ type: c === "'" ? 'sq' : 'dq' }); adv(1);
            prevSig = c; lastWord = ''; continue;
        }

        if (c === '{' || c === '}') {
            if (c === '}') {
                const popped = stack.pop();
                if (!popped || popped.type === 'root') { adv(1); continue; }
                // duplicate obj keys are collected at record time, not here
                adv(1); prevSig = '}'; lastWord = '';
                // returning from an expr restores template context implicitly
                if (top().type === 'obj') expectKey = false;
                continue;
            }
            // opening: classify block vs object literal
            let kind;
            if (prevSig === ')' || prevSig === '>') kind = 'block';
            else if (BLOCK_WORDS.has(lastWord)
                && prevSig === lastWord[lastWord.length - 1]) kind = 'block';
            else if (OBJ_PREV.has(prevSig) || lastWord === 'return') kind = 'obj';
            else kind = 'block';
            stack.push({ type: kind, keys: [] });
            if (kind === 'obj') expectKey = true;
            adv(1); prevSig = '{'; lastWord = ''; continue;
        }

        if (c === '[') { stack.push({ type: 'arr' }); adv(1); prevSig = '['; lastWord = ''; continue; }
        if (c === ']') { if (top().type === 'arr') stack.pop(); adv(1); prevSig = ']'; lastWord = ''; continue; }

        if (c === '/') {
            const wordRelevant = lastWord && prevSig === lastWord[lastWord.length - 1];
            const asRegex = REGEX_PREV.has(prevSig)
                || (wordRelevant && REGEX_WORDS.has(lastWord));
            if (asRegex) {
                adv(1);
                let inClass = false;
                while (i < n) {
                    const d = src[i];
                    if (d === '\\') { adv(2); continue; }
                    if (d === '\n') break;        // unterminated: bail as code
                    if (d === '[') inClass = true;
                    else if (d === ']') inClass = false;
                    else if (d === '/' && !inClass) { adv(1); break; }
                    adv(1);
                }
                while (i < n && /[a-z]/.test(src[i])) adv(1); // flags
                prevSig = '/'; lastWord = '';
                continue;
            }
            adv(1); prevSig = '/'; lastWord = ''; continue;
        }

        if (isIdentStart(c)) {
            let j = i;
            while (j < n && isIdentPart(src[j])) j++;
            const ident = src.slice(i, j);
            if (t === 'obj' && expectKey) {
                let k = j;
                while (k < n && (src[k] === ' ' || src[k] === '\t')) k++;
                if (src[k] === ':' || src[k] === '(') {
                    recordKey(ident, line);
                    expectKey = false;
                    adv(j - i);
                    prevSig = ident[ident.length - 1]; lastWord = ident;
                    continue;
                }
            }
            adv(j - i);
            prevSig = ident[ident.length - 1]; lastWord = ident;
            continue;
        }

        if (c === ',') {
            adv(1);
            if (t === 'obj') expectKey = true;
            prevSig = ','; lastWord = '';
            continue;
        }
        if (c === '=' && src[i + 1] === '>') { adv(2); prevSig = '>'; lastWord = ''; continue; }
        if (c === '.' && src[i + 1] === '.' && src[i + 2] === '.') {
            adv(3); prevSig = '.'; lastWord = ''; continue;
        }
        if (/[0-9]/.test(c)) {
            let j = i;
            while (j < n && /[\w.]/.test(src[j])) j++;
            adv(j - i);
            prevSig = src[j - 1] || c; lastWord = '';
            continue;
        }
        if (/\s/.test(c)) { adv(1); continue; }
        adv(1);
        prevSig = c; lastWord = '';
    }
    return violations;
}

// ---- CLI ---------------------------------------------------------------
const args = process.argv.slice(2);
let files = args.length ? args.map((f) => path.resolve(f)) : defaultFiles();
if (!files.length) {
    console.error('FAIL: no files to scan');
    process.exit(2);
}
let total = 0;
let failed = false;
for (const file of files) {
    let src;
    try {
        src = readFileSync(file, 'utf8');
    } catch (err) {
        console.error(`FAIL: cannot read ${file}: ${err.message}`);
        process.exit(2);
    }
    for (const v of findDuplicateKeys(src, path.relative(ROOT, file))) {
        failed = true;
        total++;
        console.error(
            `FAIL: ${v.file}:${v.line}: duplicate key \`${v.name}\` `
            + `(first defined at line ${v.firstLine}) — object literals keep `
            + `only the last one; the earlier definition is silently discarded`);
    }
}
if (failed) {
    console.error(`FAIL: ${total} duplicate object-literal key(s) in web/ JS`);
    process.exit(1);
}
console.log(`OK: no duplicate object-literal keys (${files.length} files scanned)`);
