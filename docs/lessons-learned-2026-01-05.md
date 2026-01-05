# Lessons Learned: nested-pve-constructor on mother (2026-01-05)

## Session Overview

- **Duration**: ~45 minutes (manual intervention required multiple times)
- **Target**: Expected ~8.5 minutes automated
- **Outcome**: Success after manual fixes

## Issues Encountered

### 1. VM Context Key Naming Mismatch

**Problem**: `StartVMAction` looked for `inner_vm_id` but `TofuApplyAction` created `nested-pve_vm_id` (from VM name in site-config).

**Root Cause**: When ConfigResolver was integrated, VM IDs are now derived from site-config VM names, not hardcoded. The context key pattern is `{vm_name}_vm_id`.

**Fix Applied**: Updated `nested_pve.py` and `cleanup_nested_pve.py` to use `nested-pve_vm_id` instead of `inner_vm_id`.

**Prevention**: Document context key naming convention: `{vm_name}_vm_id` where `vm_name` comes from `envs/{env}.yaml`.

---

### 2. Remote ConfigResolver Import Path

**Problem**: `TofuApplyRemoteAction` failed with `ModuleNotFoundError: No module named 'config'`.

**Root Cause**: Remote script ran `cd /opt/homestak/iac-driver` then `from src.config_resolver import ConfigResolver`, but `config_resolver.py` has `from config import ...` which expects to run from the `src/` directory.

**Fix Applied**: Changed remote script to `cd /opt/homestak/iac-driver/src` and `from config_resolver import ConfigResolver`.

**Prevention**: Always run Python scripts from the `src/` directory when using relative imports.

---

### 3. Local Datastore Missing Snippets Content Type

**Problem**: `tofu apply` hung for 10+ minutes on `proxmox_virtual_environment_file.cloud_init_user`.

**Root Cause**: Fresh PVE installs don't have `snippets` enabled on the `local` datastore by default. The bpg/proxmox provider was waiting (default 1800s timeout) for an operation that would never succeed.

**Fix Applied**: Run `pvesm set local -content images,rootdir,vztmpl,backup,iso,snippets` on nested PVE.

**Prevention**: Add this to `nested-pve-setup.yml` ansible playbook as a setup task.

**Fail-Fast Improvement**: Reduce `timeout_upload` in tofu module from 1800s to 120s. A snippet upload should take < 1 second.

---

### 4. PVE SSL Certificate Generation Failure

**Problem**: pveproxy workers crashed immediately with `failed to use local certificate chain`.

**Root Cause**: IPv6 link-local addresses with zone IDs (e.g., `fe80::...%vmbr0`) are invalid in X.509 certificate SANs. The `pvecm updatecerts` command failed silently on fresh install.

**Fix Applied**:
1. Temporarily disable IPv6: `sysctl -w net.ipv6.conf.all.disable_ipv6=1`
2. Run `pvecm updatecerts --force`
3. Re-enable IPv6
4. Restart pveproxy

**Prevention**: Add certificate regeneration with IPv6 workaround to `pve-install.yml` playbook, or ensure proper `/etc/hosts` entries exist before PVE install.

---

### 5. Excessive SSH Timeouts

**Problem**: `ConnectTimeout=600` (10 minutes) in bootstrap action is excessive for connection establishment.

**Root Cause**: Timeout was set high to allow for bootstrap script execution, but `ConnectTimeout` is for TCP connection, not command execution.

**Fix Needed**: Separate `ConnectTimeout` (should be 30s max) from command `timeout` (can be longer for bootstrap).

---

### 6. rsync Overwrites Ansible-Injected Secrets

**Problem**: After ansible injected the real API token into secrets.yaml on nested PVE, a subsequent rsync from the controller overwrote it with the placeholder.

**Root Cause**: Manual rsync during debugging. In normal flow, ansible is the last to touch secrets.yaml.

**Prevention**:
- Don't manually rsync site-config after ansible runs
- Consider making ansible inject tokens into a separate file that rsync excludes

---

### 7. APT Lock During Cloud-Init

**Problem**: `bootstrap-install` failed because cloud-init was still running apt-get.

**Root Cause**: Test VM was freshly booted and cloud-init was updating packages.

**Fix Applied**: Wait ~60 seconds before running bootstrap.

**Prevention**: Add `WaitForCloudInitAction` or poll for apt lock before bootstrap.

---

## Timing Analysis

| Phase | Expected | Actual | Notes |
|-------|----------|--------|-------|
| provision (tofu apply) | ~10s | 10s | OK |
| start_vm | ~2s | 1s | OK |
| wait_ip | ~30s | 10s | OK |
| install_pve | ~10m | 11m | OK |
| configure | ~30s | 31s | OK |
| download_image | ~30s | 30s | OK |
| test_vm_apply | ~10s | **10m+** | SSL/snippets issue |
| test_vm_start | ~2s | 1s | OK (after fix) |
| test_vm_wait | ~30s | 10s | OK |
| verify | ~5s | 2s | OK |

**Total expected**: ~12 minutes
**Total actual**: ~45 minutes (with debugging)
**With fixes**: Should be ~12-15 minutes

---

## Recommended Fixes for Next Run

### Ansible (nested-pve-setup.yml)

1. Enable snippets on local datastore
2. Regenerate SSL certs (with IPv6 workaround)
3. Add hostname entry to /etc/hosts

### iac-driver

1. Reduce timeout_upload in TofuApplyRemoteAction
2. Add WaitForCloudInitAction before bootstrap
3. Fix SSH timeout parameters (ConnectTimeout vs command timeout)

### site-config

1. Add `nested-pve` API token key (matches node name)

---

## Future Improvements

1. **Recursive git-based repo sharing**: Instead of rsync, have nested PVE clone repos from parent via SSH git protocol. Enables arbitrary nesting depth.

2. **Pre-flight checks**: Verify PVE API is healthy before running tofu (check pveproxy, SSL certs, storage config).

3. **Fail-fast timeouts**: Reduce all provider timeouts to reasonable values (2-5 minutes max for most operations).

4. **Idempotent setup**: Make all ansible tasks idempotent so re-running doesn't break things.
