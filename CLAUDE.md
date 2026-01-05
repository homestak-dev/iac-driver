# iac-driver

Infrastructure orchestration engine for Proxmox VE.

## Overview

This repo provides scenario-based workflows that coordinate the tool repositories:

| Repo | Purpose | URL |
|------|---------|-----|
| bootstrap | Entry point, curl\|bash installer | https://github.com/homestak-dev/bootstrap |
| site-config | Site-specific secrets and configuration | https://github.com/homestak-dev/site-config |
| ansible | Proxmox host configuration, PVE installation | https://github.com/homestak-dev/ansible |
| tofu | VM provisioning with OpenTofu | https://github.com/homestak-dev/tofu |
| packer | Custom Debian cloud image building | https://github.com/homestak-dev/packer |

## Quick Start

```bash
# Clone this repo and tool repos
git clone https://github.com/homestak-dev/iac-driver.git
cd iac-driver
./scripts/setup-tools.sh  # Clones ansible, tofu, packer, site-config as siblings

# Setup site-config (secrets management)
cd ../site-config
make setup
make decrypt
```

## Secrets Management

Credentials are managed in the [site-config](https://github.com/homestak-dev/site-config) repository using [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age).

**Discovery:** iac-driver finds site-config via:
1. `$HOMESTAK_SITE_CONFIG` environment variable
2. `../site-config/` sibling directory
3. `/opt/homestak/site-config/` bootstrap default

**Setup:**
```bash
cd ../site-config
make setup    # Configure git hooks, check dependencies
make decrypt  # Decrypt secrets (requires age key)
```

## Directory Structure

All repos are siblings in a common parent directory:

```
<parent>/
├── iac-driver/           # This repo - Infrastructure orchestration
│   ├── run.sh            # CLI entry point (bash wrapper)
│   ├── src/              # Python package
│   │   ├── cli.py        # CLI implementation
│   │   ├── common.py     # ActionResult + shared utilities
│   │   ├── config.py     # Host configuration (auto-discovery from site-config)
│   │   ├── actions/      # Reusable primitive operations
│   │   │   ├── tofu.py   # TofuApplyAction, TofuDestroyAction
│   │   │   ├── ansible.py# AnsiblePlaybookAction
│   │   │   ├── ssh.py    # SSHCommandAction, WaitForSSHAction
│   │   │   ├── proxmox.py# StartVMAction, WaitForGuestAgentAction
│   │   │   └── file.py   # DownloadFileAction, RemoveImageAction
│   │   ├── scenarios/    # Workflow definitions
│   │   │   ├── nested_pve.py        # nested-pve-{constructor,destructor,roundtrip}
│   │   │   ├── simple_vm.py         # simple-vm-{constructor,destructor,roundtrip}
│   │   │   ├── pve_configure.py     # pve-configure (local/remote)
│   │   │   ├── bootstrap.py         # bootstrap-install
│   │   │   └── cleanup_nested_pve.py # Shared cleanup actions
│   │   └── reporting/    # Test report generation (JSON + markdown)
│   ├── reports/          # Generated test reports
│   └── scripts/          # Helper scripts
├── site-config/          # Site-specific secrets and configuration
│   ├── site.yaml         # Site-wide defaults
│   ├── secrets.yaml      # All sensitive values (SOPS encrypted)
│   ├── nodes/            # PVE instance configuration
│   └── envs/             # Environment configuration (for tofu)
├── ansible/              # Tool repo (sibling)
├── tofu/                 # Tool repo (sibling)
└── packer/               # Tool repo (sibling)
```

Scripts use relative paths (`../ansible`, `../tofu`, `../packer`) so the parent directory can be anywhere.

## Common Commands

### Ansible (from ansible/)
```bash
ansible-playbook -i inventory/local.yml playbooks/pve-setup.yml      # PVE config
ansible-playbook -i inventory/local.yml playbooks/user.yml           # User management
ansible-playbook -i inventory/remote-dev.yml playbooks/pve-install.yml \
  -e ansible_host=<IP> -e pve_hostname=<hostname>                    # Install PVE on Debian 13
```

### Packer (from packer/)
```bash
./build.sh       # Interactive build menu (Debian 12 or 13)
./publish.sh     # Copy images to /var/lib/vz/template/iso/
```

### OpenTofu (from tofu/envs/<env>/)
```bash
tofu init        # Initialize providers/modules
tofu plan        # Preview changes
tofu apply       # Deploy VMs
tofu destroy     # Tear down
tofu fmt         # Format HCL files
```

## Architecture

### Typical Deployment Workflow
```
1. Bootstrap Proxmox host → iac-driver (pve-configure) or ansible (pve-setup.yml, user.yml)
2. Build custom images     → packer (build.sh, publish.sh)
3. Provision VMs           → tofu (plan, apply)
4. Reconfigure as needed   → ansible (pve-setup.yml, user.yml)
```

### Tofu 3-Level Configuration Inheritance
Node configuration merges in `tofu/envs/common/locals.tf`:
1. **Defaults** - base values for all VMs
2. **Cluster** - per-environment overrides (bridge, DNS, packages)
3. **Node** - individual VM specifics (hostname, IP, MAC, VM ID)

### Tofu Module Responsibilities
| Module | Purpose |
|--------|---------|
| `proxmox-vm` | Single VM: CPU, memory, disk, network, cloud-init |
| `proxmox-file` | Cloud image management (local or URL source) |
| `proxmox-sdn` | VXLAN zone, vnet, subnet configuration |

### Ansible Role Hierarchy
- Core playbooks: `pve-setup.yml`, `user.yml`, `pve-install.yml`
- Core roles: base, users, security, proxmox, pve-install
- E2E roles: pve-iac (generic IaC tools), nested-pve (E2E test config)
- Environment-specific variables in `inventory/group_vars/`

## Conventions

- **VM IDs**: 5-digit (10000+ dev, 20000+ k8s)
- **MAC prefix**: BC:24:11:*
- **Hostnames**: `{cluster}{instance}` (dev1, router, kubeadm1)
- **Cloud-init files**: `{hostname}-meta.yaml`, `{hostname}-user.yaml`
- **Environments**: dev (permissive SSH, passwordless sudo) vs prod (strict SSH, fail2ban)

## Network Topology

Environments use SDN VXLAN with a router VM as gateway:
- **dev**: 10.10.10.0/24 (router VM 10000)
- **k8s**: 10.10.20.0/24 (router VM 20000)
- Both route through vmbr0 (10.0.12.0/24)

## Key Files

| File | Purpose |
|------|---------|
| `ansible/inventory/group_vars/*.yml` | Environment-specific Ansible variables |
| `ansible/roles/pve-install/defaults/main.yml` | PVE installation defaults |
| `tofu/envs/common/locals.tf` | Configuration inheritance logic |
| `tofu/envs/*/locals.tf` | Per-environment cluster definitions |
| `packer/templates/*.pkr.hcl` | Debian image build definitions |

## Node Configuration

PVE node configuration is stored in `site-config/nodes/*.yaml`:

| File | Node | API Endpoint |
|------|------|--------------|
| `site-config/nodes/pve.yaml` | pve | https://localhost:8006 |
| `site-config/nodes/pve-deb.yaml` | pve-deb | (dynamic, nested PVE) |

API tokens are stored separately in `site-config/secrets.yaml` and resolved by key reference:
```yaml
# nodes/pve.yaml (primary key derived from filename)
host: pve                         # FK -> hosts/pve.yaml
api_endpoint: https://localhost:8006
api_token: pve                    # FK -> secrets.api_tokens.pve
```

**Setup:** First-time clone requires:
```bash
cd ../site-config
make setup    # Configure git hooks, check dependencies
make decrypt  # Decrypt secrets (requires age key)
```

**Usage:** iac-driver automatically discovers nodes via `get_site_config_dir()`.

**Configuration Merge Order:** `site.yaml` → `nodes/{node}.yaml` → `secrets.yaml`

## Known Issues

**Debian 12 Cloud-Init First-Boot Kernel Panic**: Add `serial_device {}` to VM resource config. Already handled in proxmox-vm module.

## E2E Nested PVE Testing

End-to-end testing uses nested virtualization to validate the full stack: VM provisioning → PVE installation → nested VM creation.

### Architecture

```
Outer PVE Host (pve)
├── IP: 10.0.12.x
└── VM 99913 (pve-deb) - Inner PVE
    ├── Debian 13 + Proxmox VE
    ├── 2 cores, 8GB RAM, 64GB disk
    └── VM 99901 (test1) - Test VM
        └── Debian 12, 1 core, 4GB RAM
```

### CLI

The orchestrator runs scenarios composed of reusable actions:

```bash
# List available scenarios
./run.sh --list-scenarios

# List phases for a scenario
./run.sh --scenario nested-pve-roundtrip --list-phases

# Run full E2E roundtrip (construct, verify, destruct)
./run.sh --scenario nested-pve-roundtrip --host pve --verbose

# Run only constructor (leave environment running)
./run.sh --scenario nested-pve-constructor --host pve

# Run only destructor (cleanup existing environment)
./run.sh --scenario nested-pve-destructor --host pve --inner-ip 10.0.12.x

# Simple VM test (deploy, verify SSH, destroy)
./run.sh --scenario simple-vm-roundtrip --host pve

# Configure PVE host (local)
./run.sh --scenario pve-configure --local

# Configure PVE host (remote)
./run.sh --scenario pve-configure --remote 10.0.12.x

# Test bootstrap on a VM (requires vm_ip)
./run.sh --scenario bootstrap-install --vm-ip 10.0.12.x --homestak-user homestak
```

**CLI Options:**
| Option | Description |
|--------|-------------|
| `--scenario`, `-S` | Scenario to run (required) |
| `--host`, `-H` | Target PVE host (default: pve) |
| `--verbose`, `-v` | Enable verbose logging |
| `--skip`, `-s` | Phases to skip (repeatable) |
| `--list-scenarios` | List available scenarios |
| `--list-phases` | List phases for selected scenario |
| `--inner-ip` | Inner PVE IP (for nested-pve-destructor) |
| `--local` | Run locally (for pve-configure) |
| `--remote` | Remote host IP (for pve-configure) |
| `--vm-ip` | Target VM IP (for bootstrap-install) |
| `--homestak-user` | User to create during bootstrap |

**Available Scenarios:**
| Scenario | Phases | Description |
|----------|--------|-------------|
| `bootstrap-install` | 3 | Run bootstrap, verify installation and user |
| `nested-pve-constructor` | 10 | Provision inner PVE, install Proxmox, create test VM, verify |
| `nested-pve-destructor` | 3 | Cleanup test VM, stop and destroy inner PVE |
| `nested-pve-roundtrip` | 13 | Full cycle: construct → verify → destruct |
| `pve-configure` | 2 | Configure PVE host (pve-setup + user) |
| `simple-vm-constructor` | 5 | Ensure image, provision VM, verify SSH |
| `simple-vm-destructor` | 1 | Destroy test VM |
| `simple-vm-roundtrip` | 6 | Full cycle: construct → verify → destruct |

**nested-pve-roundtrip runtime: ~8.5 minutes**

### Test Reports

Reports are generated in `reports/` with format: `YYYYMMDD-HHMMSS.{passed|failed}.{md|json}`

Both JSON and markdown reports are generated for each run.

### Helper Scripts

| Script | Purpose |
|--------|---------|
| `scripts/wait-for-guest-agent.sh` | Poll for VM IP (used by orchestrator) |
| `scripts/setup-tools.sh` | Clone/update tool repos |

### Tofu Environments

**pve-deb** - Inner PVE VM (in `../tofu/envs/pve-deb/`):

| Property | Value |
|----------|-------|
| VM ID | 99913 |
| Hostname | pve-deb |
| CPU | 2 cores (faster packer builds) |
| Memory | 8192 MB |
| Disk | 64 GB on local-zfs |
| Image | debian-13-custom.img |

**test** - Parameterized test VM (in `../tofu/envs/test/`):

Works on both outer and inner PVE via `-var="node=..."` override:

```bash
# Deploy to outer PVE (default)
cd ../tofu/envs/test && tofu apply

# Deploy to nested PVE
tofu apply -var="node=pve-deb"
```

Configuration is loaded from `site-config/nodes/{node}.yaml` and `site-config/envs/test.yaml`.

### Ansible Roles

**pve-iac** - Generic IaC tooling (in `../ansible/roles/pve-iac/`):

Reusable for any Proxmox host (dev, k8s, etc.):
- `tools.yml` - Install packer and tofu from official repos
- `api-token.yml` - Create `root@pam!tofu` API token

**nested-pve** - E2E test configuration (in `../ansible/roles/nested-pve/`):

Depends on `pve-iac` role:
- `network.yml` - Configure vmbr0 bridge (required after Debian→PVE conversion)
- `ssh-keys.yml` - Copy SSH keys for nested VM access
- `copy-files.yml` - Sync homestak repos, create API token, configure test env

Synced to inner PVE at `/opt/homestak/`:
- `site-config/` - Configuration with test.yaml override (node: pve-deb)
- `tofu/` - Modules and environments
- `packer/` - Templates and scripts
- API token created via `pveum` and injected into secrets.yaml

## Prerequisites

- Ansible 2.0+, OpenTofu, Packer with QEMU/KVM
- SSH key at `~/.ssh/id_rsa`
- age + sops for secrets decryption (see `make setup`)
- age key at `~/.config/sops/age/keys.txt`
- Nested virtualization enabled (`cat /sys/module/kvm_intel/parameters/nested` = Y)

## Tool Documentation

Each tool repo has its own CLAUDE.md with detailed context:
- `../bootstrap/CLAUDE.md` - curl|bash installer and homestak CLI
- `../site-config/CLAUDE.md` - Secrets management and encryption
- `../ansible/CLAUDE.md` - Ansible-specific commands and structure
- `../tofu/CLAUDE.md` - OpenTofu modules and environment details
