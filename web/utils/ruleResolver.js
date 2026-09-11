/*
 * Resolve an incoming notable to the canonical detection rule family.
 *
 * Notables imported from Splunk ES do not always preserve the platform's
 * internal rule_id. Use stable IDs when available, then fall back to
 * correlation labels, titles, descriptions, and recognizable field
 * signatures so the correct evidence/query pack is still selected.
 */
(function () {
    const stopWords = new Set([
        'a', 'an', 'and', 'alert', 'detection', 'endpoint', 'event',
        'for', 'in', 'possible', 'rule', 'the', 'of', 'on', 'security'
    ]);

    const aliases = {
        linux_ssh_key_creation: [
            'linux ssh key creation',
            'ssh key file creation',
            'ssh authorized key added',
            'authorized keys',
            'authorized_keys'
        ]
    };

    function normalize(value) {
        return String(value || '')
            .replace(/([a-z])([A-Z])/g, '$1 $2')
            .toLowerCase()
            .replace(/[_-]+/g, ' ')
            .replace(/[^a-z0-9]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function tokens(value) {
        return new Set(normalize(value).split(' ').filter(t => t && !stopWords.has(t)));
    }

    function overlap(left, right) {
        if (!left.size || !right.size) return 0;
        let shared = 0;
        left.forEach(token => { if (right.has(token)) shared += 1; });
        return shared / Math.max(left.size, right.size);
    }

    function valuesFromNotable(notable) {
        const fields = (notable && (notable.raw_fields || notable.fields)) || {};
        return Object.keys(fields).map(key => `${key} ${fields[key]}`).join(' ');
    }

    function resolveCanonicalRule({ case_, notable, availableRules }) {
        const rules = Array.isArray(availableRules) ? availableRules : [];
        if (!case_ || !rules.length) return null;

        const incomingId = String(case_.rule_id || '').trim();
        const exact = rules.find(rule => String(rule.rule_id || '').trim() === incomingId);
        if (exact) return exact;

        const incomingText = [
            case_.rule_name,
            case_.correlation_search,
            case_.title,
            case_.description,
            notable && notable.correlation_search,
            notable && notable.title,
            notable && notable.sanitized_text,
            valuesFromNotable(notable)
        ].filter(Boolean).join(' ');
        const incomingNormalized = normalize(incomingText);
        const incomingTokens = tokens(incomingText);

        let best = null;
        for (const rule of rules) {
            const id = String(rule.rule_id || '').trim();
            const label = String(rule.rule_name || '');
            const canonical = normalize(`${id} ${label}`);
            const canonicalTokens = tokens(canonical);
            let score = overlap(incomingTokens, canonicalTokens) * 0.72;
            let reason = 'field and label similarity';

            if (incomingNormalized && normalize(label) === normalize(case_.rule_name)) {
                score = Math.max(score, 0.98);
                reason = 'exact rule name';
            }

            for (const alias of (aliases[id] || [])) {
                if (incomingNormalized.includes(normalize(alias))) {
                    score = Math.max(score, 0.94);
                    reason = `known alias: ${alias}`;
                }
            }

            if (id === 'linux_ssh_key_creation') {
                const signature = incomingText.toLowerCase();
                const matches = [
                    /authorized[_ ]keys/.test(signature),
                    /ssh[- ]keygen/.test(signature),
                    /\.ssh/.test(signature),
                    /linux\s+ssh/.test(signature),
                    /auditd|type=syscall|type=path/.test(signature)
                ].filter(Boolean).length;
                if (matches >= 2) {
                    score = Math.max(score, 0.96);
                    reason = 'Linux SSH authorized_keys field signature';
                }
            }

            if (!best || score > best.score) best = { rule, score, reason };
        }

        if (!best || best.score < 0.48) return null;
        return Object.assign({}, best.rule, {
            _resolution: { score: Math.round(best.score * 100), reason: best.reason }
        });
    }

    window.RuleFamilyResolver = { resolveCanonicalRule };
})();
