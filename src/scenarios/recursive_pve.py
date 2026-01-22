"""Recursive PVE scenarios with manifest-driven depth.

These scenarios use RecursiveScenarioAction to execute scenarios on nested
bootstrapped hosts, enabling N-level nesting defined by manifest configuration.

Key differences from nested-pve scenarios:
- Manifest-driven: N is data, not code
- Uses RecursiveScenarioAction: SSH streaming with --json-output
- Uses bootstrap: Inner hosts install homestak, not file sync

Scenarios:
- recursive-pve-constructor: Build N-level stack per manifest
- recursive-pve-destructor: Tear down stack (reverse order)
- recursive-pve-roundtrip: Constructor + verify + destructor
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from actions import (
    TofuApplyAction,
    TofuDestroyAction,
    StartVMAction,
    WaitForGuestAgentAction,
    WaitForSSHAction,
    RecursiveScenarioAction,
    DownloadGitHubReleaseAction,
)
from common import ActionResult, run_ssh
from config import HostConfig
from manifest import Manifest, ManifestLevel, load_manifest
from scenarios import register_scenario

logger = logging.getLogger(__name__)


# Default timeout for recursive scenario execution
DEFAULT_RECURSIVE_TIMEOUT = 1200


def _image_to_asset_name(image: str) -> str:
    """Convert manifest image name to packer asset filename.

    Maps image names from manifests/envs to packer release asset names:
    - debian-12 → debian-12-custom.qcow2
    - debian-13-pve → debian-13-pve.qcow2

    Args:
        image: Image name from manifest (e.g., 'debian-12', 'debian-13-pve')

    Returns:
        Packer release asset filename (e.g., 'debian-12-custom.qcow2')
    """
    # If image already has -custom or -pve suffix, use as-is
    if image.endswith('-custom') or image.endswith('-pve'):
        return f"{image}.qcow2"
    # Otherwise, append -custom
    return f"{image}-custom.qcow2"


@dataclass
class CreateApiTokenAction:
    """Create API token on inner PVE and inject into secrets.yaml.

    This action:
    1. Regenerates PVE SSL certificates (IPv6 workaround)
    2. Restarts pveproxy
    3. Creates tofu API token via pveum
    4. Injects token into secrets.yaml
    """
    name: str
    host_attr: str = 'vm_ip'
    token_name: str = 'nested-pve'  # Token key in secrets.yaml
    timeout: int = 120

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Create API token and inject into secrets.yaml."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Creating API token on {host}...")

        # Step 1: Regenerate PVE SSL certificates and restart pveproxy
        # This fixes IPv6-related SSL issues on fresh installs
        ssl_cmd = '''
sysctl -w net.ipv6.conf.all.disable_ipv6=1
sysctl -w net.ipv6.conf.default.disable_ipv6=1
pvecm updatecerts --force 2>/dev/null || true
sysctl -w net.ipv6.conf.all.disable_ipv6=0
sysctl -w net.ipv6.conf.default.disable_ipv6=0
systemctl restart pveproxy
sleep 2
'''
        rc, out, err = run_ssh(host, ssl_cmd, user='root', timeout=60)
        if rc != 0:
            logger.warning(f"[{self.name}] SSL cert regen warning: {err or out}")
            # Continue anyway - this might fail on some systems

        # Step 2: Delete any existing token and create new one
        token_cmd = '''
pveum user token remove root@pam tofu 2>/dev/null || true
pveum user token add root@pam tofu --privsep 0 --output-format json
'''
        rc, out, err = run_ssh(host, token_cmd, user='root', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to create API token: {err or out}",
                duration=time.time() - start
            )

        # Step 3: Parse the token from JSON output
        import json
        try:
            token_data = json.loads(out.strip())
            full_token = f"{token_data['full-tokenid']}={token_data['value']}"
        except (json.JSONDecodeError, KeyError) as e:
            return ActionResult(
                success=False,
                message=f"Failed to parse API token: {e}",
                duration=time.time() - start
            )

        # Step 4: Inject token into secrets.yaml on the inner host
        # Use sed to update the nested-pve line under api_tokens
        inject_cmd = f'''
sed -i 's|^\\(\\s*\\){self.token_name}:.*$|\\1{self.token_name}: {full_token}|' /usr/local/etc/homestak/secrets.yaml
'''
        rc, out, err = run_ssh(host, inject_cmd, user='root', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to inject token into secrets.yaml: {err or out}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] API token created and injected on {host}")
        return ActionResult(
            success=True,
            message=f"API token created on {host}",
            duration=time.time() - start
        )


@dataclass
class BootstrapAction:
    """Bootstrap homestak on a remote host.

    Runs the bootstrap curl|bash installer on a target host. Integrates with
    serve-repos infrastructure when HOMESTAK_SOURCE env var is set.

    Environment variables (from --serve-repos):
    - HOMESTAK_SOURCE: HTTP server URL for local repo access
    - HOMESTAK_TOKEN: Bearer token for authentication
    - HOMESTAK_REF: Git ref to use (default: _working)
    """
    name: str
    host_attr: str = 'vm_ip'
    source_url: Optional[str] = None  # HTTP server URL for dev workflow
    ref: str = 'master'  # Git ref for bootstrap
    timeout: int = 600

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Run bootstrap on target host."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        # Check for serve-repos env vars (dev workflow)
        env_source = os.environ.get('HOMESTAK_SOURCE')
        env_token = os.environ.get('HOMESTAK_TOKEN')
        env_ref = os.environ.get('HOMESTAK_REF', '_working')

        # Build bootstrap command
        if env_source:
            # Dev workflow: use HTTP server from --serve-repos
            # Pass env vars to bash (not curl) so install.sh uses local (uncommitted) code
            # The env vars must be set for bash, which receives curl's output
            env_prefix = f'HOMESTAK_SOURCE={env_source}'
            if env_token:
                env_prefix += f' HOMESTAK_TOKEN={env_token}'
            env_prefix += f' HOMESTAK_REF={env_ref}'
            # Include Bearer token in curl header (serve-repos requires auth)
            auth_header = f'-H "Authorization: Bearer {env_token}"' if env_token else ''
            # IMPORTANT: env_prefix goes before 'bash', not 'curl', so the vars are in bash's environment
            bootstrap_cmd = f'curl -fsSL {auth_header} {env_source}/bootstrap.git/install.sh | {env_prefix} bash'
            logger.info(f"[{self.name}] Using serve-repos source: {env_source} (ref={env_ref})")
        elif self.source_url:
            # Explicit source_url parameter (legacy)
            bootstrap_cmd = f'curl -fsSL {self.source_url}/install.sh | bash'
        else:
            # Production: use GitHub
            bootstrap_url = 'https://raw.githubusercontent.com/homestak-dev/bootstrap'
            bootstrap_cmd = f'curl -fsSL {bootstrap_url}/{self.ref}/install.sh | bash'

        logger.info(f"[{self.name}] Bootstrapping {host}...")

        # Run bootstrap
        rc, out, err = run_ssh(
            host,
            bootstrap_cmd,
            user='root',
            timeout=self.timeout
        )

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Bootstrap failed: {err or out}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Bootstrap completed on {host}")
        return ActionResult(
            success=True,
            message=f"Bootstrap completed on {host}",
            duration=time.time() - start
        )


@dataclass
class CopySecretsAction:
    """Copy secrets.yaml from outer host to inner host.

    Required for inner hosts to have valid API tokens and SSH keys.
    """
    name: str
    host_attr: str = 'vm_ip'
    timeout: int = 60

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Copy secrets to target host."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        # Use scp to copy secrets.yaml
        from config import get_site_config_dir
        secrets_path = get_site_config_dir() / 'secrets.yaml'

        if not secrets_path.exists():
            return ActionResult(
                success=False,
                message=f"secrets.yaml not found at {secrets_path}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Copying secrets to {host}...")

        import subprocess
        cmd = [
            'scp',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            str(secrets_path),
            f'root@{host}:/usr/local/etc/homestak/secrets.yaml'
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )

            if result.returncode != 0:
                return ActionResult(
                    success=False,
                    message=f"Failed to copy secrets: {result.stderr}",
                    duration=time.time() - start
                )

            return ActionResult(
                success=True,
                message=f"Secrets copied to {host}",
                duration=time.time() - start
            )

        except subprocess.TimeoutExpired:
            return ActionResult(
                success=False,
                message=f"Timeout copying secrets to {host}",
                duration=time.time() - start
            )


@dataclass
class InjectSSHKeyAction:
    """Inject outer host's SSH public key into inner host's secrets.yaml.

    This is critical for SSH access to leaf VMs - the outer host's key must
    be in secrets.yaml so ConfigResolver includes it in cloud-init.
    """
    name: str
    host_attr: str = 'vm_ip'
    key_name: str = 'outer_host'  # Key name in secrets.yaml ssh_keys
    timeout: int = 60

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Inject SSH key into target host's secrets.yaml."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        # Read local SSH public key
        from pathlib import Path
        pubkey_path = Path.home() / '.ssh' / 'id_rsa.pub'
        if not pubkey_path.exists():
            pubkey_path = Path.home() / '.ssh' / 'id_ed25519.pub'
        if not pubkey_path.exists():
            return ActionResult(
                success=False,
                message="No SSH public key found (~/.ssh/id_rsa.pub or id_ed25519.pub)",
                duration=time.time() - start
            )

        pubkey = pubkey_path.read_text().strip()
        logger.info(f"[{self.name}] Injecting SSH key ({self.key_name}) to {host}...")

        # Escape the key for sed (forward slashes and ampersands)
        escaped_key = pubkey.replace('/', r'\/').replace('&', r'\&')

        # Inject key into secrets.yaml using sed
        # First check if outer_host already exists
        check_cmd = f"grep -q '^\\s*{self.key_name}:' /usr/local/etc/homestak/secrets.yaml"
        rc, _, _ = run_ssh(host, check_cmd, user='root', timeout=30)

        if rc == 0:
            # Key exists, update it
            inject_cmd = f"sed -i 's|^\\(\\s*\\){self.key_name}:.*$|\\1{self.key_name}: {escaped_key}|' /usr/local/etc/homestak/secrets.yaml"
        else:
            # Key doesn't exist, add it after ssh_keys:
            inject_cmd = f"sed -i '/^ssh_keys:/a\\    {self.key_name}: {escaped_key}' /usr/local/etc/homestak/secrets.yaml"

        rc, out, err = run_ssh(host, inject_cmd, user='root', timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to inject SSH key: {err or out}",
                duration=time.time() - start
            )

        # Verify the key was injected
        verify_cmd = f"grep -q '{self.key_name}:' /usr/local/etc/homestak/secrets.yaml"
        rc, _, _ = run_ssh(host, verify_cmd, user='root', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message="SSH key injection verification failed",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] SSH key injected on {host}")
        return ActionResult(
            success=True,
            message=f"SSH key ({self.key_name}) injected on {host}",
            duration=time.time() - start
        )


@dataclass
class InjectSelfSSHKeyAction:
    """Inject a host's own SSH public key into its secrets.yaml.

    This enables the host to SSH to VMs it provisions - the VM's cloud-init
    will include this key in authorized_keys.
    """
    name: str
    host_attr: str = 'vm_ip'
    key_name: str = 'inner_host'  # Key name in secrets.yaml ssh_keys
    timeout: int = 60

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Inject host's own SSH key into its secrets.yaml."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Injecting {host}'s own SSH key as {self.key_name}...")

        # Inject via Python script encoded in base64 to avoid shell quoting issues
        python_script = f'''
import sys
key_name = sys.argv[1]
secrets_file = "/usr/local/etc/homestak/secrets.yaml"

# Find public key
pubkey = None
for keyfile in ["/root/.ssh/id_ed25519.pub", "/root/.ssh/id_rsa.pub"]:
    try:
        with open(keyfile) as f:
            pubkey = f.read().strip()
            break
    except FileNotFoundError:
        continue

if not pubkey:
    print("No SSH public key found")
    sys.exit(1)

with open(secrets_file, "r") as f:
    lines = f.readlines()

key_exists = any(key_name + ":" in line for line in lines)

with open(secrets_file, "w") as f:
    for line in lines:
        if key_name + ":" in line:
            indent = len(line) - len(line.lstrip())
            f.write(" " * indent + key_name + ": " + pubkey + "\\n")
        else:
            f.write(line)
            if not key_exists and line.strip() == "ssh_keys:":
                f.write("    " + key_name + ": " + pubkey + "\\n")
                key_exists = True

# Verify
with open(secrets_file, "r") as f:
    if key_name + ":" not in f.read():
        print("Verification failed")
        sys.exit(1)
print(f"Injected {{key_name}}")
'''
        import base64
        encoded = base64.b64encode(python_script.encode()).decode()
        inject_script = f"echo '{encoded}' | base64 -d | python3 - {self.key_name}"

        rc, out, err = run_ssh(host, inject_script, user='root', timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to inject self SSH key: {err or out}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Self SSH key injected on {host}")
        return ActionResult(
            success=True,
            message=f"Self SSH key ({self.key_name}) injected on {host}",
            duration=time.time() - start
        )


@dataclass
class ConfigureNetworkBridgeAction:
    """Configure vmbr0 network bridge on inner PVE.

    Creates vmbr0 bridge from eth0 (required for nested VMs to get network).
    Uses a simple shell script rather than ansible for speed.
    """
    name: str
    host_attr: str = 'vm_ip'
    timeout: int = 120

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Configure vmbr0 bridge on target host."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Configuring vmbr0 bridge on {host}...")

        # Check if vmbr0 already exists
        check_cmd = "ip link show vmbr0 2>/dev/null && ip addr show vmbr0 | grep -q 'inet '"
        rc, out, err = run_ssh(host, check_cmd, user='root', timeout=30)
        if rc == 0:
            logger.info(f"[{self.name}] vmbr0 already exists on {host}")
            return ActionResult(
                success=True,
                message=f"vmbr0 already configured on {host}",
                duration=time.time() - start
            )

        # Script to create vmbr0 bridge from eth0 with DHCP
        # This preserves the current IP during transition
        bridge_script = '''
set -e

# Get current interface info
IFACE=$(ip -o route get 8.8.8.8 2>/dev/null | grep -oP 'dev \\K\\S+' || echo eth0)
echo "Detected interface: $IFACE"

# Backup interfaces
cp /etc/network/interfaces /etc/network/interfaces.backup.$(date +%s) 2>/dev/null || true

# Create bridge config with DHCP
cat > /etc/network/interfaces << 'IFACE_EOF'
auto lo
iface lo inet loopback

iface eth0 inet manual

auto vmbr0
iface vmbr0 inet dhcp
    bridge-ports eth0
    bridge-stp off
    bridge-fd 0
IFACE_EOF

# Apply network configuration
# Use systemctl to restart networking
systemctl restart networking 2>/dev/null || ifdown eth0; ifup vmbr0

# Wait for bridge to get IP
for i in $(seq 1 30); do
    if ip addr show vmbr0 | grep -q 'inet '; then
        echo "vmbr0 configured successfully"
        ip addr show vmbr0 | grep 'inet '
        exit 0
    fi
    sleep 1
done

echo "Warning: vmbr0 did not get IP within 30s"
exit 0
'''

        rc, out, err = run_ssh(host, bridge_script, user='root', timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to configure vmbr0: {err or out}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] vmbr0 configured on {host}")
        return ActionResult(
            success=True,
            message=f"vmbr0 bridge configured on {host}",
            duration=time.time() - start
        )


@dataclass
class GenerateNodeConfigAction:
    """Generate node config on inner host.

    Runs 'make node-config' on the inner host to generate the
    nodes/{hostname}.yaml file needed for tofu operations.
    """
    name: str
    host_attr: str = 'vm_ip'
    timeout: int = 120

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Generate node config on target host."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Generating node config on {host}...")

        # Use FORCE=1 in case node config was copied from outer host
        cmd = 'cd /usr/local/etc/homestak && make node-config FORCE=1'
        rc, out, err = run_ssh(host, cmd, user='root', timeout=self.timeout)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to generate node config: {err or out}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Node config generated on {host}",
            duration=time.time() - start
        )


class RecursivePVEBase:
    """Base class for recursive PVE scenarios with manifest support.

    Attributes:
        manifest: Loaded manifest defining recursion levels
        keep_on_failure: If True, don't cleanup on failure (for debugging)
            Set by --keep-on-failure CLI flag or manifest settings.cleanup_on_failure
    """

    # To be set by subclasses or CLI
    manifest: Optional[Manifest] = None
    keep_on_failure: bool = False

    def _get_effective_keep_on_failure(self) -> bool:
        """Get effective keep_on_failure value.

        CLI --keep-on-failure flag takes precedence over manifest setting.
        Manifest cleanup_on_failure=false means keep_on_failure=true.
        """
        # CLI flag takes precedence (if set to True)
        if self.keep_on_failure:
            return True
        # Otherwise, invert manifest setting (cleanup_on_failure=false -> keep=true)
        if self.manifest and not self.manifest.settings.cleanup_on_failure:
            return True
        return False

    def _get_recursive_timeout(self, base_timeout: int = DEFAULT_RECURSIVE_TIMEOUT) -> int:
        """Get timeout for recursive actions, applying timeout_buffer.

        Each level gets base_timeout minus timeout_buffer to ensure outer
        levels have time for cleanup if inner levels timeout.
        """
        if self.manifest:
            buffer = self.manifest.settings.timeout_buffer
            return max(base_timeout - buffer, 60)  # Minimum 60s
        return base_timeout

    def get_level_phases(
        self,
        level: ManifestLevel,
        config: HostConfig,
        remaining_manifest: Optional[Manifest] = None,
        is_leaf: bool = False
    ) -> list[tuple[str, object, str]]:
        """Get phases for a single level.

        Args:
            level: Current level from manifest
            config: Host config
            remaining_manifest: Manifest with remaining levels (for recursion)
            is_leaf: True if this is the leaf level (no children)

        Returns:
            List of (phase_name, action, description) tuples
        """
        phases = []
        host_key = f'{level.name}_ip'
        vm_id_key = f'{level.name}_vm_id'

        # Phase 1: Provision VM using tofu
        phases.append((
            f'provision_{level.name}',
            TofuApplyAction(
                name=f'provision-{level.name}',
                env_name=level.env,
                image_override=level.image,
                vmid_offset=level.vmid_offset,
                context_prefix=level.name,  # Use level name for context keys
            ),
            f'Provision {level.name}'
        ))

        # Phase 2: Start VM
        phases.append((
            f'start_{level.name}',
            StartVMAction(
                name=f'start-{level.name}',
                vm_id_attr=vm_id_key,
                pve_host_attr='ssh_host',
            ),
            f'Start {level.name}'
        ))

        # Phase 3: Wait for IP
        phases.append((
            f'wait_ip_{level.name}',
            WaitForGuestAgentAction(
                name=f'wait-ip-{level.name}',
                vm_id_attr=vm_id_key,
                pve_host_attr='ssh_host',
                ip_context_key=host_key,
                timeout=300,
            ),
            f'Wait for {level.name} IP'
        ))

        # Phase 4: Verify SSH
        phases.append((
            f'verify_ssh_{level.name}',
            WaitForSSHAction(
                name=f'verify-ssh-{level.name}',
                host_key=host_key,
                timeout=120,
            ),
            f'Verify SSH on {level.name}'
        ))

        # If not leaf: bootstrap and recurse
        if not is_leaf and remaining_manifest:
            # Phase 5: Bootstrap
            phases.append((
                f'bootstrap_{level.name}',
                BootstrapAction(
                    name=f'bootstrap-{level.name}',
                    host_attr=host_key,
                    timeout=600,
                ),
                f'Bootstrap {level.name}'
            ))

            # Phase 6: Copy secrets
            phases.append((
                f'secrets_{level.name}',
                CopySecretsAction(
                    name=f'secrets-{level.name}',
                    host_attr=host_key,
                ),
                f'Copy secrets to {level.name}'
            ))

            # Phase 6b: Inject outer host SSH key for leaf VM access
            phases.append((
                f'sshkey_{level.name}',
                InjectSSHKeyAction(
                    name=f'sshkey-{level.name}',
                    host_attr=host_key,
                ),
                f'Inject SSH key on {level.name}'
            ))

            # Phase 7: Run post_scenario (e.g., pve-setup) - installs PVE
            if level.post_scenario:
                phases.append((
                    f'post_{level.name}',
                    RecursiveScenarioAction(
                        name=f'post-{level.name}',
                        scenario_name=level.post_scenario,
                        host_attr=host_key,
                        scenario_args=level.post_scenario_args,
                        timeout=self._get_recursive_timeout(600),
                    ),
                    f'Run {level.post_scenario} on {level.name}'
                ))

            # Phase 8: Configure vmbr0 bridge (required for nested VMs)
            phases.append((
                f'network_{level.name}',
                ConfigureNetworkBridgeAction(
                    name=f'network-{level.name}',
                    host_attr=host_key,
                ),
                f'Configure vmbr0 bridge on {level.name}'
            ))

            # Phase 9: Generate node config (requires PVE installed)
            phases.append((
                f'nodeconfig_{level.name}',
                GenerateNodeConfigAction(
                    name=f'nodeconfig-{level.name}',
                    host_attr=host_key,
                ),
                f'Generate node config on {level.name}'
            ))

            # Phase 9: Create API token and inject into secrets.yaml
            phases.append((
                f'apitoken_{level.name}',
                CreateApiTokenAction(
                    name=f'apitoken-{level.name}',
                    host_attr=host_key,
                ),
                f'Create API token on {level.name}'
            ))

            # Phase 9b: Inject inner host's own SSH key for VM access
            # This enables the inner host to SSH to VMs it provisions
            phases.append((
                f'selfsshkey_{level.name}',
                InjectSelfSSHKeyAction(
                    name=f'selfsshkey-{level.name}',
                    host_attr=host_key,
                ),
                f'Inject {level.name} SSH key'
            ))

            # Phase 10: Download packer image for next level
            next_level = remaining_manifest.get_current_level()
            # Get image from next level (fall back to debian-12 if not specified)
            next_image = next_level.image or 'debian-12'
            next_asset = _image_to_asset_name(next_image)
            phases.append((
                f'download_image_{next_level.name}',
                DownloadGitHubReleaseAction(
                    name=f'download-image-{next_level.name}',
                    asset_name=next_asset,
                    dest_dir='/var/lib/vz/template/iso',
                    host_key=host_key,
                    rename_ext='.img',
                    timeout=300,
                ),
                f'Download {next_asset} for {next_level.name}'
            ))

            # Phase 11: Recurse to next level
            # Build args for recursive call
            # Use current level's env as the host (assumes single-VM env where VM name = env name)
            # Skip preflight on recursive calls - API may not be fully ready after pve-setup
            recurse_args = ['--host', level.env, '--manifest-json', remaining_manifest.to_json(), '--skip-preflight']
            # Propagate keep_on_failure setting
            if self._get_effective_keep_on_failure():
                recurse_args.append('--keep-on-failure')

            phases.append((
                f'recurse_{next_level.name}',
                RecursiveScenarioAction(
                    name=f'recurse-{next_level.name}',
                    scenario_name='recursive-pve-constructor',
                    host_attr=host_key,
                    scenario_args=recurse_args,
                    context_keys=[f'{next_level.name}_ip', f'{next_level.name}_vm_id'],
                    timeout=self._get_recursive_timeout(),
                ),
                f'Build {next_level.name}'
            ))

        return phases


@register_scenario
class RecursivePVEConstructor(RecursivePVEBase):
    """Build N-level nested PVE stack from manifest."""

    name = 'recursive-pve-constructor'
    description = 'Build N-level nested PVE stack per manifest'
    expected_runtime = 360  # ~6 min for N=2

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for recursive construction."""
        # Load manifest from context or default
        # Note: In recursive calls, manifest comes from --manifest-json
        if self.manifest is None:
            self.manifest = load_manifest()

        level = self.manifest.get_current_level()

        if self.manifest.is_leaf:
            # Leaf level: just provision, start, verify
            return self.get_level_phases(level, config, is_leaf=True)
        else:
            # Non-leaf: provision, bootstrap, recurse
            remaining = self.manifest.get_remaining_manifest()
            return self.get_level_phases(level, config, remaining, is_leaf=False)


@register_scenario
class RecursivePVEDestructor(RecursivePVEBase):
    """Tear down N-level nested PVE stack."""

    name = 'recursive-pve-destructor'
    description = 'Destroy N-level nested PVE stack'
    expected_runtime = 120  # ~2 min
    requires_confirmation = True

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for recursive destruction.

        Destruction happens in reverse order - destroy innermost first.
        """
        if self.manifest is None:
            self.manifest = load_manifest()

        phases = []

        # For destruction, we need to destroy in reverse order
        # First, recurse to destroy inner levels (if any)
        if not self.manifest.is_leaf:
            level = self.manifest.get_current_level()
            host_key = f'{level.name}_ip'
            remaining = self.manifest.get_remaining_manifest()

            # Build args for recursive call
            # Use current level's env as the host (assumes single-VM env where VM name = env name)
            # Skip preflight on recursive calls - we're already inside a running scenario
            recurse_args = [
                '--host', level.env,
                '--manifest-json', remaining.to_json(),
                '--yes',  # Already confirmed at outer level
                '--skip-preflight'
            ]

            # Recurse to destroy inner levels first
            phases.append((
                f'recurse_destroy',
                RecursiveScenarioAction(
                    name='recurse-destroy',
                    scenario_name='recursive-pve-destructor',
                    host_attr=host_key,
                    scenario_args=recurse_args,
                    timeout=self._get_recursive_timeout(600),
                ),
                'Destroy inner levels'
            ))

        # Then destroy this level (outermost for this invocation)
        level = self.manifest.get_current_level()
        phases.append((
            f'destroy_{level.name}',
            TofuDestroyAction(
                name=f'destroy-{level.name}',
                env_name=level.env,
            ),
            f'Destroy {level.name}'
        ))

        return phases


@register_scenario
class RecursivePVERoundtrip(RecursivePVEBase):
    """Full roundtrip: construct, verify, destruct."""

    name = 'recursive-pve-roundtrip'
    description = 'Build N-level stack, verify, destroy (full cycle)'
    expected_runtime = 540  # ~9 min for N=2

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for full roundtrip.

        Combines constructor phases with destructor phases.
        """
        if self.manifest is None:
            self.manifest = load_manifest()

        # Get all construction phases
        constructor = RecursivePVEConstructor()
        constructor.manifest = self.manifest
        phases = constructor.get_phases(config)

        # Add destruction phases
        destructor = RecursivePVEDestructor()
        destructor.manifest = self.manifest
        destroy_phases = destructor.get_phases(config)

        # Prefix destruction phase names to avoid conflicts
        for name, action, desc in destroy_phases:
            phases.append((f'cleanup_{name}', action, f'Cleanup: {desc}'))

        return phases
