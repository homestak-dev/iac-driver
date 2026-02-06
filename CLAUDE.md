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
│   │   ├── manifest.py        # Manifest schema v1 (levels) + v2 (nodes graph)
│   │   ├── manifest_opr/ # Operator engine for manifest-based orchestration
│   │   │   ├── graph.py       # ExecutionNode, ManifestGraph, topo sort
│   │   │   ├── state.py       # NodeState, ExecutionState persistence
│   │   │   ├── executor.py    # NodeExecutor - walks graph, runs actions
│   │   │   └── cli.py         # create/destroy/test verb handlers
│   │   ├── resolver/     # Configuration resolution
│   │   │   ├── base.py        # Shared FK resolution utilities
│   │   │   ├── spec_resolver.py # Spec loading and FK resolution
│   │   │   └── spec_client.py   # HTTP client for spec fetching
│   │   ├── controller/   # Unified controller daemon
│   │   │   ├── tls.py         # TLS certificate management
│   │   │   ├── auth.py        # Authentication middleware
│   │   │   ├── specs.py       # Spec endpoint handler
│   │   │   ├── repos.py       # Repo endpoint handler
│   │   │   ├── server.py      # Unified HTTPS server
│   │   │   └── cli.py         # serve verb CLI integration
│   │   ├── actions/      # Reusable primitive operations
│   │   │   ├── tofu.py   # TofuApplyAction, TofuDestroyAction
│   │   │   ├── ansible.py# AnsiblePlaybookAction
│   │   │   ├── ssh.py    # SSHCommandAction, WaitForSSHAction
│   │   │   ├── proxmox.py# StartVMAction, WaitForGuestAgentAction
│   │   │   ├── file.py   # DownloadFileAction, RemoveImageAction
│   │   │   ├── recursive.py   # RecursiveScenarioAction
│   │   │   └── pve_lifecycle.py # PVE lifecycle actions (bootstrap, secrets, bridge, etc.)
│   │   ├── scenarios/    # Workflow definitions
│   │   │   ├── pve_setup.py         # pve-setup (local/remote)
│   │   │   ├── user_setup.py        # user-setup (local/remote)
│   │   │   ├── bootstrap.py         # bootstrap-install
│   │   │   └── spec_vm.py           # spec-vm-push-roundtrip
│   │   └── reporting/    # Test report generation (JSON + markdown)
│   ├── reports/          # Generated test reports
│   └── scripts/          # Helper scripts
├── site-config/          # Site-specific secrets and configuration
│   ├── site.yaml         # Site-wide defaults
│   ├── secrets.yaml      # All sensitive values (SOPS encrypted)
│   ├── nodes/            # PVE instance configuration
│   ├── envs/             # Environment configuration (for tofu)
│   └── manifests/        # Recursive scenario manifests
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
4. `v2/postures/{posture}.yaml` - Auth method for spec discovery (v0.45+)

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
    "spec_server": "https://controller:44443",  # v0.45+
    "vms": [
        {
            "name": "test",
            "vmid": 99900,
            "image": "debian-12",
            "cores": 1,
            "memory": 2048,
            "disk": 20,
            "bridge": "vmbr0",
            "auth_token": ""  # v0.45+ - based on posture
        }
    ]
}
```

### Auth Token Resolution (v0.45+)

Per-VM `auth_token` is resolved based on the environment's posture:

| Posture | Auth Method | Token Source |
|---------|-------------|--------------|
| dev/local | `network` | Empty (trust network boundary) |
| stage | `site_token` | `secrets.auth.site_token` |
| prod | `node_token` | `secrets.auth.node_tokens.{vm_name}` |

The auth method is determined by `v2/postures/{posture}.yaml` (not v1 postures).

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

**State Isolation:** Each env+node gets isolated state via explicit `-state` flag:
```
iac-driver/.states/{env}-{node}/terraform.tfstate
```

**Important:** The `-state` flag is required because `TF_DATA_DIR` only affects plugin/module caching, not state file location. Without explicit state isolation, running scenarios on different hosts can cause state conflicts.

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

## Controller Daemon

The unified controller daemon serves both specs and git repos over a single HTTPS endpoint.

### Starting the Controller

```bash
# Start with defaults (port 44443, auto-generated TLS cert)
./run.sh serve

# Custom port and bind address
./run.sh serve --port 8443 --bind 127.0.0.1

# With explicit TLS certificate
./run.sh serve --cert /path/to/cert.pem --key /path/to/key.pem

# With repo token for git access
./run.sh serve --repo-token my-secret-token
```

### Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | None | Health check |
| GET | `/specs` | None | List available specs |
| GET | `/spec/{identity}` | Posture | Fetch resolved spec |
| GET | `/{repo}.git/*` | Bearer | Git dumb HTTP protocol |
| GET | `/{repo}.git/{path}` | Bearer | Raw file extraction |

### Spec Authentication

Spec endpoints use posture-based authentication from `v2/postures/{posture}.yaml`:

| Method | Description | Token Source |
|--------|-------------|--------------|
| `network` | Trust network boundary | None required |
| `site_token` | Shared site-wide token | `secrets.auth.site_token` |
| `node_token` | Per-node unique token | `secrets.auth.node_tokens.{name}` |

### Repo Authentication

All repo endpoints require Bearer token authentication:
```bash
curl -H "Authorization: Bearer <repo-token>" \
  https://controller:44443/bootstrap.git/install.sh
```

### TLS Certificates

The controller auto-generates a self-signed certificate if none provided:
- Certificate stored in temp directory (ephemeral)
- SHA256 fingerprint displayed on startup for verification
- Supports explicit cert/key paths via `--cert` and `--key` flags

### Signal Handling

| Signal | Action |
|--------|--------|
| SIGTERM/SIGINT | Graceful shutdown, cleanup temp repos |
| SIGHUP | Clear resolver cache (reload specs without restart) |

### Git Repo Serving

The controller creates temporary bare repos with uncommitted changes:

1. **Bare clone**: Creates `{repo}.git` from source repo
2. **_working branch**: Contains snapshot of uncommitted changes
3. **update-server-info**: Enables dumb HTTP protocol

Clients can clone or fetch files:
```bash
# Clone via dumb HTTP
git clone https://controller:44443/bootstrap.git

# Fetch raw file (extracts via git show)
curl -H "Authorization: Bearer <token>" \
  https://controller:44443/bootstrap.git/install.sh
```

## Operator Engine (v0.46+)

The operator engine (`manifest_opr/`) walks a v2 manifest graph to execute create/destroy/test lifecycle operations.

### Manifest Schema v2

Schema v2 uses graph-based `nodes[]` with `parent` references instead of linear `levels[]`:

```yaml
schema_version: 2
name: n2-quick
pattern: tiered
nodes:
  - name: root-pve
    type: pve
    preset: vm-large
    image: debian-13-pve
    vmid: 99011
    disk: 64
  - name: edge
    type: vm
    preset: vm-medium
    image: debian-12
    vmid: 99021
    parent: root-pve
```

v2 manifests are backward-compatible: nodes are converted to levels via topological sort for v1 compatibility.

### Verb Commands

```bash
# Create infrastructure from manifest
./run.sh create -M n2-quick -H father [--dry-run] [--json-output] [--verbose]

# Destroy infrastructure
./run.sh destroy -M n2-quick -H father [--dry-run] [--yes]

# Full cycle: create, verify SSH, destroy
./run.sh test -M n2-quick -H father [--dry-run] [--json-output]
```

### Architecture

```
ManifestGraph (graph.py)          NodeExecutor (executor.py)
┌─────────────────────┐           ┌────────────────────────┐
│ build from manifest │           │ walks graph in order   │
│ create_order() BFS  │──────────▶│ TofuApplyInlineAction  │
│ destroy_order() rev │           │ StartVMAction          │
│ get_parent_ip_key() │           │ WaitForGuestAgentAction│
└─────────────────────┘           │ WaitForSSHAction       │
                                  └────────────────────────┘
ExecutionState (state.py)                    │
┌─────────────────────┐                      │
│ per-node status     │◄─────────────────────┘
│ vm_id, ip tracking  │
│ save/load JSON      │
└─────────────────────┘
```

### Error Handling

The `on_error` setting controls behavior when a node fails:

| Mode | Behavior |
|------|----------|
| `stop` | Halt immediately (default) |
| `rollback` | Destroy already-created nodes, then halt |
| `continue` | Skip failed node, continue with independent nodes |

### Delegation Model

The operator handles root nodes (depth 0) locally. PVE nodes with children trigger:
1. PVE lifecycle setup (bootstrap, secrets, bridge, API token, image download)
2. Subtree delegation via SSH — extracts child nodes as a new manifest, runs `./run.sh create --manifest-json` on the inner PVE

This recursion handles arbitrary depth (N=2, N=3, etc.) without depth limits.

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

## Manifest-Driven Orchestration

Manifests define N-level nested PVE deployments. Schema v2 (graph-based) is the primary format; v1 (linear levels) is supported for backward compatibility. Manifests are YAML files in `site-config/manifests/`.

### Usage

```bash
# Create infrastructure from manifest
./run.sh create -M n2-quick -H father

# Destroy infrastructure
./run.sh destroy -M n2-quick -H father --yes

# Full roundtrip: create, verify SSH, destroy
./run.sh test -M n2-quick -H father

# Dry-run preview
./run.sh create -M n2-quick -H father --dry-run

# JSON output for programmatic use
./run.sh test -M n1-basic -H father --json-output
```

### RecursiveScenarioAction

The `RecursiveScenarioAction` executes commands on remote hosts via SSH with PTY streaming. Used by the operator for subtree delegation:

```python
RecursiveScenarioAction(
    name='delegate-subtree',
    raw_command='cd /usr/local/lib/homestak/iac-driver && ./run.sh create --manifest-json \'...\' -H hostname --json-output',
    host_attr='pve_ip',
    context_keys=['edge_ip', 'edge_vm_id'],
    timeout=1200,
)
```

Key features:
- Real-time output streaming via PTY allocation
- JSON result parsing from `--json-output` commands
- Context key extraction for parent operator consumption
- `raw_command` field for verb delegation (v0.47+); `scenario_name` field for legacy scenario execution

## Naming Conventions

### Scenarios, Phases, and Actions

| Type | Pattern | Examples |
|------|---------|----------|
| **Scenarios** | `noun-verb` | `pve-setup`, `user-setup`, `bootstrap-install`, `spec-vm-push-roundtrip` |
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
class PVESetup:
    name = 'pve-setup'

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

## Host Resolution (v0.36+)

The `--host` flag resolves configuration from site-config with fallback:

| Priority | Path | Use Case |
|----------|------|----------|
| 1 | `nodes/{host}.yaml` | PVE node with API access |
| 2 | `hosts/{host}.yaml` | Physical machine, SSH-only (pre-PVE) |

**Pre-PVE Host Provisioning:**

When provisioning a fresh Debian host that doesn't have PVE yet:

1. Create `hosts/{hostname}.yaml` (or run `make host-config` on the target)
2. Run `./run.sh --scenario pve-setup --host {hostname}`
3. After PVE install, `nodes/{hostname}.yaml` is auto-generated
4. Host is now usable for `./run.sh create` and other PVE scenarios

**HostConfig.is_host_only:**

When loaded from `hosts/*.yaml`, the `HostConfig` object has `is_host_only=True` and PVE-specific fields (`api_endpoint`, `api_token`) are empty. Scenarios can check this flag to handle pre-PVE hosts differently.

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

**Provider Lockfile Mismatch**: When provider version constraints change (e.g., Dependabot updates `providers.tf`), cached lockfiles in `.states/*/data/.terraform.lock.hcl` can have stale versions, causing `tofu init` to fail with "does not match configured version constraint". **Resolution**: Preflight checks now auto-detect and clear stale lockfiles. Manual fix: `rm -rf .states/*/data/.terraform.lock.hcl`.

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
| `AnsiblePlaybookAction` | timeout | 600s | Playbook execution |
| `AnsiblePlaybookAction` | ssh_timeout | 60s | Pre-playbook SSH wait |
| `WaitForSSHAction` | timeout | 60s | SSH availability |
| `WaitForSSHAction` | interval | 5s | Retry interval |
| `WaitForGuestAgentAction` | timeout | 300s | Guest agent |
| `WaitForGuestAgentAction` | interval | 5s | Retry interval |
| `SSHCommandAction` | timeout | 60s | Single SSH command |
| `DownloadGitHubReleaseAction` | timeout | 300s | Asset download |
| `VerifySSHChainAction` | timeout | 60s | Jump host verification |
| `RecursiveScenarioAction` | timeout | 1200s | Subtree delegation via SSH |

### Operator PVE Lifecycle Timeouts

| Phase | Timeout | Rationale |
|-------|---------|-----------|
| wait_ip | 300s | Guest agent can be slow on first boot |
| bootstrap | 600s | curl\|bash installer on remote host |
| pve-setup (delegation) | 1200s | PVE install includes apt, kernel, reboot |
| configure_bridge | 120s | Network bridge configuration |
| download_images | 300s | ~200MB image from GitHub |
| subtree_delegation | 1200s | SSH to inner PVE, run operator |

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

The orchestrator supports two command styles: verb commands for manifest-based orchestration, and `--scenario` for standalone workflows.

```bash
# Verb commands (manifest-based)
./run.sh create -M n2-quick -H father              # Create infrastructure
./run.sh destroy -M n2-quick -H father --yes        # Destroy infrastructure
./run.sh test -M n2-quick -H father                 # Full roundtrip

# Scenarios (standalone workflows)
./run.sh --scenario pve-setup --local                   # Install + configure PVE
./run.sh --scenario pve-setup --remote 10.0.12.x        # Remote PVE setup
./run.sh --scenario user-setup --local                  # Create homestak user
./run.sh --scenario bootstrap-install --vm-ip 10.0.12.x # Test bootstrap
./run.sh --list-scenarios                                # List available scenarios
```

**CLI Options:**
| Option | Description |
|--------|-------------|
| `--version` | Show CLI version |
| `--scenario`, `-S` | Scenario to run (required) |
| `--host`, `-H` | Target PVE host (required for most scenarios) |
| `--env`, `-E` | Environment to deploy (overrides scenario default) |
| `--context-file`, `-C` | Save/load context for chained runs |
| `--verbose`, `-v` | Enable verbose logging |
| `--skip`, `-s` | Phases to skip (repeatable) |
| `--list-scenarios` | List available scenarios |
| `--list-phases` | List phases for selected scenario |
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
| `--json-output` | Output structured JSON to stdout (logs to stderr) |
| `--manifest`, `-M` | Manifest name from site-config/manifests/ (for verb commands) |
| `--manifest-file` | Path to manifest file (for verb commands) |
| `--manifest-json` | Inline manifest JSON (for delegation) |

**JSON Output (v0.38+):**

The `--json-output` flag emits structured JSON for programmatic consumption:

```bash
./run.sh test -M n1-basic -H father --json-output 2>/dev/null | jq .
```

Output schema:
```json
{
  "scenario": "test",
  "success": true,
  "duration_seconds": 45.2,
  "phases": [
    {"name": "ensure_image", "status": "passed", "duration": 0.2},
    {"name": "provision", "status": "passed", "duration": 6.8}
  ],
  "context": {
    "vm_ip": "10.0.12.155",
    "vm_id": 99900
  }
}
```

| Field | Description |
|-------|-------------|
| `scenario` | Scenario name |
| `success` | Boolean result |
| `duration_seconds` | Total runtime |
| `phases[]` | Phase results (name, status, duration) |
| `context` | Collected values (IPs, IDs, etc.) |
| `error` | Error message (on failure only) |

**Preflight Checks:**

Preflight checks validate host prerequisites before running scenarios:

```bash
# Standalone preflight check (local)
./run.sh --preflight --local

# Standalone preflight check (remote)
./run.sh --preflight --host mother

# Skip preflight for faster iteration
./run.sh --scenario pve-setup --host father --skip-preflight
```

Checks include:
- Bootstrap installation (core repos present)
- site-init completion (secrets.yaml decrypted, node config exists)
- PVE API connectivity and token validity
- Provider lockfile sync (auto-clears stale lockfiles in `.states/`)
- Nested virtualization (for tiered manifests)

**Packer Release:**

The packer release tag for image downloads is resolved in this order (first match wins):
1. CLI: `--packer-release v0.8.0-rc1`
2. site.yaml: `defaults.packer_release: v0.8.0-rc1`
3. Default: `latest` (points to most recent packer release with images)

The `latest` tag is maintained by the packer release process (see packer#5).

**Split File Handling:** Large images (>2GB) are split into parts on GitHub releases (e.g., `debian-13-pve.qcow2.partaa`, `.partab`). `DownloadGitHubReleaseAction` automatically detects split files, downloads all parts, reassembles them, and cleans up.

**Available Scenarios:**
| Scenario | Runtime | Description |
|----------|---------|-------------|
| `bootstrap-install` | ~2m | Run bootstrap, verify installation and user |
| `packer-build` | ~3m | Build packer images (local or remote) |
| `packer-build-fetch` | ~5m | Build remotely, fetch to local |
| `packer-build-publish` | ~7m | Build and publish to PVE storage |
| `packer-sync` | ~30s | Sync local packer to remote |
| `packer-sync-build-fetch` | ~6m | Sync, build, fetch (dev workflow) |
| `pve-setup` | ~3m | Install PVE (if needed), configure host, generate node config |
| `spec-vm-push-roundtrip` | ~3m | Spec discovery integration test (push verification) |
| `user-setup` | ~30s | Create homestak user |

**Retired Scenarios (v0.47):** `vm-constructor`, `vm-destructor`, `vm-roundtrip`, `nested-pve-*`, `recursive-pve-*` — replaced by verb commands (`create`/`destroy`/`test`). Running a retired scenario prints a migration hint.

Runtime estimates are shown by `--list-scenarios` and used for `--timeout` defaults.

### Test Reports

Reports are generated in `reports/` with format: `YYYYMMDD-HHMMSS.{passed|failed}.{md|json}`

Both JSON and markdown reports are generated for each run.

### Helper Scripts

| Script | Purpose |
|--------|---------|
| `scripts/wait-for-guest-agent.sh` | Poll for VM IP (used by orchestrator) |
| `scripts/setup-tools.sh` | Clone/update tool repos (ansible, tofu, packer, site-config) |

All helper scripts support `--help` for usage information.

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
