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

```
<parent>/
├── iac-driver/           # This repo - Infrastructure orchestration
│   ├── run.sh            # CLI entry point (bash wrapper)
│   ├── src/              # Python package
│   │   ├── cli.py        # CLI implementation
│   │   ├── common.py     # ActionResult + shared utilities
│   │   ├── config.py          # Host configuration (auto-discovery from site-config)
│   │   ├── config_apply.py    # Config phase: spec-to-ansible-vars + apply
│   │   ├── config_resolver.py # ConfigResolver - resolves site-config for tofu
│   │   ├── manifest.py        # Manifest schema v2 (nodes graph)
│   │   ├── manifest_opr/ # Operator engine for manifest-based orchestration
│   │   │   ├── graph.py       # ExecutionNode, ManifestGraph, topo sort
│   │   │   ├── state.py       # NodeState, ExecutionState persistence
│   │   │   ├── executor.py    # NodeExecutor - walks graph, runs actions
│   │   │   └── cli.py         # create/destroy/test verb handlers
│   │   ├── resolver/     # Configuration resolution
│   │   │   ├── base.py        # Shared FK resolution utilities
│   │   │   ├── spec_resolver.py # Spec loading and FK resolution
│   │   │   └── spec_client.py   # HTTP client for spec fetching
│   │   ├── server/      # Server daemon
│   │   │   ├── tls.py         # TLS certificate management
│   │   │   ├── auth.py        # Authentication middleware
│   │   │   ├── specs.py       # Spec endpoint handler
│   │   │   ├── repos.py       # Repo endpoint handler
│   │   │   ├── httpd.py       # HTTPS server
│   │   │   ├── daemon.py      # Double-fork daemonization, PID management
│   │   │   └── cli.py         # server start/stop/status CLI
│   │   ├── actions/      # Reusable primitive operations
│   │   │   ├── tofu.py   # TofuApplyAction, TofuDestroyAction
│   │   │   ├── ansible.py# AnsiblePlaybookAction
│   │   │   ├── ssh.py    # SSHCommandAction, WaitForSSHAction, WaitForFileAction
│   │   │   ├── proxmox.py# StartVMAction, WaitForGuestAgentAction
│   │   │   ├── file.py   # DownloadFileAction, RemoveImageAction
│   │   │   ├── recursive.py   # RecursiveScenarioAction
│   │   │   └── pve_lifecycle.py # PVE lifecycle actions (bootstrap, secrets, bridge, etc.)
│   │   ├── scenarios/    # Workflow definitions
│   │   │   ├── pve_setup.py         # pve-setup (local/remote)
│   │   │   ├── user_setup.py        # user-setup (local/remote)
│   │   │   └── vm_roundtrip.py       # push-vm-roundtrip, pull-vm-roundtrip
│   │   └── reporting/    # Test report generation (JSON + markdown)
│   ├── reports/          # Generated test reports
│   └── scripts/          # Helper scripts
├── site-config/          # Site-specific secrets and configuration
├── ansible/              # Tool repo (sibling)
├── tofu/                 # Tool repo (sibling)
└── packer/               # Tool repo (sibling)
```

## ConfigResolver

The `ConfigResolver` class resolves site-config YAML files into flat configurations for tofu and ansible. All template, preset, and posture inheritance is resolved in Python, so consumers receive fully-computed values.

### Usage

```python
from src.config_resolver import ConfigResolver

resolver = ConfigResolver()  # Auto-discover site-config

# Resolve inline VM for tofu
config = resolver.resolve_inline_vm(
    node='father', vm_name='test', vmid=99900,
    vm_preset='vm-small', image='debian-12'
)
resolver.write_tfvars(config, '/tmp/tfvars.json')

# Resolve ansible vars from posture
ansible_vars = resolver.resolve_ansible_vars('dev')
resolver.write_ansible_vars(ansible_vars, '/tmp/ansible-vars.json')
```

### Resolution Order (Tofu)

1. `presets/{vm_preset}.yaml` - VM size presets (cores, memory, disk)
2. Inline VM overrides (name, vmid, image) from manifest nodes or CLI
3. `postures/{posture}.yaml` - Auth method for spec discovery

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
    "spec_server": "https://father:44443",
    "vms": [
        {
            "name": "test",
            "vmid": 99900,
            "image": "debian-12",
            "cores": 1,
            "memory": 2048,
            "disk": 20,
            "bridge": "vmbr0",
            "auth_token": ""  # HMAC-signed provisioning token
        }
    ]
}
```

Per-VM `auth_token` is an HMAC-SHA256 provisioning token minted by `ConfigResolver._mint_provisioning_token()` when both `spec_server` and `spec` are set. See [provisioning-token.md](../docs/designs/provisioning-token.md) for token format, signing, and verification.

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

The `-state` flag is required because `TF_DATA_DIR` only affects plugin/module caching, not state file location.

**Context Passing:** TofuApplyAction extracts VM IDs from resolved config and adds them to context:
```python
context['test_vm_id'] = 99900
context['provisioned_vms'] = [{'name': 'test', 'vmid': 99900}, ...]
```

**Multi-VM Actions:** `StartProvisionedVMsAction` and `WaitForProvisionedVMsAction` operate on all VMs from `provisioned_vms` context. After completion, context contains `{vm_name}_ip` for each VM and `vm_ip` for backward compatibility.

## Server Daemon

The server daemon serves specs and git repos over HTTPS. See [server-daemon.md](../docs/designs/server-daemon.md) for architecture, double-fork daemonization, PID management, and operator lifecycle integration.

### Management

```bash
./run.sh server start                    # Start as daemon
./run.sh server start --repos --repo-token <token>  # With repo serving
./run.sh server start --foreground       # Development mode
./run.sh server status [--json]          # Check status
./run.sh server stop                     # Stop daemon
```

PID file: `/var/run/homestak/server.pid` | Log file: `/var/log/homestak/server.log`

Operator (executor.py) auto-manages server lifecycle for manifest verbs with reference counting.

### Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | None | Health check |
| GET | `/specs` | None | List available specs |
| GET | `/spec/{identity}` | Provisioning token | Fetch resolved spec |
| GET | `/{repo}.git/*` | Bearer | Git dumb HTTP protocol |
| GET | `/{repo}.git/{path}` | Bearer | Raw file extraction |

Spec endpoints authenticate via HMAC-signed provisioning tokens. See [provisioning-token.md](../docs/designs/provisioning-token.md).

Auto-generates self-signed TLS certificate if none provided via `--cert`/`--key`.

## Operator Engine

The operator engine (`manifest_opr/`) walks a v2 manifest graph to execute create/destroy/test lifecycle operations. See [node-orchestration.md](../docs/designs/node-orchestration.md) for topology patterns and execution model comparison.

### Manifest Schema v2

```yaml
schema_version: 2
name: n2-tiered
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
    preset: vm-small
    image: debian-12
    vmid: 99021
    parent: root-pve
    execution:
      mode: pull  # Default: push
```

### Noun-Action Commands

```bash
./run.sh manifest apply -M n2-tiered -H father [--dry-run] [--json-output] [--verbose]
./run.sh manifest destroy -M n2-tiered -H father [--dry-run] [--yes]
./run.sh manifest test -M n2-tiered -H father [--dry-run] [--json-output]
./run.sh config fetch [--insecure]
./run.sh config apply [--spec /path.yaml] [--dry-run]
```

### Error Handling

| Mode | Behavior |
|------|----------|
| `stop` | Halt immediately (default) |
| `rollback` | Destroy already-created nodes, then halt |
| `continue` | Skip failed node, continue with independent nodes |

### Delegation Model

Root nodes (depth 0) are handled locally. PVE nodes with children trigger:
1. PVE lifecycle setup (bootstrap, secrets, bridge, API token, image download)
2. Subtree delegation via SSH — `./run.sh manifest apply --manifest-json` on inner PVE

This recursion handles arbitrary depth without limits.

### Execution Modes

Nodes use **push** (default) or **pull** for config phase. See [config-phase.md](../docs/designs/config-phase.md) for spec-to-ansible mapping and implementation details.

| Mode | How Config Runs | Operator Behavior |
|------|----------------|-------------------|
| `push` | Driver SSHes in and runs config | Default, used for PVE lifecycle |
| `pull` | VM self-configures via cloud-init | Operator polls for config-complete.json |

PVE nodes always use push regardless of setting.

## Manifest-Driven Orchestration

Manifests define N-level nested PVE deployments using graph-based schema v2. Manifests are YAML files in `site-config/manifests/`.

```bash
./run.sh manifest apply -M n2-tiered -H father
./run.sh manifest destroy -M n2-tiered -H father --yes
./run.sh manifest test -M n2-tiered -H father
./run.sh manifest apply -M n2-tiered -H father --dry-run
./run.sh manifest test -M n1-push -H father --json-output
```

`RecursiveScenarioAction` executes commands on remote hosts via SSH with PTY streaming. Used by the operator for subtree delegation. Supports `raw_command` for verb delegation and `scenario_name` for legacy scenarios. Extracts context keys from `--json-output` results.

## Naming Conventions

### Scenarios, Phases, and Actions

| Type | Pattern | Examples |
|------|---------|----------|
| **Scenarios** | `noun-verb` | `pve-setup`, `user-setup`, `push-vm-roundtrip` |
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

## Conventions

- **VM IDs**: 5-digit (10000+ dev, 20000+ k8s)
- **MAC prefix**: BC:24:11:*
- **Hostnames**: `{cluster}{instance}` (dev1, router, kubeadm1)
- **Cloud-init files**: `{hostname}-meta.yaml`, `{hostname}-user.yaml`
- **Environments**: dev (permissive SSH, passwordless sudo) vs prod (strict SSH, fail2ban)

## Host Resolution (v0.36+)

The `--host` flag resolves configuration from site-config with fallback:

| Priority | Path | Use Case |
|----------|------|----------|
| 1 | `nodes/{host}.yaml` | PVE node with API access |
| 2 | `hosts/{host}.yaml` | Physical machine, SSH-only (pre-PVE) |

**Pre-PVE Host Provisioning:**

1. Create `hosts/{hostname}.yaml` (or run `make host-config` on the target)
2. Run `./run.sh --scenario pve-setup --host {hostname}`
3. After PVE install, `nodes/{hostname}.yaml` is auto-generated
4. Host is now usable for `./run.sh create` and other PVE scenarios

`HostConfig.is_host_only` is `True` when loaded from `hosts/*.yaml` (PVE-specific fields are empty).

## Node Configuration

PVE node configuration is stored in `site-config/nodes/*.yaml`. Filename must match the actual PVE node name (`pvesh get /nodes`).

API tokens are stored separately in `site-config/secrets.yaml` and resolved by key reference:
```yaml
# nodes/father.yaml
host: father                      # FK -> hosts/father.yaml
api_endpoint: https://10.0.12.61:8006
api_token: father                 # FK -> secrets.api_tokens.father
```

**Configuration Merge Order:** `site.yaml` → `nodes/{node}.yaml` → `secrets.yaml`

## CLI Reference

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

### Commands

Run `./run.sh` with no arguments for top-level usage, or `./run.sh scenario --help` for scenario list.

```bash
# Manifest commands (infrastructure lifecycle)
./run.sh manifest apply -M n2-tiered -H father
./run.sh manifest destroy -M n2-tiered -H father --yes
./run.sh manifest test -M n2-tiered -H father

# Config commands (spec fetch and apply)
./run.sh config fetch --insecure
./run.sh config apply

# Scenario commands (standalone workflows)
./run.sh scenario run pve-setup --local
./run.sh scenario run user-setup --local

# Preflight checks
./run.sh --preflight --host father
```

Use `--json-output` for structured JSON to stdout (logs to stderr). Use `--dry-run` to preview without executing. Use `--verbose` for detailed logging.

### Available Scenarios

| Scenario | Runtime | Description |
|----------|---------|-------------|
| `pve-setup` | ~3m | Install PVE (if needed), configure host, generate node config |
| `user-setup` | ~30s | Create homestak user |
| `push-vm-roundtrip` | ~3m | Spec discovery integration test (push verification) |
| `pull-vm-roundtrip` | ~5m | Config phase integration test (pull verification) |

### Test Reports

Reports are generated in `reports/` with format: `YYYYMMDD-HHMMSS.{passed|failed}.{md|json}`

### Timeouts

Operations use tiered timeouts (Quick: 5-30s through Extended: 1200s). Defaults are defined in `src/actions/*.py` and `src/common.py`. Override per-action in scenario definitions when needed.

### Claude Code Autonomy

For fully autonomous integration test runs, add these to Claude Code allowed tools:
```
Bash(ansible-playbook:*), Bash(ansible:*), Bash(rsync:*)
```

## Prerequisites

- Ansible 2.15+ (via pipx), OpenTofu, Packer with QEMU/KVM
- SSH key at `~/.ssh/id_rsa`
- age + sops for secrets decryption (see `make setup`)
- age key at `~/.config/sops/age/keys.txt`
- Nested virtualization enabled (`cat /sys/module/kvm_intel/parameters/nested` = Y)

## Development Setup

```bash
make install-dev   # Creates .venv/, installs linters + runtime deps, hooks
make test          # Run unit tests (558 tests)
make lint          # Run pre-commit hooks (pylint, mypy)
```

Uses a `.venv/` virtual environment for PEP 668 compatibility (Debian 12+). Pre-commit hooks run pylint and mypy on staged Python files automatically on `git commit`.

## Design Documents

Detailed architecture and design rationale:

| Document | Covers |
|----------|--------|
| [node-orchestration.md](../docs/designs/node-orchestration.md) | Topology patterns, execution models, system test catalog |
| [server-daemon.md](../docs/designs/server-daemon.md) | Daemon architecture, PID management, operator integration |
| [config-phase.md](../docs/designs/config-phase.md) | Push/pull execution, spec-to-ansible mapping |
| [provisioning-token.md](../docs/designs/provisioning-token.md) | HMAC token format, signing, verification |
| [scenario-consolidation.md](../docs/designs/scenario-consolidation.md) | Scenario migration, PVE lifecycle phases |
| [node-lifecycle.md](../docs/designs/node-lifecycle.md) | Single-node lifecycle (create/config/run/destroy) |
| [test-strategy.md](../docs/designs/test-strategy.md) | Test hierarchy, system test catalog (ST-1 through ST-8) |

## Tool Documentation

Each tool repo has its own CLAUDE.md with detailed context:
- `../bootstrap/CLAUDE.md` - curl|bash installer and homestak CLI
- `../site-config/CLAUDE.md` - Secrets management and encryption
- `../ansible/CLAUDE.md` - Ansible-specific commands and structure
- `../tofu/CLAUDE.md` - OpenTofu modules and environment details
