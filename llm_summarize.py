#!/usr/bin/env python3
"""Post-processor: reads ContainerIRQTool JSON from stdin, writes LLM-friendly summary to stdout.

Usage: ./ContainerIRQTool.sh ... --output-format json | python3 llm_summarize.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared_data import format_cpu_list_range


def summarize_system(data):
    info = data.get('system_info', {})
    result = {
        'hostname': info.get('hostname', ''),
        'uptime_seconds': info.get('uptime_seconds'),
        'uptime_hours': info.get('uptime_hours'),
    }
    analyzed = info.get('analyzed_file', '')
    if analyzed:
        result['source'] = os.path.basename(analyzed)
    return result


def summarize_isolation(data):
    ca = data.get('container_analysis', {})
    cfg = data.get('computed_irq_configuration', {})
    return {
        'isolated_cpus': ca.get('isolated_cpus_formatted', ''),
        'total_host_cpus': cfg.get('host_cpu_count'),
    }


def summarize_irq_config(data):
    cfg = data.get('computed_irq_configuration', {})
    allowed = cfg.get('allowed_irq_cpus', {})
    banned = cfg.get('banned_irq_cpus', {})
    return {
        'allowed_cpus': allowed.get('cpus_formatted', ''),
        'banned_cpus': banned.get('cpus_formatted', ''),
        'kernel_mask': allowed.get('kernel_mask', ''),
        'irqbalance_mask': banned.get('irqbalance_mask', ''),
    }


def summarize_irq_violations(data):
    irq = data.get('irq_violation_analysis', {})
    if not irq.get('enabled'):
        return {'enabled': False}

    violations = irq.get('violations', {})
    if not violations:
        return {
            'enabled': True,
            'total_violations': 0,
            'total_irqs_scanned': irq.get('total_irqs_scanned', 0),
        }

    red = yellow = green = 0
    green_irqs_by_cpu = {}
    device_agg = {}

    for cpu, cpu_data in violations.items():
        cpu_greens = set()
        for det in cpu_data.get('violation_details', []):
            severity = det.get('color_name', '')
            if severity == 'green':
                green += 1
                cpu_greens.add(det['irq'])
            elif severity == 'yellow':
                yellow += 1
            elif severity == 'red':
                red += 1

            if det.get('interrupt_count', 0) == 0:
                continue

            dev = det.get('device_info', 'unknown')
            if dev not in device_agg:
                device_agg[dev] = {
                    'total_count': 0,
                    'max_rate': 0,
                    'severity': 'yellow',
                    'affected_cpus': set(),
                }
            entry = device_agg[dev]
            entry['total_count'] += det['interrupt_count']
            entry['max_rate'] = max(entry['max_rate'], det.get('interrupts_per_hour', 0))
            if severity == 'red':
                entry['severity'] = 'red'
            entry['affected_cpus'].add(int(cpu))

        if cpu_greens:
            green_irqs_by_cpu[cpu] = frozenset(cpu_greens)

    # Group identical green IRQ sets across CPUs
    clean_irqs = []
    seen_sets = {}
    for cpu, irq_set in green_irqs_by_cpu.items():
        if irq_set not in seen_sets:
            seen_sets[irq_set] = []
        seen_sets[irq_set].append(int(cpu))

    for irq_set, cpus in seen_sets.items():
        irq_list = format_cpu_list_range(sorted(irq_set))
        cpu_count = len(cpus)
        total_isolated = len(violations)
        if cpu_count == total_isolated:
            note = f"zero interrupts across all {cpu_count} isolated CPUs"
        else:
            note = f"zero interrupts on {cpu_count} of {total_isolated} isolated CPUs"
        clean_irqs.append({'irqs': irq_list, 'note': note})

    # Finalize device aggregation
    by_device = {}
    for dev, entry in sorted(device_agg.items(), key=lambda x: x[1]['max_rate'], reverse=True):
        affected = sorted(entry['affected_cpus'])
        by_device[dev] = {
            'total_count': entry['total_count'],
            'max_rate': round(entry['max_rate'], 1),
            'severity': entry['severity'],
            'affected_cpus': format_cpu_list_range(affected) if len(affected) > 5 else affected,
        }

    return {
        'enabled': True,
        'total_violations': irq.get('total_violations', 0),
        'total_irqs_scanned': irq.get('total_irqs_scanned', 0),
        'cpus_with_violations': len(violations),
        'severity_counts': {'red': red, 'yellow': yellow, 'green': green},
        'clean_irqs': clean_irqs,
        'by_device': by_device,
    }


def _summarize_topology_section(section_data, section_key):
    """Shared logic for NUMA and LLC topology sections."""
    if not section_data.get('enabled'):
        return {'enabled': False}
    if not section_data.get('analysis_available', True):
        return {'enabled': True, 'analysis_available': False}

    topo_key = f'{section_key}_topology'
    raw_topo = section_data.get(topo_key, {})
    topology = {}
    for node, info in raw_topo.items():
        if isinstance(info, dict):
            topology[node] = info.get('cpus_formatted', format_cpu_list_range(info.get('cpus', [])))
        else:
            topology[node] = str(info)

    compact_containers = []
    for c in section_data.get('containers', []):
        cc = {
            'name': c.get('container_name', ''),
            'cpus': c.get('container_cpus_formatted', ''),
            'pci': c.get('pci_devices', []),
        }

        alignment_key = f'{section_key}_alignment'
        alignment = c.get(alignment_key, {})
        if alignment:
            nodes_key = f'container_{section_key}_nodes' if section_key == 'numa' else 'container_llc_groups'
            cc[f'{section_key}_nodes'] = alignment.get(nodes_key, alignment.get('container_numa_nodes', []))
            pci_info = alignment.get('pci_numa_info', alignment.get('pci_llc_info', {}))
            if pci_info:
                cc[f'pci_{section_key}'] = {
                    addr: {'node': info.get('numa_node', info.get('llc_group')), 'aligned': info.get('aligned', False)}
                    for addr, info in pci_info.items()
                }

        netns = c.get('netns_validation', {})
        if netns:
            cc['netns'] = {addr: info.get('found_in_netns', False) for addr, info in netns.items()}

        if c.get('analysis_skipped'):
            cc['skipped'] = True
        if c.get('skip_reason'):
            cc['skip_reason'] = c['skip_reason']

        compact_containers.append(cc)

    result = {
        'enabled': True,
        'summary': section_data.get('summary', {}),
        'topology': topology,
    }
    if compact_containers:
        result['containers'] = compact_containers
    return result


def summarize_numa(data):
    return _summarize_topology_section(data.get('numa_alignment_analysis', {}), 'numa')


def summarize_llc(data):
    return _summarize_topology_section(data.get('llc_alignment_analysis', {}), 'llc')


def summarize(data):
    return {
        'system': summarize_system(data),
        'isolation': summarize_isolation(data),
        'irq_config': summarize_irq_config(data),
        'irq_violations': summarize_irq_violations(data),
        'numa': summarize_numa(data),
        'llc': summarize_llc(data),
        'current_state': data.get('current_system_state', {}),
        'recommendations': data.get('recommendations', {}),
    }


def main():
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    result = summarize(raw)
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
