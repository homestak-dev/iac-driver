#!/bin/bash
# Wait for QEMU guest agent to report an IP address
# Usage: wait-for-guest-agent.sh <vmid> [interface] [timeout_seconds]
#
# Examples:
#   ./wait-for-guest-agent.sh 99913              # Wait for eth0 IP, 120s timeout
#   ./wait-for-guest-agent.sh 99913 vmbr0        # Wait for vmbr0 IP
#   ./wait-for-guest-agent.sh 99913 eth0 60      # 60 second timeout

set -euo pipefail

VMID="${1:?Usage: $0 <vmid> [interface] [timeout_seconds]}"
IFACE="${2:-eth0}"
TIMEOUT="${3:-120}"

echo "Waiting for guest agent on VM $VMID (interface: $IFACE, timeout: ${TIMEOUT}s)..." >&2

for ((i=0; i<TIMEOUT/5; i++)); do
  IP=$(qm guest cmd "$VMID" network-get-interfaces 2>/dev/null | \
    jq -r --arg iface "$IFACE" '.[] | select(.name == $iface) | .["ip-addresses"][]? | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' 2>/dev/null || true)
  if [[ -n "$IP" ]]; then
    echo "$IP"
    exit 0
  fi
  sleep 5
done

echo "Timeout waiting for guest agent on VM $VMID" >&2
exit 1
