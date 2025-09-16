#!/usr/bin/env python3
"""
High-performance IRQ analyzer for container CPU isolation.

This script handles the computationally intensive parts of IRQ analysis
that were causing performance issues in the bash script.
"""

import os
import sys
import json
import argparse
import glob
import re

def parse_cpu_range(cpu_range_str):
    """Parse CPU range string (e.g., '0-3,8-11,16') into list of CPU numbers."""
    if not cpu_range_str or cpu_range_str in ('null', 'empty', ''):
        return []
    
    cpus = []
    for part in cpu_range_str.split(','):
        part = part.strip()
        if '-' in part:
            start, end = map(int, part.split('-'))
            cpus.extend(range(start, end + 1))
        elif part.isdigit():
            cpus.append(int(part))
    return cpus

def get_isolated_cpus_sosreport(base_dir):
    """Extract isolated CPUs from sosreport container data."""
    isolated_cpus = set()
    containers_dir = os.path.join(base_dir, "sos_commands", "crio", "containers")
    
    if not os.path.isdir(containers_dir):
        return isolated_cpus
    
    container_files = glob.glob(os.path.join(containers_dir, "*"))
    
    for container_file in container_files:
        if not os.path.isfile(container_file):
            continue
            
        try:
            with open(container_file, 'r') as f:
                container_data = json.load(f)
            
            # Check for isolation annotations
            annotations = container_data.get('info', {}).get('runtimeSpec', {}).get('annotations', {})
            if (annotations.get('irq-load-balancing.crio.io') == 'disable' and 
                annotations.get('cpu-quota.crio.io') == 'disable'):
                
                cpu_set = container_data.get('status', {}).get('resources', {}).get('linux', {}).get('cpusetCpus')
                if cpu_set:
                    cpus = parse_cpu_range(cpu_set)
                    isolated_cpus.update(cpus)
                    
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            continue
    
    return sorted(isolated_cpus)

def get_container_info_for_cpu(cpu, base_dir):
    """Get container information for a specific CPU from sosreport."""
    containers_on_cpu = []
    containers_dir = os.path.join(base_dir, "sos_commands", "crio", "containers")
    
    if not os.path.isdir(containers_dir):
        return containers_on_cpu
    
    container_files = glob.glob(os.path.join(containers_dir, "*"))
    
    for container_file in container_files:
        if not os.path.isfile(container_file):
            continue
            
        try:
            with open(container_file, 'r') as f:
                container_data = json.load(f)
            
            # Get container name and ID
            container_name = (container_data.get('info', {}).get('config', {}).get('metadata', {}).get('name') or
                            container_data.get('status', {}).get('metadata', {}).get('name') or
                            "unknown")
            
            container_id = (container_data.get('status', {}).get('id') or
                          container_data.get('info', {}).get('id') or
                          "unknown")
            
            # Get CPU set
            cpu_set = container_data.get('status', {}).get('resources', {}).get('linux', {}).get('cpusetCpus')
            
            if cpu_set:
                cpus = parse_cpu_range(cpu_set)
                if cpu in cpus:
                    short_id = container_id[:12] if len(container_id) > 12 else container_id
                    containers_on_cpu.append(f"{container_name} ({short_id})")
                    
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            continue
    
    return containers_on_cpu

def get_uptime_seconds(base_dir):
    """Read uptime from sosreport uptime file and return seconds."""
    if not base_dir:
        # If no sosreport dir, try to read from system
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_line = f.read().strip()
                return float(uptime_line.split()[0])
        except (OSError, ValueError, IndexError):
            return None
    
    uptime_file = os.path.join(base_dir, 'uptime')
    if not os.path.isfile(uptime_file):
        return None
    
    try:
        with open(uptime_file, 'r') as f:
            uptime_output = f.read().strip()
            # Parse uptime output like " 14:25:07 up 2 days,  3:14,  1 user,  load average: 0.00, 0.01, 0.05"
            # or " 14:25:07 up  3:14,  1 user,  load average: 0.00, 0.01, 0.05"
            # or " 14:25:07 up 25 min,  1 user,  load average: 0.00, 0.01, 0.05"
            
            # Extract the time portion after "up"
            match = re.search(r'up\s+(.+?),\s+\d+\s+user', uptime_output)
            if not match:
                return None
            
            time_str = match.group(1).strip()
            total_seconds = 0
            
            # Parse different time formats
            # Days
            days_match = re.search(r'(\d+)\s+days?', time_str)
            if days_match:
                total_seconds += int(days_match.group(1)) * 24 * 3600
                time_str = re.sub(r'\d+\s+days?,?\s*', '', time_str)
            
            # Hours and minutes (e.g., "3:14" or "14:25")
            hm_match = re.search(r'(\d+):(\d+)', time_str)
            if hm_match:
                hours = int(hm_match.group(1))
                minutes = int(hm_match.group(2))
                total_seconds += hours * 3600 + minutes * 60
            else:
                # Just minutes (e.g., "25 min")
                min_match = re.search(r'(\d+)\s+min', time_str)
                if min_match:
                    total_seconds += int(min_match.group(1)) * 60
            
            return total_seconds if total_seconds > 0 else None
            
    except (OSError, ValueError):
        return None

def parse_proc_interrupts(base_dir):
    """Parse /proc/interrupts and return interrupt counts for each IRQ."""
    if base_dir:
        interrupts_file = os.path.join(base_dir, 'proc', 'interrupts')
    else:
        interrupts_file = '/proc/interrupts'
    
    if not os.path.isfile(interrupts_file):
        return {}
    
    irq_counts = {}
    
    try:
        with open(interrupts_file, 'r') as f:
            lines = f.readlines()
        
        if not lines:
            return {}
        
        # First line contains CPU headers (CPU0, CPU1, etc.)
        cpu_count = len(lines[0].split()) - 1  # Subtract 1 for the first column
        
        for line in lines[1:]:  # Skip the header line
            line = line.strip()
            if not line:
                continue
            
            parts = line.split()
            if not parts:
                continue
            
            # First part should be IRQ number followed by ':'
            irq_part = parts[0]
            if not irq_part.endswith(':'):
                continue
            
            try:
                irq_num = int(irq_part.rstrip(':'))
            except ValueError:
                continue
            
            # Sum interrupt counts across all CPUs
            total_interrupts = 0
            for i in range(1, min(len(parts), cpu_count + 1)):
                try:
                    total_interrupts += int(parts[i])
                except ValueError:
                    break
            
            irq_counts[irq_num] = total_interrupts
        
    except (OSError, ValueError):
        pass
    
    return irq_counts

def calculate_interrupts_per_hour(interrupt_count, uptime_seconds):
    """Calculate interrupts per hour given interrupt count and uptime."""
    if not uptime_seconds or uptime_seconds <= 0:
        return None
    
    hours = uptime_seconds / 3600
    return interrupt_count / hours

def get_irq_color_code(interrupt_count, interrupts_per_hour):
    """Get color code for IRQ based on interrupt count and rate."""
    # ANSI color codes
    colors = {
        'green': '\033[92m',   # Bright green
        'yellow': '\033[93m',  # Bright yellow  
        'red': '\033[91m',     # Bright red
        'reset': '\033[0m'     # Reset color
    }
    
    if interrupt_count == 0:
        return colors['green'], 'green'
    elif interrupts_per_hour is None or interrupts_per_hour < 1000:
        return colors['yellow'], 'yellow'
    else:
        return colors['red'], 'red'

def format_colored_text(text, color_code):
    """Format text with color code."""
    return f"{color_code}{text}\033[0m"

def build_irq_to_cpu_mapping(irq_base_dir):
    """Build a mapping of IRQ number to list of CPUs it's assigned to."""
    irq_to_cpus = {}
    total_irqs_processed = 0
    
    try:
        irq_dirs = [d for d in os.listdir(irq_base_dir) 
                   if os.path.isdir(os.path.join(irq_base_dir, d)) and d.isdigit()]
    except OSError:
        return irq_to_cpus, 0
    
    for irq_num in irq_dirs:
        irq_dir = os.path.join(irq_base_dir, irq_num)
        affinity_file = os.path.join(irq_dir, "smp_affinity_list")
        
        if not os.path.isfile(affinity_file):
            continue
        
        total_irqs_processed += 1
        
        try:
            with open(affinity_file, 'r') as f:
                current_affinity = f.read().strip()
            
            if current_affinity:
                # Parse affinity and store CPU list for this IRQ
                affinity_cpus = parse_cpu_range(current_affinity)
                if affinity_cpus:  # Only store if non-empty
                    irq_to_cpus[int(irq_num)] = set(affinity_cpus)
        except (OSError, ValueError):
            continue
    
    return irq_to_cpus, total_irqs_processed

def check_irq_violations_for_cpu(cpu, irq_to_cpus_map):
    """Check IRQ violations for a single CPU using pre-built mapping."""
    violations = []
    
    # Look up which IRQs are assigned to this CPU
    for irq_num, cpu_set in irq_to_cpus_map.items():
        if cpu in cpu_set:
            violations.append(irq_num)
    
    return violations

def analyze_irq_violations(isolated_cpus, irq_base_dir, base_dir=None, max_workers=None):
    """Analyze IRQ violations using optimized mapping approach with interrupt rate analysis."""
    if not isolated_cpus:
        return {}, 0, {'uptime_seconds': None, 'uptime_hours': None}
    
    # Build IRQ to CPU mapping once (major optimization!)
    print("Building IRQ to CPU mapping...", file=sys.stderr)
    irq_to_cpus_map, total_irqs_processed = build_irq_to_cpu_mapping(irq_base_dir)
    print(f"Processed {total_irqs_processed} IRQs", file=sys.stderr)
    
    if not irq_to_cpus_map:
        return {}, total_irqs_processed, {'uptime_seconds': None, 'uptime_hours': None}
    
    # Get uptime and interrupt counts for rate analysis
    print("Reading uptime and interrupt counts...", file=sys.stderr)
    uptime_seconds = get_uptime_seconds(base_dir)
    irq_interrupt_counts = parse_proc_interrupts(base_dir)
    
    results = {}
    
    # Check violations for each isolated CPU using the pre-built mapping
    for cpu in isolated_cpus:
        violations = check_irq_violations_for_cpu(cpu, irq_to_cpus_map)
        
        if violations:  # Only store results for CPUs with violations
            # Get container info for this CPU if there are violations
            containers_info = []
            if base_dir:
                containers_info = get_container_info_for_cpu(cpu, base_dir)
            
            # Analyze each violating IRQ for interrupt rate and color coding
            violation_details = []
            for irq_num in violations:
                interrupt_count = irq_interrupt_counts.get(irq_num, 0)
                interrupts_per_hour = calculate_interrupts_per_hour(interrupt_count, uptime_seconds)
                color_code, color_name = get_irq_color_code(interrupt_count, interrupts_per_hour)
                
                violation_details.append({
                    'irq': irq_num,
                    'interrupt_count': interrupt_count,
                    'interrupts_per_hour': interrupts_per_hour,
                    'color_code': color_code,
                    'color_name': color_name
                })
            
            results[cpu] = {
                'violations': violations,
                'violation_details': violation_details,
                'containers': containers_info
            }
    
    # Include uptime info in results for reference
    results_metadata = {
        'uptime_seconds': uptime_seconds,
        'uptime_hours': uptime_seconds / 3600 if uptime_seconds else None
    }
    
    return results, total_irqs_processed, results_metadata

def main():
    parser = argparse.ArgumentParser(description='Analyze IRQ violations for isolated CPUs')
    parser.add_argument('--sosreport-dir', help='Path to sosreport directory')
    parser.add_argument('--isolated-cpus', help='Comma-separated list of isolated CPUs')
    parser.add_argument('--irq-dir', default='/proc/irq', help='IRQ directory path')
    parser.add_argument('--max-workers', type=int, help='Maximum number of worker threads')
    parser.add_argument('--output-format', choices=['json', 'summary'], default='summary',
                       help='Output format')
    
    args = parser.parse_args()
    
    # Determine isolated CPUs
    if args.isolated_cpus:
        isolated_cpus = parse_cpu_range(args.isolated_cpus)
    elif args.sosreport_dir:
        isolated_cpus = get_isolated_cpus_sosreport(args.sosreport_dir)
    else:
        print("Error: Must specify either --isolated-cpus or --sosreport-dir", file=sys.stderr)
        sys.exit(1)
    
    if not isolated_cpus:
        if args.output_format == 'json':
            print(json.dumps({'isolated_cpus': [], 'violations': {}, 'total_violations': 0}))
        else:
            print("No isolated CPUs found")
        sys.exit(0)
    
    # Set IRQ directory path for sosreport analysis
    if args.sosreport_dir:
        irq_dir = os.path.join(args.sosreport_dir, 'proc', 'irq')
    else:
        irq_dir = args.irq_dir
    
    if not os.path.isdir(irq_dir):
        print(f"Error: IRQ directory not found: {irq_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Analyze violations
    violations_data, total_irqs_scanned, metadata = analyze_irq_violations(isolated_cpus, irq_dir, args.sosreport_dir, args.max_workers)
    
    # Output results
    if args.output_format == 'json':
        total_violations = sum(len(data['violations']) for data in violations_data.values())
        output = {
            'isolated_cpus': isolated_cpus,
            'violations': violations_data,
            'total_violations': total_violations,
            'total_irqs_scanned': total_irqs_scanned,
            'metadata': metadata
        }
        print(json.dumps(output, indent=2))
    else:
        # Summary format for bash script consumption with color coding
        total_violations = sum(len(data['violations']) for data in violations_data.values())
        
        # Always output isolated CPUs first
        isolated_cpus_str = ','.join(map(str, isolated_cpus)) if isolated_cpus else ""
        print(f"ISOLATED_CPUS={isolated_cpus_str}")
        print(f"TOTAL_VIOLATIONS={total_violations}")
        print(f"TOTAL_IRQS_CHECKED={total_irqs_scanned}")
        
        # Output uptime info if available
        if metadata.get('uptime_hours'):
            print(f"UPTIME_HOURS={metadata['uptime_hours']:.2f}")
        
        print()  # Add a blank line before detailed output
        print("IRQ VIOLATION ANALYSIS (Color-coded by interrupt rate):")
        print("  🟢 Green: 0 interrupts")  
        print("  🟡 Yellow: < 1000 interrupts/hour")
        print("  🔴 Red: ≥ 1000 interrupts/hour")
        print()
        
        for cpu in sorted(violations_data.keys()):
            data = violations_data[cpu]
            containers_str = ', '.join(data['containers']) if data['containers'] else '[none found]'
            
            print(f"CPU {cpu} ({len(data['violations'])} violations):")
            print(f"  Containers: {containers_str}")
            print(f"  IRQs with improper affinity:")
            
            # Sort violations by interrupt rate (highest first)
            violation_details = data.get('violation_details', [])
            sorted_violations = sorted(violation_details, 
                                     key=lambda x: x.get('interrupts_per_hour', 0) or 0, 
                                     reverse=True)
            
            for detail in sorted_violations:
                irq = detail['irq']
                count = detail['interrupt_count']
                rate = detail['interrupts_per_hour']
                color_code = detail['color_code']
                
                if rate is not None:
                    rate_str = f"{rate:.1f}/hr"
                else:
                    rate_str = "N/A"
                
                colored_irq = format_colored_text(f"IRQ {irq}", color_code)
                print(f"    {colored_irq}: {count} interrupts ({rate_str})")
            
            print()  # Blank line between CPUs
            
            # Still output the basic format for bash script compatibility
            violations_str = ','.join(map(str, data['violations']))
            print(f"CPU_{cpu}_VIOLATIONS={len(data['violations'])}")
            print(f"CPU_{cpu}_VIOLATION_LIST={violations_str}")
            print(f"CPU_{cpu}_CONTAINERS={containers_str}")
            print()

if __name__ == '__main__':
    main()
