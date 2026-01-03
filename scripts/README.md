# Homestak Scripts

Utility scripts for the homestak IaC project.

## generate-test-summary.sh

Generates a markdown summary of an end-to-end nested PVE test run.

### Usage

```bash
# Auto-detect inner PVE IP from VM 99913
./generate-test-summary.sh

# Specify test name and inner PVE IP
./generate-test-summary.sh nested-pve-e2e 10.0.12.195
```

### Output

Creates a markdown file in `../test-runs/` with:
- Architecture diagram
- VM configurations (IDs, IPs, MACs, resources)
- Authentication details (SSH keys, API tokens, password hashes)
- Access commands
- Git commit references
- SSH verification results
- Cleanup commands

### Requirements

- `jq` for JSON parsing
- SSH access to outer and inner PVE hosts
- VMs must be running with qemu-guest-agent
