# Changelog

## Unreleased

## v0.45 - 2026-02-02

### Theme: Create Integration

Integrates Create phase with Specify mechanism for automatic spec discovery on first boot.

### Added
- Add `spec-vm-roundtrip` scenario for Create → Specify validation (#154)
  - Verifies spec_server env vars injected via cloud-init
  - Tests VM connectivity to spec server
  - Full roundtrip: provision → verify → destroy
- Add `spec_server` to ConfigResolver output for Create → Specify flow (#154)
  - Reads from `site.yaml` defaults.spec_server
  - Included in tfvars.json for tofu cloud-init injection
- Add per-VM `auth_token` resolution based on posture (#154)
  - Loads v2/postures for `auth.method` (network, site_token, node_token)
  - Resolves tokens from `secrets.yaml` auth section
  - Added to each VM in vms[] list for cloud-init injection
- Add `posture` parameter to `resolve_inline_vm()` for manifest-driven scenarios

### Changed
- Add serve command availability check to `StartSpecServerAction` (#154)
  - Verifies `homestak serve` exists before attempting to start
  - Provides clear error message with upgrade instructions for older installations

## v0.44 - 2026-02-02

- Release alignment with homestak v0.44

## v0.43 - 2026-02-01

- Release alignment with homestak v0.43

## v0.42 - 2026-01-31

- Release alignment with homestak v0.42

## v0.41 - 2026-01-31

### Added
- Add vm_preset mode to manifest schema (#135)
  - Levels can now use `vm_preset` + `vmid` + `image` instead of `env` FK
  - Decouples manifests from envs/ - simpler configuration
  - ConfigResolver resolves vm_preset directly from vms/presets/

- Add `LookupVMIPAction` for destructor IP resolution
  - Queries PVE guest agent for VM IP when context doesn't have it
  - Enables destructor to find inner host without context file
  - Falls back gracefully when VM is stopped or unreachable

- Add `CopySSHPrivateKeyAction` for recursive PVE scenarios (#133)
  - Copies outer host's SSH private key to inner host
  - Enables inner-pve to SSH to its nested VMs
  - Keys copied to both root and homestak users
  - Required for n3-full (3-level nesting) to work

- Add serve-repos propagation to `RecursiveScenarioAction` (#134)
  - Passes HOMESTAK_SOURCE, HOMESTAK_TOKEN, HOMESTAK_REF to inner hosts
  - Enables nested bootstrap operations to use serve-repos instead of GitHub
  - Required for testing uncommitted code at level 2+ in recursive scenarios

- Add n3-full validation to release process (#130)
  - 3-level nested PVE now validated before releases
  - Proves recursive architecture scales beyond N=2

### Fixed
- Fix SSH authentication for automation_user in recursive scenarios (#133)
  - All SSH actions now use `config.automation_user` (homestak) for VM connections
  - `config.ssh_user` (root) reserved for PVE host connections
  - Fixes "Permission denied (publickey)" errors in nested deployments

- Fix API token injection in n3-full recursive scenarios (#130)
  - `CreateApiTokenAction` now uses level name as token key (was hardcoded to 'nested-pve')
  - Token injection now adds new entries if key doesn't exist (was replace-only)
  - Fixes test-vm provisioning failure at level 3

- Fix sudo env var passing in SSH commands
  - Use `sudo env VAR=value command` instead of `VAR=value sudo command`
  - Ensures environment variables reach the command on remote hosts

## v0.40 - 2026-01-29

### Added
- Add provider lockfile validation to preflight checks (#122)
  - Detects when cached lockfiles in `.states/*/data/` have stale provider versions
  - Auto-fixes by deleting stale lockfiles (regenerated on next `tofu init`)
  - Prevents "does not match configured version constraint" errors after Dependabot updates
  - New functions: `parse_provider_version()`, `parse_lockfile_version()`, `validate_provider_lockfiles()`

- Add split file handling to `DownloadGitHubReleaseAction` (#123)
  - Automatically detects and downloads split parts (`.partaa`, `.partab`, etc.)
  - Reassembles parts into single file after download
  - Cleans up part files after successful reassembly
  - Enables downloading large images (>2GB) from GitHub releases

## v0.39 - 2026-01-22

### Added
- Add `RecursiveScenarioAction` for SSH-streamed scenario execution (#104)
  - PTY allocation for real-time output streaming
  - JSON result parsing from `--json-output` scenarios
  - Context key extraction for parent scenario consumption
  - Configurable timeout and SSH user

- Add manifest-driven recursive scenarios (#114)
  - `manifest.py` with `Manifest`, `ManifestLevel`, `ManifestSettings` dataclasses
  - `ManifestLoader` for YAML file loading from site-config/manifests/
  - Schema versioning (v1 = linear levels array)
  - Depth limiting via `--depth` flag
  - JSON serialization for recursive calls via `--manifest-json`

- Add recursive-pve scenarios for N-level nested PVE (#114)
  - `recursive-pve-constructor`: Build N-level stack per manifest
  - `recursive-pve-destructor`: Tear down stack in reverse order
  - `recursive-pve-roundtrip`: Constructor + destructor full cycle
  - Helper actions: `BootstrapAction`, `CopySecretsAction`, `GenerateNodeConfigAction`

- Add CLI flags for manifest-driven scenarios
  - `--manifest`, `-M`: Manifest name from site-config/manifests/
  - `--manifest-file`: Path to manifest file
  - `--manifest-json`: Inline manifest JSON (for recursive calls)
  - `--keep-on-failure`: Keep levels on failure for debugging
  - `--depth`: Limit manifest to first N levels

- Add raw file serving to serve-repos.sh for fully offline bootstrap (#119)
  - `serve_raw_file()` extracts files from bare repos via `git show`
  - BootstrapAction fetches `{source_url}/bootstrap.git/install.sh` when HOMESTAK_SOURCE set
  - Enables recursive scenarios without GitHub connectivity

### Fixed
- Fix `BootstrapAction` to integrate with serve-repos env vars (#116)
  - Reads `HOMESTAK_SOURCE`, `HOMESTAK_TOKEN`, `HOMESTAK_REF` from environment
  - Builds bootstrap command with proper env var prefix for dev workflow
  - Falls back to GitHub URL when serve-repos not configured

- Fix `timeout_buffer` manifest setting not applied to recursive timeouts (#117)
  - Add `_get_recursive_timeout()` method to `RecursivePVEBase`
  - Subtracts `timeout_buffer` from base timeout to ensure cleanup time
  - Applies to all `RecursiveScenarioAction` invocations

- Fix `cleanup_on_failure` manifest setting not propagated (#118)
  - Add `_get_effective_keep_on_failure()` method to `RecursivePVEBase`
  - CLI `--keep-on-failure` takes precedence over manifest setting
  - Manifest `cleanup_on_failure: false` maps to `keep_on_failure: true`
  - Setting propagated to recursive constructor calls

### Testing
- Add unit tests for RecursiveScenarioAction (27 tests)
- Add unit tests for manifest loading and validation (29 tests)

## v0.38 - 2026-01-21

### Added
- Add `--json-output` flag for structured scenario results (#109)
  - JSON output to stdout, logs to stderr
  - Includes scenario name, success status, duration, phase results
  - Context values (vm_ip, vm_id, etc.) included for parent consumption
  - Error details included on failure

## v0.37 - 2026-01-20

### Theme: Foundation for Recursion

### Added
- Add HTTP server helper for dev workflows (iac-driver#110)
  - `scripts/serve-repos.sh` creates bare repos with `_working` branch containing uncommitted changes
  - Bearer token authentication via custom Python HTTP handler
  - OS-assigned ports by default with `--json` output for programmatic use
  - Automatic cleanup on exit (trap EXIT)

- Add `--serve-repos` flag to run.sh for HTTP server lifecycle management (iac-driver#110)
  - `--serve-repos` starts serve-repos.sh before scenario, stops on exit
  - `--serve-port` for explicit port (default: OS-assigned)
  - `--serve-timeout` for auto-shutdown
  - `--serve-ref` for ref selection (default: `_working`)
  - Exports `HOMESTAK_SOURCE`, `HOMESTAK_TOKEN`, `HOMESTAK_REF` for scenarios

## v0.36 - 2026-01-20

### Theme: Host Provisioning Workflow

### Added
- Host resolution fallback for pre-PVE hosts (#66)
  - `--host X` now checks `nodes/X.yaml` first, falls back to `hosts/X.yaml`
  - Enables provisioning fresh Debian hosts before PVE is installed
  - `list_hosts()` returns combined list from both directories (deduplicated)
  - `HostConfig.is_host_only` flag indicates SSH-only config (no PVE API)
  - Improved error message with instructions for creating host config

- Add `generate_node_config` phase to pve-setup scenario (#66)
  - Automatically generates `nodes/{hostname}.yaml` after PVE install
  - Local mode: runs `make node-config FORCE=1` in site-config
  - Remote mode: generates on target, copies back via scp
  - Host becomes usable for vm-constructor immediately after pve-setup

### Documentation
- Add "Host Resolution (v0.36+)" section to CLAUDE.md
- Update pve-setup scenario description (now 3 phases)

## v0.33 - 2026-01-19

### Theme: Unit Testing

### Added
- Add pytest job to CI workflow (#106)
  - Tests run on push/PR to master
  - 165 tests validated

### Fixed
- Fix Makefile test target to run from correct directory (#106)

## v0.32 - 2026-01-19

### Added
- Add `--version` to run.sh/cli.py using git-derived version pattern (#102)
- Add `--help` to helper scripts (setup-tools.sh, wait-for-guest-agent.sh) (#102)

### Fixed
- Fix GitHub org in setup-tools.sh (`john-derose` → `homestak-dev`) (#102)
- Add site-config to repos cloned by setup-tools.sh (#102)

## v0.31 - 2026-01-19

### Added
- Expand pytest coverage (#98)
  - Add tests/test_config.py for config discovery (get_site_config_dir, list_hosts, load_host_config)
  - Add tests/test_common.py for utilities (run_command, run_ssh, wait_for_ping, wait_for_ssh)

### Changed
- Make vmid_range configurable in NestedPVEDestructor (#101)
  - Add `vmid_range` class attribute (default: 99800-99999)
  - Can be overridden via subclass or at runtime

## v0.30 - 2026-01-18

### Fixed
- Use unique temp files for tfvars to avoid permission issues
  - Add `create_temp_tfvars()` helper using Python tempfile module
  - Clean up temp files after tofu commands complete
  - Remote actions use PID-based unique filenames

## v0.28 - 2026-01-18

### Features

- Add VM discovery actions for pattern-based cleanup (#41)
  - `DiscoverVMsAction`: Query PVE API and filter by name pattern + vmid range
  - `DestroyDiscoveredVMsAction`: Stop and destroy all discovered VMs
  - `DestroyRemoteVMAction`: Best-effort cleanup on remote PVE (handles missing host gracefully)
  - Destructor no longer requires context file with VM IDs

- Update NestedPVEConstructor to use granular playbooks (#49)
  - `setup_network`: Configure vmbr0 bridge via nested-pve-network.yml
  - `setup_ssh`: Copy SSH keys via nested-pve-ssh.yml
  - `setup_repos`: Sync repos and configure PVE via nested-pve-repos.yml
  - Better phase-level visibility and easier debugging

- Update NestedPVEDestructor to use discovery-based cleanup (#41)
  - Discovers VMs matching `nested-pve*` pattern in vmid range 99800-99999
  - Works without context file - just specify `--host`
  - Gracefully skips inner PVE cleanup when not reachable

### Fixed

- Fix DownloadGitHubReleaseAction to resolve 'latest' tag via GitHub API
  - GitHub download URLs require actual tag names, not 'latest'
  - New `_resolve_latest_tag()` method queries API for real tag name
  - Enables `packer_release: latest` in site-config to work correctly

## v0.26 - 2026-01-17

- Release alignment with homestak v0.26

## v0.25 - 2026-01-16

- Release alignment with homestak v0.25

## v0.24 - 2026-01-16

### Added
- Add comprehensive preflight checks (#97)
  - Bootstrap installation validation (checks for core repos)
  - site-init completion check (secrets.yaml decrypted, node config exists)
  - Nested virtualization check (for nested-pve-* scenarios)
  - Standalone `--preflight` mode for checking without scenario execution
  - `--skip-preflight` flag to bypass checks for experienced users
  - Clear, actionable error messages with remediation hints

### Changed
- Update site-config discovery to support FHS-compliant paths (#97)
  - Add `/usr/local/etc/homestak/` as priority 3 in resolution order
  - Legacy `/opt/homestak/site-config/` remains as fallback (priority 4)

## v0.22 - 2026-01-15

### Changed

- Refactor nested-pve scenario to pass `homestak_src_dir` instead of individual repo paths
  - Aligns with ansible#13 role refactor
  - Simplifies variable passing to ansible playbooks

## v0.20 - 2026-01-14

### Changed

- Refactored packer scenarios to use build.sh wrapper
  - PackerBuildAction now runs `./build.sh <template>` instead of direct packer commands
  - Ensures version detection, renaming, and cleanup scripts run during scenario builds
  - Increased timeout to 900s to accommodate PVE image builds

## v0.19 - 2026-01-14

### Features

- Add API token validation via `--validate-only` flag (#31)
  - Validates API token without running scenario
  - Reports PVE version on success
- Add host availability check with SSH reachability test (#32)
  - Pre-flight validation includes SSH connectivity
  - Fails fast before scenario execution
- Enhance `--local` flag with auto-config from hostname (#26)
  - Auto-discovers node config from system hostname
  - Simplifies local execution without explicit host parameter

### Fixed

- Fix EnsurePVEAction to detect pre-installed debian-13-pve image
  - Checks for `/etc/pve-packages-preinstalled` marker before pveproxy status
  - Skips ansible pve-install.yml when using pre-built PVE image

## v0.18 - 2026-01-13

### Features

- Add `--dry-run` mode for scenario preview (#40)
  - Shows phases, actions, and parameters without execution
  - Useful for release verification and understanding scenario behavior
  - Orchestrator returns preview report without side effects

## v0.17 - 2026-01-11

### Features
- Add site-config integration to ansible actions (#92)
  - `use_site_config` parameter to enable ConfigResolver integration
  - `env` parameter to specify environment for posture resolution
  - Resolves timezone, packages, SSH settings from site-config
  - Works with both `AnsiblePlaybookAction` and `AnsibleLocalPlaybookAction`

## v0.16 - 2026-01-11

### Features

- Add `--vm-id` CLI flag for ad-hoc VM ID overrides (closes #18)
  - Repeatable: `--vm-id test=99990 --vm-id inner=99912`
  - Format validation with clear error messages
  - Applied via `vm_id_overrides` in TofuApplyAction

### Code Quality

- Align CI workflow with local pre-commit configuration (closes #71)
  - Run `pre-commit run --all-files` in CI (advisory mode)
  - Replaces standalone pylint/mypy steps
  - Consistent tooling between local dev and CI

### Refactoring

- Remove redundant timeout overrides in nested-pve scenarios (closes #44)
  - Delete 7 overrides that matched action defaults
  - Keep 2 VerifySSHChainAction timeouts with rationale comments
  - Reduces maintenance burden and improves readability

### Testing

- Add unit tests for `--vm-id` CLI flag (5 tests):
  - Flag acceptance, format validation, edge cases
- Add unit test for TofuApplyAction VM ID override logic

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
