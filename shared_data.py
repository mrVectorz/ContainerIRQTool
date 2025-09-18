#!/usr/bin/env python3
"""
Shared Data Module for Container IRQ Tool

This module provides efficient caching and shared access to container data
and system topology information across all analyzers (IRQ, NUMA, LLC).
"""

import os
import sys
import json
import glob
import re
import subprocess
from typing import Dict, List, Optional, Tuple, Any


# Global cache to avoid redundant data fetching
_container_cache = {}
_numa_topology_cache = {}
_llc_topology_cache = {}
_cache_initialized = False


def parse_cpu_range(cpu_range_str: str) -> List[int]:
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


def format_cpu_list_range(cpu_list: List[int]) -> str:
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


def extract_pci_devices_from_container(container_data: Dict) -> List[str]:
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


def get_container_network_namespace(container_data: Dict) -> Optional[str]:
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


def parse_container_data(container_file: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Parse container data from file and extract common information."""
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
    
    # Get container CPU set
    cpu_set = container_data.get('status', {}).get('resources', {}).get('linux', {}).get('cpusetCpus')
    container_cpus = parse_cpu_range(cpu_set) if cpu_set else []
    
    # Extract PCI devices
    pci_devices = extract_pci_devices_from_container(container_data)
    
    # Get network namespace
    netns_id = get_container_network_namespace(container_data)
    
    return {
        'container_name': container_name,
        'container_id': container_id[:12] if len(container_id) > 12 else container_id,
        'full_container_id': container_id,
        'is_isolated': is_isolated,
        'container_cpus': container_cpus,
        'container_cpus_formatted': cpu_set or '',
        'pci_devices': pci_devices,
        'network_namespace': netns_id,
        'raw_data': container_data,
        'analysis_skipped': False
    }


def get_numa_topology_from_cpuinfo(base_dir: str) -> Dict[int, Dict]:
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


def load_all_container_data(base_dir: Optional[str] = None) -> Dict[str, Dict]:
    """Load and cache all container data from sosreport or live system."""
    global _container_cache, _cache_initialized
    
    if _cache_initialized and base_dir in _container_cache:
        return _container_cache[base_dir]
    
    containers = {}
    
    if base_dir:
        # Sosreport analysis
        containers_dir = os.path.join(base_dir, "sos_commands", "crio", "containers")
        if not os.path.isdir(containers_dir):
            print(f"Warning: Container directory not found: {containers_dir}", file=sys.stderr)
            return containers
        
        container_files = glob.glob(os.path.join(containers_dir, "*"))
        
        for container_file in container_files:
            if not os.path.isfile(container_file):
                continue
            
            container_info = parse_container_data(container_file, base_dir)
            if not container_info.get('error'):
                containers[container_info['full_container_id']] = container_info
    else:
        # Live system analysis - would need crictl integration
        # This is more complex and would require similar optimization
        pass
    
    # Cache the results
    _container_cache[base_dir] = containers
    _cache_initialized = True
    
    return containers


def get_numa_topology(base_dir: Optional[str] = None) -> Dict[int, Dict]:
    """Get NUMA topology information from the system (cached)."""
    global _numa_topology_cache
    
    cache_key = base_dir or 'live'
    if cache_key in _numa_topology_cache:
        return _numa_topology_cache[cache_key]
    
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
    
    # Cache the results
    _numa_topology_cache[cache_key] = numa_info
    return numa_info


def get_llc_topology(base_dir: Optional[str] = None) -> Tuple[Dict[int, Dict], Dict[int, int]]:
    """Get LLC topology information from the system (cached)."""
    global _llc_topology_cache
    
    cache_key = base_dir or 'live'
    if cache_key in _llc_topology_cache:
        return _llc_topology_cache[cache_key]
    
    llc_info = {}
    cpu_to_llc = {}
    
    if base_dir:
        # For sosreport analysis
        cpu_base = os.path.join(base_dir, "sys", "devices", "system", "cpu")
    else:
        # For live system
        cpu_base = "/sys/devices/system/cpu"
    
    if not os.path.isdir(cpu_base):
        _llc_topology_cache[cache_key] = (llc_info, cpu_to_llc)
        return llc_info, cpu_to_llc
    
    try:
        # Find all CPU directories
        cpu_dirs = [d for d in os.listdir(cpu_base) 
                   if d.startswith('cpu') and d[3:].isdigit()]
        
        llc_groups = {}  # Map LLC signature to list of CPUs
        
        for cpu_dir in cpu_dirs:
            cpu_num = int(cpu_dir[3:])
            cpu_path = os.path.join(cpu_base, cpu_dir)
            
            # Get LLC shared CPU list (index3 is typically L3/LLC)
            shared_cpu_file = os.path.join(cpu_path, "cache", "index3", "shared_cpu_list")
            if os.path.isfile(shared_cpu_file):
                try:
                    with open(shared_cpu_file, 'r') as f:
                        shared_cpu_list = f.read().strip()
                    
                    # Parse the shared CPU list
                    shared_cpus = parse_cpu_range(shared_cpu_list)
                    
                    # Use the shared_cpu_list string as a signature for this LLC group
                    if shared_cpu_list not in llc_groups:
                        llc_groups[shared_cpu_list] = {
                            'cpus': set(shared_cpus),
                            'cpu_list_formatted': format_cpu_list_range(shared_cpus)
                        }
                    
                    # Map this CPU to its LLC group
                    cpu_to_llc[cpu_num] = shared_cpu_list
                    
                except (OSError, ValueError):
                    continue
    except OSError:
        pass
    
    # Convert LLC groups to numbered nodes for easier reference
    llc_node_num = 0
    for llc_signature, llc_data in llc_groups.items():
        llc_info[llc_node_num] = {
            'cpus': sorted(list(llc_data['cpus'])),
            'cpulist': llc_data['cpu_list_formatted'],
            'signature': llc_signature
        }
        # Update cpu_to_llc mapping to use node numbers
        for cpu in cpu_to_llc:
            if cpu_to_llc[cpu] == llc_signature:
                cpu_to_llc[cpu] = llc_node_num
        llc_node_num += 1
    
    # Cache the results
    result = (llc_info, cpu_to_llc)
    _llc_topology_cache[cache_key] = result
    return result


def get_isolated_containers(base_dir: Optional[str] = None) -> Dict[str, Dict]:
    """Get all isolated containers (cached)."""
    all_containers = load_all_container_data(base_dir)
    return {cid: cdata for cid, cdata in all_containers.items() if cdata.get('is_isolated')}


def get_isolated_cpus(base_dir: Optional[str] = None) -> List[int]:
    """Get list of all isolated CPUs from containers (cached)."""
    isolated_containers = get_isolated_containers(base_dir)
    isolated_cpus = set()
    
    for container_data in isolated_containers.values():
        isolated_cpus.update(container_data.get('container_cpus', []))
    
    return sorted(list(isolated_cpus))


def clear_cache():
    """Clear all cached data (useful for testing or when data changes)."""
    global _container_cache, _numa_topology_cache, _llc_topology_cache, _cache_initialized
    _container_cache.clear()
    _numa_topology_cache.clear()
    _llc_topology_cache.clear()
    _cache_initialized = False
