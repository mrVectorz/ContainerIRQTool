# ContainerIRQTool

Analyzes and configures IRQ affinity for CPU-isolated containers in OpenShift/Kubernetes. Detects when hardware interrupts land on CPUs that should be reserved for latency-sensitive workloads (DPDK, SR-IOV, etc.) and computes the correct affinity masks.

Works against live nodes or sosreport directories. Intentionally uses only standard system tools — Python 3 stdlib, bash, grep, sed, awk, jq — so it runs on any RHEL/CoreOS node without installing packages.

## What it does

1. Finds containers with CRI-O isolation annotations (`irq-load-balancing.crio.io=disable` + `cpu-quota.crio.io=disable`)
2. Collects their pinned CPU sets and computes which CPUs should be banned from handling IRQs
3. Generates the correct `default_smp_affinity` and `IRQBALANCE_BANNED_CPUS` masks
4. Optionally analyzes:
   - **IRQ violations** — which IRQs are still firing on isolated CPUs, how often, and from what device
   - **NUMA alignment** — whether PCI devices (SR-IOV VFs) are on the same NUMA node as the container's CPUs
   - **LLC alignment** — whether container CPUs share the same last-level cache (relevant on chiplet architectures)
5. On live nodes, applies the computed masks and restarts irqbalance. On sosreports, reports what needs to change.

## Requirements

- Python 3.6+, Bash 4.0+, grep, sed, awk, jq
- Root privileges for live system modifications
- No pip packages or extra RPMs needed

## Usage

```
./ContainerIRQTool.sh [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--local DIR` | Analyze a sosreport directory instead of the live host |
| `--check-violations` | Enable IRQ violation analysis |
| `--check-numa-alignment` | Enable NUMA alignment analysis |
| `--check-llc-alignment` | Enable LLC alignment analysis |
| `--full-analysis` | Show all CPUs/containers (default caps at top 10) |
| `--output-format FORMAT` | `text` (default), `json`, or `llm-summary` |

### Examples

```bash
# Sosreport analysis with all checks
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --check-numa-alignment --full-analysis

# Live system — compute and apply masks (requires root)
sudo ./ContainerIRQTool.sh

# JSON for automation
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --output-format json | jq '.recommendations'

# Compact output for LLM consumption (~97% smaller than full JSON)
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --check-numa-alignment --output-format llm-summary
```

## Output formats

**text** — Human-readable with ANSI color-coded severity:
- Green: zero interrupts on isolated CPU
- Yellow: <1000 interrupts/hour
- Red: >=1000 interrupts/hour (needs attention)

**json** — Full structured data for the web viewer or automation. Includes per-CPU per-IRQ violation details, per-container NUMA/LLC analysis, current system state, and recommendations.

**llm-summary** — Compressed JSON (~15 KB vs ~500 KB) for LLM ingestion. Aggregates IRQ violations by device, groups clean IRQs into summary lines, and compacts topology data while preserving all actionable information.

## Web viewer

For large datasets, the included `container_analyzer_viewer.html` provides an interactive browser-based viewer. Generate JSON output, then open the HTML file and upload it:

```bash
./ContainerIRQTool.sh --local /path/to/sosreport --check-violations --check-numa-alignment --output-format json > analysis.json
```

See [Docs/WEB_VIEWER.md](./Docs/WEB_VIEWER.md) for details.

## How it works

### IRQ mask computation

The tool reads each container's `cpusetCpus` from CRI-O inspect data, unions them into a "banned" set, and builds two hex masks:
- **kernel mask** for `/proc/irq/default_smp_affinity` (the inverse — allowed CPUs)
- **irqbalance mask** for `IRQBALANCE_BANNED_CPUS` (accounts for an irqbalance parsing quirk)

### IRQ violation analysis

Reads `/proc/irq/*/smp_affinity_list` and `/proc/interrupts` to find IRQs whose affinity includes isolated CPUs. Calculates interrupt rates from counts and uptime, then classifies by severity. Identifies the source device for each IRQ (NIC, NVMe, storage controller, etc.).

### NUMA alignment

Checks that PCI devices assigned to a container (via `PCIDEVICE_OPENSHIFT*_INFO` env vars) are on the same NUMA node as the container's CPUs. Uses sysfs topology first, falls back to parsing `/proc/cpuinfo` and `lspci -nnvv` for sosreports with incomplete sysfs data. Also validates PCI devices appear in the container's network namespace.

### LLC alignment

Reads `/sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list` to map CPUs to LLC groups, then checks whether a container's CPUs all share the same LLC. Cross-LLC containers take a performance hit on chiplet architectures (AMD EPYC, newer Xeon).

## File structure

```
ContainerIRQTool.sh              Main orchestrator (bash)
irq_analyzer.py                  IRQ violation analysis
numa_analyzer.py                 NUMA alignment analysis
llc_analyzer.py                  LLC alignment analysis
shared_data.py                   Shared data caching and CPU range formatting
llm_summarize.py                 LLM-friendly JSON post-processor
container_analyzer_viewer.html   Interactive web viewer (standalone HTML)
```

## Troubleshooting

**Permission denied on live system** — Run with `sudo`.

**Python analyzer not found** — Ensure `irq_analyzer.py` is in the same directory as `ContainerIRQTool.sh` and is executable.

**Output too long** — Default mode caps at top 10. Use `--full-analysis` only when you need everything. Use `--output-format json` and pipe through `jq` to extract specific sections.

**Debug** — Run with `bash -x ./ContainerIRQTool.sh ...` for trace output.

## License

GPL-3.0
