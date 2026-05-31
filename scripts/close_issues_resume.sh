#!/usr/bin/env bash
set -euo pipefail
SHA="8280d356681dec8ce035bab2a864e788799431bf"
REPO="KorenKrita/nokori"

close_fixed() {
  local n="$1" msg="$2"
  gh issue comment "$n" --repo "$REPO" --body "**Fixed in \`${SHA:0:7}\`** ($SHA)

$msg" || return 1
  gh issue close "$n" --repo "$REPO" --reason completed || return 1
  sleep 0.5
}

close_wontfix() {
  local n="$1" msg="$2"
  gh issue comment "$n" --repo "$REPO" --body "**Closed — no code change** (triage)

$msg" || return 1
  gh issue close "$n" --repo "$REPO" --reason "not planned" || return 1
  sleep 0.5
}

for n in $(seq 6 68); do
  echo "=== #$n ==="
  case $n in
  6) close_fixed 6 "Return empty injection when no rule content rendered (same as #1)." ;;
  7) close_fixed 7 "Guard when fetchone returns None after insert in merger." ;;
  8) close_fixed 8 "SessionEnd catches missing transcript on stat." ;;
  9) close_fixed 9 "Extract handles vanished transcript after merge." ;;
  10) close_fixed 10 "UserPromptSubmit catches OSError from retrieve." ;;
  11) close_fixed 11 "Import rejects empty trigger/action." ;;
  12) close_fixed 12 "_was_extracted returns False on stat OSError." ;;
  13) close_fixed 13 "Candidate cleanup verifies status=candidate in txn." ;;
  14) close_fixed 14 "candidate_key excludes confidence." ;;
  15) close_fixed 15 "hot_cache uses mtime > current." ;;
  16) close_fixed 16 "reactivate_dormant increments hit_count." ;;
  17) close_fixed 17 "CJK punctuation excluded from bigrams." ;;
  18) close_wontfix 18 "Multi-chunk query embed: follow-up feature." ;;
  19) close_fixed 19 "Skip corrupt embedding blobs." ;;
  20) close_fixed 20 "Compressor enforces token budget." ;;
  21) close_fixed 21 "Skip promotion when project_id is NULL." ;;
  22) close_fixed 22 "HOT spillover before warm list." ;;
  23) close_fixed 23 "add requires trigger len >= 3." ;;
  24) close_fixed 24 "Random UUID fallback session_id." ;;
  25) close_wontfix 25 "ReDoS: document risky gate_matcher patterns." ;;
  26) close_wontfix 26 "PID atomic write deferred." ;;
  27) close_wontfix 27 "Job TOCTOU rare; acceptable." ;;
  28) close_fixed 28 "TOML decode fail-open." ;;
  29) close_wontfix 29 "Embed timeout product default unchanged." ;;
  30) close_wontfix 30 "DB open on PreToolUse when gate enabled." ;;
  31) close_wontfix 31 "Per-process config parse acceptable." ;;
  32) close_wontfix 32 "Session project_id cache + project_id_from_git on main." ;;
  33) close_wontfix 33 "Session touch debounce deferred." ;;
  34) close_wontfix 34 "BM25 cache key opt deferred." ;;
  35) close_wontfix 35 "Marker has hash; DB fallback legacy." ;;
  36) close_wontfix 36 "Marker read cost acceptable." ;;
  37) close_wontfix 37 "Lazy row_to_rule deferred." ;;
  38) close_wontfix 38 "Config reduction v0.2." ;;
  39) close_wontfix 39 "Windows branch out of scope." ;;
  40) close_wontfix 40 "strict flag kept for debug." ;;
  41) close_wontfix 41 "Retrieve layering kept for tests." ;;
  42) close_wontfix 42 "SQLite markers v0.2." ;;
  43) close_wontfix 43 "LLMAdapter DI for tests." ;;
  44) close_wontfix 44 "Extract mode flags kept." ;;
  45) close_wontfix 45 "RRF when embed on; BM25-only default OK." ;;
  46) close_fixed 46 "strict documented at top level in example." ;;
  47) close_fixed 47 "log_level validated." ;;
  48) close_fixed 48 "Dismiss error message fixed." ;;
  49) close_wontfix 49 "Hook path uses read_tail; 50MiB is extract-only." ;;
  50) close_wontfix 50 "Install timeout 5s kept." ;;
  51) close_wontfix 51 "Index flock v0.2." ;;
  52) close_wontfix 52 "Needs host prompt_hash in tool payload." ;;
  53) close_wontfix 53 "--simulate-hook CLI follow-up." ;;
  54) close_wontfix 54 "Dismiss+marker same-turn edge case." ;;
  55) close_wontfix 55 "BM25 set dedup is standard." ;;
  56) close_wontfix 56 "SIGTERM embed server deferred." ;;
  57) close_wontfix 57 "Evidence log cap deferred." ;;
  58) close_wontfix 58 "Frozen Rule v0.2." ;;
  59) close_wontfix 59 "Import single txn deferred." ;;
  60) close_wontfix 60 "Use delete_rule_cascade; CASCADE needs migration." ;;
  61) close_wontfix 61 "Session file cleanup deferred." ;;
  62) close_fixed 62 "short_id prefix collision both ways." ;;
  63) close_wontfix 63 "status double-read deferred." ;;
  64) close_fixed 64 "Log invalid hook JSON." ;;
  65) close_fixed 65 "logs tail via deque." ;;
  66) close_wontfix 66 "Embed spawn lock deferred." ;;
  67) close_fixed 67 "edit active clears archive fields." ;;
  68) close_fixed 68 "maintain prints injection_cleanup." ;;
  esac
done

echo "Resume complete."
