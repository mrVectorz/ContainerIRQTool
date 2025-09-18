# IRQ Affinity Configuration Tool

A high-performance tool for analyzing and configuring IRQ affinity in containerized environments, specifically designed for CPU isolation scenarios in OpenShift/Kubernetes workloads.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
  - [🔍 Analysis Capabilities](#-analysis-capabilities)
  - [⚙️ Configuration Management](#️-configuration-management)
  - [📊 Output Formats](#-output-formats)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Basic Syntax](#basic-syntax)
  - [Command Line Options](#command-line-options)
  - [Usage Examples](#usage-examples)
    - [1. Analyze Live System](#1-analyze-live-system)
    - [2. Analyze SOS Report](#2-analyze-sos-report)
    - [3. Configuration Only (No Violation Analysis)](#3-configuration-only-no-violation-analysis)
    - [4. NUMA Alignment Analysis](#4-numa-alignment-analysis)
    - [5. LLC Alignment Analysis](#5-llc-alignment-analysis)
    - [6. JSON Output for Automation](#6-json-output-for-automation)
    - [7. Web Interface Viewer](#7-web-interface-viewer)
- [Output Interpretation](#output-interpretation)
  - [JSON Output Format](#json-output-format)
  - [IRQ Violation Analysis](#irq-violation-analysis)
  - [NUMA Alignment Analysis](#numa-alignment-analysis)
  - [LLC Alignment Analysis](#llc-alignment-analysis)
  - [Sample LLC Output](#sample-llc-output)
  - [Sample NUMA Output](#sample-numa-output)
  - [Configuration Output](#configuration-output)
- [Advanced Usage](#advanced-usage)
  - [Python IRQ Analyzer (Direct Usage)](#python-irq-analyzer-direct-usage)
  - [Integration with Automation](#integration-with-automation)
- [Performance Characteristics](#performance-characteristics)
  - [Optimizations](#optimizations)
  - [Typical Performance](#typical-performance)
- [Troubleshooting](#troubleshooting)
  - [Common Issues](#common-issues)
  - [Debug Mode](#debug-mode)
- [File Structure](#file-structure)
- [Use Cases](#use-cases)
  - [1. OpenShift/Kubernetes Performance Tuning](#1-openshiftkubernetes-performance-tuning)
  - [2. SOS Report Analysis](#2-sos-report-analysis)
  - [3. System Configuration Validation](#3-system-configuration-validation)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Overview

This tool helps identify and resolve IRQ affinity violations where interrupt requests are being processed on CPUs that should be isolated for containerized workloads.
It provides detailed analysis of IRQ violations, NUMA alignment, LLC cache alignment, and PCI device NUMA alignment.

## Features

### 🔍 **Analysis Capabilities**
- **Container CPU Isolation Detection**: Automatically identifies CPUs isolated for containers with `irq-load-balancing.crio.io=disable` and `cpu-quota.crio.io=disable` annotations
- **IRQ Violation Analysis**: Detects IRQs incorrectly assigned to isolated CPUs
- **NUMA Alignment Analysis**: Validates PCI device NUMA alignment with isolated container CPUs
- **LLC Alignment Analysis**: Validates Last Level Cache alignment for isolated container CPUs
- **Interrupt Rate Analysis**: Calculates interrupts per hour based on system uptime
- **Color-coded Severity**: Visual indication of IRQ priority (Green/Yellow/Red)
- **High-Performance Processing**: Optimized Python analyzer for large-scale IRQ analysis

### ⚙️ **Configuration Management**
- **Kernel IRQ Affinity**: Generates and applies `/proc/irq/default_smp_affinity` masks
- **irqbalance Configuration**: Creates/updates `IRQBALANCE_BANNED_CPUS` settings
- **Live System Updates**: Applies changes to running systems with service restarts
- **SOS Report Analysis**: Analyzes historical data from sosreports

### 📊 **Output Formats**
- **Summary View**: Quick overview with first 10 CPUs (default)
- **Full Analysis**: Detailed analysis of all CPUs with violations (`--full-analysis`)
- **JSON Output**: Machine-readable format for automation
- **Color-coded Terminal**: ANSI color support for visual severity indication
  - **IRQ Analysis**: Green/Yellow/Red based on interrupt rates
  - **NUMA Analysis**: Green (aligned), Red (misaligned), Yellow (errors)

## Requirements

- **Python 3.6+** (for IRQ analyzer)
- **Bash 4.0+** (for main script)
- **Standard Linux utilities**: `grep`, `sed`, `awk`, `jq`
- **Root privileges** (for live system modifications)

## Installation

```bash
# Clone or download the tool
git clone <repository-url>
cd ContainerIRQTool

# Make scripts executable
chmod +x ContainerIRQTool.sh
chmod +x irq_analyzer.py
```

## Usage

### Basic Syntax

```bash
./ContainerIRQTool.sh [OPTIONS]
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `--local DIR` | Run against sosreport directory instead of live host |
| `--check-violations` | Enable IRQ violation analysis (disabled by default) |
| `--check-numa-alignment` | Enable NUMA alignment analysis for isolated containers |
| `--check-llc-alignment` | Enable LLC alignment analysis for isolated containers |
| `--full-analysis` | Show detailed analysis for all CPUs (default: limit to first 10) |
| `--output-format FORMAT` | Output format: 'text' (default) or 'json' |
| `-h, --help` | Show help message |

### Usage Examples

#### 1. Analyze Live System
```bash
# Basic analysis (configuration only)
sudo ./ContainerIRQTool.sh

# Full analysis with IRQ violations
sudo ./ContainerIRQTool.sh --check-violations --full-analysis
```

#### 2. Analyze SOS Report
```bash
# Quick analysis (first 10 CPUs with violations)
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations

# Complete analysis (all CPUs with violations)
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --full-analysis

# Save detailed report to file
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --full-analysis > irq_report.txt
```

#### 3. Configuration Only (No Violation Analysis)
```bash
# Generate IRQ masks without checking violations (faster)
./ContainerIRQTool.sh --local /path/to/sosreport
```

#### 4. NUMA Alignment Analysis
```bash
# Check NUMA alignment for isolated containers
./ContainerIRQTool.sh --local /path/to/sosreport --check-numa-alignment

# Combined IRQ violation and NUMA alignment analysis
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --check-numa-alignment

# Full analysis with NUMA alignment in JSON format
./ContainerIRQTool.sh --local /path/to/sosreport --check-numa-alignment --output-format json
```

#### 5. LLC Alignment Analysis
```bash
# Check LLC alignment for isolated containers
./ContainerIRQTool.sh --local /path/to/sosreport --check-llc-alignment

# Combined analysis: IRQ violations, NUMA and LLC alignment
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --check-numa-alignment --check-llc-alignment

# Full LLC analysis with all containers in JSON format
./ContainerIRQTool.sh --local /path/to/sosreport --check-llc-alignment --full-analysis --output-format json
```

#### 6. JSON Output for Automation
```bash
# Generate JSON output for machine processing
./ContainerIRQTool.sh --local /path/to/sosreport --output-format json

# JSON output with full IRQ violation analysis
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --output-format json

# Pipe JSON to jq for specific data extraction
./ContainerIRQTool.sh --local /path/to/sosreport --output-format json | jq '.container_analysis.isolated_cpus_formatted'

# Extract violation count for monitoring
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --output-format json | jq '.irq_violation_analysis.total_violations'
```

#### 7. Web Interface Viewer
For large datasets with hundreds of CPUs and containers, use the interactive web viewer:

```bash
# Generate JSON output for web viewer
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --check-numa-alignment --check-llc-alignment --output-format json > analysis.json

# Start web server
python3 -m http.server 8000

# Open browser to: http://localhost:8000/container_analyzer_viewer.html
# Upload your analysis.json file
```

The web viewer provides:
- **Summary dashboard** with system overview and recommendations
- **Interactive container analysis** with expandable details and search
- **NUMA topology visualization** with nested diagrams  
- **IRQ violation graphs** with per-container and per-CPU breakdowns
- **Color-coded sections** for immediate issue identification

For detailed viewer documentation, see [Docs/WEB_VIEWER.md](./Docs/WEB_VIEWER.md).

## Output Interpretation

### JSON Output Format

The tool can output structured JSON data for machine processing and automation. The JSON structure includes:

```json
{
  "analysis_type": "IRQ Affinity Configuration Analysis",
  "mode": "sosreport|live",
  "container_analysis": {
    "isolated_cpus_found": true|false,
    "isolated_cpus_formatted": "human-readable CPU ranges",
    "isolated_cpus_raw": "comma-separated CPU list"
  },
  "computed_irq_configuration": {
    "host_cpu_count": 208,
    "allowed_irq_cpus": { "kernel_mask": "...", "irqbalance_mask": "...", "cpus_formatted": "...", "cpus_raw": "..." },
    "banned_irq_cpus": { "kernel_mask": "...", "irqbalance_mask": "...", "cpus_formatted": "...", "cpus_raw": "..." }
  },
  "irq_violation_analysis": {
    "enabled": true|false,
    "violations_possible": true|false,
    "isolated_cpus": [...],
    "violations": { /* detailed violation data when enabled */ },
    "total_violations": 42,
    "total_irqs_scanned": 1500
  },
  "current_system_state": {
    "source": "sosreport|live_system",
    "default_smp_affinity": { "file_found": true|false, "current_mask": "..." },
    "irqbalance_config": { "file_found": true|false, "banned_cpus_set": true|false, "current_mask": "..." }
  },
  "recommendations": {
    "default_smp_affinity": { "action_required": true|false, "action": "UPDATE|CREATE|NONE", "current": "...", "required": "..." },
    "irqbalance_config": { "action_required": true|false, "action": "UPDATE|CREATE|ADD|NONE", "current": "...", "required": "..." }
  },
  "changes_applied": { /* only in live mode - details of actual changes made */ },
  "mode": "analysis_only|live_system",
  "changes_made": true|false
}
```

### IRQ Violation Analysis

The tool provides color-coded analysis of IRQ violations:

#### 🟢 **Green IRQs** - Low Priority
- **0 interrupts** on isolated CPUs
- Safe but should still be moved for optimal isolation

#### 🟡 **Yellow IRQs** - Medium Priority  
- **< 1000 interrupts/hour** on isolated CPUs
- Moderate impact on container performance

#### 🔴 **Red IRQs** - High Priority
- **≥ 1000 interrupts/hour** on isolated CPUs
- Significant impact on container performance - **immediate attention required**

### Sample Output

```
IRQ VIOLATION ANALYSIS (Color-coded by interrupt rate):
  🟢 Green: 0 interrupts
  🟡 Yellow: < 1000 interrupts/hour
  🔴 Red: ≥ 1000 interrupts/hour

CPU 2 (14 violations):
  Containers: workload-container (a1b2c3d4e5f6)
  IRQs with improper affinity:
    IRQ 138: 9920179 interrupts (8778.0/hr)    # 🔴 Critical
    IRQ 0: 65 interrupts (0.1/hr)              # 🟡 Low impact
    IRQ 7: 0 interrupts (0.0/hr)               # 🟢 No impact
```

### NUMA Alignment Analysis

The tool analyzes NUMA alignment between isolated container CPUs and their assigned PCI devices:

#### ✅ **NUMA Aligned** - Optimal Performance
- Container CPUs and PCI devices are on the same NUMA node
- Optimal memory bandwidth and latency
- No performance degradation due to cross-NUMA traffic

#### ❌ **NUMA Misaligned** - Performance Impact
- Container CPUs and PCI devices are on different NUMA nodes
- Increased memory latency due to cross-NUMA memory access
- **Significant performance degradation** - requires immediate attention

#### ⚠️ **Validation Issues**
- PCI devices not found in container network namespace
- Unable to determine NUMA topology
- Missing PCI device NUMA information

#### 🔧 **NUMA Detection Methods**
The tool uses multiple methods to detect NUMA topology in sosreports:

1. **Primary Method**: `/sys/devices/system/node/nodeX/cpulist` files
2. **Fallback Method**: Parse CPU-to-NUMA mapping from `/proc/cpuinfo` (physical id field)
3. **PCI NUMA Detection**: Parse `sos_commands/pci/lspci_-nnvv` output for device NUMA nodes

**Smart Range Formatting**: CPU lists are automatically formatted using intelligent range detection:
- **Consecutive ranges**: `0-15` instead of `0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15`
- **Interleaved patterns**: `0-206:2 (even)` for hyperthreading systems with interleaved cores
- **Block patterns**: `0-51,104-155` for socket-based NUMA allocation
- **Mixed patterns**: `0-15,32-47,64-79` for complex topologies

**Common NUMA Patterns**:
- **Interleaved (Hyperthreading)**: Even cores on Node 0, odd cores on Node 1
- **Block (Socket-based)**: Consecutive core ranges assigned to each socket
- **Mixed**: Complex arrangements based on specific hardware configurations

This ensures NUMA analysis works even when standard sysfs files are missing from sosreports.

### LLC Alignment Analysis

The tool analyzes Last Level Cache (LLC) alignment for isolated container CPUs to identify performance issues on modern CPU chiplet architectures:

#### ✅ **LLC Aligned** - Optimal Performance
- All container CPUs share the same LLC (Last Level Cache)
- Optimal cache coherency and memory bandwidth
- No performance degradation due to cross-LLC communication

#### ❌ **LLC Misaligned** - Performance Impact
- Container CPUs are spread across multiple LLC domains
- Increased cache miss penalties and cross-LLC traffic
- **Significant performance degradation** on chiplet-based CPUs

#### ⚠️ **Analysis Issues**
- LLC topology information not available
- Missing `/sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list` files
- Unable to determine cache hierarchy

#### 🔧 **LLC Detection Methods**
The tool reads LLC topology from system cache information:

1. **Primary Method**: Parse `/sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list` files
2. **Cache Grouping**: Group CPUs that share the same LLC based on shared_cpu_list content
3. **Alignment Check**: Verify all container CPUs belong to the same LLC group

**LLC Topology Examples**:
- **Aligned**: All container CPUs show same shared_cpu_list (e.g., `0-15,32-47`)
- **Misaligned**: Container CPUs show different shared_cpu_list values
- **Chiplet Architecture**: Different CPU clusters with separate LLCs (e.g., AMD EPYC, Intel Xeon with chiplets)

### Sample LLC Output

```
LLC ALIGNMENT ANALYSIS
============================================================

Color-coded LLC alignment status:
  🟢 Green: LLC Aligned (Optimal Performance)
  🟡 Yellow: Analysis Errors
  🔴 Red: LLC Misaligned (Performance Impact)

SUMMARY:
  Total containers: 8
  Isolated containers: 3
  Containers analyzed: 3
  LLC aligned: 2
  LLC misaligned: 1
  Containers with errors: 0

LLC TOPOLOGY:
  LLC Node 0: CPUs 0-51,104-155
  LLC Node 1: CPUs 52-103,156-207

DETAILED ANALYSIS:

Container: dpdk-workload (a1b2c3d4e5f6)
  CPUs: 2-5,58-61
  PCI Devices: 0000:89:00.5, 0000:89:01.4
  ✓ LLC Alignment: ALIGNED                      # 🟢 Green text
  Container LLC nodes: 0

Container: mixed-workload (b2c3d4e5f7a8)
  CPUs: 30-33,86-89
  ✗ LLC Alignment: MISALIGNED                   # 🔴 Red text
  Misaligned CPUs: 86-89                        # 🔴 Red text
  Container LLC nodes: 0, 1

Container: numa-aligned (c3d4e5f8a9b1)
  CPUs: 52-55,100-103
  ✓ LLC Alignment: ALIGNED                      # 🟢 Green text
  Container LLC nodes: 1
```

### Sample NUMA Output

```
NUMA ALIGNMENT ANALYSIS
============================================================

Color-coded NUMA alignment status:
  🟢 Green: NUMA Aligned (Optimal Performance)
  🟡 Yellow: Analysis Errors
  🔴 Red: NUMA Misaligned (Performance Impact)

SUMMARY:
  Total containers: 5
  Isolated containers: 2
  Containers with PCI devices: 2
  NUMA aligned: 1
  NUMA misaligned: 1
  Containers with errors: 0

NUMA TOPOLOGY:
  Node 0: CPUs 0-206:2 (even)                 # Interleaved pattern (hyperthreading)
  Node 1: CPUs 1-207:2 (odd)                  # Each CPU belongs to exactly one NUMA node

DETAILED ANALYSIS:

Container: dpdk-app (a1b2c3d4e5f6)
  CPUs: 2-5,58-61
  PCI Devices: 0000:89:00.5, 0000:89:01.4
  ✓ NUMA Alignment: ALIGNED                    # 🟢 Green text
  Container NUMA nodes: 0
  ✓ PCI 0000:89:00.5: NUMA node 0             # 🟢 Green text
  ✓ PCI 0000:89:01.4: NUMA node 0             # 🟢 Green text
  Network namespace validation:
    ✓ 0000:89:00.5: Found in netns            # 🟢 Green text
    ✓ 0000:89:01.4: Found in netns            # 🟢 Green text

Container: sriov-workload (b2c3d4e5f7a8)
  CPUs: 30-33,86-89
  PCI Devices: 0000:17:00.2
  ✗ NUMA Alignment: MISALIGNED                # 🔴 Red text
  Container NUMA nodes: 1
  ✗ PCI 0000:17:00.2: NUMA node 0            # 🔴 Red text
  Network namespace validation:
    ✓ 0000:17:00.2: Found in netns            # 🟢 Green text
```

### Configuration Output

```
COMPUTED IRQ CONFIGURATION:
  Allowed IRQ CPUs:
    Kernel mask (/proc/irq/default_smp_affinity): c000,00000000,3c000000
    irqbalance mask (IRQBALANCE_BANNED_CPUS): c000,00000000,3c000000
    CPUs: 0-1, 50-53, 102-105

RECOMMENDATIONS:
  ✓ CORRECT: /proc/irq/default_smp_affinity is already properly configured
  ✗ UPDATE REQUIRED: IRQBALANCE_BANNED_CPUS
    Current:  [NOT SET]
    Required: c000,00000000,3c000000
```

## Advanced Usage

### Python IRQ Analyzer (Direct Usage)

The tool includes a high-performance Python analyzer that can be used independently:

```bash
# Analyze sosreport with JSON output
python3 irq_analyzer.py --sosreport-dir /path/to/sosreport --output-format json

# Analyze specific CPUs
python3 irq_analyzer.py --isolated-cpus "2,4,6-8" --output-format summary

# Get help
python3 irq_analyzer.py --help
```

### Integration with Automation

```bash
# Check if violations exist (exit code based)
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations > /dev/null
if [ $? -eq 0 ]; then
    echo "Analysis completed successfully"
fi

# Parse violations count programmatically (text output)
violations=$(./ContainerIRQTool.sh --local /path/to/sosreport --check-violations | grep "Total violations found:" | awk '{print $4}')
echo "Found $violations total violations"

# Using JSON output for easier parsing
violations=$(./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --output-format json | jq -r '.irq_violation_analysis.total_violations // 0')
echo "Found $violations total violations"

# Check if any action is required
action_required=$(./ContainerIRQTool.sh --local /path/to/sosreport --output-format json | jq -r '.recommendations | to_entries[] | select(.value.action_required == true) | .key')
if [ -n "$action_required" ]; then
    echo "Action required for: $action_required"
else
    echo "No configuration changes needed"
fi

# Extract isolated CPUs for monitoring dashboards
isolated_cpus=$(./ContainerIRQTool.sh --local /path/to/sosreport --output-format json | jq -r '.container_analysis.isolated_cpus_formatted')
echo "Isolated CPUs: $isolated_cpus"
```

## Performance Characteristics

### Optimizations
- **Pre-computed IRQ mappings**: Single scan of `/proc/irq/` directory
- **Efficient container analysis**: JSON parsing with error handling
- **Smart output limiting**: Default 10 CPU limit for readability
- **Parallel processing**: Optimized for multi-core systems

### Typical Performance
- **Small systems** (< 50 CPUs): < 5 seconds
- **Large systems** (> 200 CPUs): < 30 seconds  
- **SOS report analysis**: Comparable to live system performance

## Troubleshooting

### Common Issues

#### 1. Permission Denied
```bash
# For live system analysis
sudo ./ContainerIRQTool.sh --check-violations
```

#### 2. Python Analyzer Not Found
```bash
# Ensure Python script is executable and in same directory
chmod +x irq_analyzer.py
ls -la irq_analyzer.py
```

#### 3. No Color Output
```bash
# Ensure terminal supports ANSI colors
echo -e "\033[92mGreen\033[0m \033[93mYellow\033[0m \033[91mRed\033[0m"
```

#### 4. Large Output Truncated
```bash
# Use --full-analysis flag for complete output
./ContainerIRQTool.sh --check-violations --local /path/to/sosreport --full-analysis
```

### Debug Mode
```bash
# Enable verbose output
bash -x ./ContainerIRQTool.sh --check-violations --local /path/to/sosreport
```

## File Structure

```
ContainerIRQTool/
├── ContainerIRQTool.sh    # Main bash script
├── irq_analyzer.py        # High-performance IRQ violation analyzer
├── numa_analyzer.py       # NUMA alignment analyzer for containers
├── llc_analyzer.py        # LLC alignment analyzer for containers
└── README.md              # This documentation
```

## Use Cases

### 1. **OpenShift/Kubernetes Performance Tuning**
- Identify IRQ interference with isolated container workloads
- Validate NUMA alignment for SR-IOV and DPDK workloads
- Validate LLC alignment for chiplet-based CPU architectures
- Optimize CPU isolation for latency-sensitive applications
- Validate IRQ configuration in production environments

### 2. **SOS Report Analysis**
- Post-incident analysis of IRQ-related performance issues
- Historical trending of IRQ violations
- NUMA misalignment analysis for performance degradation
- LLC misalignment analysis for chiplet architecture issues
- Capacity planning for CPU isolation requirements

### 3. **System Configuration Validation**
- Verify IRQ affinity settings after system changes
- Validate PCI device NUMA placement for containers
- Validate LLC alignment for optimal cache performance
- Automate IRQ configuration in deployment pipelines
- Compliance checking for performance requirements

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with appropriate tests
4. Submit a pull request

## License

GPL-3.0 license

## Support

For issues and questions:
- Create an issue in the repository
- Include system information and command output
- Provide sosreport data if possible (sanitized)
