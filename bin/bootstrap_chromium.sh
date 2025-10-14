#!/usr/bin/env bash

# Bootstraps a local Chromium binary for browser-use development.
# Creates a virtual environment (if missing), installs dependencies,
# and downloads Playwright's Chromium build into ./.playwright-browsers.

set -o errexit
set -o nounset
set -o pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PLAYWRIGHT_CACHE="${REPO_ROOT}/.playwright-browsers"

log() {
	echo "[bootstrap] $*"
}

ensure_python() {
	if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
		echo "python3 was not found on PATH. Please install Python 3.11+ and re-run this script." >&2
		exit 1
	fi
}

ensure_venv() {
	if [[ ! -d "${VENV_DIR}" ]]; then
		log "Creating virtual environment at ${VENV_DIR}"
		"${PYTHON_BIN}" -m venv "${VENV_DIR}"
	fi
}

venv_python() {
	echo "${VENV_DIR}/bin/python"
}

venv_pip() {
	echo "${VENV_DIR}/bin/pip"
}

venv_playwright() {
	echo "${VENV_DIR}/bin/playwright"
}

install_python_deps() {
	log "Upgrading pip"
	"$(venv_python)" -m pip install --upgrade pip

	log "Installing browser-use in editable mode"
	"$(venv_pip)" install -e "${REPO_ROOT}"

	log "Ensuring Playwright CLI is available"
	"$(venv_pip)" install playwright
}

chromium_already_present() {
	[[ -d "${PLAYWRIGHT_CACHE}" ]] && \
		find "${PLAYWRIGHT_CACHE}" -maxdepth 3 -type f -name "chrome" -print -quit >/dev/null 2>&1
}

install_chromium() {
	if chromium_already_present; then
		log "Chromium binary already present in ${PLAYWRIGHT_CACHE}"
		return
	fi

	log "Downloading Chromium via Playwright"
	mkdir -p "${PLAYWRIGHT_CACHE}"
	PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_CACHE}" "$(venv_playwright)" install chromium
	log "Chromium downloaded to ${PLAYWRIGHT_CACHE}"
}

print_summary() {
	log "Setup complete."
	echo
	echo "Playwright cache directory: ${PLAYWRIGHT_CACHE}"
	echo "Playwright executable: $(venv_playwright)"
	echo
	echo "To use this environment:"
	echo "  source ${VENV_DIR}/bin/activate"
	echo
	echo "If you encounter missing system libraries, run:"
	echo "  PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_CACHE} $(venv_playwright) install-deps"
}

main() {
	ensure_python
	ensure_venv
	# shellcheck source=/dev/null
	source "${VENV_DIR}/bin/activate"

	install_python_deps
	install_chromium
	print_summary
}

main "$@"
