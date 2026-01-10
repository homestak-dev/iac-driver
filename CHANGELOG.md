# Changelog

## v0.13 - 2026-01-10

### Features

- Add `resolve_ansible_vars()` to ConfigResolver
  - Resolves site-config postures to ansible-compatible variables
  - Merges packages from site.yaml + posture (deduplicated)
  - Outputs timezone, SSH settings, sudo, fail2ban config
- Add `readiness.py` module with pre-flight checks:
  - `validate_api_token()` - Test PVE API token before tofu runs
  - `validate_host_available()` - Check host reachability via SSH
  - `validate_host_resolvable()` - Verify DNS resolution

### Testing

- Add `conftest.py` with shared pytest fixtures
  - `site_config_dir` - Temporary site-config structure
  - `mock_config_resolver` - Pre-configured resolver for tests
- Add tests for `resolve_ansible_vars()` (posture loading, package merging)
- Add tests for readiness checks (API validation, host checks)

### Code Quality

- Add `.pre-commit-config.yaml` for pylint/mypy hooks
- Add `mypy.ini` configuration
- Update Makefile with `lint` and `install-hooks` targets

### Documentation

- Document `resolve_ansible_vars()` in CLAUDE.md
- Add ansible output structure example

## v0.12 - 2025-01-09

- Release alignment with homestak-dev v0.12

## v0.11 - 2026-01-08

### Code Quality

- Improve pylint score from 8.31 to 9.58/10
- Fix all mypy type errors (8 → 0)
- Add `.pylintrc` configuration with project-specific rules
- Add encoding parameter to all `open()` calls
- Add explicit `check=False` to `subprocess.run()` calls
- Fix unused variables by using `_` convention
- Remove unused imports across action modules

### Refactoring

- Rename `test_ip` context key to `leaf_ip` for generalized naming (closes #68)
  - Better describes position in nesting hierarchy (leaf = innermost VM)
  - Prepares for v0.20 recursive nested PVE architecture

### Security

- Add confirmation prompt for destructive scenarios (closes #65)
  - `vm-destructor` and `nested-pve-destructor` now require confirmation
  - New `--yes`/`-y` flag to skip prompt (for automation/CI)
  - Destructive scenarios have `requires_confirmation = True` attribute

### Testing

- Add ConfigResolver test suite (16 tests):
  - IP validation (CIDR format, dhcp, None, bare IP rejection)
  - VM resolution with preset/template inheritance
  - vmid allocation (base + index, explicit override)
  - tfvars.json generation
  - list_envs/templates/presets methods
- Add action test suite (11 tests):
  - SSHCommandAction success/failure/missing context
  - WaitForSSHAction with mocked SSH
  - StartVMAction with mocked Proxmox
  - ActionResult dataclass defaults
- Add confirmation requirement tests (4 tests)
- Total tests: 30 → 61 (+31 tests)

### Documentation

- Update CLAUDE.md with `--yes`/`-y` flag
- Update context key documentation (`test_ip` → `leaf_ip`)

## v0.10 - 2026-01-08

### Documentation

- Update terminology: E2E → integration testing throughout
- Fix CLAUDE.md: correct CLI help text (pve-configure → pve-setup)

### CI/CD

- Add GitHub Actions workflow for pylint

### Housekeeping

- Enable secret scanning and Dependabot

## v0.9 - 2026-01-07

### Features

- Add scenario annotations for declarative behavior (closes #58, #60)
  - `requires_root`: Scenarios can declare root requirement for `--local` mode
  - `requires_host_config`: Scenarios can opt out of requiring `--host`
  - `expected_runtime`: Runtime estimates shown in `--list-scenarios`
- Add `--timeout`/`-t` flag for scenario-level timeout (closes #33)
  - Checks elapsed time before each phase
  - Fails gracefully if timeout exceeded (does not interrupt running phases)
- Add host auto-detection for `--local` mode (closes #58)
  - When `--local` specified without `--host`, detects from hostname
  - Works when hostname matches a configured node name
- Add runtime estimates to `--list-scenarios` output (closes #33)
  - Shows `~Nm` or `~Ns` format next to each scenario
  - All 14 scenarios now have `expected_runtime` attribute

### Changes

- Scenarios without host config requirement no longer need `--host`:
  - `pve-setup`, `user-setup`: Can auto-detect or use `--remote`
  - All packer scenarios: Work with `--local` or `--remote`
- Orchestrator now logs total scenario duration on completion

### Testing

- Add unit test suite (`tests/`) with 30 tests covering:
  - Scenario attributes (requires_root, requires_host_config, expected_runtime)
  - CLI integration (auto-detect, timeout flag, list-scenarios)
  - All scenarios have required attributes

### Documentation

- Update CLAUDE.md Available Scenarios table with Runtime column
- Add `--timeout` to CLI Options table
- Document scenario annotation system in Protocol docstring

## v0.8 - 2026-01-06

### Features

- Add `--context-file`/`-C` flag for scenario chaining (closes #38)
  - Persist VM IDs and IPs between constructor/destructor runs
  - Enables split workflows without `--inner-ip` workarounds
- Add `--packer-release` flag for image version override (closes #39)
  - Defaults to `latest` tag (maintained by packer release process)
  - Override with specific version: `--packer-release v0.7`
- Add CIDR validation for static IP configuration (closes #35)
  - ConfigResolver validates IPs use CIDR notation (e.g., `10.0.12.100/24`)
  - Catches misconfiguration before tofu/cloud-init errors

### Changes

- Remove default `--host` value (closes #36)
  - CLI now requires explicit `--host` for scenarios that need it
  - Prevents accidental operations on wrong host
- Harmonize and reduce timeout defaults (closes #34)
  - `TofuApplyAction.timeout_apply`: 600s → 300s
  - `wait_for_ssh()`: 300s → 60s
  - `WaitForSSHAction.timeout`: 120s → 60s
  - SSH waits now consistent across actions

### Bug Fixes

- Fix context loss between constructor and destructor runs (closes #37)
  - Resolved by `--context-file` feature

### Documentation

- Add Timeout Configuration section to CLAUDE.md
- Document `--context-file` usage patterns
- Document packer release resolution order

## v0.7 - 2026-01-06

### Features

- Pass `gateway` through ConfigResolver for static IP configurations (closes #30)

### Changes

- Move state storage from `tofu/.states/` to `iac-driver/.states/` (closes #29)
  - State now lives alongside the orchestrator that manages it
  - Each env+node gets isolated state: `.states/{env}-{node}/terraform.tfstate`
- Update docs: replace deprecated `pve` with real node names (`father`, `mother`)

### Documentation

- Fix state storage path in CLAUDE.md

## v0.6 - 2026-01-06

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

### Housekeeping

- Rename `pve-deb` to `nested-pve` across codebase (closes #13)
- Update documentation for ansible collection structure (closes #19)
  - Roles now in `homestak.debian` and `homestak.proxmox` collections
  - Playbooks use FQCN (e.g., `homestak.debian.iac_tools`)

### Bug Fixes

- **OpenTofu state version 4 workaround**: Separate `TF_DATA_DIR` (data/) from state file location to avoid legacy code path that rejects v4 states. See [opentofu/opentofu#3643](https://github.com/opentofu/opentofu/issues/3643)
- **rsync fallback**: `SyncReposToVMAction` now uses tar pipe when rsync unavailable on target
- **VM ID context passing**: `TofuApplyRemoteAction` resolves config locally to extract VM IDs for downstream actions

### Packer Build Scenarios (closes #25)

- Add `packer-build` scenarios for remote image builds
  - `packer-build`: Build images locally or remotely
  - `packer-build-fetch`: Build on remote, fetch to local (for releases)
  - `packer-build-publish`: Build and publish to PVE storage
  - `packer-sync`: Sync local packer repo to remote
  - `packer-sync-build-fetch`: Dev workflow (sync, build, fetch)
- Add `--templates` CLI flag for building specific templates
- Add `--local`/`--remote` support for packer scenarios
- Prerequisites: Remote host must be bootstrapped with `homestak install packer`

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
