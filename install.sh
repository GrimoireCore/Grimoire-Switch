#!/bin/bash
set -euo pipefail

REPO_SLUG="${GRIMOIRE_SWITCH_REPO_SLUG:-GrimoireCore/Grimoire-Switch}"
RELEASE_API_URL="${GRIMOIRE_SWITCH_RELEASE_API_URL:-https://api.github.com/repos/${REPO_SLUG}/releases/latest}"
DOWNLOAD_BASE_URL="${GRIMOIRE_SWITCH_DOWNLOAD_BASE_URL:-https://github.com/${REPO_SLUG}/releases/download}"
INSTALL_DIR="${GRIMOIRE_SWITCH_INSTALL_DIR:-$HOME/.local/bin}"
INSTALL_PATH="${INSTALL_DIR}/grimoire-switch"
OS_NAME="${GRIMOIRE_SWITCH_UNAME:-$(uname -s)}"
CURL_FLAGS=(--fail --silent --show-error --location --connect-timeout 15 --max-time 60 --retry 3)

usage() {
  cat <<'EOF'
Usage: install.sh [--version <tag>] [--help]

Install Grimoire Switch into ~/.local/bin/grimoire-switch.

Options:
  --version <tag>  Install a specific GitHub Release tag.
  --help           Show this help message.
EOF
}

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

resolve_binary() {
  local name="$1"
  local override="${2:-}"
  local resolved=""

  if [[ -n "$override" ]]; then
    if [[ -x "$override" ]]; then
      printf '%s\n' "$override"
      return 0
    fi
    fail "Missing required executable for ${name}: ${override}"
  fi

  resolved="$(command -v "$name" || true)"
  if [[ -z "$resolved" ]]; then
    fail "Missing required executable: ${name}"
  fi
  printf '%s\n' "$resolved"
}

parse_latest_release_tag() {
  local python_bin="$1"
  "$python_bin" -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except Exception as exc:
    raise SystemExit(f"Failed to parse latest release metadata: {exc}")

tag_name = payload.get("tag_name")
if not tag_name:
    raise SystemExit("Latest release metadata did not include tag_name")

print(tag_name)
'
}

requested_version=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      shift
      [[ $# -gt 0 ]] || fail "--version requires a tag value"
      requested_version="$1"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
  shift
done

if [[ "$OS_NAME" != "Darwin" ]]; then
  fail "Grimoire Switch installer currently supports macOS only."
fi

CURL_BIN="$(resolve_binary "curl" "${GRIMOIRE_SWITCH_CURL_BIN:-}")"
PYTHON_BIN="$(resolve_binary "python3" "${GRIMOIRE_SWITCH_PYTHON_BIN:-}")"

mkdir -p "$INSTALL_DIR"
tmp_file="$(mktemp "${INSTALL_DIR}/grimoire-switch.XXXXXX")"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

if [[ -n "$requested_version" ]]; then
  selected_version="$requested_version"
else
  release_metadata="$("$CURL_BIN" "${CURL_FLAGS[@]}" "$RELEASE_API_URL")" || fail "Failed to fetch latest release metadata from ${RELEASE_API_URL}"
  selected_version="$(printf '%s' "$release_metadata" | parse_latest_release_tag "$PYTHON_BIN")" || fail "Failed to resolve the latest release tag"
fi

download_url="${DOWNLOAD_BASE_URL}/${selected_version}/grimoire_switch.py"
"$CURL_BIN" "${CURL_FLAGS[@]}" "$download_url" -o "$tmp_file" || fail "Failed to download ${download_url}"

chmod +x "$tmp_file"
mv "$tmp_file" "$INSTALL_PATH"
trap - EXIT

printf 'Installed Grimoire Switch %s\n' "$selected_version"
printf 'Binary: %s\n' "$INSTALL_PATH"

case ":${PATH:-}:" in
  *":$INSTALL_DIR:"*)
    printf 'PATH already includes %s\n' "$INSTALL_DIR"
    ;;
  *)
    printf 'Add %s to PATH before running grimoire-switch directly.\n' "$INSTALL_DIR"
    printf 'Example:\n'
    printf '  export PATH="%s:$PATH"\n' "$INSTALL_DIR"
    ;;
esac
