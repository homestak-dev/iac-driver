#!/usr/bin/env bash
# IaC Driver - Infrastructure orchestration and testing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# HTTP server management variables
SERVE_REPOS=false
SERVE_PORT=""
SERVE_TIMEOUT=""
SERVE_REF="_working"  # Default to working tree for dev workflow
SERVE_PID=""
SERVE_OUTPUT=""

# Parse serve-repos flags (before passing to Python CLI)
CLI_ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --serve-repos)
            SERVE_REPOS=true
            shift
            ;;
        --serve-port)
            SERVE_PORT="$2"
            shift 2
            ;;
        --serve-timeout)
            SERVE_TIMEOUT="$2"
            shift 2
            ;;
        --serve-ref)
            SERVE_REF="$2"
            shift 2
            ;;
        *)
            CLI_ARGS+=("$1")
            shift
            ;;
    esac
done

# Server lifecycle management
start_serve_repos() {
    local port_arg=""
    local timeout_arg=""
    [[ -n "$SERVE_PORT" ]] && port_arg="--port $SERVE_PORT"
    [[ -n "$SERVE_TIMEOUT" ]] && timeout_arg="--timeout $SERVE_TIMEOUT"

    SERVE_OUTPUT=$(mktemp)

    # Start server with JSON output for reliable parsing
    # shellcheck disable=SC2086
    "$SCRIPT_DIR/scripts/serve-repos.sh" \
        $port_arg \
        $timeout_arg \
        --json \
        > "$SERVE_OUTPUT" 2>&1 &
    SERVE_PID=$!

    # Wait for JSON output (server writes JSON then blocks)
    local retries=50
    local token=""
    local url=""
    while [[ $retries -gt 0 ]]; do
        if grep -q '"token"' "$SERVE_OUTPUT" 2>/dev/null; then
            # Parse JSON output
            token=$(grep -o '"token": *"[^"]*"' "$SERVE_OUTPUT" | head -1 | cut -d'"' -f4)
            url=$(grep -o '"url": *"[^"]*"' "$SERVE_OUTPUT" | head -1 | cut -d'"' -f4)
            break
        fi
        # Check if server process died
        if ! kill -0 "$SERVE_PID" 2>/dev/null; then
            echo "ERROR: serve-repos failed to start" >&2
            cat "$SERVE_OUTPUT" >&2
            rm -f "$SERVE_OUTPUT"
            return 1
        fi
        sleep 0.1
        ((retries--))
    done

    if [[ -z "$token" ]] || [[ -z "$url" ]]; then
        echo "ERROR: Failed to get token/URL from serve-repos" >&2
        cat "$SERVE_OUTPUT" >&2
        kill "$SERVE_PID" 2>/dev/null || true
        rm -f "$SERVE_OUTPUT"
        return 1
    fi

    # Log with truncated token for security
    echo "HTTP server started: $url (token: ${token:0:8}..., ref: $SERVE_REF)"

    # Export for scenario/ansible to use
    export HOMESTAK_SOURCE="$url"
    export HOMESTAK_TOKEN="$token"
    export HOMESTAK_REF="$SERVE_REF"
}

stop_serve_repos() {
    if [[ -n "${SERVE_PID:-}" ]]; then
        echo "Stopping HTTP server..."
        kill "$SERVE_PID" 2>/dev/null || true
        wait "$SERVE_PID" 2>/dev/null || true
        SERVE_PID=""
    fi
    if [[ -n "${SERVE_OUTPUT:-}" ]] && [[ -f "$SERVE_OUTPUT" ]]; then
        rm -f "$SERVE_OUTPUT"
        SERVE_OUTPUT=""
    fi
}

# Main execution
if [[ "$SERVE_REPOS" == true ]]; then
    start_serve_repos || exit 1
    trap stop_serve_repos EXIT INT TERM
    # Don't use exec - we need bash process to stay alive for trap handler
    python3 "$SCRIPT_DIR/src/cli.py" "${CLI_ARGS[@]}"
    exit_code=$?
    stop_serve_repos
    exit $exit_code
else
    # No serve-repos, safe to exec
    exec python3 "$SCRIPT_DIR/src/cli.py" "${CLI_ARGS[@]}"
fi
