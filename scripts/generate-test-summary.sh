#!/bin/bash
# Generate a test run summary for nested PVE end-to-end tests
# Usage: ./generate-test-summary.sh [test-name] [nested-pve-ip]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IAC_DRIVER_DIR="$(dirname "$SCRIPT_DIR")"
HOMESTAK_DIR="$(dirname "$IAC_DRIVER_DIR")"
TEST_RUNS_DIR="$IAC_DRIVER_DIR/test-runs"

# Arguments
TEST_NAME="${1:-nested-pve-e2e}"
INNER_PVE_IP="${2:-}"

# Auto-detect inner PVE IP if not provided
if [[ -z "$INNER_PVE_IP" ]]; then
    INNER_PVE_IP=$(qm guest cmd 99913 network-get-interfaces 2>/dev/null | \
        jq -r '.[] | select(.name != "lo") | .["ip-addresses"][] | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' 2>/dev/null | head -1 || echo "")
fi

if [[ -z "$INNER_PVE_IP" ]]; then
    echo "Error: Could not detect inner PVE IP. Provide it as second argument."
    exit 1
fi

# Timestamps
DATE=$(date +%Y-%m-%d)
TIME=$(date +%H:%M:%S)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
TEST_ID="${TEST_NAME}-${TIMESTAMP}"
# Output file will be set after determining status

echo "Inner PVE IP: $INNER_PVE_IP"

# Gather outer host info
OUTER_HOST=$(hostname)
OUTER_IP=$(ip -4 addr show vmbr0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "unknown")

# Gather VM 99913 (inner PVE) info
echo "Querying VM 99913 (inner PVE)..."
INNER_PVE_CONFIG=$(qm config 99913 2>/dev/null || echo "")
INNER_PVE_NAME=$(echo "$INNER_PVE_CONFIG" | grep '^name:' | awk '{print $2}')
INNER_PVE_MEMORY=$(echo "$INNER_PVE_CONFIG" | grep '^memory:' | awk '{print $2}')
INNER_PVE_CORES=$(echo "$INNER_PVE_CONFIG" | grep '^cores:' | awk '{print $2}')
INNER_PVE_MAC=$(echo "$INNER_PVE_CONFIG" | grep '^net0:' | grep -oP 'virtio=\K[^,]+')
INNER_PVE_DISK=$(echo "$INNER_PVE_CONFIG" | grep '^virtio0:' | grep -oP 'size=\K[^,]+')

# Gather test VM info from inner PVE
echo "Querying VMs on inner PVE..."
TEST_VMS=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new root@$INNER_PVE_IP "qm list" 2>/dev/null | tail -n +2 || echo "")

# Get first test VM details
TEST_VM_ID=$(echo "$TEST_VMS" | awk 'NR==1 {print $1}')
if [[ -n "$TEST_VM_ID" ]]; then
    TEST_VM_CONFIG=$(ssh root@$INNER_PVE_IP "qm config $TEST_VM_ID" 2>/dev/null || echo "")
    TEST_VM_NAME=$(echo "$TEST_VM_CONFIG" | grep '^name:' | awk '{print $2}')
    TEST_VM_MEMORY=$(echo "$TEST_VM_CONFIG" | grep '^memory:' | awk '{print $2}')
    TEST_VM_CORES=$(echo "$TEST_VM_CONFIG" | grep '^cores:' | awk '{print $2}')
    TEST_VM_MAC=$(echo "$TEST_VM_CONFIG" | grep '^net0:' | grep -oP 'virtio=\K[^,]+')
    TEST_VM_DISK=$(echo "$TEST_VM_CONFIG" | grep '^virtio0:' | grep -oP 'size=\K[^,]+')

    # Get test VM IP
    TEST_VM_IP=$(ssh root@$INNER_PVE_IP "qm guest cmd $TEST_VM_ID network-get-interfaces" 2>/dev/null | \
        jq -r '.[] | select(.name != "lo") | .["ip-addresses"][] | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' 2>/dev/null | head -1 || echo "unknown")
fi

# Get SSH public key
SSH_PUBKEY=$(cat ~/.ssh/id_rsa.pub 2>/dev/null || echo "not found")

# Get API tokens from tfvars
OUTER_TOKEN=$(grep proxmox_api_token "$HOMESTAK_DIR/tofu/envs/pve-deb/terraform.tfvars" 2>/dev/null | awk -F'"' '{print $2}' || echo "not found")
INNER_TOKEN=$(ssh root@$INNER_PVE_IP "grep proxmox_api_token /root/tofu/envs/test/terraform.tfvars 2>/dev/null | awk -F'\"' '{print \$2}'" 2>/dev/null || echo "not found")

# Get password hash
ROOT_HASH=$(grep root_password_hash "$HOMESTAK_DIR/tofu/envs/pve-deb/terraform.tfvars" 2>/dev/null | awk -F'"' '{print $2}' || echo "not found")

# Get git commits
TOFU_COMMIT=$(git -C "$HOMESTAK_DIR/tofu" rev-parse --short HEAD 2>/dev/null || echo "unknown")
ANSIBLE_COMMIT=$(git -C "$HOMESTAK_DIR/ansible" rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Verify SSH chain
echo "Verifying SSH chain..."
SSH_VERIFY=""
if [[ -n "$TEST_VM_IP" && "$TEST_VM_IP" != "unknown" ]]; then
    SSH_VERIFY=$(ssh -o ConnectTimeout=5 -J root@$INNER_PVE_IP root@$TEST_VM_IP "hostname && uname -a" 2>/dev/null || echo "FAILED")
fi

# Determine status and output filename
if [[ "$SSH_VERIFY" == *"FAILED"* || -z "$SSH_VERIFY" ]]; then
    STATUS="FAILED"
    STATUS_SUFFIX="failed"
else
    STATUS="SUCCESS"
    STATUS_SUFFIX="passed"
fi

OUTPUT_FILE="$TEST_RUNS_DIR/${DATE}.${TIME}-${STATUS_SUFFIX}.md"
echo "Generating test summary: $OUTPUT_FILE"

# Generate the report
mkdir -p "$TEST_RUNS_DIR"

cat > "$OUTPUT_FILE" << EOF
# Nested PVE End-to-End Test Run

**Date**: $DATE
**Test ID**: $TEST_ID
**Status**: $STATUS

## Summary

End-to-end nested virtualization test: VM within VM within host.

## Architecture

\`\`\`
$OUTER_HOST (Outer Host)
├── IP: $OUTER_IP
├── Node: pve
└── VM 99913 ($INNER_PVE_NAME) - Inner PVE
    ├── IP: $INNER_PVE_IP
    ├── Node: $INNER_PVE_NAME
EOF

if [[ -n "$TEST_VM_ID" ]]; then
cat >> "$OUTPUT_FILE" << EOF
    └── VM $TEST_VM_ID ($TEST_VM_NAME) - Test VM
        └── IP: $TEST_VM_IP
EOF
fi

cat >> "$OUTPUT_FILE" << EOF
\`\`\`

## Event Log

| Timestamp | Phase | Event | Duration |
|-----------|-------|-------|----------|
| - | 1 | Provision inner PVE VM | - |
| - | 2 | Install Proxmox VE | - |
| - | 3 | Configure inner PVE | - |
| - | 4 | Build packer image | - |
| - | 5 | Provision test VM | - |
| - | 6 | Verify SSH chain | - |

**Total Duration**: See execution log

## Virtual Machines

### VM 99913 - Inner PVE ($INNER_PVE_NAME)

| Property | Value |
|----------|-------|
| VM ID | 99913 |
| Hostname | $INNER_PVE_NAME |
| IP Address | $INNER_PVE_IP |
| MAC Address | $INNER_PVE_MAC |
| Memory | $INNER_PVE_MEMORY MB |
| Cores | $INNER_PVE_CORES |
| Disk | $INNER_PVE_DISK on local-zfs |
| Network | vmbr0 (bridge) |
| OS | Debian 13 Trixie + Proxmox VE |
EOF

if [[ -n "$TEST_VM_ID" ]]; then
cat >> "$OUTPUT_FILE" << EOF

### VM $TEST_VM_ID - Test VM ($TEST_VM_NAME)

| Property | Value |
|----------|-------|
| VM ID | $TEST_VM_ID |
| Hostname | $TEST_VM_NAME |
| IP Address | $TEST_VM_IP |
| MAC Address | $TEST_VM_MAC |
| Memory | $TEST_VM_MEMORY MB |
| Cores | $TEST_VM_CORES |
| Disk | $TEST_VM_DISK on local |
| Network | vmbr0 (inner PVE bridge) |
| OS | Debian 12 Bookworm |
EOF
fi

cat >> "$OUTPUT_FILE" << EOF

## Authentication

### SSH Keys

**Key ID**: root@pve

\`\`\`
$SSH_PUBKEY
\`\`\`

**Private Key Location**: \`~/.ssh/id_rsa\`

### API Tokens

| Host | Token |
|------|-------|
| Outer PVE (pve.homestak) | \`$OUTER_TOKEN\` |
| Inner PVE ($INNER_PVE_NAME) | \`$INNER_TOKEN\` |

### Root Password Hash

\`\`\`
$ROOT_HASH
\`\`\`

## Access Commands

\`\`\`bash
# SSH to inner PVE
ssh root@$INNER_PVE_IP

# SSH to test VM via jump host
ssh -J root@$INNER_PVE_IP root@$TEST_VM_IP

# Inner PVE Web UI
https://$INNER_PVE_IP:8006
\`\`\`

## Git Commits

| Repository | Commit |
|------------|--------|
| tofu | $TOFU_COMMIT |
| ansible | $ANSIBLE_COMMIT |

## Verification

\`\`\`bash
\$ ssh -J root@$INNER_PVE_IP root@$TEST_VM_IP "hostname && uname -a"
$SSH_VERIFY
\`\`\`

## Cleanup Commands

\`\`\`bash
# Destroy test VM on inner PVE
ssh root@$INNER_PVE_IP "cd /root/tofu/envs/test && tofu destroy -auto-approve"

# Destroy inner PVE
cd \$HOMESTAK_DIR/tofu/envs/pve-deb && tofu destroy -auto-approve
\`\`\`

## Issues Encountered

None recorded by automated script. See execution log for details.

## Notes

- Generated by automated script
- Manual edits may be needed for Event Log timestamps and durations

## Generated

- **Script**: \`$0\`
- **Timestamp**: $(date -Iseconds)
EOF

echo ""
echo "Test summary generated: $OUTPUT_FILE"
echo "Status: $STATUS"
