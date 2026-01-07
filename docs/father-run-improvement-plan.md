# Father Run Improvement Plan

**Date:** 2026-01-05
**Goal:** Address issues from father run and prepare for autonomous re-run

## Issues Identified

### 1. Tofu State File Isolation (Critical)

**Problem:** The generic tofu environment stores state in the default `terraform.tfstate` file, which gets overwritten between deployments. When running on father after mother, stale state referencing `node_name: "mother"` caused API requests to the wrong node, triggering SSL verification failures.

**Root Cause:** `TF_DATA_DIR` only affects plugin/module caching, not state file location.

**Fix Options:**
- **A. Use `-state` flag** (recommended): Pass explicit state file path per env+node
- **B. Use workspace isolation**: Create tofu workspaces per env+node
- **C. Use backend configuration**: Configure remote/local backend with per-deployment paths

**Implementation (Option A):**
```python
# In TofuApplyAction.run()
state_file = f'.states/{self.env_name}-{config.name}.tfstate'
cmd = ['tofu', 'apply', '-auto-approve', '-input=false',
       f'-state={state_file}', f'-var-file={tfvars_path}']
```

### 2. Missing /opt/homestak on Test VM

**Problem:** `test@nested-pve@father` didn't have `/opt/homestak` while `test@nested-pve@mother` did.

**Root Cause:** On father, I ran `pve-iac-setup.yml` which only installs tools (tofu, packer) but doesn't sync the homestak repos. On mother, additional manual steps synced the repos.

**Fix:** The nested-pve-setup role should:
1. Sync repos to inner PVE (already does this)
2. The test VM should receive repos via a separate "bootstrap" step that copies from the inner PVE

**Current flow:**
```
outer PVE → [rsync repos] → inner PVE (/opt/homestak)
                                      → [tofu creates test VM]
                                      → test VM has NO /opt/homestak
```

**Fixed flow:**
```
outer PVE → [rsync repos] → inner PVE (/opt/homestak)
                                      → [tofu creates test VM]
                                      → [rsync repos to test VM] → test VM (/opt/homestak)
```

**Implementation:** Add a new action `SyncReposAction` that runs after test VM is created but before bootstrap-install.

### 3. Pre-Authorization/Autonomy Issue (Critical)

**Problem:** Despite agreeing to hands-free execution, authorization prompts kept appearing during the run.

**Root Cause Analysis:**
Commands that likely triggered approval prompts:
- `ansible-playbook` - not in allowed list
- `rsync` - not in allowed list
- Complex SSH commands with inline scripts

**Solutions:**

**A. Expand allowed command patterns:**
Add to Claude Code settings:
```
Bash(ansible-playbook:*), Bash(ansible:*), Bash(rsync:*)
```

**B. Use iac-driver/run.sh exclusively:**
The run.sh script is pre-approved. If all orchestration goes through it, no additional approvals needed.

**C. Hybrid approach (recommended):**
1. Pre-approve ansible/rsync in Claude Code settings
2. Refactor scenarios to minimize direct shell commands
3. Use run.sh for all integration testing

### 4. Scenario Execution Path

**Current state:** Running scenarios requires manual intervention and direct tofu/ansible commands.

**Target state:** `./run.sh --scenario nested-pve-roundtrip --host father` handles everything.

**Gaps to close:**
1. ~~TofuApplyAction needs ConfigResolver integration~~ (done)
2. State isolation needs implementation
3. Scenarios need to handle all ansible steps internally
4. Remote actions need to use iac-driver on inner PVE (not direct tofu)

## Implementation Plan

### Phase 1: State Isolation Fix (15 min)

1. Modify `TofuApplyAction` and `TofuDestroyAction` to use explicit state file paths
2. Test with tofu validate

**Files to modify:**
- `iac-driver/src/actions/tofu.py`

### Phase 2: Test VM /opt/homestak (10 min)

1. Add `SyncReposRemoteAction` to sync repos to inner VMs
2. Update `nested_pve.py` scenario to call this after test VM creation

**Files to modify:**
- `iac-driver/src/actions/remote.py` (new action)
- `iac-driver/src/scenarios/nested_pve.py`

### Phase 3: Pre-Authorization (5 min)

**Required Claude Code Settings for Autonomous Runs:**

The following command patterns must be added to the allowed tools list in Claude Code settings:

```
Bash(ansible-playbook:*), Bash(ansible:*), Bash(rsync:*)
```

**Location:** `~/.claude/settings.json` or project-level `.claude/settings.json`

**Example configuration:**
```json
{
  "permissions": {
    "allow": [
      "Bash(ansible-playbook:*)",
      "Bash(ansible:*)",
      "Bash(rsync:*)"
    ]
  }
}
```

Without these settings, ansible-playbook and rsync commands will trigger authorization prompts, breaking the autonomous execution goal

### Phase 4: Verification Run (30 min)

1. Run `./run.sh --scenario nested-pve-roundtrip --host father --verbose`
2. Verify no authorization prompts
3. Verify test VM has /opt/homestak
4. Verify clean state isolation

## Success Criteria

- [ ] `nested-pve-roundtrip` completes on father with zero manual interventions
- [ ] State files properly isolated per env+node
- [ ] test@nested-pve@father has /opt/homestak
- [ ] Total runtime < 30 minutes

## Notes for Autonomous Execution

For true hands-free automation, ensure:
1. Claude Code settings include ansible/rsync in allowed patterns
2. No state files exist from previous runs
3. PVE hosts are accessible via SSH
4. API tokens are valid in site-config/secrets.yaml
