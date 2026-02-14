# Homestak Scripts

Utility scripts for the homestak IaC project.

## generate-test-summary.sh

Generates a markdown summary of an integration test run.

### Usage

```bash
# Auto-detect PVE node IP from VM 99011
./generate-test-summary.sh

# Specify test name and PVE node IP
./generate-test-summary.sh nested-pve-integration 198.51.100.195
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
- SSH access to PVE hosts
- VMs must be running with qemu-guest-agent
