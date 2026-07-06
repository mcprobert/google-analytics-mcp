#!/usr/bin/env bash
#
# Per-user, mount-independent setup for the GA4 MCP server.
#
# The server source lives on a shared network volume that macOS mounts under a
# different name for each user (e.g. /Volumes/Whitehat vs /Volumes/Whitehat1),
# so nothing user-specific may reference an absolute /Volumes path. This script
# installs everything the running user needs into their own $HOME:
#
#   ~/.venvs/ga4-mcp/                 the Python venv (interpreter + package copy)
#   ~/.config/ga4-mcp/service-account.json   the service-account key (optional)
#   <repo>/.mcp.json                  a portable config using ${HOME}
#
# The package is installed NON-editable (copied into the venv), so once set up
# the server no longer reads code from the share and is immune to mount renames.
# Re-run this script after pulling code changes to refresh the installed copy.
#
# Usage:  bash scripts/setup_user_env.sh
set -euo pipefail

# Locate the repo root from this script, whatever the mount is called.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$HOME/.venvs/ga4-mcp"
CONFIG_DIR="$HOME/.config/ga4-mcp"

echo "Repo:   $REPO"
echo "Venv:   $VENV"

# 1. Pick a Python interpreter (3.10+). Prefer the stable framework build if present.
PYBIN="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
[ -x "$PYBIN" ] || PYBIN="$(command -v python3)"
echo "Python: $PYBIN ($("$PYBIN" --version 2>&1))"

# 2. Create the venv and install the package NON-editable (decoupled from the share).
"$PYBIN" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$REPO"

# 3. Copy the service-account key into $HOME, if one ships in docs/ (optional:
#    the server also works with OAuth accounts in ~/.config/ga4-mcp/accounts/).
mkdir -p "$CONFIG_DIR"
sa=""
for f in "$REPO"/docs/*.json; do
  [ -e "$f" ] || continue
  if grep -q '"type": *"service_account"' "$f" 2>/dev/null; then sa="$f"; break; fi
done
if [ -n "$sa" ]; then
  cp "$sa" "$CONFIG_DIR/service-account.json"
  chmod 600 "$CONFIG_DIR/service-account.json"
  echo "Service account -> $CONFIG_DIR/service-account.json"
else
  echo "No service-account key found in docs/ (OK — use OAuth via the add_account tool)."
fi

# 4. Write a portable .mcp.json (gitignored). ${HOME} is expanded per-user by Claude Code.
cat > "$REPO/.mcp.json" <<'JSON'
{
  "mcpServers": {
    "ga4-analytics": {
      "type": "stdio",
      "command": "${HOME}/.venvs/ga4-mcp/bin/python",
      "args": ["-m", "ga4_mcp"],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "${HOME}/.config/ga4-mcp/service-account.json",
        "GA4_PROPERTY_ID": "443025343"
      }
    }
  }
}
JSON
echo "Wrote $REPO/.mcp.json"

# 5. Smoke test: the package must import from the venv, not the share.
#    Run from $HOME so the repo dir isn't on sys.path (cwd would otherwise shadow
#    the installed copy) — this proves the venv works even if the share is gone.
resolved="$(cd "$HOME" && "$VENV/bin/python" -c 'import ga4_mcp; print(ga4_mcp.__file__)')"
case "$resolved" in
  "$VENV"/*) echo "OK: ga4_mcp imports from the venv ($resolved)";;
  *) echo "WARNING: ga4_mcp imported from $resolved (expected under $VENV)"; exit 1;;
esac

echo
echo "Done. Restart Claude Code, then verify with:  claude mcp list | grep ga4"
