/**
 * utils/queryRender.js
 * Pure query-rendering helpers (placeholder substitution, unresolved checks).
 *
 * All functions take explicit arguments — no Vue `this`.
 *
 * Usage:
 *   QueryRender.hasUnresolved(text)
 *   QueryRender.renderSupportive(q, sourceNotable, aliasMap)
 *   QueryRender.renderPhase2(template, sourceNotable, aliasMap)
 */
(function (global) {
    'use strict';

    const DEFAULT_ALIAS_MAP = {
        host: ['host', 'destination_nt_hostname', 'destination', 'destination_ip'],
        dest: ['destination', 'destination_ip', 'host'],
        user: ['user', 'username', 'user_identity'],
        account: ['user', 'username', 'user_identity'],
        process: ['process', 'ProcessName', 'process_name', 'Image', 'image', 'file_name', 'value'],
        dest_ip: ['dest_ip', 'RemoteAddress', 'destination_ip', 'dest'],
        src_ip: ['src_ip', 'source_ip'],
        source_ip: ['source_ip', 'src_ip']
    };

    const PLACEHOLDER_RE = /\$([A-Za-z0-9_]+)\$/g;

    function hasUnresolved(text) {
        return /\$[A-Za-z0-9_]+\$/.test((text || '').toString());
    }

    /**
     * Substitute $placeholder$ tokens using fields from a notable + optional alias map.
     * @param {object} q - object with .spl_query (or plain string treated as template)
     * @param {object|null} sourceNotable - notable with .raw_fields or .fields
     * @param {object|null} aliasMap - map of alias -> array of field names (optional)
     */
    function renderSupportive(q, sourceNotable, aliasMap) {
        const text = (typeof q === 'string' ? q : (q && q.spl_query) || '').toString();
        if (!sourceNotable) {
            return text;
        }

        const fields = sourceNotable.raw_fields || sourceNotable.fields || {};
        const aliases = (aliasMap && Object.keys(aliasMap).length > 0)
            ? aliasMap
            : DEFAULT_ALIAS_MAP;

        function resolveValue(name) {
            const lower = name.toLowerCase();
            let value = '';

            if (aliases[lower]) {
                for (const key of aliases[lower]) {
                    if (fields[key]) {
                        value = fields[key];
                        break;
                    }
                }
            }

            if (!value) {
                if (Object.prototype.hasOwnProperty.call(fields, name)) {
                    value = fields[name];
                } else {
                    const keys = Object.keys(fields);
                    for (const key of keys) {
                        if (key.toLowerCase() === lower) {
                            value = fields[key];
                            break;
                        }
                    }
                }
            }

            if (typeof value === 'string' && value.includes('\\')) {
                return value.replace(/\\/g, '\\\\');
            }
            return value;
        }

        return text.replace(PLACEHOLDER_RE, (match, name) => {
            const value = resolveValue(name);
            return value ? value : match;
        });
    }

    function renderPhase2(template, sourceNotable, aliasMap) {
        return renderSupportive({ spl_query: template }, sourceNotable, aliasMap);
    }

    const QueryRender = {
        hasUnresolved,
        renderSupportive,
        renderPhase2,
        DEFAULT_ALIAS_MAP
    };

    global.QueryRender = QueryRender;

})(typeof window !== 'undefined' ? window : globalThis);
