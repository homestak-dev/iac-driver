# iac-driver

E2E test orchestration for Proxmox VE infrastructure-as-code.

## Overview

This repo coordinates three tool repositories for end-to-end testing:

| Repo | Purpose | URL |
|------|---------|-----|
| ansible | Proxmox host configuration, PVE installation | https://github.com/homestak-dev/ansible |
| tofu | VM provisioning with OpenTofu | https://github.com/homestak-dev/tofu |
| packer | Custom Debian cloud image building | https://github.com/homestak-dev/packer |

## Quick Start

```bash
# Clone this repo and tool repos
git clone https://github.com/homestak-dev/iac-driver.git
cd iac-driver
make setup    # Configure git hooks for secrets
make decrypt  # Decrypt secrets (requires age key)
./scripts/setup-tools.sh  # Clones ansible, tofu, packer as siblings
```

## Secrets Management

Credentials are encrypted with [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age):

```
secrets/
├── pve.tfvars.enc      # Encrypted (committed)
├── pve.tfvars          # Plaintext (gitignored, local only)
└── ...
```

**Setup:**
```bash
make setup    # Configure git hooks, check dependencies
make decrypt  # Decrypt secrets (requires age key at ~/.config/sops/age/keys.txt)
```

**Makefile targets:** `setup`, `decrypt`, `encrypt`, `clean`, `check`

Git hooks auto-encrypt on commit and auto-decrypt on checkout.

## Directory Structure

All repos are siblings in a common parent directory:

```
<parent>/
├── iac-driver/           # This repo - E2E orchestration
│   ├── CLAUDE.md
│   ├── e2e/              # E2E test orchestrator
│   │   ├── orchestrator.py
│   │   ├── phases/
│   │   ├── config/
│   │   └── reports/
│   ├── scripts/
│   │   ├── wait-for-guest-agent.sh
│   │   └── setup-tools.sh
│   └── secrets/            # Encrypted credentials (SOPS + age)
│       ├── pve.tfvars.enc      # Host config for pve.homestak
│       └── father.tfvars.enc   # Host config for father.core
├── ansible/              # Tool repo (sibling)
├── tofu/                 # Tool repo (sibling)
└── packer/               # Tool repo (sibling)
```

Scripts use relative paths (`../ansible`, `../tofu`, `../packer`) so the parent directory can be anywhere.

## Common Commands

### Ansible (from ansible/)
```bash
ansible-playbook -i inventory/local.yml playbooks/site.yml           # Full post-install
ansible-playbook -i inventory/local.yml playbooks/pve-setup.yml      # PVE config only
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
1. Bootstrap Proxmox host → ansible (site.yml)
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
- `site.yml` imports `pve-setup.yml` + `user.yml`
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

## Host Configuration

Host-specific Proxmox credentials are encrypted with SOPS + age in `secrets/`:

| File | Target Host | API Endpoint |
|------|-------------|--------------|
| `secrets/pve.tfvars.enc` | pve | https://pve.homestak:8006 |
| `secrets/father.tfvars.enc` | father | https://father.core:8006 |

**Setup:** First-time clone requires:
```bash
make setup    # Configure git hooks, check dependencies
make decrypt  # Decrypt secrets (requires age key)
```

**Usage:** Pass `-var-file` when provisioning from outer host:
```bash
cd ../tofu/envs/pve-deb
tofu apply -var-file=../../../iac-driver/secrets/pve.tfvars
```

Environment `terraform.tfvars` files default to localhost for local execution.

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

### E2E Orchestrator

The E2E test is automated via a Python orchestrator:

```bash
# Run full E2E test
python3 -m e2e.orchestrator --host pve --verbose

# List available phases
python3 -m e2e.orchestrator --list-phases

# Skip phases (e.g., resume after provision)
python3 -m e2e.orchestrator --host pve --skip provision --inner-ip 10.0.12.x
```

**Phases:**
| Phase | Duration | Description |
|-------|----------|-------------|
| provision | ~30s | Tofu creates inner PVE VM, starts it, waits for IP |
| install_pve | ~9min | Ansible installs Proxmox VE |
| configure | ~45s | Ansible configures inner PVE (API token, tofu, etc.) |
| download_image | ~35s | Downloads packer image from GitHub release |
| test_vm | ~50s | Tofu creates test VM on inner PVE |
| verify | ~10s | Validates SSH chain through jump host |

**Total runtime: ~12 minutes**

### Test Reports

Reports are generated in `e2e/reports/` with format: `YYYYMMDD-HHMMSS.{passed|failed}.{md|json}`

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

Works on both outer and inner PVE via tfvars:

| Variable | Outer PVE | Inner PVE |
|----------|-----------|-----------|
| `proxmox_node_name` | pve | pve-deb |
| `vm_datastore_id` | local-zfs | local |
| `proxmox_api_endpoint` | https://pve:8006 | https://<inner-ip>:8006 |

### Ansible Roles

**pve-iac** - Generic IaC tooling (in `../ansible/roles/pve-iac/`):

Reusable for any Proxmox host (dev, k8s, etc.):
- `tools.yml` - Install packer and tofu from official repos
- `api-token.yml` - Create `root@pam!tofu` API token

**nested-pve** - E2E test configuration (in `../ansible/roles/nested-pve/`):

Depends on `pve-iac` role:
- `network.yml` - Configure vmbr0 bridge (required after Debian→PVE conversion)
- `ssh-keys.yml` - Copy SSH keys for nested VM access
- `copy-files.yml` - Deploy packer/tofu files, generate tfvars

Generated files on inner PVE:
- `/root/packer/` - Packer templates and scripts
- `/root/tofu/` - Tofu modules and environments
- `/root/tofu/envs/test/terraform.tfvars` - Auto-generated with API token

## Prerequisites

- Ansible 2.0+, OpenTofu, Packer with QEMU/KVM
- SSH key at `~/.ssh/id_rsa`
- age + sops for secrets decryption (see `make setup`)
- age key at `~/.config/sops/age/keys.txt`
- Nested virtualization enabled (`cat /sys/module/kvm_intel/parameters/nested` = Y)

## Tool Documentation

Each tool repo has its own CLAUDE.md with detailed context:
- `../ansible/CLAUDE.md` - Ansible-specific commands and structure
- `../tofu/CLAUDE.md` - OpenTofu modules and environment details
