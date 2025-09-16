#!/usr/bin/env python3
"""
NUMA Alignment Analyzer for Container IRQ Tool

This script analyzes NUMA alignment between isolated containers and their PCI devices.
It checks for proper alignment to ensure optimal performance in OpenShift/Kubernetes environments.
"""

import os
import sys
import json
import argparse
import glob
import re
import subprocess

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

def get_numa_topology_from_cpuinfo(base_dir):
    """Parse NUMA topology from proc/cpuinfo in sosreport."""
    numa_info = {}
    
    cpuinfo_file = os.path.join(base_dir, "proc", "cpuinfo")
    if not os.path.isfile(cpuinfo_file):
        return numa_info
    
    try:
        with open(cpuinfo_file, 'r') as f:
            content = f.read()
        
        cpu_numa_map = {}  # Map CPU number to NUMA node
        current_cpu = None
        current_numa_node = None
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('processor'):
                # Extract CPU number
                parts = line.split(':')
                if len(parts) >= 2:
                    current_cpu = int(parts[1].strip())
            elif line.startswith('physical id'):
                # This often corresponds to NUMA node in multi-socket systems
                parts = line.split(':')
                if len(parts) >= 2:
                    current_numa_node = int(parts[1].strip())
            elif line == '' and current_cpu is not None:
                # End of CPU block
                if current_numa_node is not None:
                    cpu_numa_map[current_cpu] = current_numa_node
                current_cpu = None
                current_numa_node = None
        
        # Group CPUs by NUMA node
        for cpu, numa_node in cpu_numa_map.items():
            if numa_node not in numa_info:
                numa_info[numa_node] = {'cpus': [], 'cpulist': ''}
            numa_info[numa_node]['cpus'].append(cpu)
        
        # Format CPU lists
        for numa_node in numa_info:
            cpus = sorted(numa_info[numa_node]['cpus'])
            numa_info[numa_node]['cpulist'] = format_cpu_list_range(cpus)
        
    except (OSError, ValueError, IndexError):
        pass
    
    return numa_info

def format_cpu_list_range(cpu_list):
    """Format a list of CPU numbers into range format with step detection."""
    if not cpu_list:
        return ""
    
    cpu_list = sorted(cpu_list)
    if len(cpu_list) == 1:
        return str(cpu_list[0])
    
    ranges = []
    i = 0
    
    while i < len(cpu_list):
        start = cpu_list[i]
        end = start
        step = 1
        
        # Look ahead to find consecutive numbers or patterns
        if i + 1 < len(cpu_list):
            # Check if we have a step pattern (like even/odd cores)
            if i + 2 < len(cpu_list):
                potential_step = cpu_list[i + 1] - cpu_list[i]
                if cpu_list[i + 2] - cpu_list[i + 1] == potential_step:
                    step = potential_step
        
        # Find the end of this sequence
        while i + 1 < len(cpu_list) and cpu_list[i + 1] == cpu_list[i] + step:
            i += 1
            end = cpu_list[i]
        
        # Format the range
        if start == end:
            ranges.append(str(start))
        elif step == 1:
            # Consecutive range
            ranges.append(f"{start}-{end}")
        else:
            # Step range - show pattern more clearly
            if end - start >= step * 3:  # Only show step notation for longer sequences
                if step == 2 and start % 2 == 0:
                    # Special case for even cores
                    ranges.append(f"{start}-{end}:2 (even)")
                elif step == 2 and start % 2 == 1:
                    # Special case for odd cores
                    ranges.append(f"{start}-{end}:2 (odd)")
                else:
                    ranges.append(f"{start}-{end}:{step}")
            else:
                # For short step sequences, just list them
                sequence = list(range(start, end + 1, step))
                ranges.append(",".join(map(str, sequence)))
        
        i += 1
    
    return ",".join(ranges)

def get_numa_color_code(alignment_status):
    """Get color code for NUMA alignment status."""
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

def get_numa_topology(base_dir=None):
    """Get NUMA topology information from the system."""
    numa_info = {}
    
    if base_dir:
        # For sosreport analysis - try standard location first
        numa_base = os.path.join(base_dir, "sys", "devices", "system", "node")
        
        if os.path.isdir(numa_base):
            try:
                node_dirs = [d for d in os.listdir(numa_base) 
                            if d.startswith('node') and d[4:].isdigit()]
                
                for node_dir in node_dirs:
                    node_num = int(node_dir[4:])
                    node_path = os.path.join(numa_base, node_dir)
                    
                    # Get CPUs for this NUMA node
                    cpulist_file = os.path.join(node_path, "cpulist")
                    if os.path.isfile(cpulist_file):
                        try:
                            with open(cpulist_file, 'r') as f:
                                cpulist = f.read().strip()
                            cpu_numbers = parse_cpu_range(cpulist)
                            formatted_cpulist = format_cpu_list_range(cpu_numbers)
                            numa_info[node_num] = {
                                'cpus': cpu_numbers,
                                'cpulist': formatted_cpulist
                            }
                        except (OSError, ValueError):
                            continue
            except OSError:
                pass
        
        # If no NUMA info found, try fallback method using cpuinfo
        if not numa_info:
            numa_info = get_numa_topology_from_cpuinfo(base_dir)
    else:
        # For live system
        numa_base = "/sys/devices/system/node"
        
        if os.path.isdir(numa_base):
            try:
                node_dirs = [d for d in os.listdir(numa_base) 
                            if d.startswith('node') and d[4:].isdigit()]
                
                for node_dir in node_dirs:
                    node_num = int(node_dir[4:])
                    node_path = os.path.join(numa_base, node_dir)
                    
                    # Get CPUs for this NUMA node
                    cpulist_file = os.path.join(node_path, "cpulist")
                    if os.path.isfile(cpulist_file):
                        try:
                            with open(cpulist_file, 'r') as f:
                                cpulist = f.read().strip()
                            cpu_numbers = parse_cpu_range(cpulist)
                            formatted_cpulist = format_cpu_list_range(cpu_numbers)
                            numa_info[node_num] = {
                                'cpus': cpu_numbers,
                                'cpulist': formatted_cpulist
                            }
                        except (OSError, ValueError):
                            continue
            except OSError:
                pass
    
    return numa_info

def get_pci_numa_info_from_lspci(pci_address, base_dir):
    """Get NUMA node information for a PCI device from lspci output in sosreport."""
    lspci_file = os.path.join(base_dir, "sos_commands", "pci", "lspci_-nnvv")
    
    if not os.path.isfile(lspci_file):
        return None
    
    try:
        with open(lspci_file, 'r') as f:
            content = f.read()
        
        # Convert full PCI address to short format for matching
        # e.g., "0000:2f:00.7" -> "2f:00.7"
        if pci_address.startswith("0000:"):
            short_pci = pci_address[5:]
        else:
            short_pci = pci_address
        
        # Parse lspci output to find NUMA node for this PCI device
        # Format as described by user: look for PCI address, then find NUMA line
        lines = content.split('\n')
        found_device = False
        
        for line in lines:
            line = line.strip()
            
            # Look for PCI device line (starts with PCI address)
            if line.startswith(short_pci):
                found_device = True
                continue
            
            # If we found our device, look for NUMA line
            if found_device and line.startswith('NUMA'):
                # Extract NUMA node number from end of line
                parts = line.split()
                if parts:
                    try:
                        numa_node = int(parts[-1])
                        if numa_node >= 0:
                            return numa_node
                    except (ValueError, IndexError):
                        pass
                # Reset after finding NUMA line (or failing to parse it)
                found_device = False
            
            # Reset if we hit another PCI device before finding NUMA
            elif found_device and line and not line.startswith('\t') and not line.startswith(' '):
                # This is likely another PCI device
                if ':' in line and '.' in line:
                    found_device = False
        
    except (OSError, IOError):
        pass
    
    return None

def get_pci_numa_info(pci_address, base_dir=None):
    """Get NUMA node information for a PCI device."""
    if base_dir:
        # For sosreport analysis - try standard location first
        pci_path = os.path.join(base_dir, "sys", "bus", "pci", "devices", pci_address)
        numa_node_file = os.path.join(pci_path, "numa_node")
        
        if os.path.isfile(numa_node_file):
            try:
                with open(numa_node_file, 'r') as f:
                    numa_node = int(f.read().strip())
                
                # Check if it's a valid NUMA node (not -1)
                if numa_node >= 0:
                    return numa_node
            except (OSError, ValueError):
                pass
        
        # If standard location doesn't work, try lspci fallback
        return get_pci_numa_info_from_lspci(pci_address, base_dir)
    else:
        # For live system
        pci_path = f"/sys/bus/pci/devices/{pci_address}"
        numa_node_file = os.path.join(pci_path, "numa_node")
        
        if not os.path.isfile(numa_node_file):
            return None
        
        try:
            with open(numa_node_file, 'r') as f:
                numa_node = int(f.read().strip())
            
            # Check if it's a valid NUMA node (not -1)
            if numa_node >= 0:
                return numa_node
        except (OSError, ValueError):
            pass
    
    return None

def extract_pci_devices_from_container(container_data):
    """Extract PCI device addresses from container annotations."""
    pci_devices = []
    
    try:
        # Get environment variables from the container spec
        env_vars = container_data.get('info', {}).get('runtimeSpec', {}).get('process', {}).get('env', [])
        
        for env_var in env_vars:
            if env_var.startswith('PCIDEVICE_OPENSHIFT') and '_INFO=' in env_var:
                # Split on first '=' to get the JSON value
                parts = env_var.split('=', 1)
                if len(parts) != 2:
                    continue
                
                try:
                    # Parse the JSON value
                    pci_info = json.loads(parts[1])
                    
                    # Extract device IDs from the nested structure
                    for device_id, device_data in pci_info.items():
                        if isinstance(device_data, dict) and 'generic' in device_data:
                            generic_info = device_data['generic']
                            if 'deviceID' in generic_info:
                                pci_devices.append(generic_info['deviceID'])
                
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    
    except (KeyError, TypeError):
        pass
    
    return pci_devices

def get_container_network_namespace(container_data):
    """Extract network namespace ID from container data."""
    try:
        namespaces = container_data.get('info', {}).get('runtimeSpec', {}).get('linux', {}).get('namespaces', [])
        
        for ns in namespaces:
            if ns.get('type') == 'network' and 'path' in ns:
                # Extract namespace ID from path like /var/run/netns/598de306-dfa4-4025-bc6b-d466c42d980d
                ns_path = ns['path']
                ns_id = ns_path.split('/')[-1]
                return ns_id
    
    except (KeyError, TypeError):
        pass
    
    return None

def validate_pci_in_netns(pci_addresses, netns_id, base_dir=None):
    """Validate that PCI devices are present in the container's network namespace."""
    validation_results = {}
    
    if not netns_id or not pci_addresses:
        return validation_results
    
    if base_dir:
        # For sosreport analysis
        netns_dir = os.path.join(base_dir, "sos_commands", "networking", "namespaces", netns_id)
        ip_addr_file = os.path.join(netns_dir, f"ip_netns_exec_{netns_id}_ip_-d_address_show")
    else:
        # For live system - we'll need to execute ip netns command
        ip_addr_file = None
    
    for pci_addr in pci_addresses:
        validation_results[pci_addr] = {
            'found_in_netns': False,
            'error': None
        }
        
        try:
            if base_dir:
                # Read from sosreport file
                if os.path.isfile(ip_addr_file):
                    with open(ip_addr_file, 'r') as f:
                        content = f.read()
                    
                    # Look for PCI device in the output
                    if f"parentdev {pci_addr}" in content:
                        validation_results[pci_addr]['found_in_netns'] = True
                else:
                    validation_results[pci_addr]['error'] = f"Network namespace file not found: {ip_addr_file}"
            else:
                # Execute command on live system
                try:
                    result = subprocess.run([
                        'ip', 'netns', 'exec', netns_id, 'ip', '-d', 'address', 'show'
                    ], capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        if f"parentdev {pci_addr}" in result.stdout:
                            validation_results[pci_addr]['found_in_netns'] = True
                    else:
                        validation_results[pci_addr]['error'] = f"Failed to execute ip netns command: {result.stderr}"
                
                except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
                    validation_results[pci_addr]['error'] = f"Command execution error: {str(e)}"
        
        except Exception as e:
            validation_results[pci_addr]['error'] = f"Validation error: {str(e)}"
    
    return validation_results

def check_numa_alignment(container_cpus, pci_devices, numa_topology, base_dir=None):
    """Check NUMA alignment between container CPUs and PCI devices."""
    alignment_results = {
        'container_numa_nodes': [],
        'pci_numa_info': {},
        'alignment_status': 'unknown',
        'misaligned_devices': [],
        'aligned_devices': [],
        'errors': []
    }
    
    if not container_cpus:
        alignment_results['errors'].append("No container CPUs provided")
        return alignment_results
    
    if not pci_devices:
        alignment_results['errors'].append("No PCI devices provided")
        return alignment_results
    
    # Determine which NUMA nodes the container CPUs belong to
    for numa_node, numa_info in numa_topology.items():
        numa_cpus = set(numa_info['cpus'])
        container_cpu_set = set(container_cpus)
        
        if numa_cpus.intersection(container_cpu_set):
            if numa_node not in alignment_results['container_numa_nodes']:
                alignment_results['container_numa_nodes'].append(numa_node)
    
    if not alignment_results['container_numa_nodes']:
        alignment_results['errors'].append("Could not determine NUMA nodes for container CPUs")
        return alignment_results
    
    # Check NUMA alignment for each PCI device
    all_aligned = True
    
    for pci_addr in pci_devices:
        pci_numa_node = get_pci_numa_info(pci_addr, base_dir)
        
        alignment_results['pci_numa_info'][pci_addr] = {
            'numa_node': pci_numa_node,
            'aligned': False
        }
        
        if pci_numa_node is None:
            alignment_results['errors'].append(f"Could not determine NUMA node for PCI device {pci_addr}")
            all_aligned = False
        elif pci_numa_node in alignment_results['container_numa_nodes']:
            alignment_results['pci_numa_info'][pci_addr]['aligned'] = True
            alignment_results['aligned_devices'].append(pci_addr)
        else:
            alignment_results['pci_numa_info'][pci_addr]['aligned'] = False
            alignment_results['misaligned_devices'].append(pci_addr)
            all_aligned = False
    
    # Determine overall alignment status
    if all_aligned and not alignment_results['errors']:
        alignment_results['alignment_status'] = 'aligned'
    elif alignment_results['misaligned_devices']:
        alignment_results['alignment_status'] = 'misaligned'
    else:
        alignment_results['alignment_status'] = 'error'
    
    return alignment_results

def analyze_container_numa_alignment(container_file, base_dir=None):
    """Analyze NUMA alignment for a single container."""
    try:
        with open(container_file, 'r') as f:
            container_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
        return {
            'error': f"Failed to read container data: {str(e)}",
            'container_id': os.path.basename(container_file),
            'analysis_skipped': True
        }
    
    # Get container metadata
    container_name = (container_data.get('info', {}).get('config', {}).get('metadata', {}).get('name') or
                     container_data.get('status', {}).get('metadata', {}).get('name') or
                     "unknown")
    
    container_id = (container_data.get('status', {}).get('id') or
                   container_data.get('info', {}).get('id') or
                   os.path.basename(container_file))
    
    # Check for isolation annotations
    annotations = container_data.get('info', {}).get('runtimeSpec', {}).get('annotations', {})
    is_isolated = (annotations.get('irq-load-balancing.crio.io') == 'disable' and 
                   annotations.get('cpu-quota.crio.io') == 'disable')
    
    result = {
        'container_name': container_name,
        'container_id': container_id[:12] if len(container_id) > 12 else container_id,
        'full_container_id': container_id,
        'is_isolated': is_isolated,
        'analysis_skipped': False
    }
    
    if not is_isolated:
        result['analysis_skipped'] = True
        result['skip_reason'] = 'Container is not isolated (missing required annotations)'
        return result
    
    # Get container CPU set
    cpu_set = container_data.get('status', {}).get('resources', {}).get('linux', {}).get('cpusetCpus')
    if not cpu_set:
        result['analysis_skipped'] = True
        result['skip_reason'] = 'No CPU set found for container'
        return result
    
    container_cpus = parse_cpu_range(cpu_set)
    result['container_cpus'] = container_cpus
    result['container_cpus_formatted'] = cpu_set
    
    # Extract PCI devices
    pci_devices = extract_pci_devices_from_container(container_data)
    result['pci_devices'] = pci_devices
    
    if not pci_devices:
        result['analysis_skipped'] = True
        result['skip_reason'] = 'No PCI devices found in container annotations'
        return result
    
    # Get network namespace
    netns_id = get_container_network_namespace(container_data)
    result['network_namespace'] = netns_id
    
    # Validate PCI devices in network namespace
    if netns_id:
        netns_validation = validate_pci_in_netns(pci_devices, netns_id, base_dir)
        result['netns_validation'] = netns_validation
    else:
        result['netns_validation'] = {}
        result['netns_validation_error'] = 'Could not determine network namespace'
    
    # Get NUMA topology
    numa_topology = get_numa_topology(base_dir)
    if not numa_topology:
        result['numa_alignment'] = {
            'alignment_status': 'error',
            'errors': ['Could not determine NUMA topology (missing both /sys/devices/system/node/nodeX/cpulist files and physical id info in /proc/cpuinfo)']
        }
        return result
    
    # Check NUMA alignment
    numa_alignment = check_numa_alignment(container_cpus, pci_devices, numa_topology, base_dir)
    result['numa_alignment'] = numa_alignment
    result['numa_topology'] = numa_topology
    
    return result

def analyze_all_containers(base_dir=None):
    """Analyze NUMA alignment for all isolated containers."""
    results = {
        'containers': [],
        'summary': {
            'total_containers': 0,
            'isolated_containers': 0,
            'containers_with_pci': 0,
            'aligned_containers': 0,
            'misaligned_containers': 0,
            'containers_with_errors': 0
        },
        'numa_topology': {}
    }
    
    # Get NUMA topology once
    numa_topology = get_numa_topology(base_dir)
    results['numa_topology'] = numa_topology
    
    if base_dir:
        # Sosreport analysis
        containers_dir = os.path.join(base_dir, "sos_commands", "crio", "containers")
        if not os.path.isdir(containers_dir):
            results['error'] = f"Container directory not found: {containers_dir}"
            return results
        
        container_files = glob.glob(os.path.join(containers_dir, "*"))
    else:
        # Live system analysis - would need to implement crictl integration
        results['error'] = "Live system analysis not yet implemented for NUMA analyzer"
        return results
    
    for container_file in container_files:
        if not os.path.isfile(container_file):
            continue
        
        results['summary']['total_containers'] += 1
        
        container_analysis = analyze_container_numa_alignment(container_file, base_dir)
        results['containers'].append(container_analysis)
        
        if container_analysis.get('is_isolated'):
            results['summary']['isolated_containers'] += 1
            
            if not container_analysis.get('analysis_skipped'):
                if container_analysis.get('pci_devices'):
                    results['summary']['containers_with_pci'] += 1
                    
                    numa_status = container_analysis.get('numa_alignment', {}).get('alignment_status')
                    if numa_status == 'aligned':
                        results['summary']['aligned_containers'] += 1
                    elif numa_status == 'misaligned':
                        results['summary']['misaligned_containers'] += 1
                    else:
                        results['summary']['containers_with_errors'] += 1
    
    return results

def format_text_output(analysis_results):
    """Format analysis results as human-readable text."""
    output_lines = []
    
    output_lines.append("=" * 60)
    output_lines.append("NUMA ALIGNMENT ANALYSIS")
    output_lines.append("=" * 60)
    output_lines.append("")
    
    # Add color legend
    green_code, _ = get_numa_color_code('aligned')
    yellow_code, _ = get_numa_color_code('error')
    red_code, _ = get_numa_color_code('misaligned')
    
    output_lines.append("Color-coded NUMA alignment status:")
    output_lines.append(f"  {format_colored_text('🟢 Green: NUMA Aligned (Optimal Performance)', green_code)}")
    output_lines.append(f"  {format_colored_text('🟡 Yellow: Analysis Errors', yellow_code)}")
    output_lines.append(f"  {format_colored_text('🔴 Red: NUMA Misaligned (Performance Impact)', red_code)}")
    output_lines.append("")
    
    if 'error' in analysis_results:
        output_lines.append(f"ERROR: {analysis_results['error']}")
        return "\n".join(output_lines)
    
    # Summary
    summary = analysis_results['summary']
    output_lines.append("SUMMARY:")
    output_lines.append(f"  Total containers: {summary['total_containers']}")
    output_lines.append(f"  Isolated containers: {summary['isolated_containers']}")
    output_lines.append(f"  Containers with PCI devices: {summary['containers_with_pci']}")
    output_lines.append(f"  NUMA aligned: {summary['aligned_containers']}")
    output_lines.append(f"  NUMA misaligned: {summary['misaligned_containers']}")
    output_lines.append(f"  Containers with errors: {summary['containers_with_errors']}")
    output_lines.append("")
    
    # NUMA topology
    if analysis_results['numa_topology']:
        output_lines.append("NUMA TOPOLOGY:")
        for node_num, node_info in sorted(analysis_results['numa_topology'].items()):
            output_lines.append(f"  Node {node_num}: CPUs {node_info['cpulist']}")
        output_lines.append("")
    else:
        output_lines.append("NUMA TOPOLOGY:")
        output_lines.append("  ⚠ WARNING: NUMA topology not available in sosreport")
        output_lines.append("  Missing both /sys/devices/system/node/nodeX/cpulist files")
        output_lines.append("  and physical id information in /proc/cpuinfo")
        output_lines.append("  NUMA alignment analysis cannot be performed")
        output_lines.append("")
    
    # Detailed analysis
    containers_analyzed = [c for c in analysis_results['containers'] 
                          if c.get('is_isolated') and not c.get('analysis_skipped')]
    
    if containers_analyzed:
        output_lines.append("DETAILED ANALYSIS:")
        output_lines.append("")
        
        for container in containers_analyzed:
            container_name = container['container_name']
            container_id = container['container_id']
            
            output_lines.append(f"Container: {container_name} ({container_id})")
            output_lines.append(f"  CPUs: {container['container_cpus_formatted']}")
            output_lines.append(f"  PCI Devices: {', '.join(container['pci_devices'])}")
            
            # NUMA alignment status
            numa_alignment = container.get('numa_alignment', {})
            alignment_status = numa_alignment.get('alignment_status', 'unknown')
            
            # Color-coded NUMA alignment status
            color_code, color_name = get_numa_color_code(alignment_status)
            
            if alignment_status == 'aligned':
                colored_status = format_colored_text("✓ NUMA Alignment: ALIGNED", color_code)
                output_lines.append(f"  {colored_status}")
            elif alignment_status == 'misaligned':
                colored_status = format_colored_text("✗ NUMA Alignment: MISALIGNED", color_code)
                output_lines.append(f"  {colored_status}")
                misaligned = numa_alignment.get('misaligned_devices', [])
                if misaligned:
                    misaligned_text = format_colored_text(f"Misaligned devices: {', '.join(misaligned)}", color_code)
                    output_lines.append(f"    {misaligned_text}")
            else:
                colored_status = format_colored_text("⚠ NUMA Alignment: ERROR", color_code)
                output_lines.append(f"  {colored_status}")
                errors = numa_alignment.get('errors', [])
                for error in errors:
                    error_text = format_colored_text(f"Error: {error}", color_code)
                    output_lines.append(f"    {error_text}")
                if not errors:
                    error_text = format_colored_text("Error: Unable to determine NUMA alignment", color_code)
                    output_lines.append(f"    {error_text}")
            
            # Container NUMA nodes
            container_numa_nodes = numa_alignment.get('container_numa_nodes', [])
            if container_numa_nodes:
                numa_nodes_str = ', '.join(map(str, sorted(container_numa_nodes)))
                output_lines.append(f"  Container NUMA nodes: {numa_nodes_str}")
            
            # PCI device NUMA info with color coding
            pci_numa_info = numa_alignment.get('pci_numa_info', {})
            for pci_addr, pci_info in pci_numa_info.items():
                numa_node = pci_info.get('numa_node')
                aligned = pci_info.get('aligned', False)
                
                # Color code PCI device alignment
                if aligned:
                    pci_color_code, _ = get_numa_color_code('aligned')
                    status_symbol = "✓"
                else:
                    pci_color_code, _ = get_numa_color_code('misaligned')
                    status_symbol = "✗"
                
                colored_pci_line = format_colored_text(f"{status_symbol} PCI {pci_addr}: NUMA node {numa_node}", pci_color_code)
                output_lines.append(f"  {colored_pci_line}")
            
            # Network namespace validation with color coding
            netns_validation = container.get('netns_validation', {})
            if netns_validation:
                output_lines.append("  Network namespace validation:")
                for pci_addr, validation in netns_validation.items():
                    if validation.get('found_in_netns'):
                        success_color, _ = get_numa_color_code('aligned')
                        colored_netns = format_colored_text(f"✓ {pci_addr}: Found in netns", success_color)
                        output_lines.append(f"    {colored_netns}")
                    elif validation.get('error'):
                        error_color, _ = get_numa_color_code('error')
                        colored_netns = format_colored_text(f"✗ {pci_addr}: {validation['error']}", error_color)
                        output_lines.append(f"    {colored_netns}")
                    else:
                        error_color, _ = get_numa_color_code('misaligned')
                        colored_netns = format_colored_text(f"✗ {pci_addr}: Not found in netns", error_color)
                        output_lines.append(f"    {colored_netns}")
            
            output_lines.append("")
    
    return "\n".join(output_lines)

def main():
    parser = argparse.ArgumentParser(description='Analyze NUMA alignment for isolated containers')
    parser.add_argument('--sosreport-dir', help='Path to sosreport directory')
    parser.add_argument('--output-format', choices=['json', 'text'], default='text',
                       help='Output format')
    parser.add_argument('--container-id', help='Analyze specific container ID')
    
    args = parser.parse_args()
    
    if args.container_id and not args.sosreport_dir:
        print("Error: --container-id requires --sosreport-dir", file=sys.stderr)
        sys.exit(1)
    
    if args.container_id:
        # Analyze single container
        if args.sosreport_dir:
            container_file = os.path.join(args.sosreport_dir, "sos_commands", "crio", "containers", args.container_id)
            if not os.path.isfile(container_file):
                print(f"Error: Container file not found: {container_file}", file=sys.stderr)
                sys.exit(1)
            
            result = analyze_container_numa_alignment(container_file, args.sosreport_dir)
        else:
            print("Error: Live system analysis for single container not implemented", file=sys.stderr)
            sys.exit(1)
        
        if args.output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            # Format single container result for text output
            print(f"Container: {result['container_name']} ({result['container_id']})")
            if result.get('analysis_skipped'):
                print(f"Analysis skipped: {result.get('skip_reason', 'Unknown reason')}")
            else:
                # Print detailed analysis...
                numa_alignment = result.get('numa_alignment', {})
                alignment_status = numa_alignment.get('alignment_status', 'unknown')
                print(f"NUMA Alignment: {alignment_status.upper()}")
                # Add more details as needed
    else:
        # Analyze all containers
        results = analyze_all_containers(args.sosreport_dir)
        
        if args.output_format == 'json':
            print(json.dumps(results, indent=2))
        else:
            print(format_text_output(results))

if __name__ == '__main__':
    main()
