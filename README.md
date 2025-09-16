# IRQ Affinity Configuration Tool

A high-performance tool for analyzing and configuring IRQ affinity in containerized environments, specifically designed for CPU isolation scenarios in OpenShift/Kubernetes workloads.

## Overview

This tool helps identify and resolve IRQ affinity violations where interrupt requests are being processed on CPUs that should be isolated for containerized workloads. It provides detailed analysis of IRQ violations with color-coded severity indicators based on interrupt rates.

## Features

### 🔍 **Analysis Capabilities**
- **Container CPU Isolation Detection**: Automatically identifies CPUs isolated for containers with `irq-load-balancing.crio.io=disable` and `cpu-quota.crio.io=disable` annotations
- **IRQ Violation Analysis**: Detects IRQs incorrectly assigned to isolated CPUs
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

## Requirements

- **Python 3.6+** (for IRQ analyzer)
- **Bash 4.0+** (for main script)
- **Standard Linux utilities**: `grep`, `sed`, `awk`, `jq`
- **Root privileges** (for live system modifications)

## Installation

```bash
# Clone or download the tool
git clone <repository-url>
cd irq_reset_workaround

# Make scripts executable
chmod +x local_set_irq_exclude_mask.sh
chmod +x irq_analyzer.py
```

## Usage

### Basic Syntax

```bash
./local_set_irq_exclude_mask.sh [OPTIONS]
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `--local DIR` | Run against sosreport directory instead of live host |
| `--check-violations` | Enable IRQ violation analysis (disabled by default) |
| `--full-analysis` | Show detailed analysis for all CPUs (default: limit to first 10) |
| `-h, --help` | Show help message |

### Usage Examples

#### 1. Analyze Live System
```bash
# Basic analysis (configuration only)
sudo ./local_set_irq_exclude_mask.sh

# Full analysis with IRQ violations
sudo ./local_set_irq_exclude_mask.sh --check-violations --full-analysis
```

#### 2. Analyze SOS Report
```bash
# Quick analysis (first 10 CPUs with violations)
./local_set_irq_exclude_mask.sh --local /path/to/sosreport --check-violations

# Complete analysis (all CPUs with violations)
./local_set_irq_exclude_mask.sh --local /path/to/sosreport --check-violations --full-analysis

# Save detailed report to file
./local_set_irq_exclude_mask.sh --local /path/to/sosreport --check-violations --full-analysis > irq_report.txt
```

#### 3. Configuration Only (No Violation Analysis)
```bash
# Generate IRQ masks without checking violations (faster)
./local_set_irq_exclude_mask.sh --local /path/to/sosreport
```

## Output Interpretation

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
./local_set_irq_exclude_mask.sh --local /path/to/sosreport --check-violations > /dev/null
if [ $? -eq 0 ]; then
    echo "Analysis completed successfully"
fi

# Parse violations count programmatically
violations=$(./local_set_irq_exclude_mask.sh --local /path/to/sosreport --check-violations | grep "Total violations found:" | awk '{print $4}')
echo "Found $violations total violations"
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
sudo ./local_set_irq_exclude_mask.sh --check-violations
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
./local_set_irq_exclude_mask.sh --check-violations --local /path/to/sosreport --full-analysis
```

### Debug Mode
```bash
# Enable verbose output
bash -x ./local_set_irq_exclude_mask.sh --check-violations --local /path/to/sosreport
```

## File Structure

```
irq_reset_workaround/
├── local_set_irq_exclude_mask.sh    # Main bash script
├── irq_analyzer.py                  # High-performance Python analyzer
└── README.md                        # This documentation
```

## Use Cases

### 1. **OpenShift/Kubernetes Performance Tuning**
- Identify IRQ interference with isolated container workloads
- Optimize CPU isolation for latency-sensitive applications
- Validate IRQ configuration in production environments

### 2. **SOS Report Analysis**
- Post-incident analysis of IRQ-related performance issues
- Historical trending of IRQ violations
- Capacity planning for CPU isolation requirements

### 3. **System Configuration Validation**
- Verify IRQ affinity settings after system changes
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
