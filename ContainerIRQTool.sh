#!/bin/bash

# Parse command line arguments
LOCAL_MODE=false
BASE_DIR="."
CHECK_VIOLATIONS=false
CHECK_NUMA=false
FULL_ANALYSIS=false
OUTPUT_FORMAT="text"

while [[ $# -gt 0 ]]; do
  case $1 in
    --local)
      LOCAL_MODE=true
      if [[ -n $2 && $2 != -* ]]; then
        BASE_DIR="$2"
        shift 2
      else
        echo "Error: --local requires a directory argument"
        exit 1
      fi
      ;;
    --check-violations)
      CHECK_VIOLATIONS=true
      shift
      ;;
    --check-numa-alignment)
      CHECK_NUMA=true
      shift
      ;;
    --full-analysis)
      FULL_ANALYSIS=true
      shift
      ;;
    --output-format)
      if [[ -n $2 && ($2 == "text" || $2 == "json") ]]; then
        OUTPUT_FORMAT="$2"
        shift 2
      else
        echo "Error: --output-format requires 'text' or 'json' as argument"
        exit 1
      fi
      ;;
    -h|--help)
      echo "Usage: $0 [--local /path/to/sosreport] [--check-violations] [--check-numa-alignment] [--full-analysis] [--output-format FORMAT]"
      echo "  --local DIR              Run against sosreport directory instead of live host"
      echo "  --check-violations       Enable IRQ violation analysis (slower, disabled by default)"
      echo "  --check-numa-alignment   Enable NUMA alignment analysis for isolated containers"
      echo "  --full-analysis          Show detailed analysis for all CPUs (default: limit to top 10 most offending)"
      echo "  --output-format FORMAT   Output format: 'text' (default) or 'json'"
      echo "  -h, --help               Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use -h or --help for usage information"
      exit 1
      ;;
  esac
done

# Ensure base directory exists
if [[ ! -d "$BASE_DIR" ]]; then
  echo "Error: Directory '$BASE_DIR' does not exist"
  exit 1
fi

# Gather a list of all the pinned CPUs that should be isolated
ISOLATED_CPUS=""

if [[ "$LOCAL_MODE" == "true" ]]; then
  # Running against sosreport - use Python script for efficient analysis
  SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
  PYTHON_SCRIPT="$SCRIPT_DIR/irq_analyzer.py"
  
  if [[ -f "$PYTHON_SCRIPT" ]]; then
    isolated_cpu_array=($(python3 "$PYTHON_SCRIPT" --sosreport-dir "$BASE_DIR" --output-format summary 2>/dev/null | grep "^ISOLATED_CPUS=" | cut -d= -f2 | tr ',' ' '))
    if [[ ${#isolated_cpu_array[@]} -gt 0 ]]; then
      # Convert array back to comma-separated string
      IFS=','
      ISOLATED_CPUS="${isolated_cpu_array[*]}"
      IFS=$' \t\n'
    fi
  else
    # Fallback to original method if Python script not found
    echo "Warning: Python analyzer not found, using slower fallback method"
    CONTAINERS_DIR="$BASE_DIR/sos_commands/crio/containers"
    if [[ -d "$CONTAINERS_DIR" ]]; then
      for container in $(ls "$CONTAINERS_DIR" 2>/dev/null); do
        cpu_list=$(cat "$CONTAINERS_DIR/$container" 2>/dev/null | jq -r '.info.runtimeSpec.annotations as $anno | select($anno."irq-load-balancing.crio.io" == "disable") | select($anno."cpu-quota.crio.io" == "disable") | .status.resources.linux.cpusetCpus' 2>/dev/null)
        if [[ -n $cpu_list && $cpu_list != "null" ]]; then
          if [[ -z $ISOLATED_CPUS ]]; then
            ISOLATED_CPUS="${cpu_list}"
          else
            ISOLATED_CPUS="${ISOLATED_CPUS},${cpu_list}"
          fi
        fi
      done
    else
      echo "Warning: Container directory '$CONTAINERS_DIR' not found"
    fi
  fi
else
  # Running on live host - fetch all containers (keeping original method for live mode)
  containers=$(crictl ps -a -o json | jq -r '.containers | map(.id) | join(",")')
  IFS=',' read -ra CONTAINER_IDS <<< "$containers"
  for container_id in "${CONTAINER_IDS[@]}"; do
    cpu_list=$(crictl inspect "$container_id" | jq -r '.info.runtimeSpec.annotations as $anno | select($anno."irq-load-balancing.crio.io" == "disable") | select($anno."cpu-quota.crio.io" == "disable") | .status.resources.linux.cpusetCpus' 2>/dev/null)
    if [[ -n $cpu_list && $cpu_list != "null" ]]; then
      if [[ -z $ISOLATED_CPUS ]]; then
        ISOLATED_CPUS="${cpu_list}"
      else
        ISOLATED_CPUS="${ISOLATED_CPUS},${cpu_list}"
      fi
    fi
  done
fi

format_cpu_list() {
  local cpu_list="$1"
  if [[ -z "$cpu_list" ]]; then
    echo ""
    return
  fi
  
  # Convert comma-separated list to array and sort numerically
  IFS=',' read -ra CPUS <<< "$cpu_list"
  IFS=$'\n' sorted_cpus=($(sort -n <<<"${CPUS[*]}"))
  
  local formatted=""
  local range_start=""
  local range_end=""
  local i=0
  
  while [[ $i -lt ${#sorted_cpus[@]} ]]; do
    local current=${sorted_cpus[i]}
    range_start=$current
    range_end=$current
    
    # Find consecutive CPUs to form a range
    while [[ $((i+1)) -lt ${#sorted_cpus[@]} && ${sorted_cpus[$((i+1))]} -eq $((current+1)) ]]; do
      i=$((i+1))
      current=${sorted_cpus[i]}
      range_end=$current
    done
    
    # Format the range
    if [[ $range_start -eq $range_end ]]; then
      # Single CPU
      if [[ -n "$formatted" ]]; then
        formatted="$formatted, $range_start"
      else
        formatted="$range_start"
      fi
    else
      # Range of CPUs
      if [[ -n "$formatted" ]]; then
        formatted="$formatted, $range_start-$range_end"
      else
        formatted="$range_start-$range_end"
      fi
    fi
    
    i=$((i+1))
  done
  
  echo "$formatted"
}

normalize_hex_mask() {
  local mask="$1"
  local normalized=""
  
  IFS=',' read -ra HEX_GROUPS <<< "$mask"
  for group in "${HEX_GROUPS[@]}"; do
    # Pad each group to exactly 8 characters with leading zeros (don't strip!)
    while [[ ${#group} -lt 8 ]]; do
      group="0$group"
    done
    
    if [[ -n "$normalized" ]]; then
      normalized="$normalized,$group"
    else
      normalized="$group"
    fi
  done
  
  echo "$normalized"
}

log() {
  logger -t irq-affinity "$1"
  echo "$1"
}

# JSON output helper functions
json_escape() {
  local str="$1"
  # Escape quotes and backslashes for JSON
  str="${str//\\/\\\\}"
  str="${str//\"/\\\"}"
  echo "$str"
}

json_array() {
  local arr=("$@")
  local result="["
  local first=true
  for item in "${arr[@]}"; do
    if [[ "$first" == "true" ]]; then
      first=false
    else
      result="$result,"
    fi
    result="$result\"$(json_escape "$item")\""
  done
  result="$result]"
  echo "$result"
}

json_output() {
  local key="$1"
  local value="$2"
  local is_number="$3"
  
  if [[ "$is_number" == "true" ]]; then
    echo "  \"$(json_escape "$key")\": $value"
  else
    echo "  \"$(json_escape "$key")\": \"$(json_escape "$value")\""
  fi
}

FORMATTED_ISOLATED_CPUS=$(format_cpu_list "$ISOLATED_CPUS")

# Start JSON output or text output
if [[ "$OUTPUT_FORMAT" == "json" ]]; then
  echo "{"
  echo "  \"analysis_type\": \"IRQ Affinity Configuration Analysis\","
  echo "  \"mode\": \"$(if [[ "$LOCAL_MODE" == "true" ]]; then echo "sosreport"; else echo "live"; fi)\","
  echo "  \"container_analysis\": {"
  if [[ -n "$FORMATTED_ISOLATED_CPUS" ]]; then
    echo "    \"isolated_cpus_found\": true,"
    echo "    \"isolated_cpus_formatted\": \"$(json_escape "$FORMATTED_ISOLATED_CPUS")\","
    echo "    \"isolated_cpus_raw\": \"$(json_escape "$ISOLATED_CPUS")\""
  else
    echo "    \"isolated_cpus_found\": false,"
    echo "    \"isolated_cpus_formatted\": \"\","
    echo "    \"isolated_cpus_raw\": \"\""
  fi
  echo "  },"
else
  log "========================================="
  log "IRQ Affinity Configuration Analysis"
  log "========================================="
  log ""
  log "CONTAINER ANALYSIS:"
  if [[ -n "$FORMATTED_ISOLATED_CPUS" ]]; then
    log "  CPUs to isolate from IRQs: $FORMATTED_ISOLATED_CPUS"
  else
    log "  No CPUs found requiring IRQ isolation"
  fi
fi

# Host CPU count
if [[ "$LOCAL_MODE" == "true" ]]; then
  CPUINFO_FILE="$BASE_DIR/proc/cpuinfo"
  if [[ -f "$CPUINFO_FILE" ]]; then
    NUM_CPUS=$(awk 'BEGIN {processor=0}; {if ($1 ~ /processor/){processor=$3}}; END {print processor+1}' "$CPUINFO_FILE")
  else
    echo "Error: CPU info file '$CPUINFO_FILE' not found"
    exit 1
  fi
else
  NUM_CPUS=$(nproc --all)
fi

# Host CPU count for thread limiting (always use actual host, not sosreport)
HOST_CPU_COUNT=$(nproc --all)

allowed=()
banned=()

for ((i=0; i<NUM_CPUS; i++)); do
  allowed[i]=1
  banned[i]=0
done

IFS=',' read -ra RANGES <<< "$ISOLATED_CPUS"
for range in "${RANGES[@]}"; do
  if [[ "$range" =~ ^([0-9]+)-([0-9]+)$ ]]; then
    for ((i=${BASH_REMATCH[1]}; i<=${BASH_REMATCH[2]}; i++)); do
      allowed[i]=0
      banned[i]=1
    done
  elif [[ "$range" =~ ^[0-9]+$ ]]; then
    allowed[$range]=0
    banned[$range]=1
  else
    log "Invalid CPU range: $range"
    exit 2
  fi
done

# Generate mask for kernel consumption (standard kernel parsing)
generate_kernel_mask() {
  local -n bits=$1
  local mask=""
  local group_masks=()
  
  # Process CPUs in groups of 32, starting from CPU 0
  local group_num=0
  while (( group_num * 32 < NUM_CPUS )); do
    local start_cpu=$((group_num * 32))
    local end_cpu=$(((group_num + 1) * 32 - 1))
    if (( end_cpu >= NUM_CPUS )); then
      end_cpu=$((NUM_CPUS - 1))
    fi
    
    # Initialize 32-bit value for this group
    local group_value=0
    
    # Map each CPU to its bit position using kernel's pattern
    for ((cpu=start_cpu; cpu<=end_cpu && cpu<NUM_CPUS; cpu++)); do
      if [[ ${bits[cpu]} -eq 1 ]]; then
        local relative_pos=$((cpu - start_cpu))
        # Use direct bit mapping: CPU position within group = bit position
        group_value=$((group_value | (1 << relative_pos)))
      fi
    done
    
    # Convert to hex (no byte swapping needed)
    local hex=$(printf "%08x" "$group_value")
    
    group_masks[group_num]="$hex"
    group_num=$((group_num + 1))
  done
  
  # Build final mask with highest group first (big-endian format)
  # First group strips leading zeros, others keep full format (like kernel)
  for ((i=${#group_masks[@]}-1; i>=0; i--)); do
    local group="${group_masks[i]}"
    if [[ -z "$mask" ]]; then
      # First (highest) group: strip leading zeros but keep at least one digit
      group=$(echo "$group" | sed 's/^0*//' | sed 's/^$/0/')
      mask="$group"
    else
      # Other groups: keep full 8-digit format
      mask="$mask,$group"
    fi
  done
  
  echo "$mask"
}

# Generate mask for irqbalance consumption (accounting for parsing bug)
generate_irqbalance_mask() {
  local -n bits=$1
  local mask=""
  local group_masks=()
  
  # Process CPUs in groups of 32, starting from CPU 0
  local group_num=0
  while (( group_num * 32 < NUM_CPUS )); do
    local start_cpu=$((group_num * 32))
    local end_cpu=$(((group_num + 1) * 32 - 1))
    if (( end_cpu >= NUM_CPUS )); then
      end_cpu=$((NUM_CPUS - 1))
    fi
    
    # Initialize 32-bit value for this group
    local group_value=0
    
    # Map each CPU to its bit position using kernel's pattern
    for ((cpu=start_cpu; cpu<=end_cpu && cpu<NUM_CPUS; cpu++)); do
      if [[ ${bits[cpu]} -eq 1 ]]; then
        local relative_pos=$((cpu - start_cpu))
        # Use direct bit mapping: CPU position within group = bit position
        group_value=$((group_value | (1 << relative_pos)))
      fi
    done
    
    # Convert to hex (no byte swapping needed)
    local hex=$(printf "%08x" "$group_value")
    
    group_masks[group_num]="$hex"
    group_num=$((group_num + 1))
  done
  
  # Build final mask accounting for irqbalance's parsing bug
  # WORKAROUND: For irqbalance, we may need different formatting to handle parsing bugs
  # For now, use same logic as kernel until we see actual differences in behavior
  for ((i=${#group_masks[@]}-1; i>=0; i--)); do
    local group="${group_masks[i]}"
    if [[ -z "$mask" ]]; then
      # First (highest) group: strip leading zeros but keep at least one digit  
      group=$(echo "$group" | sed 's/^0*//' | sed 's/^$/0/')
      mask="$group"
    else
      # Other groups: keep full 8-digit format
      mask="$mask,$group"
    fi
  done
  
  echo "$mask"
}

# Legacy function for backward compatibility
bits_to_hex_mask() {
  generate_kernel_mask "$1"
}

# Generate masks for different targets
kernel_allowed_mask=$(generate_kernel_mask allowed)
kernel_banned_mask=$(generate_kernel_mask banned)
irqbalance_allowed_mask=$(generate_irqbalance_mask allowed)
irqbalance_banned_mask=$(generate_irqbalance_mask banned)

# Create formatted CPU lists from the arrays
allowed_cpus=""
banned_cpus=""
for ((i=0; i<NUM_CPUS; i++)); do
  if [[ ${allowed[i]} -eq 1 ]]; then
    if [[ -n "$allowed_cpus" ]]; then
      allowed_cpus="$allowed_cpus,$i"
    else
      allowed_cpus="$i"
    fi
  fi
  if [[ ${banned[i]} -eq 1 ]]; then
    if [[ -n "$banned_cpus" ]]; then
      banned_cpus="$banned_cpus,$i"
    else
      banned_cpus="$i"
    fi
  fi
done

formatted_allowed_cpus=$(format_cpu_list "$allowed_cpus")
formatted_banned_cpus=$(format_cpu_list "$banned_cpus")

# Function to parse CPU list format (e.g., "0-3,8-11,16") into individual CPU numbers
parse_cpulist_to_array() {
    local cpulist="$1"
    local -n result_array=$2
    
    # Clear the result array
    result_array=()
    
    if [[ -z "$cpulist" || "$cpulist" == "" ]]; then
        return
    fi
    
    # Split by commas
    IFS=',' read -ra RANGES <<< "$cpulist"
    for range in "${RANGES[@]}"; do
        # Trim whitespace
        range=$(echo "$range" | xargs)
        
        if [[ "$range" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            # Range format: start-end
            local start=${BASH_REMATCH[1]}
            local end=${BASH_REMATCH[2]}
            for ((cpu=start; cpu<=end; cpu++)); do
                result_array+=($cpu)
            done
        elif [[ "$range" =~ ^[0-9]+$ ]]; then
            # Single CPU
            result_array+=($range)
        fi
    done
}

# Simplified function using Python analyzer (kept for compatibility but not performance-critical)
get_containers_on_cpu() {
    local target_cpu="$1"
    local proc_base_dir="$2"
    local -n container_results=$3
    
    # Clear the result array
    container_results=()
    
    # This function is now handled by the Python script in the main analysis
    # Keeping this stub for any remaining compatibility needs
}

# Function to check if a CPU is within an affinity list (handles ranges properly)
is_cpu_in_affinity() {
    local target_cpu="$1"
    local affinity_list="$2"
    
    # Split by commas and check each range/CPU
    IFS=',' read -ra AFFINITY_PARTS <<< "$affinity_list"
    for part in "${AFFINITY_PARTS[@]}"; do
        # Trim whitespace
        part=$(echo "$part" | xargs)
        
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            # Range format: start-end
            local start=${BASH_REMATCH[1]}
            local end=${BASH_REMATCH[2]}
            if [[ $target_cpu -ge $start && $target_cpu -le $end ]]; then
                return 0  # CPU is within this range
            fi
        elif [[ "$part" =~ ^[0-9]+$ ]]; then
            # Single CPU
            if [[ $part -eq $target_cpu ]]; then
                return 0  # Exact match
            fi
        fi
    done
    
    return 1  # CPU not found in affinity list
}

# Legacy function stub - now handled by Python analyzer
check_cpu_violations_worker() {
    # This function has been replaced by the Python analyzer for performance
    echo "This function is deprecated - use Python analyzer instead" >&2
}

# High-performance IRQ violation checking using Python analyzer
check_irq_violations() {
    local proc_base_dir="$1"
    
    log ""
    log "IRQ VIOLATION ANALYSIS:"
    log "======================"
    
    if [[ -z "$formatted_banned_cpus" ]]; then
        log "  No CPUs are isolated - no violations possible"
        return
    fi
    
    log "  Checking for IRQs assigned to isolated CPUs: $formatted_banned_cpus"
    log "  Using high-performance Python analyzer..."
    log ""
    
    # Get script directory and Python analyzer
    SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
    PYTHON_SCRIPT="$SCRIPT_DIR/irq_analyzer.py"
    
    if [[ ! -f "$PYTHON_SCRIPT" ]]; then
        log "  ✗ ERROR: Python analyzer not found: $PYTHON_SCRIPT"
        log "  Falling back to slower bash analysis would take too long - skipping"
        return
    fi
    
    # Prepare arguments for Python script
    local python_args=""
    if [[ "$LOCAL_MODE" == "true" ]]; then
        python_args="--sosreport-dir $proc_base_dir"
    else
        # Convert formatted banned CPUs back to simple comma-separated list for Python
        local banned_cpus_simple=""
        for ((i=0; i<NUM_CPUS; i++)); do
            if [[ ${banned[i]} -eq 1 ]]; then
                if [[ -n "$banned_cpus_simple" ]]; then
                    banned_cpus_simple="$banned_cpus_simple,$i"
                else
                    banned_cpus_simple="$i"
                fi
            fi
        done
        python_args="--isolated-cpus $banned_cpus_simple"
    fi
    
    # Run Python analyzer and capture results
    local analyzer_output
    local limit_flag=""
    if [[ "$FULL_ANALYSIS" != "true" ]]; then
        limit_flag="--limit-display"
    fi
    analyzer_output=$(python3 "$PYTHON_SCRIPT" $python_args --output-format summary $limit_flag)
    
    if [[ $? -ne 0 || -z "$analyzer_output" ]]; then
        log "  ✗ ERROR: Python analyzer failed to run"
        return
    fi
    
    # Parse results from Python analyzer
    local total_violations=0
    local total_irqs_checked=0
    
    # Parse the summary output
    while IFS= read -r line; do
        if [[ "$line" =~ ^TOTAL_VIOLATIONS=(.*)$ ]]; then
            total_violations="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^TOTAL_IRQS_CHECKED=(.*)$ ]]; then
            total_irqs_checked="${BASH_REMATCH[1]}"
        fi
    done <<< "$analyzer_output"
    
    log "  Total IRQs checked: $total_irqs_checked"
    log "  Total violations found: $total_violations"
    log ""
    
    if [[ $total_violations -eq 0 ]]; then
        log "  ✓ GOOD: No IRQs assigned to isolated CPUs"
        return
    fi
    
    log "  ✗ VIOLATIONS FOUND: IRQs assigned to isolated CPUs"
    log ""
    
    # Extract and display the detailed colored analysis from Python script
    local detailed_section=""
    detailed_section=$(echo "$analyzer_output" | sed -n '/^IRQ VIOLATION ANALYSIS/,$p')
    
    # Display the colored analysis if we found it
    if [[ -n "$detailed_section" ]]; then
        # Count total CPUs with violations for user awareness
        local cpu_count=$(echo "$detailed_section" | grep -c "^CPU [0-9]* (.*violations):")
        
        if [[ $cpu_count -gt 10 && "$FULL_ANALYSIS" != "true" ]]; then
            # For many violations, show top 10 most offending CPUs + summary (unless --full-analysis is used)
            log "  Showing detailed analysis for top 10 most offending CPUs (of $cpu_count total CPUs with violations):"
            log ""
            
            # Extract just the first 10 CPUs from detailed section
            local lines_shown=0
            local cpu_shown=0
            while IFS= read -r line; do
                echo "$line"
                lines_shown=$((lines_shown + 1))
                
                # Count CPUs shown
                if [[ "$line" =~ ^CPU\ [0-9]+\ \([0-9]+\ violations\): ]]; then
                    cpu_shown=$((cpu_shown + 1))
                    if [[ $cpu_shown -ge 10 ]]; then
                        # Skip to next CPU to finish this one
                        while IFS= read -r next_line; do
                            echo "$next_line"
                            if [[ "$next_line" =~ ^CPU_[0-9]+_CONTAINERS= ]]; then
                                break
                            fi
                        done <<< "$(echo "$detailed_section" | tail -n +$((lines_shown + 1)))"
                        break
                    fi
                fi
            done <<< "$detailed_section"
            
            log ""
            log "  ... (showing only top 10 most offending of $cpu_count CPUs with violations)"
            log "  Use --full-analysis flag to see all $cpu_count CPUs with violations"
        else
            # For manageable number of violations or when --full-analysis is used, show all
            if [[ "$FULL_ANALYSIS" == "true" && $cpu_count -gt 10 ]]; then
                log "  Showing detailed analysis for all $cpu_count CPUs with violations:"
                log ""
            fi
            echo "$detailed_section"
        fi
        log ""
    else
        # Fallback to basic summary if detailed analysis wasn't found
        log "  Violations by CPU:"
        
        # Parse and display per-CPU violation details
        while IFS= read -r line; do
            if [[ "$line" =~ ^CPU_([0-9]+)_VIOLATIONS=(.*)$ ]]; then
                local cpu="${BASH_REMATCH[1]}"
                local count="${BASH_REMATCH[2]}"
                
                # Get violation list and container info for this CPU
                local violations_list=""
                local containers_info=""
                
                while IFS= read -r detail_line; do
                    if [[ "$detail_line" =~ ^CPU_${cpu}_VIOLATION_LIST=(.*)$ ]]; then
                        violations_list="${BASH_REMATCH[1]}"
                    elif [[ "$detail_line" =~ ^CPU_${cpu}_CONTAINERS=(.*)$ ]]; then
                        containers_info="${BASH_REMATCH[1]}"
                    fi
                done <<< "$analyzer_output"
                
                # Format IRQ list for display (limit length)
                local display_list="$violations_list"
                if [[ ${#violations_list} -gt 100 ]]; then
                    local first_few=$(echo "$violations_list" | cut -d',' -f1-10)
                    display_list="$first_few,... (and $((count - 10)) more)"
                fi
                
                log "    CPU $cpu: $count IRQ(s) - $display_list"
                log "      Containers on CPU $cpu: $containers_info"
            fi
        done <<< "$analyzer_output"
    fi
    
    log ""
    log "  RECOMMENDED ACTION:"
    log "    These IRQs should be moved away from isolated CPUs"
    log "    Consider running this script to update /proc/irq/default_smp_affinity"
    log "    and restarting irqbalance to redistribute existing IRQs"
}

# JSON version of NUMA alignment checking
check_numa_alignment_json() {
    local base_dir="$1"
    
    # Get script directory and NUMA analyzer
    SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
    NUMA_SCRIPT="$SCRIPT_DIR/numa_analyzer.py"
    
    if [[ ! -f "$NUMA_SCRIPT" ]]; then
        echo "    \"error\": true,"
        echo "    \"message\": \"NUMA analyzer not found: $NUMA_SCRIPT\""
        return
    fi
    
    # Prepare arguments for NUMA script
    local numa_args=""
    if [[ "$LOCAL_MODE" == "true" ]]; then
        numa_args="--sosreport-dir $base_dir"
    else
        echo "    \"error\": true,"
        echo "    \"message\": \"Live system NUMA analysis not yet implemented\""
        return
    fi
    
    # Run NUMA analyzer with JSON output
    local analyzer_output
    analyzer_output=$(python3 "$NUMA_SCRIPT" $numa_args --output-format json 2>/dev/null)
    
    if [[ $? -ne 0 || -z "$analyzer_output" ]]; then
        echo "    \"error\": true,"
        echo "    \"message\": \"NUMA analyzer failed to run\""
        return
    fi
    
    # Output the JSON data from the NUMA analyzer (removing the outer braces)
    echo "    \"analysis_available\": true,"
    echo "$analyzer_output" | sed '1d;$d' | sed 's/^/    /'
}

# Text version of NUMA alignment checking
check_numa_alignment_text() {
    local base_dir="$1"
    
    log ""
    log "NUMA ALIGNMENT ANALYSIS:"
    log "========================"
    
    # Get script directory and NUMA analyzer
    SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
    NUMA_SCRIPT="$SCRIPT_DIR/numa_analyzer.py"
    
    if [[ ! -f "$NUMA_SCRIPT" ]]; then
        log "  ✗ ERROR: NUMA analyzer not found: $NUMA_SCRIPT"
        return
    fi
    
    # Prepare arguments for NUMA script
    local numa_args=""
    if [[ "$LOCAL_MODE" == "true" ]]; then
        numa_args="--sosreport-dir $base_dir"
    else
        log "  ✗ ERROR: Live system NUMA analysis not yet implemented"
        return
    fi
    
    # Run NUMA analyzer with text output
    local analyzer_output
    analyzer_output=$(python3 "$NUMA_SCRIPT" $numa_args --output-format text 2>/dev/null)
    
    if [[ $? -ne 0 || -z "$analyzer_output" ]]; then
        log "  ✗ ERROR: NUMA analyzer failed to run"
        return
    fi
    
    # Display the output
    echo "$analyzer_output"
}

# JSON version of IRQ violation checking
check_irq_violations_json() {
    local proc_base_dir="$1"
    
    if [[ -z "$formatted_banned_cpus" ]]; then
        echo "    \"violations_possible\": false,"
        echo "    \"message\": \"No CPUs are isolated - no violations possible\""
        return
    fi
    
    # Get script directory and Python analyzer
    SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
    PYTHON_SCRIPT="$SCRIPT_DIR/irq_analyzer.py"
    
    if [[ ! -f "$PYTHON_SCRIPT" ]]; then
        echo "    \"error\": true,"
        echo "    \"message\": \"Python analyzer not found: $PYTHON_SCRIPT\""
        return
    fi
    
    # Prepare arguments for Python script
    local python_args=""
    if [[ "$LOCAL_MODE" == "true" ]]; then
        python_args="--sosreport-dir $proc_base_dir"
    else
        # Convert formatted banned CPUs back to simple comma-separated list for Python
        local banned_cpus_simple=""
        for ((i=0; i<NUM_CPUS; i++)); do
            if [[ ${banned[i]} -eq 1 ]]; then
                if [[ -n "$banned_cpus_simple" ]]; then
                    banned_cpus_simple="$banned_cpus_simple,$i"
                else
                    banned_cpus_simple="$i"
                fi
            fi
        done
        python_args="--isolated-cpus $banned_cpus_simple"
    fi
    
    # Run Python analyzer with JSON output
    local analyzer_output
    local limit_flag=""
    if [[ "$FULL_ANALYSIS" != "true" ]]; then
        limit_flag="--limit-display"
    fi
    analyzer_output=$(python3 "$PYTHON_SCRIPT" $python_args --output-format json $limit_flag 2>/dev/null)
    
    if [[ $? -ne 0 || -z "$analyzer_output" ]]; then
        echo "    \"error\": true,"
        echo "    \"message\": \"Python analyzer failed to run\""
        return
    fi
    
    # Output the JSON data from the Python analyzer (removing the outer braces)
    echo "    \"violations_possible\": true,"
    echo "$analyzer_output" | sed '1d;$d' | sed 's/^/    /'
}

# Output computed IRQ configuration
if [[ "$OUTPUT_FORMAT" == "json" ]]; then
  echo "  \"computed_irq_configuration\": {"
  echo "    \"host_cpu_count\": $NUM_CPUS,"
  echo "    \"allowed_irq_cpus\": {"
  echo "      \"kernel_mask\": \"$(json_escape "$kernel_allowed_mask")\","
  echo "      \"irqbalance_mask\": \"$(json_escape "$irqbalance_allowed_mask")\","
  echo "      \"cpus_formatted\": \"$(json_escape "$formatted_allowed_cpus")\","
  echo "      \"cpus_raw\": \"$(json_escape "$allowed_cpus")\""
  echo "    },"
  echo "    \"banned_irq_cpus\": {"
  echo "      \"kernel_mask\": \"$(json_escape "$kernel_banned_mask")\","
  echo "      \"irqbalance_mask\": \"$(json_escape "$irqbalance_banned_mask")\","
  echo "      \"cpus_formatted\": \"$(json_escape "$formatted_banned_cpus")\","
  echo "      \"cpus_raw\": \"$(json_escape "$banned_cpus")\""
  echo "    }"
  echo "  },"
else
  log ""
  log "COMPUTED IRQ CONFIGURATION:"
  log "  Allowed IRQ CPUs:"
  log "    Kernel mask (/proc/irq/default_smp_affinity): $kernel_allowed_mask"
  log "    irqbalance mask (IRQBALANCE_BANNED_CPUS uses banned): $irqbalance_allowed_mask"
  log "    CPUs: $formatted_allowed_cpus"
  log "  Banned IRQ CPUs:"
  log "    Kernel mask: $kernel_banned_mask"
  log "    irqbalance mask (IRQBALANCE_BANNED_CPUS): $irqbalance_banned_mask"
  log "    CPUs: $formatted_banned_cpus"
fi

# Handle IRQ violation analysis
if [[ "$OUTPUT_FORMAT" == "json" ]]; then
  echo "  \"irq_violation_analysis\": {"
  if [[ "$CHECK_VIOLATIONS" == "true" ]]; then
    echo "    \"enabled\": true,"
    # We'll call a modified check_irq_violations function that returns JSON data
    if [[ "$LOCAL_MODE" == "true" ]]; then
      check_irq_violations_json "$BASE_DIR"
    else
      check_irq_violations_json ""
    fi
  else
    echo "    \"enabled\": false,"
    echo "    \"message\": \"Skipped (use --check-violations to enable)\""
  fi
  echo "  },"
  echo "  \"numa_alignment_analysis\": {"
  if [[ "$CHECK_NUMA" == "true" ]]; then
    echo "    \"enabled\": true,"
    if [[ "$LOCAL_MODE" == "true" ]]; then
      check_numa_alignment_json "$BASE_DIR"
    else
      check_numa_alignment_json ""
    fi
  else
    echo "    \"enabled\": false,"
    echo "    \"message\": \"Skipped (use --check-numa-alignment to enable)\""
  fi
  echo "  }"
else
  # Check for IRQ violations (works for both local and live modes)
  if [[ "$CHECK_VIOLATIONS" == "true" ]]; then
    if [[ "$LOCAL_MODE" == "true" ]]; then
      check_irq_violations "$BASE_DIR"
    else
      check_irq_violations ""
    fi
  else
    log ""
    log "IRQ VIOLATION ANALYSIS:"
    log "======================"
    log "  Skipped (use --check-violations to enable)"
  fi
  
  # Check for NUMA alignment (works for both local and live modes)
  if [[ "$CHECK_NUMA" == "true" ]]; then
    if [[ "$LOCAL_MODE" == "true" ]]; then
      check_numa_alignment_text "$BASE_DIR"
    else
      check_numa_alignment_text ""
    fi
  else
    log ""
    log "NUMA ALIGNMENT ANALYSIS:"
    log "========================"
    log "  Skipped (use --check-numa-alignment to enable)"
  fi
fi

# Handle sosreport analysis mode
if [[ "$LOCAL_MODE" == "true" ]]; then
  
  # Check if sosreport contains current irq configuration for comparison
  SMP_FILE="$BASE_DIR/proc/irq/default_smp_affinity"
  if [[ -f "$SMP_FILE" ]]; then
    current_mask=$(cat "$SMP_FILE" | tr 'A-Z' 'a-z')
  else
    current_mask=""
  fi

  # Check irqbalance configuration if available in sosreport
  IRQBALANCE_CONF="$BASE_DIR/etc/sysconfig/irqbalance"
  if [[ -f "$IRQBALANCE_CONF" ]]; then
    existing_mask=$(grep '^IRQBALANCE_BANNED_CPUS=' "$IRQBALANCE_CONF" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr 'A-Z' 'a-z')
  else
    existing_mask=""
  fi
  
  if [[ "$OUTPUT_FORMAT" == "json" ]]; then
    echo "  ,"
    echo "  \"current_system_state\": {"
    echo "    \"source\": \"sosreport\","
    echo "    \"default_smp_affinity\": {"
    if [[ -f "$SMP_FILE" ]]; then
      echo "      \"file_found\": true,"
      echo "      \"current_mask\": \"$(json_escape "$current_mask")\""
    else
      echo "      \"file_found\": false,"
      echo "      \"current_mask\": null"
    fi
    echo "    },"
    echo "    \"irqbalance_config\": {"
    if [[ -f "$IRQBALANCE_CONF" ]]; then
      echo "      \"file_found\": true,"
      if [[ -n "$existing_mask" ]]; then
        echo "      \"banned_cpus_set\": true,"
        echo "      \"current_mask\": \"$(json_escape "$existing_mask")\""
      else
        echo "      \"banned_cpus_set\": false,"
        echo "      \"current_mask\": null"
      fi
    else
      echo "      \"file_found\": false,"
      echo "      \"banned_cpus_set\": false,"
      echo "      \"current_mask\": null"
    fi
    echo "    }"
    echo "  },"
    echo "  \"recommendations\": {"
    
    # Compare and recommend changes for default_smp_affinity
    if [[ -f "$SMP_FILE" ]]; then
      normalized_current=$(normalize_hex_mask "$current_mask")
      normalized_allowed=$(normalize_hex_mask "${kernel_allowed_mask,,}")
      if [[ "$normalized_current" != "$normalized_allowed" ]]; then
        echo "    \"default_smp_affinity\": {"
        echo "      \"action_required\": true,"
        echo "      \"action\": \"UPDATE\","
        echo "      \"current\": \"$(json_escape "$current_mask")\","
        echo "      \"required\": \"$(json_escape "${kernel_allowed_mask,,}")\""
        echo "    },"
      else
        echo "    \"default_smp_affinity\": {"
        echo "      \"action_required\": false,"
        echo "      \"action\": \"NONE\","
        echo "      \"status\": \"CORRECT\""
        echo "    },"
      fi
    else
      echo "    \"default_smp_affinity\": {"
      echo "      \"action_required\": true,"
      echo "      \"action\": \"CREATE\","
      echo "      \"required\": \"$(json_escape "${kernel_allowed_mask,,}")\""
      echo "    },"
    fi

    # Compare and recommend changes for irqbalance
    if [[ -f "$IRQBALANCE_CONF" ]]; then
      if [[ -n "$existing_mask" ]]; then
        if [[ "$existing_mask" != "${irqbalance_banned_mask,,}" ]]; then
          echo "    \"irqbalance_config\": {"
          echo "      \"action_required\": true,"
          echo "      \"action\": \"UPDATE\","
          echo "      \"current\": \"$(json_escape "$existing_mask")\","
          echo "      \"required\": \"$(json_escape "${irqbalance_banned_mask,,}")\""
          echo "    }"
        else
          echo "    \"irqbalance_config\": {"
          echo "      \"action_required\": false,"
          echo "      \"action\": \"NONE\","
          echo "      \"status\": \"CORRECT\""
          echo "    }"
        fi
      else
        echo "    \"irqbalance_config\": {"
        echo "      \"action_required\": true,"
        echo "      \"action\": \"ADD\","
        echo "      \"required\": \"$(json_escape "${irqbalance_banned_mask,,}")\""
        echo "    }"
      fi
    else
      echo "    \"irqbalance_config\": {"
      echo "      \"action_required\": true,"
      echo "      \"action\": \"CREATE\","
      echo "      \"required\": \"$(json_escape "${irqbalance_banned_mask,,}")\""
      echo "    }"
    fi
    echo "  },"
    echo "  \"mode\": \"analysis_only\","
    echo "  \"changes_made\": false"
    echo "}"
    
  else
    # Text output for sosreport analysis
    log ""
    log "CURRENT SYSTEM STATE (from sosreport):"
    
    if [[ -f "$SMP_FILE" ]]; then
      log "  /proc/irq/default_smp_affinity: $current_mask"
    else
      log "  /proc/irq/default_smp_affinity: [FILE NOT FOUND]"
    fi

    if [[ -f "$IRQBALANCE_CONF" ]]; then
      if [[ -n "$existing_mask" ]]; then
        log "  IRQBALANCE_BANNED_CPUS: $existing_mask"
      else
        log "  IRQBALANCE_BANNED_CPUS: [NOT SET]"
      fi
    else
      log "  /etc/sysconfig/irqbalance: [FILE NOT FOUND]"
    fi
    
    log ""
    log "RECOMMENDATIONS:"
    
    # Compare and recommend changes for default_smp_affinity
    if [[ -f "$SMP_FILE" ]]; then
      normalized_current=$(normalize_hex_mask "$current_mask")
      normalized_allowed=$(normalize_hex_mask "${kernel_allowed_mask,,}")
      if [[ "$normalized_current" != "$normalized_allowed" ]]; then
        log "  ✗ UPDATE REQUIRED: /proc/irq/default_smp_affinity"
        log "    Current:  $current_mask"
        log "    Required: ${kernel_allowed_mask,,}"
      else
        log "  ✓ CORRECT: /proc/irq/default_smp_affinity is already properly configured"
      fi
    else
      log "  ✗ CREATE REQUIRED: /proc/irq/default_smp_affinity = ${kernel_allowed_mask,,}"
    fi

    # Compare and recommend changes for irqbalance
    if [[ -f "$IRQBALANCE_CONF" ]]; then
      if [[ -n "$existing_mask" ]]; then
        if [[ "$existing_mask" != "${irqbalance_banned_mask,,}" ]]; then
          log "  ✗ UPDATE REQUIRED: IRQBALANCE_BANNED_CPUS"
          log "    Current:  $existing_mask"
          log "    Required: ${irqbalance_banned_mask,,}"
        else
          log "  ✓ CORRECT: IRQBALANCE_BANNED_CPUS is already properly configured"
        fi
      else
        log "  ✗ ADD REQUIRED: IRQBALANCE_BANNED_CPUS=\"${irqbalance_banned_mask,,}\""
      fi
    else
      log "  ✗ CREATE REQUIRED: /etc/sysconfig/irqbalance with IRQBALANCE_BANNED_CPUS=\"${irqbalance_banned_mask,,}\""
    fi
    
    log ""
    log "NOTE: Running in analysis mode - no changes will be made to the system"
    
    log ""
    log "========================================="
    log "Analysis Complete"
    log "========================================="
  fi
  
  exit 0
fi

# Live system modifications (only when not in local mode)
SMP_FILE="/proc/irq/default_smp_affinity"
if [ -r "$SMP_FILE" ]; then
  current_smp_mask=$(cat "$SMP_FILE" | tr 'A-Z' 'a-z')
else
  current_smp_mask=""
fi

IRQBALANCE_CONF="/etc/sysconfig/irqbalance"
if [ -r "$IRQBALANCE_CONF" ]; then
  existing_irq_mask=$(grep '^IRQBALANCE_BANNED_CPUS=' "$IRQBALANCE_CONF" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr 'A-Z' 'a-z')
else
  existing_irq_mask=""
fi

if [[ "$OUTPUT_FORMAT" == "json" ]]; then
  echo "  ,"
  echo "  \"current_system_state\": {"
  echo "    \"source\": \"live_system\","
  echo "    \"default_smp_affinity\": {"
  if [ -r "$SMP_FILE" ]; then
    echo "      \"readable\": true,"
    echo "      \"current_mask\": \"$(json_escape "$current_smp_mask")\""
  else
    echo "      \"readable\": false,"
    echo "      \"current_mask\": null"
  fi
  echo "    },"
  echo "    \"irqbalance_config\": {"
  if [ -r "$IRQBALANCE_CONF" ]; then
    echo "      \"readable\": true,"
    if [[ -n "$existing_irq_mask" ]]; then
      echo "      \"banned_cpus_set\": true,"
      echo "      \"current_mask\": \"$(json_escape "$existing_irq_mask")\""
    else
      echo "      \"banned_cpus_set\": false,"
      echo "      \"current_mask\": null"
    fi
  else
    echo "      \"readable\": false,"
    echo "      \"banned_cpus_set\": false,"
    echo "      \"current_mask\": null"
  fi
  echo "    }"
  echo "  },"
  echo "  \"changes_applied\": {"
  
  RESTART_IRQBALANCE=false
  
  # Update /proc/irq/default_smp_affinity
  echo "    \"default_smp_affinity\": {"
  if [ -w "$SMP_FILE" ]; then
    normalized_current=$(normalize_hex_mask "$current_smp_mask")
    normalized_allowed=$(normalize_hex_mask "${kernel_allowed_mask,,}")
    if [[ "$normalized_current" != "$normalized_allowed" ]]; then
      echo "$kernel_allowed_mask" > "$SMP_FILE"
      echo "      \"success\": true,"
      echo "      \"action\": \"UPDATED\","
      echo "      \"previous\": \"$(json_escape "$current_smp_mask")\","
      echo "      \"new\": \"$(json_escape "${kernel_allowed_mask,,}")\""
    else
      echo "      \"success\": true,"
      echo "      \"action\": \"NO_CHANGE\","
      echo "      \"reason\": \"already_correct\""
    fi
  else
    echo "      \"success\": false,"
    echo "      \"action\": \"FAILED\","
    echo "      \"reason\": \"permission_denied\""
  fi
  echo "    },"
  
  # Update irqbalance configuration
  echo "    \"irqbalance_config\": {"
  if [ -w "$IRQBALANCE_CONF" ]; then
    if [[ "$existing_irq_mask" != "${irqbalance_banned_mask,,}" ]]; then
      if grep -q '^IRQBALANCE_BANNED_CPUS=' "$IRQBALANCE_CONF"; then
        sed -i "s/^IRQBALANCE_BANNED_CPUS=.*/IRQBALANCE_BANNED_CPUS=\"$irqbalance_banned_mask\"/" "$IRQBALANCE_CONF"
        echo "      \"success\": true,"
        echo "      \"action\": \"UPDATED\","
      else
        echo "IRQBALANCE_BANNED_CPUS=\"$irqbalance_banned_mask\"" >> "$IRQBALANCE_CONF"
        echo "      \"success\": true,"
        echo "      \"action\": \"ADDED\","
      fi
      echo "      \"previous\": \"$(json_escape "${existing_irq_mask:-[NOT SET]}")\","
      echo "      \"new\": \"$(json_escape "${irqbalance_banned_mask,,}")\""
      RESTART_IRQBALANCE=true
    else
      echo "      \"success\": true,"
      echo "      \"action\": \"NO_CHANGE\","
      echo "      \"reason\": \"already_correct\""
    fi
  else
    echo "      \"success\": false,"
    echo "      \"action\": \"FAILED\","
    echo "      \"reason\": \"permission_denied\""
  fi
  echo "    },"
  
  # Restart irqbalance service if needed
  echo "    \"irqbalance_service\": {"
  if $RESTART_IRQBALANCE; then
    if systemctl is-active --quiet irqbalance; then
      if systemctl restart irqbalance; then
        echo "      \"restart_attempted\": true,"
        echo "      \"restart_success\": true"
      else
        echo "      \"restart_attempted\": true,"
        echo "      \"restart_success\": false"
      fi
    else
      echo "      \"restart_attempted\": false,"
      echo "      \"reason\": \"service_not_active\""
    fi
  else
    echo "      \"restart_attempted\": false,"
    echo "      \"reason\": \"no_changes_made\""
  fi
  echo "    }"
  echo "  },"
  echo "  \"mode\": \"live_system\","
  echo "  \"changes_made\": true"
  echo "}"

else
  # Text output for live system
  log ""
  log "CURRENT SYSTEM STATE (live system):"

  if [ -r "$SMP_FILE" ]; then
    log "  /proc/irq/default_smp_affinity: $current_smp_mask"
  else
    log "  /proc/irq/default_smp_affinity: [CANNOT READ]"
  fi

  if [ -r "$IRQBALANCE_CONF" ]; then
    if [[ -n "$existing_irq_mask" ]]; then
      log "  IRQBALANCE_BANNED_CPUS: $existing_irq_mask"
    else
      log "  IRQBALANCE_BANNED_CPUS: [NOT SET]"
    fi
  else
    log "  /etc/sysconfig/irqbalance: [CANNOT READ]"
  fi

  log ""
  log "APPLYING CHANGES:"

  RESTART_IRQBALANCE=false

  # Update /proc/irq/default_smp_affinity
  if [ -w "$SMP_FILE" ]; then
    normalized_current=$(normalize_hex_mask "$current_smp_mask")
    normalized_allowed=$(normalize_hex_mask "${kernel_allowed_mask,,}")
    if [[ "$normalized_current" != "$normalized_allowed" ]]; then
      echo "$kernel_allowed_mask" > "$SMP_FILE"
      log "  ✓ UPDATED: /proc/irq/default_smp_affinity"
      log "    Previous: $current_smp_mask"
      log "    New:      ${kernel_allowed_mask,,}"
    else
      log "  ✓ NO CHANGE: /proc/irq/default_smp_affinity already correct"
    fi
  else
    log "  ✗ FAILED: Cannot write to $SMP_FILE (permission denied)"
  fi

  # Update irqbalance configuration
  if [ -w "$IRQBALANCE_CONF" ]; then
    if [[ "$existing_irq_mask" != "${irqbalance_banned_mask,,}" ]]; then
      if grep -q '^IRQBALANCE_BANNED_CPUS=' "$IRQBALANCE_CONF"; then
        sed -i "s/^IRQBALANCE_BANNED_CPUS=.*/IRQBALANCE_BANNED_CPUS=\"$irqbalance_banned_mask\"/" "$IRQBALANCE_CONF"
        log "  ✓ UPDATED: IRQBALANCE_BANNED_CPUS in $IRQBALANCE_CONF"
      else
        echo "IRQBALANCE_BANNED_CPUS=\"$irqbalance_banned_mask\"" >> "$IRQBALANCE_CONF"
        log "  ✓ ADDED: IRQBALANCE_BANNED_CPUS to $IRQBALANCE_CONF"
      fi
      if [[ -n "$existing_irq_mask" ]]; then
        log "    Previous: $existing_irq_mask"
      else
        log "    Previous: [NOT SET]"
      fi
      log "    New:      ${irqbalance_banned_mask,,}"
      RESTART_IRQBALANCE=true
    else
      log "  ✓ NO CHANGE: IRQBALANCE_BANNED_CPUS already correct"
    fi
  else
    log "  ✗ FAILED: Cannot write to $IRQBALANCE_CONF (permission denied)"
  fi

  # Restart irqbalance service if needed
  if $RESTART_IRQBALANCE; then
    log ""
    log "SERVICE RESTART:"
    if systemctl is-active --quiet irqbalance; then
      log "  Restarting irqbalance service..."
      if systemctl restart irqbalance; then
        log "  ✓ SUCCESS: irqbalance service restarted"
      else
        log "  ✗ FAILED: irqbalance service restart failed"
      fi
    else
      log "  ⚠ SKIPPED: irqbalance service is not active"
    fi
  fi

  log ""
  log "========================================="
  log "Configuration Complete"
  log "========================================="
fi
