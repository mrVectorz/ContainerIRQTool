#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE="$SCRIPT_DIR/fixture"
TOOL="$REPO_DIR/ContainerIRQTool.sh"

PASS=0
FAIL=0
ERRORS=""

pass() {
  PASS=$((PASS + 1))
  echo "  PASS: $1"
}

fail() {
  FAIL=$((FAIL + 1))
  ERRORS="${ERRORS}\n  FAIL: $1"
  echo "  FAIL: $1"
}

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    pass "$desc"
  else
    fail "$desc (expected '$expected', got '$actual')"
  fi
}

assert_contains() {
  local desc="$1" haystack="$2" needle="$3"
  if echo "$haystack" | grep -qF -- "$needle"; then
    pass "$desc"
  else
    fail "$desc (output does not contain '$needle')"
  fi
}

assert_not_empty() {
  local desc="$1" value="$2"
  if [[ -n "$value" ]]; then
    pass "$desc"
  else
    fail "$desc (value is empty)"
  fi
}

assert_exit_code() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    pass "$desc"
  else
    fail "$desc (expected exit $expected, got $actual)"
  fi
}

# ============================================================
echo "=== ContainerIRQTool E2E Tests ==="
echo ""

# --help
echo "[1] --help flag"
help_out=$($TOOL --help 2>&1) || true
assert_contains "--help shows usage" "$help_out" "--local DIR"
assert_contains "--help shows output-format" "$help_out" "--output-format"
assert_contains "--help shows llm-summary" "$help_out" "llm-summary"

# Bad arguments
echo "[2] Invalid arguments"
set +e
bad_out=$($TOOL --bogus 2>&1); bad_rc=$?
set -e
assert_eq "Unknown option exits non-zero" "1" "$bad_rc"
assert_contains "Unknown option error message" "$bad_out" "Unknown option"

set +e
bad_fmt=$($TOOL --output-format xml 2>&1); bad_fmt_rc=$?
set -e
assert_eq "Bad format exits non-zero" "1" "$bad_fmt_rc"

# Text output
echo "[3] Text output (sosreport fixture)"
text_out=$($TOOL --local "$FIXTURE" --check-violations --check-numa-alignment --check-llc-alignment 2>&1)
assert_contains "Text shows hostname" "$text_out" "testnode01.lab.example.com"
assert_contains "Text shows isolated CPUs" "$text_out" "2-8"
assert_contains "Text shows kernel mask" "$text_out" "fe03"
assert_contains "Text shows banned mask" "$text_out" "1fc"
assert_contains "Text shows violations found" "$text_out" "VIOLATIONS FOUND"
assert_contains "Text shows UPDATE REQUIRED for smp_affinity" "$text_out" "UPDATE REQUIRED"
assert_contains "Text shows NUMA analysis" "$text_out" "NUMA"
assert_contains "Text shows LLC analysis" "$text_out" "LLC"

# JSON output
echo "[4] JSON output validity and structure"
json_out=$($TOOL --local "$FIXTURE" --check-violations --check-numa-alignment --check-llc-alignment --output-format json 2>/dev/null)
echo "$json_out" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "JSON is valid" "0" "$?"

hostname=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['system_info']['hostname'])")
assert_eq "JSON hostname" "testnode01.lab.example.com" "$hostname"

isolated=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['container_analysis']['isolated_cpus_found'])")
assert_eq "JSON isolated_cpus_found" "True" "$isolated"

cpu_count=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['computed_irq_configuration']['host_cpu_count'])")
assert_eq "JSON host_cpu_count" "16" "$cpu_count"

violations_enabled=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['irq_violation_analysis']['enabled'])")
assert_eq "JSON violations enabled" "True" "$violations_enabled"

numa_enabled=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['numa_alignment_analysis']['enabled'])")
assert_eq "JSON NUMA enabled" "True" "$numa_enabled"

llc_enabled=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['llc_alignment_analysis']['enabled'])")
assert_eq "JSON LLC enabled" "True" "$llc_enabled"

action_smp=$(echo "$json_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['recommendations']['default_smp_affinity']['action_required'])")
assert_eq "JSON smp_affinity needs update" "True" "$action_smp"

# IRQ violations content
echo "[5] IRQ violation details"
violation_cpus=$(echo "$json_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)['irq_violation_analysis']['violations']
print(len(d))
")
assert_eq "7 CPUs with violations (CPUs 2-8)" "7" "$violation_cpus"

has_red=$(echo "$json_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)['irq_violation_analysis']['violations']
reds = sum(1 for cpu in d.values() for v in cpu.get('violation_details',[]) if v.get('color_name')=='red')
print('yes' if reds > 0 else 'no')
")
assert_eq "Red (high-rate) violations exist" "yes" "$has_red"

has_green=$(echo "$json_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)['irq_violation_analysis']['violations']
greens = sum(1 for cpu in d.values() for v in cpu.get('violation_details',[]) if v.get('color_name')=='green')
print('yes' if greens > 0 else 'no')
")
assert_eq "Green (zero) violations exist" "yes" "$has_green"

# NUMA alignment content
echo "[6] NUMA alignment details"
numa_containers=$(echo "$json_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)['numa_alignment_analysis']
print(d.get('summary',{}).get('isolated_containers', 0))
")
assert_eq "NUMA found 2 isolated containers" "2" "$numa_containers"

# LLC alignment content
echo "[7] LLC alignment details"
llc_containers=$(echo "$json_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)['llc_alignment_analysis']
print(d.get('summary',{}).get('isolated_containers', 0))
")
assert_eq "LLC found 2 isolated containers" "2" "$llc_containers"

# LLM summary output
echo "[8] LLM summary output"
llm_out=$($TOOL --local "$FIXTURE" --check-violations --check-numa-alignment --check-llc-alignment --output-format llm-summary 2>/dev/null)
echo "$llm_out" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "LLM summary is valid JSON" "0" "$?"

llm_hostname=$(echo "$llm_out" | python3 -c "import sys,json; print(json.load(sys.stdin)['system']['hostname'])")
assert_eq "LLM summary hostname" "testnode01.lab.example.com" "$llm_hostname"

llm_has_violations=$(echo "$llm_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if d.get('irq_violations',{}).get('total_violations',0) > 0 else 'no')
")
assert_eq "LLM summary has violations" "yes" "$llm_has_violations"

llm_has_clean=$(echo "$llm_out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('yes' if d.get('irq_violations',{}).get('clean_irqs') else 'no')
")
assert_eq "LLM summary has clean_irqs" "yes" "$llm_has_clean"

# Size comparison
echo "[9] LLM summary size reduction"
json_size=$(echo "$json_out" | wc -c)
llm_size=$(echo "$llm_out" | wc -c)
is_smaller=$(python3 -c "print('yes' if $llm_size < $json_size else 'no')")
assert_eq "LLM summary is smaller than full JSON" "yes" "$is_smaller"

# Disabled analyses
echo "[10] Disabled analyses show as disabled"
minimal_json=$($TOOL --local "$FIXTURE" --output-format json 2>/dev/null)
echo "$minimal_json" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "Minimal JSON is valid" "0" "$?"

viol_disabled=$(echo "$minimal_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['irq_violation_analysis']['enabled'])")
assert_eq "Violations disabled by default" "False" "$viol_disabled"

numa_disabled=$(echo "$minimal_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['numa_alignment_analysis']['enabled'])")
assert_eq "NUMA disabled by default" "False" "$numa_disabled"

# Python analyzers run independently
echo "[11] Python analyzers run standalone"
set +e
irq_direct=$(python3 "$REPO_DIR/irq_analyzer.py" --sosreport-dir "$FIXTURE" --output-format json 2>/dev/null)
irq_rc=$?
set -e
assert_eq "irq_analyzer.py runs standalone" "0" "$irq_rc"
echo "$irq_direct" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "irq_analyzer.py produces valid JSON" "0" "$?"

set +e
numa_direct=$(python3 "$REPO_DIR/numa_analyzer.py" --sosreport-dir "$FIXTURE" --output-format json 2>/dev/null)
numa_rc=$?
set -e
assert_eq "numa_analyzer.py runs standalone" "0" "$numa_rc"
echo "$numa_direct" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "numa_analyzer.py produces valid JSON" "0" "$?"

set +e
llc_direct=$(python3 "$REPO_DIR/llc_analyzer.py" --sosreport-dir "$FIXTURE" --output-format json 2>/dev/null)
llc_rc=$?
set -e
assert_eq "llc_analyzer.py runs standalone" "0" "$llc_rc"
echo "$llc_direct" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "llc_analyzer.py produces valid JSON" "0" "$?"

# llm_summarize.py standalone pipe
echo "[12] llm_summarize.py standalone pipe"
llm_pipe=$(echo "$json_out" | python3 "$REPO_DIR/llm_summarize.py")
echo "$llm_pipe" | python3 -m json.tool > /dev/null 2>&1
assert_exit_code "llm_summarize.py pipe produces valid JSON" "0" "$?"

# ============================================================
echo ""
echo "=== Results ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [[ $FAIL -gt 0 ]]; then
  echo ""
  echo "Failures:"
  echo -e "$ERRORS"
  exit 1
fi
echo ""
echo "All tests passed."
