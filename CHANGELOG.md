# Changelog

## Unreleased

### Features

- Add `pve-configure` scenario for PVE host configuration (runs pve-setup.yml + user.yml)
- Add `AnsibleLocalPlaybookAction` for local playbook execution
- Add `--local` and `--remote` CLI flags for execution mode

### Changes

- Rename `pve.tfvars` to `mother.tfvars`, update endpoint to mother.core

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
