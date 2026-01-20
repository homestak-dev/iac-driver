#!/usr/bin/env bash
# serve-repos.sh - HTTP server for local repo serving during dev workflows
#
# Creates temporary bare repos from working trees (including uncommitted changes)
# and serves them via HTTP with Bearer token authentication.
#
# Usage:
#   ./scripts/serve-repos.sh [OPTIONS]
#
# Options:
#   --port PORT          Port to serve on (default: OS-assigned)
#   --bind ADDR          Address to bind to (default: 0.0.0.0)
#   --timeout SECONDS    Auto-shutdown after N seconds (default: none)
#   --token TOKEN        Use specific token (default: auto-generated)
#   --advertise-url URL  URL to advertise (default: auto-detected)
#   --repos DIR          Parent directory containing repos (default: ..)
#   --exclude REPO       Exclude specific repo (repeatable)
#   --json               Output connection info as JSON (for programmatic use)
#   --help               Show help message
#   --version            Show version
#
set -euo pipefail

VERSION="0.37"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS_DIR="${REPOS_DIR:-$(dirname "$SCRIPT_DIR")}"

# Defaults
PORT=""  # Empty = OS-assigned
BIND="0.0.0.0"
TIMEOUT=""
TOKEN=""
ADVERTISE_URL=""
JSON_OUTPUT=false
KNOWN_REPOS=(bootstrap ansible iac-driver tofu packer)
EXCLUDE_REPOS=(site-config)  # site-config excluded by default (secrets)
SERVE_DIR=""  # Set during execution
SERVER_PID=""

show_help() {
    cat << 'EOF'
serve-repos.sh - HTTP server for local repo serving during dev workflows

Usage:
  ./scripts/serve-repos.sh [OPTIONS]

Options:
  --port PORT          Port to serve on (default: OS-assigned)
  --bind ADDR          Address to bind to (default: 0.0.0.0)
  --timeout SECONDS    Auto-shutdown after N seconds (default: none)
  --token TOKEN        Use specific token (default: auto-generated)
  --advertise-url URL  URL to advertise (default: auto-detected from hostname -I)
  --repos DIR          Parent directory containing repos (default: ..)
  --exclude REPO       Exclude specific repo (repeatable)
  --json               Output connection info as JSON (for programmatic use)
  --help               Show help message
  --version            Show version

Examples:
  # Serve all repos with OS-assigned port
  ./scripts/serve-repos.sh

  # Serve with auto-shutdown after 1 hour
  ./scripts/serve-repos.sh --timeout 3600

  # Serve with explicit port and token
  ./scripts/serve-repos.sh --port 9000 --token mysecret

  # JSON output for programmatic consumption
  ./scripts/serve-repos.sh --json

  # Use with run.sh (automatic)
  ./run.sh --scenario nested-pve-roundtrip --host father --serve-repos

Bootstrap usage (after starting server):
  HOMESTAK_SOURCE=http://192.0.2.1:54321 \
  HOMESTAK_TOKEN=<token> \
  HOMESTAK_REF=_working \
  ./install.sh
EOF
}

cleanup() {
    local exit_code=$?
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ -n "${SERVE_DIR:-}" ]] && [[ -d "$SERVE_DIR" ]]; then
        rm -rf "$SERVE_DIR"
    fi
    exit "$exit_code"
}

trap cleanup EXIT INT TERM

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        --bind)
            BIND="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --token)
            TOKEN="$2"
            shift 2
            ;;
        --advertise-url)
            ADVERTISE_URL="$2"
            shift 2
            ;;
        --repos)
            REPOS_DIR="$2"
            shift 2
            ;;
        --exclude)
            EXCLUDE_REPOS+=("$2")
            shift 2
            ;;
        --json)
            JSON_OUTPUT=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        --version)
            echo "serve-repos.sh v$VERSION"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Use --help for usage information" >&2
            exit 1
            ;;
    esac
done

# Generate token if not provided (16 alphanumeric chars)
if [[ -z "$TOKEN" ]]; then
    TOKEN=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 16)
fi

# Create temp directory for bare repos
SERVE_DIR=$(mktemp -d -t serve-repos.XXXXXX)

# Track repo status for JSON output
declare -a REPO_STATUS_JSON

# Create bare repo with _working branch containing current working tree state
create_bare_repo() {
    local repo_name=$1
    local repo_path="$REPOS_DIR/$repo_name"
    local bare_path="$SERVE_DIR/$repo_name.git"

    # Check if it's a git repo
    if [[ ! -d "$repo_path/.git" ]]; then
        if [[ "$JSON_OUTPUT" != true ]]; then
            echo "  - $repo_name (not found, skipping)"
        fi
        REPO_STATUS_JSON+=("{\"name\": \"$repo_name\", \"status\": \"not_found\"}")
        return 1
    fi

    # Create bare clone (this preserves all branches and tags)
    git clone --bare --quiet "$repo_path" "$bare_path" 2>/dev/null

    # Create _working branch with current working tree state
    local changes=0
    pushd "$repo_path" > /dev/null

    # Count uncommitted changes
    changes=$(git status --porcelain 2>/dev/null | wc -l)

    if [[ $changes -gt 0 ]]; then
        # Backup and restore index to preserve staged files
        local git_dir
        git_dir=$(git rev-parse --git-dir)
        local index_backup
        index_backup=$(mktemp)
        cp "$git_dir/index" "$index_backup"

        # Add all files, create tree and commit
        git add -A 2>/dev/null
        local tree
        tree=$(git write-tree)
        local commit
        commit=$(git commit-tree "$tree" -p HEAD -m "Working tree snapshot for dev workflow")

        # Restore original index
        cp "$index_backup" "$git_dir/index"
        rm -f "$index_backup"

        # Push the commit to the bare repo's _working branch
        # This transfers the objects and creates the ref
        git push --quiet "$bare_path" "$commit:refs/heads/_working" 2>/dev/null

        if [[ "$JSON_OUTPUT" != true ]]; then
            echo "  + $repo_name.git (_working: $changes uncommitted files)"
        fi
        REPO_STATUS_JSON+=("{\"name\": \"$repo_name\", \"status\": \"ok\", \"uncommitted\": $changes}")
    else
        # No changes - _working points to HEAD
        local head
        head=$(git rev-parse HEAD)
        # For clean repos, we can use update-ref since all objects are in the bare clone
        git -C "$bare_path" update-ref refs/heads/_working "$head"

        if [[ "$JSON_OUTPUT" != true ]]; then
            echo "  + $repo_name.git (_working: clean)"
        fi
        REPO_STATUS_JSON+=("{\"name\": \"$repo_name\", \"status\": \"ok\", \"uncommitted\": 0}")
    fi

    popd > /dev/null

    # Enable dumb HTTP protocol
    git -C "$bare_path" update-server-info
}

# Generate Python HTTP server script with embedded token
generate_server_script() {
    local port_value="${PORT:-0}"  # 0 means OS-assigned

    cat << PYEOF > "$SERVE_DIR/server.py"
#!/usr/bin/env python3
"""HTTP server with Bearer token authentication for git dumb protocol."""
import http.server
import os
import sys

TOKEN = "$TOKEN"
PORT = $port_value
BIND = "$BIND"


class AuthHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that validates Bearer token authentication."""

    def do_GET(self):
        auth = self.headers.get('Authorization', '')
        if auth != f'Bearer {TOKEN}':
            self.send_error(401, "Unauthorized")
            return
        super().do_GET()

    def do_HEAD(self):
        auth = self.headers.get('Authorization', '')
        if auth != f'Bearer {TOKEN}':
            self.send_error(401, "Unauthorized")
            return
        super().do_HEAD()

    def log_message(self, format, *args):
        # Quieter logging - only log non-2xx responses
        if len(args) >= 2:
            status = str(args[1])
            if not status.startswith('2'):
                super().log_message(format, *args)


if __name__ == '__main__':
    directory = sys.argv[1] if len(sys.argv) > 1 else '.'
    os.chdir(directory)

    import socketserver
    with socketserver.TCPServer((BIND, PORT), AuthHandler) as httpd:
        actual_port = httpd.server_address[1]
        # Signal ready with actual port (parsed by shell wrapper)
        print(f"READY:{actual_port}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
PYEOF
}

# Main execution
if [[ "$JSON_OUTPUT" != true ]]; then
    echo "Preparing repos for HTTP serving..."
fi

# Prepare each known repo
for repo in "${KNOWN_REPOS[@]}"; do
    # Check if excluded
    skip=false
    for excluded in "${EXCLUDE_REPOS[@]}"; do
        if [[ "$repo" == "$excluded" ]]; then
            skip=true
            break
        fi
    done

    if [[ "$skip" == true ]]; then
        if [[ "$JSON_OUTPUT" != true ]]; then
            echo "  - $repo (excluded)"
        fi
        REPO_STATUS_JSON+=("{\"name\": \"$repo\", \"status\": \"excluded\"}")
        continue
    fi

    create_bare_repo "$repo" || true
done

# Generate server script
generate_server_script

# Determine server IP for advertise URL
if [[ -z "$ADVERTISE_URL" ]]; then
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
fi

# Start server
SERVER_OUTPUT=$(mktemp)
if [[ -n "$TIMEOUT" ]]; then
    timeout "$TIMEOUT" python3 "$SERVE_DIR/server.py" "$SERVE_DIR" > "$SERVER_OUTPUT" 2>&1 &
else
    python3 "$SERVE_DIR/server.py" "$SERVE_DIR" > "$SERVER_OUTPUT" 2>&1 &
fi
SERVER_PID=$!

# Wait for READY signal with actual port
ACTUAL_PORT=""
for _ in {1..50}; do
    if grep -q "^READY:" "$SERVER_OUTPUT" 2>/dev/null; then
        ACTUAL_PORT=$(grep "^READY:" "$SERVER_OUTPUT" | head -1 | cut -d: -f2)
        break
    fi
    # Check if server died
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ERROR: Server failed to start" >&2
        cat "$SERVER_OUTPUT" >&2
        rm -f "$SERVER_OUTPUT"
        exit 1
    fi
    sleep 0.1
done

rm -f "$SERVER_OUTPUT"

if [[ -z "$ACTUAL_PORT" ]]; then
    echo "ERROR: Server failed to report ready" >&2
    exit 1
fi

# Build advertise URL if not provided
if [[ -z "$ADVERTISE_URL" ]]; then
    ADVERTISE_URL="http://${SERVER_IP}:${ACTUAL_PORT}"
fi

# Output connection info
if [[ "$JSON_OUTPUT" == true ]]; then
    # Build repos JSON array
    REPOS_JSON=$(printf '%s\n' "${REPO_STATUS_JSON[@]}" | paste -sd ',' -)

    cat << JSONEOF
{
  "token": "$TOKEN",
  "url": "$ADVERTISE_URL",
  "port": $ACTUAL_PORT,
  "bind": "$BIND",
  "repos": [$REPOS_JSON]
}
JSONEOF
else
    echo ""
    echo "Token: $TOKEN"
    echo "Serving at $ADVERTISE_URL"
    echo ""
    echo "Bootstrap usage:"
    echo "  HOMESTAK_SOURCE=$ADVERTISE_URL \\"
    echo "  HOMESTAK_TOKEN=$TOKEN \\"
    echo "  HOMESTAK_REF=_working \\"
    echo "  ./install.sh"
    echo ""
    echo "Press Ctrl+C to stop..."
fi

# Wait for server (it's running in background)
wait "$SERVER_PID" 2>/dev/null || true
