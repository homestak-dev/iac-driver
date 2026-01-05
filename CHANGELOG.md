# Changelog

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
