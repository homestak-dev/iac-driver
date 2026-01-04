# iac-driver

Infrastructure orchestration engine for Proxmox VE.

## Overview

This repo provides scenario-based workflows that coordinate the [homestak-dev](https://github.com/homestak-dev) tool repositories:

| Repo | Purpose |
|------|---------|
| [bootstrap](https://github.com/homestak-dev/bootstrap) | Entry point - curl\|bash setup |
| [site-config](https://github.com/homestak-dev/site-config) | Site-specific secrets and configuration |
| [ansible](https://github.com/homestak-dev/ansible) | Proxmox host configuration, PVE installation |
| [tofu](https://github.com/homestak-dev/tofu) | VM provisioning with OpenTofu |
| [packer](https://github.com/homestak-dev/packer) | Custom Debian cloud images |

## Quick Start

```bash
# Clone iac-driver and site-config
git clone https://github.com/homestak-dev/iac-driver.git
git clone https://github.com/homestak-dev/site-config.git

# Setup secrets
cd site-config
make setup && make decrypt

# Clone sibling tool repos
cd ../iac-driver
./scripts/setup-tools.sh

# List available scenarios
./run.sh --list-scenarios

# Run a scenario
./run.sh --scenario pve-configure --local
```

## CLI Usage

```bash
./run.sh --scenario <name> [--host <host>] [options]

Options:
  --scenario, -S    Scenario to run (required)
  --host, -H        Target PVE host (default: pve)
  --skip, -s        Skip phase(s) (can be repeated)
  --list-scenarios  List available scenarios
  --list-phases     List phases for selected scenario
  --verbose, -v     Enable debug logging
  --local           Run locally (for pve-configure)
  --remote <IP>     Remote host IP (for pve-configure)
  --vm-ip <IP>      Target VM IP (for bootstrap-install)
```

**Available scenarios:**
- `pve-configure` - Configure PVE host (pve-setup + user)
- `bootstrap-install` - Test bootstrap on a VM
- `simple-vm-constructor` - Deploy and verify SSH (~30s)
- `simple-vm-destructor` - Destroy test VM (~3s)
- `simple-vm-roundtrip` - Deploy, verify SSH, destroy (~33s)
- `nested-pve-constructor` - Provision inner PVE for E2E (~10 min)
- `nested-pve-destructor` - Cleanup inner PVE (~30s)
- `nested-pve-roundtrip` - Full nested PVE E2E test (~12 min)

## Secrets Management

Credentials are managed in the [site-config](https://github.com/homestak-dev/site-config) repository using SOPS + age.

```bash
cd ../site-config
make setup    # Configure git hooks, check dependencies
make decrypt  # Decrypt secrets (requires age key)
```

See [site-config README](https://github.com/homestak-dev/site-config#readme) for setup instructions.

## Prerequisites

- [site-config](https://github.com/homestak-dev/site-config) set up and decrypted
- Ansible 2.0+, OpenTofu, Packer
- SSH key at `~/.ssh/id_rsa`
- Proxmox VE host with API access

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed architecture and scenario documentation.

## License

Apache 2.0 - see [LICENSE](LICENSE)
