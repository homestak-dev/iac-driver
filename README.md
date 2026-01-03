# iac-driver

E2E test orchestration for Proxmox VE infrastructure-as-code.

## Overview

This repo coordinates the [homestak-dev](https://github.com/homestak-dev) tool repositories for end-to-end testing of Proxmox VE infrastructure:

| Repo | Purpose |
|------|---------|
| [ansible](https://github.com/homestak-dev/ansible) | Proxmox host configuration, PVE installation |
| [tofu](https://github.com/homestak-dev/tofu) | VM provisioning with OpenTofu |
| [packer](https://github.com/homestak-dev/packer) | Custom Debian cloud images |

## Quick Start

```bash
# Clone and setup
git clone https://github.com/homestak-dev/iac-driver.git
cd iac-driver
make setup      # Configure git hooks
make decrypt    # Decrypt secrets (requires age key)

# Clone sibling repos
./scripts/setup-tools.sh

# List available scenarios
./run.sh --list-scenarios

# Run a scenario
./run.sh --scenario simple-vm-roundtrip --host pve
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
```

**Available scenarios:**
- `simple-vm-constructor` - Deploy and verify SSH (~30s)
- `simple-vm-destructor` - Destroy only (~3s)
- `simple-vm-roundtrip` - Deploy, verify SSH, destroy (~33s)
- `nested-pve-constructor` - Provision inner PVE (~10 min)
- `nested-pve-destructor` - Cleanup inner PVE only (~30s)
- `nested-pve-roundtrip` - Full nested PVE E2E test (~12 min)

## Secrets Management

Credentials are encrypted with [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age).

```bash
make setup    # Configure git hooks, check dependencies
make decrypt  # Decrypt secrets
make encrypt  # Re-encrypt after changes
make check    # Verify setup
```

**First-time setup:** You need an age key at `~/.config/sops/age/keys.txt`. See `secrets/README.md` for details.

## Prerequisites

- age + sops for secrets decryption
- Ansible 2.0+, OpenTofu, Packer
- SSH key at `~/.ssh/id_rsa`
- Proxmox VE host with API access

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed architecture, E2E test procedures, and conventions.

## License

Apache 2.0 - see [LICENSE](LICENSE)
