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
2. `../site-config/` sibling directory (dev workspace)
3. `/usr/local/etc/homestak/` (FHS-compliant bootstrap)
4. `/opt/homestak/site-config/` (legacy bootstrap)

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
│   │   ├── config.py          # Host configuration (auto-discovery from site-config)
│   │   ├── config_resolver.py # ConfigResolver - resolves site-config for tofu
│   │   ├── actions/      # Reusable primitive operations
│   │   │   ├── tofu.py   # TofuApply/Destroy[Remote]Action
│   │   │   ├── ansible.py# AnsiblePlaybookAction
│   │   │   ├── ssh.py    # SSHCommandAction, WaitForSSHAction
│   │   │   ├── proxmox.py# StartVMAction, WaitForGuestAgentAction
│   │   │   └── file.py   # DownloadFileAction, RemoveImageAction
│   │   ├── scenarios/    # Workflow definitions
│   │   │   ├── nested_pve.py        # nested-pve-{constructor,destructor,roundtrip}
│   │   │   ├── vm.py                # vm-{constructor,destructor,roundtrip}
│   │   │   ├── pve_setup.py         # pve-setup (local/remote)
│   │   │   ├── user_setup.py        # user-setup (local/remote)
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

## ConfigResolver

The `ConfigResolver` class resolves site-config YAML files into flat configurations for tofu and ansible. All template, preset, and posture inheritance is resolved in Python, so consumers receive fully-computed values.

### Usage

```python
from src.config_resolver import ConfigResolver

# Auto-discover site-config (env var, sibling, /opt/homestak)
resolver = ConfigResolver()

# Or specify path explicitly
resolver = ConfigResolver('/path/to/site-config')

# Resolve environment for tofu
config = resolver.resolve_env(env='dev', node='pve')
resolver.write_tfvars(config, '/tmp/tfvars.json')

# Resolve environment for ansible (v0.13+)
ansible_vars = resolver.resolve_ansible_vars(env='dev')
resolver.write_ansible_vars(ansible_vars, '/tmp/ansible-vars.json')

# List available entities
resolver.list_envs()      # ['dev', 'test', 'nested-pve']
resolver.list_postures()  # ['dev', 'prod', 'local']
resolver.list_templates() # ['debian-12-custom', 'nested-pve', ...]
resolver.list_presets()   # ['small', 'medium', 'large', ...]
```

### Resolution Order (Tofu)

1. `vms/presets/{preset}.yaml` - Size presets (if template uses `preset:`)
2. `vms/{template}.yaml` - Template definition
3. `envs/{env}.yaml` - Instance overrides (name, ip, vmid)

### Resolution Order (Ansible)

1. `site.yaml` defaults - timezone, packages, pve settings
2. `postures/{posture}.yaml` - Security settings from env's posture FK
3. Packages merged: site packages + posture packages (deduplicated)

### Output Structure (Tofu)

```python
{
    "node": "pve",
    "api_endpoint": "https://localhost:8006",
    "api_token": "root@pam!tofu=...",
    "ssh_user": "root",
    "datastore": "local-zfs",
    "root_password": "$6$...",
    "ssh_keys": ["ssh-rsa ...", ...],
    "vms": [
        {
            "name": "test",
            "vmid": 99900,
            "image": "debian-12",
            "cores": 1,
            "memory": 2048,
            "disk": 20,
            "bridge": "vmbr0"
        }
    ]
}
```

### Output Structure (Ansible)

```python
{
    "timezone": "America/Denver",
    "pve_remove_subscription_nag": true,
    "packages": ["htop", "curl", "wget", "net-tools", "strace"],
    "ssh_port": 22,
    "ssh_permit_root_login": "yes",
    "ssh_password_authentication": "yes",
    "sudo_nopasswd": true,
    "fail2ban_enabled": false,
    "env_name": "dev",
    "posture_name": "dev",
    "ssh_authorized_keys": ["ssh-rsa ...", ...]
}
```

### vmid Allocation

- If `vmid_base` is defined in env: `vmid = vmid_base + index`
- If `vmid_base` is not defined: `vmid = null` (PVE auto-assigns)
- Per-VM `vmid` override always takes precedence

### Tofu Actions

Actions in `src/actions/tofu.py` use ConfigResolver to generate tfvars and run tofu:

| Action | Description |
|--------|-------------|
| `TofuApplyAction` | Run tofu apply with ConfigResolver on local host |
| `TofuDestroyAction` | Run tofu destroy with ConfigResolver on local host |
| `TofuApplyRemoteAction` | Run ConfigResolver + tofu apply on remote host via SSH |
| `TofuDestroyRemoteAction` | Run ConfigResolver + tofu destroy on remote host via SSH |

**State Isolation:** Each env+node gets isolated state via explicit `-state` flag:
```
iac-driver/.states/{env}-{node}/terraform.tfstate
```

**Important:** The `-state` flag is required because `TF_DATA_DIR` only affects plugin/module caching, not state file location. Without explicit state isolation, running scenarios on different hosts can cause state conflicts.

**Remote Actions:** Run ConfigResolver on the target host (recursive pattern):
```python
TofuApplyRemoteAction(
    name='provision-test-vm',
    env_name='test',           # Environment to deploy
    node_name='nested-pve',    # Node in remote site-config
    host_key='inner_ip',       # Context key for SSH target
)
```

**Context Passing:** TofuApplyAction extracts VM IDs from resolved config and adds them to context:
```python
# After tofu apply, context contains:
context['test_vm_id'] = 99900  # From vm name 'test' with vmid 99900
context['inner_vm_id'] = 99913  # From vm name 'inner' with vmid 99913
context['provisioned_vms'] = [{'name': 'test', 'vmid': 99900}, ...]  # All VMs
```

Downstream actions (StartVMAction, WaitForGuestAgentAction) check context first, then fall back to config attributes.

**Multi-VM Actions:** For environments with multiple VMs, use these actions instead of single-VM variants:

| Action | Description |
|--------|-------------|
| `StartProvisionedVMsAction` | Start all VMs from `provisioned_vms` context |
| `WaitForProvisionedVMsAction` | Wait for guest agent on all VMs, collect IPs |

```python
# Multi-VM scenario phases
('start', StartProvisionedVMsAction(
    name='start-vms',
    pve_host_attr='ssh_host',
), 'Start VM(s)'),

('wait_ip', WaitForProvisionedVMsAction(
    name='wait-for-ips',
    pve_host_attr='ssh_host',
    timeout=180,
), 'Wait for VM IP(s)'),
```

After `WaitForProvisionedVMsAction`, context contains:
- `{vm_name}_ip` for each VM (e.g., `deb12-test_ip`, `deb13-leaf_ip`)
- `vm_ip` - first VM's IP (backward compatibility)

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

### Packer Build Scenarios (via iac-driver)

Build images on a remote host with proper QEMU/KVM support:

```bash
# Prerequisites: remote host must be bootstrapped
ssh root@<host> "curl -fsSL .../install.sh | bash && homestak install packer"

# Build and fetch images (for release)
./run.sh --scenario packer-build-fetch --remote <host-ip>

# Build and publish to PVE storage (for local use)
./run.sh --scenario packer-build-publish --remote <host-ip>

# Build specific template only
./run.sh --scenario packer-build-fetch --remote <host-ip> --templates debian-12-custom

# Dev workflow: sync local changes, build, fetch
./run.sh --scenario packer-sync-build-fetch --remote <host-ip>
```

**Available packer scenarios:**
| Scenario | Description |
|----------|-------------|
| `packer-build` | Build images (local or remote) |
| `packer-build-publish` | Build and publish to PVE storage |
| `packer-build-fetch` | Build remotely, fetch to local |
| `packer-sync` | Sync local packer to remote |
| `packer-sync-build-fetch` | Sync, build, fetch (dev workflow) |

**Output:** Images fetched to `/tmp/packer-images/`

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
1. Bootstrap Proxmox host → iac-driver (pve-setup) or ansible (pve-setup.yml, user.yml)
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
- integration roles: pve-iac (generic IaC tools), nested-pve (integration test config)
- Environment-specific variables in `inventory/group_vars/`

## Conventions

- **VM IDs**: 5-digit (10000+ dev, 20000+ k8s)
- **MAC prefix**: BC:24:11:*
- **Hostnames**: `{cluster}{instance}` (dev1, router, kubeadm1)
- **Cloud-init files**: `{hostname}-meta.yaml`, `{hostname}-user.yaml`
- **Environments**: dev (permissive SSH, passwordless sudo) vs prod (strict SSH, fail2ban)

## Naming Conventions

### Scenarios, Phases, and Actions

| Type | Pattern | Examples |
|------|---------|----------|
| **Scenarios** | `noun-verb` | `pve-setup`, `vm-constructor`, `user-setup`, `nested-pve-roundtrip` |
| **Phases** | `verb_noun` | `ensure_pve`, `setup_pve`, `provision_vm`, `create_user` |
| **Actions** | `VerbNounAction` | `EnsurePVEAction`, `StartVMAction`, `WaitForSSHAction` |

### Phase Verb Conventions

| Verb | Meaning | Idempotent? |
|------|---------|-------------|
| `ensure_*` | Make sure X exists/is running | Yes - checks first |
| `setup_*` | Configure X for use | Usually yes |
| `provision_*` | Create new resource | No - creates |
| `start_*` | Start existing resource | Yes - checks state |
| `wait_*` | Wait for condition | Yes |
| `verify_*` | Check/validate | Yes |
| `destroy_*` | Remove resource | Yes - checks exists |
| `sync_*` | Synchronize data | Yes |

### Examples

```python
# Scenario class (noun-verb pattern)
class VMConstructor:
    name = 'vm-constructor'

# Phase definitions (verb_noun pattern)
phases = [
    ('provision_vm', TofuApplyAction(...), 'Provision VM'),
    ('start_vm', StartVMAction(...), 'Start VM'),
    ('wait_ip', WaitForGuestAgentAction(...), 'Wait for IP'),
    ('verify_ssh', WaitForSSHAction(...), 'Verify SSH'),
]

# Action class (VerbNounAction pattern)
class EnsurePVEAction:
    """Idempotent action - checks if PVE running before installing."""
    def run(self, config, context):
        # Check first (idempotent)
        if self._pve_running(context):
            return ActionResult(success=True, message="PVE already running")
        # Then act
        return self._install_pve(config, context)
```

## Network Topology

Environments use SDN VXLAN with a router VM as gateway:
- **dev**: 10.10.10.0/24 (router VM 10000)
- **k8s**: 10.10.20.0/24 (router VM 20000)
- Both route through vmbr0 (10.0.12.0/24)

## Key Files

| File | Purpose |
|------|---------|
| `ansible/inventory/group_vars/*.yml` | Environment-specific Ansible variables |
| `ansible/collections/.../proxmox/roles/install/defaults/main.yml` | PVE installation defaults |
| `tofu/envs/common/locals.tf` | Configuration inheritance logic |
| `tofu/envs/*/locals.tf` | Per-environment cluster definitions |
| `packer/templates/*.pkr.hcl` | Debian image build definitions |

## Node Configuration

PVE node configuration is stored in `site-config/nodes/*.yaml`:

| File | Node | API Endpoint |
|------|------|--------------|
| `site-config/nodes/{nodename}.yaml` | {nodename} | https://{ip}:8006 |
| `site-config/nodes/nested-pve.yaml` | nested-pve | (dynamic, nested PVE) |

**Important:** The filename must match the actual PVE node name (check with `pvesh get /nodes`).

API tokens are stored separately in `site-config/secrets.yaml` and resolved by key reference:
```yaml
# nodes/father.yaml (primary key derived from filename)
host: father                      # FK -> hosts/father.yaml
api_endpoint: https://10.0.12.61:8006
api_token: father                 # FK -> secrets.api_tokens.father
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

**PVE SSL Certificate Generation with IPv6**: IPv6 link-local addresses with zone IDs (e.g., `fe80::...%vmbr0`) break PVE SSL certificate generation. Fix: temporarily disable IPv6, run `pvecm updatecerts --force`, re-enable IPv6. Handled in ansible `nested-pve` role.

**Snippets Content Type Required**: Cloud-init user-data files require `snippets` content type on local datastore. Run `pvesm set local -content images,rootdir,vztmpl,backup,iso,snippets`. Handled in ansible `nested-pve` role.

**Claude Code Autonomy**: For fully autonomous integration test runs, add these to Claude Code allowed tools:
```
Bash(ansible-playbook:*), Bash(ansible:*), Bash(rsync:*)
```
Or run with `--dangerously-skip-permissions` flag.

**OpenTofu State Version 4 Bug**: When `TF_DATA_DIR` contains a `terraform.tfstate` file, OpenTofu's legacy code path reads it and rejects valid v4 states with "does not support state version 4". **Workaround**: Store state file outside `TF_DATA_DIR` - we use a `data/` subdirectory for `TF_DATA_DIR` while keeping state at the parent level. See [opentofu/opentofu#3643](https://github.com/opentofu/opentofu/issues/3643).

## Timeout Configuration

Operations use tiered timeouts based on expected duration. Scenarios can override action defaults.

### Timeout Tiers

| Tier | Duration | Use Case |
|------|----------|----------|
| Quick | 5-30s | Simple SSH commands, status checks |
| Short | 60s | Ping waits, basic operations |
| Medium | 120-300s | Tofu apply/destroy, downloads, SSH waits |
| Long | 600s | Complex ansible playbooks, tofu init+apply |
| Extended | 1200s | PVE installation with reboot |

### Core Utilities (common.py)

| Function | Timeout | Interval | Notes |
|----------|---------|----------|-------|
| `run_command()` | 600s | - | General command execution |
| `run_ssh()` | 60s | - | SSH command (also sets ConnectTimeout) |
| `wait_for_ping()` | 60s | 2s | ICMP ping polling |
| `wait_for_ssh()` | 60s | 3s | SSH availability polling |
| `wait_for_guest_agent()` | 300s | 5s | QEMU guest agent polling |

### Action Defaults (src/actions/)

| Action | Parameter | Default | Notes |
|--------|-----------|---------|-------|
| `TofuApplyAction` | timeout_init | 120s | `tofu init` |
| `TofuApplyAction` | timeout_apply | 300s | `tofu apply` |
| `TofuDestroyAction` | timeout | 300s | `tofu destroy` |
| `TofuApplyRemoteAction` | timeout_init | 120s | Remote init |
| `TofuApplyRemoteAction` | timeout_apply | 300s | Remote apply |
| `TofuDestroyRemoteAction` | timeout | 300s | Remote destroy |
| `AnsiblePlaybookAction` | timeout | 600s | Playbook execution |
| `AnsiblePlaybookAction` | ssh_timeout | 60s | Pre-playbook SSH wait |
| `WaitForSSHAction` | timeout | 60s | SSH availability |
| `WaitForSSHAction` | interval | 5s | Retry interval |
| `WaitForGuestAgentAction` | timeout | 300s | Guest agent |
| `WaitForGuestAgentAction` | interval | 5s | Retry interval |
| `SSHCommandAction` | timeout | 60s | Single SSH command |
| `SyncReposToVMAction` | timeout | 300s | rsync/tar transfer |
| `DownloadGitHubReleaseAction` | timeout | 300s | Asset download |
| `VerifySSHChainAction` | timeout | 60s | Jump host verification |

### Scenario Overrides (nested-pve)

| Phase | Timeout | Rationale |
|-------|---------|-----------|
| wait_ip | 300s | Guest agent can be slow on first boot |
| install_pve | 1200s | PVE install includes apt, kernel, reboot |
| configure | 600s | Ansible nested-pve-setup playbook |
| download_image | 300s | ~200MB image from GitHub |
| test_vm_apply | 300s | Remote tofu on nested PVE |

### Tuning Guidelines

- **Monitor actual durations**: integration test reports include phase timings - use these to tune
- **Nested operations multiply**: Remote tofu = SSH + init + apply timeouts
- **Guest agent is slow**: First boot can take 60-90s for agent to respond
- **PVE install varies**: Network speed affects apt, allow 20+ min buffer
- **Override in scenarios**: When a phase needs more time, override the default explicitly

## integration Nested PVE Testing

End-to-end testing uses nested virtualization to validate the full stack: VM provisioning → PVE installation → nested VM creation.

### Architecture

```
Outer PVE Host (pve)
├── IP: 10.0.12.x
└── VM 99913 (nested-pve) - Inner PVE
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

# Run full integration roundtrip (construct, verify, destruct)
./run.sh --scenario nested-pve-roundtrip --host father --verbose

# Run only constructor (leave environment running)
./run.sh --scenario nested-pve-constructor --host father

# Run only destructor (cleanup existing environment)
./run.sh --scenario nested-pve-destructor --host father --inner-ip 10.0.12.x

# VM test (deploy, verify SSH, destroy)
./run.sh --scenario vm-roundtrip --host father

# Deploy custom environment (multi-VM)
./run.sh --scenario vm-constructor --host father --env ansible-test

# Install + configure PVE (local)
./run.sh --scenario pve-setup --local

# Install + configure PVE (remote)
./run.sh --scenario pve-setup --remote 10.0.12.x

# Create homestak user (local)
./run.sh --scenario user-setup --local

# Create homestak user (remote)
./run.sh --scenario user-setup --remote 10.0.12.x

# Test bootstrap on a VM (requires vm_ip)
./run.sh --scenario bootstrap-install --vm-ip 10.0.12.x --homestak-user homestak
```

**CLI Options:**
| Option | Description |
|--------|-------------|
| `--scenario`, `-S` | Scenario to run (required) |
| `--host`, `-H` | Target PVE host (required for most scenarios) |
| `--env`, `-E` | Environment to deploy (overrides scenario default) |
| `--context-file`, `-C` | Save/load context for chained runs |
| `--verbose`, `-v` | Enable verbose logging |
| `--skip`, `-s` | Phases to skip (repeatable) |
| `--list-scenarios` | List available scenarios |
| `--list-phases` | List phases for selected scenario |
| `--inner-ip` | Inner PVE IP (for nested-pve-destructor) |
| `--local` | Run locally (for pve-setup, user-setup, packer-build) |
| `--remote` | Remote host IP (for pve-setup, user-setup, packer-build) |
| `--templates` | Comma-separated packer templates (for packer-build) |
| `--vm-ip` | Target VM IP (for bootstrap-install) |
| `--homestak-user` | User to create during bootstrap |
| `--packer-release` | Packer release tag (e.g., v0.8.0-rc1, default: latest) |
| `--timeout`, `-t` | Overall scenario timeout in seconds (checked between phases) |
| `--yes`, `-y` | Skip confirmation prompt for destructive scenarios |
| `--vm-id` | Override VM ID (repeatable): `--vm-id test=99990` |
| `--dry-run` | Preview scenario phases without executing actions |
| `--preflight` | Run preflight checks only (no scenario execution) |
| `--skip-preflight` | Skip preflight checks before scenario execution |

**Preflight Checks:**

Preflight checks validate host prerequisites before running scenarios:

```bash
# Standalone preflight check (local)
./run.sh --preflight --local

# Standalone preflight check (remote)
./run.sh --preflight --host mother

# Skip preflight for faster iteration
./run.sh --scenario nested-pve-roundtrip --host father --skip-preflight
```

Checks include:
- Bootstrap installation (core repos present)
- site-init completion (secrets.yaml decrypted, node config exists)
- PVE API connectivity and token validity
- Nested virtualization (for nested-pve-* scenarios)

**Context File Usage:**

The `--context-file` flag enables running constructor and destructor scenarios separately by persisting context (VM IDs, IPs) between invocations:

```bash
# Constructor saves context
./run.sh --scenario nested-pve-constructor --host father -C /tmp/nested-pve.ctx

# Inspect context
cat /tmp/nested-pve.ctx
# {"nested-pve_vm_id": 99913, "inner_ip": "10.0.12.152", ...}

# Destructor loads context
./run.sh --scenario nested-pve-destructor --host father -C /tmp/nested-pve.ctx
```

Context keys populated by nested-pve scenarios:
- `nested-pve_vm_id` - Inner PVE VM ID
- `inner_ip` - Inner PVE IP address
- `test_vm_id` - Test VM ID (on inner PVE)
- `leaf_ip` - Leaf VM IP address (innermost VM in nesting hierarchy)
- `provisioned_vms` - List of all provisioned VMs

**Packer Release:**

The packer release tag for image downloads is resolved in this order (first match wins):
1. CLI: `--packer-release v0.8.0-rc1`
2. site.yaml: `defaults.packer_release: v0.8.0-rc1`
3. Default: `latest` (points to most recent packer release with images)

The `latest` tag is maintained by the packer release process (see packer#5).

**Available Scenarios:**
| Scenario | Runtime | Phases | Description |
|----------|---------|--------|-------------|
| `bootstrap-install` | ~2m | 3 | Run bootstrap, verify installation and user |
| `nested-pve-constructor` | ~6m | 11 | Provision inner PVE, install Proxmox, create test VM, verify |
| `nested-pve-destructor` | ~2m | 3 | Cleanup test VM, stop and destroy inner PVE |
| `nested-pve-roundtrip` | ~9m | 14 | Full cycle: construct → verify → destruct |
| `packer-build` | ~3m | 1 | Build packer images (local or remote) |
| `packer-build-fetch` | ~5m | 2 | Build remotely, fetch to local |
| `packer-build-publish` | ~7m | 2 | Build and publish to PVE storage |
| `packer-sync` | ~30s | 1 | Sync local packer to remote |
| `packer-sync-build-fetch` | ~6m | 3 | Sync, build, fetch (dev workflow) |
| `pve-setup` | ~3m | 2 | Install PVE (if needed) and configure host |
| `user-setup` | ~30s | 1 | Create homestak user |
| `vm-constructor` | ~1.5m | 5 | Ensure image, provision VM, verify SSH |
| `vm-destructor` | ~30s | 1 | Destroy VM |
| `vm-roundtrip` | ~2m | 6 | Full cycle: construct → verify → destruct |

Runtime estimates are shown by `--list-scenarios` and used for `--timeout` defaults.

### Test Reports

Reports are generated in `reports/` with format: `YYYYMMDD-HHMMSS.{passed|failed}.{md|json}`

Both JSON and markdown reports are generated for each run.

### Helper Scripts

| Script | Purpose |
|--------|---------|
| `scripts/wait-for-guest-agent.sh` | Poll for VM IP (used by orchestrator) |
| `scripts/setup-tools.sh` | Clone/update tool repos |

### Tofu Environments

**nested-pve** - Inner PVE VM (in `../tofu/envs/nested-pve/`):

| Property | Value |
|----------|-------|
| VM ID | 99913 |
| Hostname | nested-pve |
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
tofu apply -var="node=nested-pve"
```

Configuration is loaded from `site-config/nodes/{node}.yaml` and `site-config/envs/test.yaml`.

### Ansible Collections

Ansible roles are now organized in collections (see `ansible/CLAUDE.md` for details):

| Collection | Roles | Purpose |
|------------|-------|---------|
| `homestak.debian` | base, users, security, iac_tools | Debian-generic configuration |
| `homestak.proxmox` | install, configure, networking, api_token | PVE-specific roles |

Playbooks use fully qualified collection names (FQCN):
```yaml
roles:
  - homestak.debian.iac_tools
  - homestak.proxmox.api_token
```

### integration Testing Role

**nested-pve** - integration test configuration (in `../ansible/roles/nested-pve/`):

Depends on `homestak.debian.iac_tools` and `homestak.proxmox.api_token`:
- `network.yml` - Configure vmbr0 bridge (required after Debian→PVE conversion)
- `ssh-keys.yml` - Copy SSH keys for nested VM access
- `copy-files.yml` - Sync homestak repos, create API token, configure test env

Synced to inner PVE at `/opt/homestak/`:
- `iac-driver/` - ConfigResolver for recursive deployment
- `site-config/` - Configuration with test.yaml override (node: nested-pve)
- `tofu/` - Modules and environments
- `packer/` - Templates and scripts
- API token created via `pveum` and injected into secrets.yaml

## Prerequisites

- Ansible 2.15+ (via pipx), OpenTofu, Packer with QEMU/KVM
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
