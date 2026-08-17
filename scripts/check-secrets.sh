#!/usr/bin/env bash
# Scan for credentials before they reach git history.
#
# Run manually over the whole tree:      ./scripts/check-secrets.sh
# Run over staged changes only:          ./scripts/check-secrets.sh --staged
#
# Exit 0 = clean, exit 1 = something that looks like a live credential.
#
# Why this exists: this repository is public, and a committed API key is not
# fixed by deleting it in a later commit -- it stays in the history and in every
# fork and clone. The only real fix after a leak is rotating the key. So the
# check runs *before* the commit exists.

# Note: deliberately no `set -u`. macOS ships bash 3.2, where expanding an empty
# array under `set -u` is itself an error -- which would make this script abort
# and, without the guards below, appear to "pass".
set -o pipefail

# Fail closed. Any unexpected error must block the commit rather than allow it:
# a scanner that errors out silently is worse than no scanner, because it
# reports success it did not verify.
trap 'echo "${RED}check-secrets.sh failed unexpectedly -- blocking commit.${NC}" >&2; exit 1' ERR

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; NC=$'\033[0m'

# Files that legitimately contain credential-shaped strings:
#   .env.example      -- placeholders only
#   docker-compose.yml -- throwaway localhost dev password
#   this script + SECURITY.md -- they contain the patterns themselves
ALLOWLIST_REGEX='(\.env\.example|docker-compose\.yml|scripts/check-secrets\.sh|SECURITY\.md|\.githooks/pre-commit)$'

# Documentation placeholders that match the credential patterns but are not
# credentials. Filtered out so the scanner stays quiet enough to be trusted -- a
# check that cries wolf gets bypassed with --no-verify, and then protects nothing.
PLACEHOLDER_REGEX=':(pass|password|PASSWORD|your[-_]?password)@|USER:PASSWORD|your[-_]?api[-_]?key|your-api-key-here|changeme|placeholder|<[A-Za-z_][A-Za-z0-9_ -]*>'

# Credential patterns. Each line: <label>|<regex>
PATTERNS=(
  # Google issues Gemini/AI Studio keys in two shapes. The legacy one is
  # AIza + 35 chars; newer AI Studio keys look like "AQ.<base64ish>" and are
  # ~53 chars. Matching only the legacy form silently passes current keys.
  "Google/Gemini API key (legacy AIza)|AIza[0-9A-Za-z_-]{35}"
  "Google/Gemini API key (AI Studio AQ.)|AQ\.[A-Za-z0-9_-]{30,}"
  "OpenAI API key|sk-[A-Za-z0-9_-]{20,}"
  "Postgres URL with password|postgresql://[^:[:space:]]+:[^@[:space:]]+@"
  "Generic API key assignment|(api[_-]?key|apikey|secret|token)[[:space:]]*[=:][[:space:]]*[\"'][A-Za-z0-9_-]{24,}[\"']"
  "Private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "Google app password|[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}$"
)

# `mapfile` is bash 4+; macOS ships 3.2, so gather the file list with a portable
# while-read loop instead.
if [[ "${1:-}" == "--staged" ]]; then
  FILE_LIST=$(git diff --cached --name-only --diff-filter=ACM) || {
    echo "${RED}Could not list staged files -- blocking commit.${NC}" >&2; exit 1; }
  SCOPE="staged files"
else
  FILE_LIST=$(git ls-files) || {
    echo "${RED}Could not list tracked files -- aborting.${NC}" >&2; exit 1; }
  SCOPE="tracked files"
fi

FILTERED=()
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  [[ -f "$f" ]] || continue
  # `if` context: a non-matching grep is a false condition, not an error, so it
  # does not trip the fail-closed ERR trap.
  if echo "$f" | grep -qE "$ALLOWLIST_REGEX"; then continue; fi
  if ! file "$f" | grep -qE 'text|JSON|empty'; then continue; fi
  FILTERED+=("$f")
done <<< "$FILE_LIST"

if [[ ${#FILTERED[@]} -eq 0 ]]; then
  # Distinguish "genuinely nothing staged" from "the scan never ran".
  if [[ -z "$(echo "$FILE_LIST" | tr -d '[:space:]')" ]]; then
    echo "${GREEN}Nothing to scan (no matching ${SCOPE}).${NC}"
    exit 0
  fi
  echo "${RED}File list was non-empty but nothing was scannable -- blocking.${NC}" >&2
  exit 1
fi

echo "Scanning ${#FILTERED[@]} ${SCOPE} for credentials..."
FOUND=0

for entry in "${PATTERNS[@]}"; do
  label="${entry%%|*}"
  regex="${entry#*|}"
  while IFS= read -r hit; do
    [[ -z "$hit" ]] && continue
    if [[ $FOUND -eq 0 ]]; then
      echo
      echo "${RED}BLOCKED: possible credential(s) found${NC}"
      echo
    fi
    FOUND=1
    # Print file:line but redact the matched value itself, so the scanner
    # never becomes the thing that prints the secret.
    file_part="${hit%%:*}"
    rest="${hit#*:}"
    line_part="${rest%%:*}"
    echo "  ${YELLOW}${label}${NC}"
    echo "    ${file_part}:${line_part}  (value redacted)"
  done < <(grep -rInE "$regex" "${FILTERED[@]}" 2>/dev/null | grep -vE "$PLACEHOLDER_REGEX")
done

# A committed .env is always wrong, regardless of contents.
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  echo
  echo "  ${YELLOW}Environment file tracked by git${NC}"
  echo "    ${f}  -- add it to .gitignore and 'git rm --cached' it"
  FOUND=1
done < <(printf '%s\n' "${FILTERED[@]}" | grep -E '(^|/)\.env$' || true)

echo
if [[ $FOUND -eq 1 ]]; then
  echo "${RED}Commit blocked.${NC} Remove the value, move it to .env, and re-stage."
  echo "If the key was ever pushed, ${RED}rotate it${NC} -- deleting it from a later"
  echo "commit does not remove it from history."
  exit 1
fi

echo "${GREEN}Clean -- no credentials detected.${NC}"
exit 0
