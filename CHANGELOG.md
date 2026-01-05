# Changelog

## Unreleased

### Phase 5: ConfigResolver

- Add `ConfigResolver` class for site-config YAML resolution
- Resolves vms/presets, vms/templates, envs with inheritance
- vmid auto-allocation: vmid_base + index (or null for PVE auto-assign)
- Generates flat tfvars.json for tofu (replaces config-loader)
- Integrate ConfigResolver into tofu actions:
  - `TofuApplyAction` / `TofuDestroyAction` - local execution with ConfigResolver
  - `TofuApplyRemoteAction` / `TofuDestroyRemoteAction` - recursive pattern via SSH
- State isolation via explicit `-state` flag per env+node
- Update scenarios to use `env_name` instead of `env_path`
- Pass VM IDs from tofu actions to context for downstream actions
- Update proxmox actions to check context first, then config (dynamic VMID support)
- Add `env` parameter to `run_command()` for environment variable passthrough
- Add `--env`/`-E` CLI flag to override scenario environment
- Add `StartProvisionedVMsAction` and `WaitForProvisionedVMsAction` for multi-VM environments
- TofuApplyAction now adds `provisioned_vms` list to context for downstream actions
- E2E validated with nested-pve-roundtrip on father (~8.5 min)
- Tested `ansible-test` environment with Debian 12 + 13 VMs on father

### Bug Fixes

- **OpenTofu state version 4 workaround**: Separate `TF_DATA_DIR` (data/) from state file location to avoid legacy code path that rejects v4 states. See [opentofu/opentofu#3643](https://github.com/opentofu/opentofu/issues/3643)
- **rsync fallback**: `SyncReposToVMAction` now uses tar pipe when rsync unavailable on target
- **VM ID context passing**: `TofuApplyRemoteAction` resolves config locally to extract VM IDs for downstream actions

## v0.5.0-rc1 - 2026-01-04

Consolidated pre-release with full YAML configuration support.

### Highlights

- YAML configuration via site-config (nodes/*.yaml, secrets.yaml)
- config-loader integration with tofu
- Full E2E validation with nested-pve-roundtrip

### Changes

- Fix ansible.posix.synchronize for /opt/homestak path
- Fix API token creation idempotency in nested-pve role
- Reduce polling intervals for faster E2E tests

## v0.4.0 - 2026-01-04

### Features

- YAML configuration support via site-config
  - Node config from `site-config/nodes/*.yaml`
  - Secrets from `site-config/secrets.yaml` (resolved by key reference)
  - Configuration merge order: site.yaml → nodes/{node}.yaml → secrets.yaml
- Pass `node` and `site_config_path` vars to tofu for config-loader module

### Changes

- Switch from tfvars to YAML configuration parsing

## v0.3.0 - 2026-01-04

### Features

- Add `pve-configure` scenario for PVE host configuration (runs pve-setup.yml + user.yml)
- Add `AnsibleLocalPlaybookAction` for local playbook execution
- Add `--local` and `--remote` CLI flags for execution mode
- Add configurable `ssh_user` for non-root SSH access (with sudo)

### Changes

- **BREAKING**: Move secrets to [site-config](https://github.com/homestak-dev/site-config) repository
- Host discovery now reads from `site-config/nodes/*.yaml`
- Remove in-repo SOPS encryption (Makefile, .githooks, .sops.yaml)

## v0.1.0-rc1 - 2026-01-03

### Features

- Modular scenario architecture with reusable actions
- Actions: tofu, ansible, ssh, proxmox, file operations
- Scenarios: simple-vm-*, nested-pve-* (constructor/destructor/roundtrip)
- CLI with --scenario, --host, --skip, --list-scenarios, --list-phases
- JSON + Markdown test report generation
- Auto-discovery of hosts from secrets/*.tfvars

### Infrastructure

- Branch protection enabled (PR reviews for non-admins)
- Dependabot for dependency updates
- secrets-check workflow for encrypted credentials
