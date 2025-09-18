#!/usr/bin/env python3
"""
LLC Alignment Analyzer for Container IRQ Tool

This script analyzes LLC (Last Level Cache) alignment between isolated containers and their CPUs.
It checks for proper alignment to ensure optimal performance in OpenShift/Kubernetes environments.

OPTIMIZED: Now uses shared_data module for efficient data access.
"""

import os
import sys
import json
import argparse
import glob
import re
import subprocess

# Import shared data module for efficient data access
try:
    import shared_data
except ImportError:
    print("Error: shared_data module not found. Please ensure shared_data.py is in the same directory.", file=sys.stderr)
    sys.exit(1)

# Use shared_data functions instead of duplicating code
parse_cpu_range = shared_data.parse_cpu_range
format_cpu_list_range = shared_data.format_cpu_list_range
extract_pci_devices_from_container = shared_data.extract_pci_devices_from_container

def get_llc_color_code(alignment_status):
    """Get color code for LLC alignment status."""
    # ANSI color codes
    colors = {
        'green': '\033[92m',   # Bright green
        'yellow': '\033[93m',  # Bright yellow  
        'red': '\033[91m',     # Bright red
        'reset': '\033[0m'     # Reset color
    }
    
    if alignment_status == 'aligned':
        return colors['green'], 'green'
    elif alignment_status == 'misaligned':
        return colors['red'], 'red'
    else:  # error or unknown
        return colors['yellow'], 'yellow'

def format_colored_text(text, color_code):
    """Format text with color code."""
    return f"{color_code}{text}\033[0m"

# Use shared_data.get_llc_topology instead of duplicating the function
get_llc_topology = shared_data.get_llc_topology

def get_live_container_list():
    """Get list of container IDs from live system using crictl."""
    try:
        result = subprocess.run(['crictl', 'ps', '-a', '-o', 'json'], 
                               capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        containers_data = json.loads(result.stdout)
        container_ids = [container['id'] for container in containers_data.get('containers', [])]
        return container_ids
    
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, 
            json.JSONDecodeError, KeyError, FileNotFoundError):
        return []

def get_live_container_data(container_id):
    """Get container data from live system using crictl inspect."""
    try:
        result = subprocess.run(['crictl', 'inspect', container_id], 
                               capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        
        container_data = json.loads(result.stdout)
        return container_data
    
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, 
            json.JSONDecodeError, FileNotFoundError):
        return None

# extract_pci_devices_from_container now imported from shared_data

def check_llc_alignment(container_cpus, llc_topology, cpu_to_llc):
    """Check LLC alignment for container CPUs."""
    alignment_results = {
        'container_llc_nodes': [],
        'alignment_status': 'unknown',
        'misaligned_cpus': [],
        'aligned_cpus': [],
        'errors': []
    }
    
    if not container_cpus:
        alignment_results['errors'].append("No container CPUs provided")
        return alignment_results
    
    if not llc_topology:
        alignment_results['errors'].append("Could not determine LLC topology")
        return alignment_results
    
    # Determine which LLC nodes the container CPUs belong to
    container_llc_nodes = set()
    cpu_llc_mapping = {}
    
    for cpu in container_cpus:
        if cpu in cpu_to_llc:
            llc_node = cpu_to_llc[cpu]
            container_llc_nodes.add(llc_node)
            cpu_llc_mapping[cpu] = llc_node
        else:
            alignment_results['errors'].append(f"Could not determine LLC node for CPU {cpu}")
    
    alignment_results['container_llc_nodes'] = sorted(list(container_llc_nodes))
    
    if len(container_llc_nodes) == 0:
        alignment_results['alignment_status'] = 'error'
        if not alignment_results['errors']:
            alignment_results['errors'].append("Could not determine LLC nodes for any container CPUs")
    elif len(container_llc_nodes) == 1:
        # All CPUs are on the same LLC node - perfectly aligned
        alignment_results['alignment_status'] = 'aligned'
        alignment_results['aligned_cpus'] = container_cpus[:]
    else:
        # CPUs are spread across multiple LLC nodes - misaligned
        alignment_results['alignment_status'] = 'misaligned'
        
        # Categorize CPUs by their LLC alignment
        main_llc_node = max(container_llc_nodes, key=lambda node: 
                           sum(1 for cpu in container_cpus if cpu_llc_mapping.get(cpu) == node))
        
        for cpu in container_cpus:
            if cpu in cpu_llc_mapping:
                if cpu_llc_mapping[cpu] == main_llc_node:
                    alignment_results['aligned_cpus'].append(cpu)
                else:
                    alignment_results['misaligned_cpus'].append(cpu)
    
    return alignment_results

def analyze_container_llc_alignment(container_data, llc_topology, cpu_to_llc, base_dir=None):
    """Analyze LLC alignment for a single container using pre-loaded data."""
    result = {
        'container_name': container_data['container_name'],
        'container_id': container_data['container_id'],
        'full_container_id': container_data['full_container_id'],
        'is_isolated': container_data['is_isolated'],
        'analysis_skipped': False
    }
    
    if not container_data['is_isolated']:
        result['analysis_skipped'] = True
        result['skip_reason'] = 'Container is not isolated (missing required annotations)'
        return result
    
    container_cpus = container_data['container_cpus']
    if not container_cpus:
        result['analysis_skipped'] = True
        result['skip_reason'] = 'No CPU set found for container'
        return result
    
    result['container_cpus'] = container_cpus
    result['container_cpus_formatted'] = container_data['container_cpus_formatted']
    
    # Extract PCI devices (for informational purposes)
    pci_devices = container_data['pci_devices']
    result['pci_devices'] = pci_devices
    
    # Use pre-loaded LLC topology
    if not llc_topology:
        result['llc_alignment'] = {
            'alignment_status': 'error',
            'errors': ['Could not determine LLC topology (missing /sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list files)']
        }
        return result
    
    # Check LLC alignment
    llc_alignment = check_llc_alignment(container_cpus, llc_topology, cpu_to_llc)
    result['llc_alignment'] = llc_alignment
    result['llc_topology'] = llc_topology
    
    return result

def analyze_all_containers(base_dir=None):
    """Analyze LLC alignment for all isolated containers using shared_data module."""
    results = {
        'containers': [],
        'summary': {
            'total_containers': 0,
            'isolated_containers': 0,
            'containers_analyzed': 0,
            'aligned_containers': 0,
            'misaligned_containers': 0,
            'containers_with_errors': 0
        },
        'llc_topology': {}
    }
    
    # Get LLC topology once using shared module (CACHED)
    llc_topology, cpu_to_llc = get_llc_topology(base_dir)
    results['llc_topology'] = llc_topology
    
    if base_dir:
        # Get all container data once using shared module (CACHED)
        all_containers = shared_data.load_all_container_data(base_dir)
        
        if not all_containers:
            results['error'] = f"Could not load container data from sosreport directory: {base_dir}"
            return results
        
        # Process each container using pre-loaded data
        for container_id, container_data in all_containers.items():
            results['summary']['total_containers'] += 1
            
            if container_data.get('is_isolated'):
                results['summary']['isolated_containers'] += 1
                
                # Analyze this container using pre-loaded data
                container_analysis = analyze_container_llc_alignment(container_data, llc_topology, cpu_to_llc, base_dir)
                results['containers'].append(container_analysis)
                
                if not container_analysis.get('analysis_skipped'):
                    results['summary']['containers_analyzed'] += 1
                    
                    llc_status = container_analysis.get('llc_alignment', {}).get('alignment_status')
                    if llc_status == 'aligned':
                        results['summary']['aligned_containers'] += 1
                    elif llc_status == 'misaligned':
                        results['summary']['misaligned_containers'] += 1
                    else:
                        results['summary']['containers_with_errors'] += 1
    else:
        # Live system analysis using crictl - would benefit from similar optimization
        container_ids = get_live_container_list()
        if not container_ids:
            results['error'] = "Could not retrieve container list from live system (crictl not available or no containers)"
            return results
        
        for container_id in container_ids:
            results['summary']['total_containers'] += 1
            
            container_data = get_live_container_data(container_id)
            if not container_data:
                # Skip containers we can't inspect
                continue
            
            # Convert live container data to our standard format
            # For live data, we need to create the container info manually since parse_container_data expects a file
            container_name = (container_data.get('info', {}).get('config', {}).get('metadata', {}).get('name') or
                             container_data.get('status', {}).get('metadata', {}).get('name') or
                             "unknown")
            
            container_full_id = (container_data.get('status', {}).get('id') or
                               container_data.get('info', {}).get('id') or
                               container_id)
            
            annotations = container_data.get('info', {}).get('runtimeSpec', {}).get('annotations', {})
            is_isolated = (annotations.get('irq-load-balancing.crio.io') == 'disable' and 
                           annotations.get('cpu-quota.crio.io') == 'disable')
            
            cpu_set = container_data.get('status', {}).get('resources', {}).get('linux', {}).get('cpusetCpus')
            container_cpus = shared_data.parse_cpu_range(cpu_set) if cpu_set else []
            
            pci_devices = shared_data.extract_pci_devices_from_container(container_data)
            netns_id = shared_data.get_container_network_namespace(container_data)
            
            container_info = {
                'container_name': container_name,
                'container_id': container_full_id[:12] if len(container_full_id) > 12 else container_full_id,
                'full_container_id': container_full_id,
                'is_isolated': is_isolated,
                'container_cpus': container_cpus,
                'container_cpus_formatted': cpu_set or '',
                'pci_devices': pci_devices,
                'network_namespace': netns_id,
                'analysis_skipped': False
            }
                
            if container_info.get('is_isolated'):
                results['summary']['isolated_containers'] += 1
                
                container_analysis = analyze_container_llc_alignment(container_info, llc_topology, cpu_to_llc, base_dir)
                results['containers'].append(container_analysis)
                
                if not container_analysis.get('analysis_skipped'):
                    results['summary']['containers_analyzed'] += 1
                    
                    llc_status = container_analysis.get('llc_alignment', {}).get('alignment_status')
                    if llc_status == 'aligned':
                        results['summary']['aligned_containers'] += 1
                    elif llc_status == 'misaligned':
                        results['summary']['misaligned_containers'] += 1
                    else:
                        results['summary']['containers_with_errors'] += 1
    
    return results

def format_text_output(analysis_results, full_analysis=False):
    """Format analysis results as human-readable text."""
    output_lines = []
    
    output_lines.append("=" * 60)
    output_lines.append("LLC ALIGNMENT ANALYSIS")
    output_lines.append("=" * 60)
    output_lines.append("")
    
    # Add color legend
    green_code, _ = get_llc_color_code('aligned')
    yellow_code, _ = get_llc_color_code('error')
    red_code, _ = get_llc_color_code('misaligned')
    
    output_lines.append("Color-coded LLC alignment status:")
    output_lines.append(f"  {format_colored_text('🟢 Green: LLC Aligned (Optimal Performance)', green_code)}")
    output_lines.append(f"  {format_colored_text('🟡 Yellow: Analysis Errors', yellow_code)}")
    output_lines.append(f"  {format_colored_text('🔴 Red: LLC Misaligned (Performance Impact)', red_code)}")
    output_lines.append("")
    
    if 'error' in analysis_results:
        output_lines.append(f"ERROR: {analysis_results['error']}")
        return "\n".join(output_lines)
    
    # Summary
    summary = analysis_results['summary']
    output_lines.append("SUMMARY:")
    output_lines.append(f"  Total containers: {summary['total_containers']}")
    output_lines.append(f"  Isolated containers: {summary['isolated_containers']}")
    output_lines.append(f"  Containers analyzed: {summary['containers_analyzed']}")
    output_lines.append(f"  LLC aligned: {summary['aligned_containers']}")
    output_lines.append(f"  LLC misaligned: {summary['misaligned_containers']}")
    output_lines.append(f"  Containers with errors: {summary['containers_with_errors']}")
    output_lines.append("")
    
    # LLC topology
    if analysis_results['llc_topology']:
        output_lines.append("LLC TOPOLOGY:")
        for node_num, node_info in sorted(analysis_results['llc_topology'].items()):
            output_lines.append(f"  LLC Node {node_num}: CPUs {node_info['cpulist']}")
        output_lines.append("")
    else:
        output_lines.append("LLC TOPOLOGY:")
        output_lines.append("  ⚠ WARNING: LLC topology not available")
        output_lines.append("  Missing /sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list files")
        output_lines.append("  LLC alignment analysis cannot be performed")
        output_lines.append("")
    
    # Detailed analysis
    containers_analyzed = [c for c in analysis_results['containers'] 
                          if c.get('is_isolated') and not c.get('analysis_skipped')]
    
    if containers_analyzed:
        output_lines.append("DETAILED ANALYSIS:")
        output_lines.append("")
        
        # Limit to first 10 containers unless full_analysis is requested
        display_containers = containers_analyzed
        if not full_analysis and len(containers_analyzed) > 10:
            display_containers = containers_analyzed[:10]
            truncated_count = len(containers_analyzed) - 10
        else:
            truncated_count = 0
        
        for container in display_containers:
            container_name = container['container_name']
            container_id = container['container_id']
            
            output_lines.append(f"Container: {container_name} ({container_id})")
            output_lines.append(f"  CPUs: {container['container_cpus_formatted']}")
            
            # Add PCI device info if available
            if container.get('pci_devices'):
                output_lines.append(f"  PCI Devices: {', '.join(container['pci_devices'])}")
            
            # LLC alignment status
            llc_alignment = container.get('llc_alignment', {})
            alignment_status = llc_alignment.get('alignment_status', 'unknown')
            
            # Color-coded LLC alignment status
            color_code, color_name = get_llc_color_code(alignment_status)
            
            if alignment_status == 'aligned':
                colored_status = format_colored_text("✓ LLC Alignment: ALIGNED", color_code)
                output_lines.append(f"  {colored_status}")
            elif alignment_status == 'misaligned':
                colored_status = format_colored_text("✗ LLC Alignment: MISALIGNED", color_code)
                output_lines.append(f"  {colored_status}")
                
                misaligned_cpus = llc_alignment.get('misaligned_cpus', [])
                if misaligned_cpus:
                    misaligned_formatted = format_cpu_list_range(misaligned_cpus)
                    misaligned_text = format_colored_text(f"Misaligned CPUs: {misaligned_formatted}", color_code)
                    output_lines.append(f"    {misaligned_text}")
            else:
                colored_status = format_colored_text("⚠ LLC Alignment: ERROR", color_code)
                output_lines.append(f"  {colored_status}")
                errors = llc_alignment.get('errors', [])
                for error in errors:
                    error_text = format_colored_text(f"Error: {error}", color_code)
                    output_lines.append(f"    {error_text}")
                if not errors:
                    error_text = format_colored_text("Error: Unable to determine LLC alignment", color_code)
                    output_lines.append(f"    {error_text}")
            
            # Container LLC nodes
            container_llc_nodes = llc_alignment.get('container_llc_nodes', [])
            if container_llc_nodes:
                llc_nodes_str = ', '.join(map(str, sorted(container_llc_nodes)))
                output_lines.append(f"  Container LLC nodes: {llc_nodes_str}")
            
            output_lines.append("")
        
        # Show truncation message if containers were limited
        if truncated_count > 0:
            output_lines.append(f"... and {truncated_count} more containers (use --full-analysis for complete list)")
            output_lines.append("")
    
    return "\n".join(output_lines)

def main():
    parser = argparse.ArgumentParser(description='Analyze LLC alignment for isolated containers')
    parser.add_argument('--sosreport-dir', help='Path to sosreport directory')
    parser.add_argument('--output-format', choices=['json', 'text'], default='text',
                       help='Output format')
    parser.add_argument('--container-id', help='Analyze specific container ID')
    parser.add_argument('--full-analysis', action='store_true',
                       help='Show all containers (default: limit to first 10)')
    
    args = parser.parse_args()
    
    # Note: --container-id can work with both sosreport and live systems
    
    if args.container_id:
        # Analyze single container using shared_data module
        if args.sosreport_dir:
            all_containers = shared_data.load_all_container_data(args.sosreport_dir)
            
            container_data = None
            for cid, cdata in all_containers.items():
                if cid.startswith(args.container_id):
                    container_data = cdata
                    break
            
            if not container_data:
                print(f"Error: Container {args.container_id} not found", file=sys.stderr)
                sys.exit(1)
            
            llc_topology, cpu_to_llc = get_llc_topology(args.sosreport_dir)
            result = analyze_container_llc_alignment(container_data, llc_topology, cpu_to_llc, args.sosreport_dir)
        else:
            # Live system analysis for single container
            container_data = get_live_container_data(args.container_id)
            if not container_data:
                print(f"Error: Could not get container data for {args.container_id} from live system", file=sys.stderr)
                sys.exit(1)
            
            # Convert live container data to our standard format (similar to above)
            container_name = (container_data.get('info', {}).get('config', {}).get('metadata', {}).get('name') or
                             container_data.get('status', {}).get('metadata', {}).get('name') or
                             "unknown")
            
            container_full_id = (container_data.get('status', {}).get('id') or
                               container_data.get('info', {}).get('id') or
                               args.container_id)
            
            annotations = container_data.get('info', {}).get('runtimeSpec', {}).get('annotations', {})
            is_isolated = (annotations.get('irq-load-balancing.crio.io') == 'disable' and 
                           annotations.get('cpu-quota.crio.io') == 'disable')
            
            cpu_set = container_data.get('status', {}).get('resources', {}).get('linux', {}).get('cpusetCpus')
            container_cpus = shared_data.parse_cpu_range(cpu_set) if cpu_set else []
            
            pci_devices = shared_data.extract_pci_devices_from_container(container_data)
            netns_id = shared_data.get_container_network_namespace(container_data)
            
            container_info = {
                'container_name': container_name,
                'container_id': container_full_id[:12] if len(container_full_id) > 12 else container_full_id,
                'full_container_id': container_full_id,
                'is_isolated': is_isolated,
                'container_cpus': container_cpus,
                'container_cpus_formatted': cpu_set or '',
                'pci_devices': pci_devices,
                'network_namespace': netns_id,
                'analysis_skipped': False
            }
            
            llc_topology, cpu_to_llc = get_llc_topology()
            result = analyze_container_llc_alignment(container_info, llc_topology, cpu_to_llc)
        
        if args.output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            # Format single container result for text output
            print(f"Container: {result['container_name']} ({result['container_id']})")
            if result.get('analysis_skipped'):
                print(f"Analysis skipped: {result.get('skip_reason', 'Unknown reason')}")
            else:
                # Print detailed analysis...
                llc_alignment = result.get('llc_alignment', {})
                alignment_status = llc_alignment.get('alignment_status', 'unknown')
                print(f"LLC Alignment: {alignment_status.upper()}")
                # Add more details as needed
    else:
        # Analyze all containers
        results = analyze_all_containers(args.sosreport_dir)
        
        if args.output_format == 'json':
            print(json.dumps(results, indent=2))
        else:
            print(format_text_output(results, args.full_analysis))

if __name__ == '__main__':
    main()
