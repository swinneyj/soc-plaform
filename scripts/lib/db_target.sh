#!/usr/bin/env bash
# Shared database-target resolution for the launchers.
#
# Sourced by scripts/dev and scripts/start AFTER .env has been sourced, so the
# same precedence applies to both and the two cannot drift apart. `db.target`
# stays the source of truth for URL construction, reporting and pinging; this
# file only picks the target and pulls the shared URL out of the keychain when
# nothing else supplied it.
#
# Caller must define: PY (interpreter), SCRIPT_DIR (scripts/).
# Caller must call:   resolve_db_target local|shared
#
# Sets on success:
#   DB_TARGET            local|shared
#   DB_WAS_SET           1 when DATABASE_URL was already set before resolution
#   DATABASE_URL         exported, resolved for that target
#   SHARED_DATABASE_URL  exported in shared mode
#   DB_SUMMARY           "local 127.0.0.1:5432/soc_platform (source: ...)"
#
# Exit codes: 1 = shared target without a shared URL (fail closed, never a
# silent local fallback — that would split the shared dataset unnoticed);
# 2 = unrecognised target.

resolve_db_target() {
    local requested="${1:-local}"
    case "$requested" in
        local|shared) ;;
        *)
            echo "✗ database target must be 'local' or 'shared' (got '$requested')" >&2
            exit 2
            ;;
    esac

    DB_WAS_SET="${DATABASE_URL:+1}"
    DB_TARGET="$requested"
    export SOC_DB_TARGET="$DB_TARGET"

    if [ "$DB_TARGET" = "shared" ]; then
        if [ -z "${SHARED_DATABASE_URL:-}" ] && [ -x "$SCRIPT_DIR/secrets-keychain" ]; then
            # Fallback chain keeps the historical keychain layout working: the
            # Neon URL lived under DATABASE_URL before the local-first split.
            SHARED_DATABASE_URL="$("$SCRIPT_DIR/secrets-keychain" get SHARED_DATABASE_URL 2>/dev/null \
                || "$SCRIPT_DIR/secrets-keychain" get DATABASE_URL 2>/dev/null || true)"
            [ -n "$SHARED_DATABASE_URL" ] && export SHARED_DATABASE_URL
        fi
        if [ -z "${SHARED_DATABASE_URL:-}" ]; then
            echo "✗ shared target needs SHARED_DATABASE_URL (env, BWS or keychain)." >&2
            echo "  Refusing to fall back to local: that would silently split the shared dataset." >&2
            exit 1
        fi
        if [ -z "${DATABASE_URL:-}" ]; then
            DATABASE_URL="$SHARED_DATABASE_URL"
            export DATABASE_URL
        fi
    elif [ -z "${DATABASE_URL:-}" ]; then
        DATABASE_URL="$("$PY" -m db.target --print)" || exit 1
        export DATABASE_URL
    elif printf '%s' "$DATABASE_URL" | grep -q 'neon\.tech'; then
        echo "  ⚠ DATABASE_URL is explicitly set to Neon — using it (local target overridden)." >&2
    fi

    # The banner describes the *target*, not the resolved export, so a local
    # run does not label itself "explicit" merely because we just set it.
    if [ -n "$DB_WAS_SET" ]; then
        DB_SUMMARY="$("$PY" -m db.target --summary 2>/dev/null || true)"
    else
        DB_SUMMARY="$(env -u DATABASE_URL SOC_DB_TARGET="$DB_TARGET" "$PY" -m db.target --summary 2>/dev/null || true)"
    fi
    return 0
}
