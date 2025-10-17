#!/usr/bin/env bash
# This script sets up a local development environment for the USE-IT project (browser-use fork).
# Usage:
#   $ ./bin/setup.sh

### Bash Environment Setup
# http://redsymbol.net/articles/unofficial-bash-strict-mode/
# https://www.gnu.org/software/bash/manual/html_node/The-Set-Builtin.html
# set -o xtrace
# set -x
# shopt -s nullglob
set -o errexit
set -o errtrace
set -o nounset
set -o pipefail
IFS=$'\n'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR"


if [ -f "$SCRIPT_DIR/lint.sh" ]; then
    echo "[√] already inside a cloned USE-IT repo"
else
    echo "[+] Cloning USE-IT repo into current directory: $SCRIPT_DIR"
    git clone https://github.com/Mega-Gorilla/USE-IT.git
    cd USE-IT
fi

echo "[+] Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh

#git checkout main git pull
echo
echo "[+] Setting up venv"
uv venv
echo
echo "[+] Installing packages in venv"
uv sync --dev --all-extras
echo
echo "[i] Tip: copy config.yaml.example to config.yaml and set your LLM API keys (or export OPENAI_API_KEY, etc.)"
echo
uv pip show browser-use

echo "Usage:"
echo "  $ browser-use               use the CLI (package name remains browser-use)"
echo "  or"
echo "  $ source .venv/bin/activate"
echo "  $ ipython                   use the library"
echo "  >>> from browser_use import BrowserSession, Agent"
echo "  >>> await Agent(task='book me a flight to fiji', browser=BrowserSession(headless=False)).run()"
echo ""
