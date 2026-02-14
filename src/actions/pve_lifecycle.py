"""PVE lifecycle actions for nested/recursive deployments.

These actions handle the bootstrapping and configuration of inner PVE hosts:
- Bootstrap (curl|bash installer)
- Secrets management (copy, inject SSH keys, API tokens)
- Network configuration (vmbr0 bridge)
- Node config generation
- Image management (ensure packer image exists)

Extracted from scenarios/recursive_pve.py for reuse by the operator engine.
"""

import base64
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from common import ActionResult, run_ssh
from config import HostConfig

logger = logging.getLogger(__name__)


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
class EnsureImageAction:
    """Ensure packer image exists on PVE host, download if missing."""
    name: str

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check for image, download from release if missing."""
        start = time.time()

        pve_host = config.ssh_host
        ssh_user = config.ssh_user
        image_name = config.packer_image.replace('.qcow2', '.img')
        image_path = f'/var/lib/vz/template/iso/{image_name}'

        # Use sudo if not root
        sudo = '' if ssh_user == 'root' else 'sudo '

        # Check if image exists
        logger.info(f"[{self.name}] Checking for {image_name} on {pve_host}...")
        rc, out, err = run_ssh(pve_host, f'{sudo}test -f {image_path} && echo exists',
                               user=ssh_user, timeout=30)

        if rc == 0 and 'exists' in out:
            return ActionResult(
                success=True,
                message=f"Image {image_name} already exists",
                duration=time.time() - start
            )

        # Download from release
        repo = config.packer_release_repo
        tag = config.packer_release
        url = f'https://github.com/{repo}/releases/download/{tag}/{config.packer_image}'

        logger.info(f"[{self.name}] Downloading {config.packer_image} from {repo} {tag}...")

        # Create directory and download
        rc, out, err = run_ssh(pve_host, f'{sudo}mkdir -p /var/lib/vz/template/iso',
                               user=ssh_user, timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to create directory: {err}",
                duration=time.time() - start
            )

        dl_cmd = f'{sudo}curl -fSL -o {image_path} {url}'
        rc, out, err = run_ssh(pve_host, dl_cmd, user=ssh_user, timeout=300)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to download image: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Downloaded {image_name}",
            duration=time.time() - start
        )


@dataclass
class CreateApiTokenAction:
    """Create API token on inner PVE and inject into secrets.yaml.

    This action:
    1. Gets the target hostname (used as token key in secrets.yaml)
    2. Regenerates PVE SSL certificates (IPv6 workaround)
    3. Restarts pveproxy
    4. Creates tofu API token via pveum
    5. Injects token into secrets.yaml using hostname as key
    """
    name: str
    host_attr: str = 'vm_ip'
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

        # Step 0: Get the hostname - this becomes the token key in secrets.yaml
        # The node config uses hostname as api_token key, so we must match it
        rc, hostname_out, err = run_ssh(host, 'hostname', user=config.automation_user, timeout=10)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to get hostname: {err or hostname_out}",
                duration=time.time() - start
            )
        token_name = hostname_out.strip()
        logger.debug(f"[{self.name}] Using token key: {token_name}")

        # Step 1: Regenerate PVE SSL certificates and restart pveproxy
        # This fixes IPv6-related SSL issues on fresh installs
        ssl_cmd = '''
sudo sysctl -w net.ipv6.conf.all.disable_ipv6=1
sudo sysctl -w net.ipv6.conf.default.disable_ipv6=1
sudo pvecm updatecerts --force 2>/dev/null || true
sudo sysctl -w net.ipv6.conf.all.disable_ipv6=0
sudo sysctl -w net.ipv6.conf.default.disable_ipv6=0
sudo systemctl restart pveproxy
sleep 2
'''
        rc, out, err = run_ssh(host, ssl_cmd, user=config.automation_user, timeout=60)
        if rc != 0:
            logger.warning(f"[{self.name}] SSL cert regen warning: {err or out}")
            # Continue anyway - this might fail on some systems

        # Step 2: Delete any existing token and create new one
        token_cmd = '''
sudo pveum user token remove root@pam tofu 2>/dev/null || true
sudo pveum user token add root@pam tofu --privsep 0 --output-format json
'''
        rc, out, err = run_ssh(host, token_cmd, user=config.automation_user, timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to create API token: {err or out}",
                duration=time.time() - start
            )

        # Step 3: Parse the token from JSON output
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
        # First try to update existing line, if not found add a new one
        # Use the token_name we retrieved from hostname
        secrets_file = '/usr/local/etc/homestak/secrets.yaml'
        inject_cmd = f'''
# Check if token key exists in secrets.yaml
if grep -q "^\\s*{token_name}:" {secrets_file}; then
    # Update existing line
    sudo sed -i 's|^\\(\\s*\\){token_name}:.*$|\\1{token_name}: {full_token}|' {secrets_file}
else
    # Add new line after api_tokens:
    sudo sed -i '/^api_tokens:/a\\    {token_name}: {full_token}' {secrets_file}
fi
'''
        rc, out, err = run_ssh(host, inject_cmd, user=config.automation_user, timeout=30)
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
        # Note: bootstrap needs sudo for apt/git operations, so we use 'sudo bash'
        if env_source:
            # Dev workflow: use HTTP server from --serve-repos
            # Pass env vars to bash (not curl) so install.sh uses local (uncommitted) code
            # Use 'sudo env VAR=value bash' because 'VAR=value sudo bash' doesn't work -
            # sudo resets the environment by default for security
            env_prefix = f'HOMESTAK_SOURCE={env_source}'
            if env_token:
                env_prefix += f' HOMESTAK_TOKEN={env_token}'
            env_prefix += f' HOMESTAK_REF={env_ref}'
            # Serve-repos uses self-signed TLS; pass -k to curl and
            # HOMESTAK_INSECURE=1 so install.sh sets git http.sslVerify=false
            env_prefix += ' HOMESTAK_INSECURE=1'
            # Include Bearer token in curl header (serve-repos requires auth)
            auth_header = f'-H "Authorization: Bearer {env_token}"' if env_token else ''
            # Use 'sudo env' to pass vars through sudo's environment reset
            bootstrap_cmd = f'curl -fsSLk {auth_header} {env_source}/bootstrap.git/install.sh | sudo env {env_prefix} bash'
            logger.info(f"[{self.name}] Using serve-repos source: {env_source} (ref={env_ref})")
        elif self.source_url:
            # Explicit source_url parameter (legacy)
            bootstrap_cmd = f'curl -fsSL {self.source_url}/install.sh | sudo bash'
        else:
            # Production: use GitHub
            bootstrap_url = 'https://raw.githubusercontent.com/homestak-dev/bootstrap'
            bootstrap_cmd = f'curl -fsSL {bootstrap_url}/{self.ref}/install.sh | sudo bash'

        logger.info(f"[{self.name}] Bootstrapping {host}...")

        # Run bootstrap
        rc, out, err = run_ssh(
            host,
            bootstrap_cmd,
            user=config.automation_user,
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
class SyncDriverCodeAction:
    """Sync iac-driver code from outer host to inner PVE.

    After bootstrap clones from GitHub (master), this action overwrites
    the installed iac-driver with the outer host's working copy. This
    ensures delegation uses the same code as the calling operator.

    Only syncs src/ and run.sh — not tests, docs, or .states/.
    """
    name: str
    host_attr: str = 'vm_ip'
    timeout: int = 120

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Rsync iac-driver source to inner host."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        # Determine local iac-driver path
        from pathlib import Path
        local_driver = Path(__file__).resolve().parent.parent.parent
        if not (local_driver / 'run.sh').exists():
            return ActionResult(
                success=False,
                message=f"Cannot find iac-driver at {local_driver}",
                duration=time.time() - start
            )

        remote_driver = '/usr/local/lib/homestak/iac-driver'

        ssh_opts = 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR'

        logger.info(f"[{self.name}] Syncing iac-driver to {host}...")

        # Rsync src/ directory
        cmd_src = [
            'rsync', '-az', '--delete',
            '-e', ssh_opts,
            f'{local_driver}/src/',
            f'root@{host}:{remote_driver}/src/',
        ]

        try:
            result = subprocess.run(
                cmd_src,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode != 0:
                return ActionResult(
                    success=False,
                    message=f"rsync src/ failed: {result.stderr}",
                    duration=time.time() - start
                )
        except subprocess.TimeoutExpired:
            return ActionResult(
                success=False,
                message=f"rsync timed out after {self.timeout}s",
                duration=time.time() - start
            )

        # Copy run.sh (CLI entry point needed for delegation)
        cmd_run = [
            'rsync', '-az',
            '-e', ssh_opts,
            f'{local_driver}/run.sh',
            f'root@{host}:{remote_driver}/run.sh',
        ]

        try:
            result = subprocess.run(
                cmd_run,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return ActionResult(
                    success=False,
                    message=f"rsync run.sh failed: {result.stderr}",
                    duration=time.time() - start
                )
        except subprocess.TimeoutExpired:
            return ActionResult(
                success=False,
                message=f"rsync run.sh timed out",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Code synced to {host}")
        return ActionResult(
            success=True,
            message=f"iac-driver synced to {host}",
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
            enc_path = secrets_path.with_suffix('.yaml.enc')
            if enc_path.exists():
                msg = (f"secrets.yaml not decrypted at {secrets_path}\n"
                       f"  Run: cd {secrets_path.parent} && make decrypt")
            else:
                msg = f"secrets.yaml not found at {secrets_path}"
            return ActionResult(
                success=False,
                message=msg,
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Copying secrets to {host}...")

        # Use automation_user for VM connections
        user = config.automation_user
        cmd = [
            'scp',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            str(secrets_path),
            f'{user}@{host}:/tmp/secrets.yaml'
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

            # Move from temp location to final location with sudo,
            # then restrict permissions (secrets contain API tokens, SSH keys, signing key)
            install_cmd = (
                'sudo mv /tmp/secrets.yaml /usr/local/etc/homestak/secrets.yaml'
                ' && sudo chmod 600 /usr/local/etc/homestak/secrets.yaml'
                ' && sudo chown root:root /usr/local/etc/homestak/secrets.yaml'
            )
            rc, out, err = run_ssh(host, install_cmd, user=config.automation_user, timeout=30)
            if rc != 0:
                return ActionResult(
                    success=False,
                    message=f"Failed to install secrets: {err or out}",
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
        check_cmd = f"sudo grep -q '^\\s*{self.key_name}:' /usr/local/etc/homestak/secrets.yaml"
        rc, _, _ = run_ssh(host, check_cmd, user=config.automation_user, timeout=30)

        if rc == 0:
            # Key exists, update it
            inject_cmd = f"sudo sed -i 's|^\\(\\s*\\){self.key_name}:.*$|\\1{self.key_name}: {escaped_key}|' /usr/local/etc/homestak/secrets.yaml"
        else:
            # Key doesn't exist, add it after ssh_keys:
            inject_cmd = f"sudo sed -i '/^ssh_keys:/a\\    {self.key_name}: {escaped_key}' /usr/local/etc/homestak/secrets.yaml"

        rc, out, err = run_ssh(host, inject_cmd, user=config.automation_user, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to inject SSH key: {err or out}",
                duration=time.time() - start
            )

        # Verify the key was injected
        verify_cmd = f"sudo grep -q '{self.key_name}:' /usr/local/etc/homestak/secrets.yaml"
        rc, _, _ = run_ssh(host, verify_cmd, user=config.automation_user, timeout=30)
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
class CopySSHPrivateKeyAction:
    """Copy outer host's SSH private key to inner host.

    This enables inner-pve to SSH to its nested VMs. The private key is
    copied to both root and homestak users so that:
    - root: ansible connections work
    - homestak: iac-driver automation_user connections work
    """
    name: str
    host_attr: str = 'vm_ip'
    timeout: int = 60

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Copy SSH private key to target host."""
        start = time.time()

        host = context.get(self.host_attr)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_attr} in context",
                duration=time.time() - start
            )

        # Read local SSH private key
        from pathlib import Path
        privkey_path = Path.home() / '.ssh' / 'id_rsa'
        pubkey_path = Path.home() / '.ssh' / 'id_rsa.pub'
        if not privkey_path.exists():
            privkey_path = Path.home() / '.ssh' / 'id_ed25519'
            pubkey_path = Path.home() / '.ssh' / 'id_ed25519.pub'
        if not privkey_path.exists():
            return ActionResult(
                success=False,
                message="No SSH private key found (~/.ssh/id_rsa or id_ed25519)",
                duration=time.time() - start
            )

        privkey = privkey_path.read_text()
        pubkey = pubkey_path.read_text().strip() if pubkey_path.exists() else ''

        logger.info(f"[{self.name}] Copying SSH private key to {host}...")

        # Copy private key to both root and homestak users
        # Using base64 encoding to avoid shell escaping issues with the key content
        privkey_b64 = base64.b64encode(privkey.encode()).decode()
        pubkey_b64 = base64.b64encode(pubkey.encode()).decode() if pubkey else ''

        copy_script = f'''
set -e
PRIVKEY=$(echo '{privkey_b64}' | base64 -d)
PUBKEY=$(echo '{pubkey_b64}' | base64 -d)

# Copy to root
sudo mkdir -p /root/.ssh
sudo chmod 700 /root/.ssh
echo "$PRIVKEY" | sudo tee /root/.ssh/id_rsa > /dev/null
sudo chmod 600 /root/.ssh/id_rsa
[ -n "$PUBKEY" ] && echo "$PUBKEY" | sudo tee /root/.ssh/id_rsa.pub > /dev/null
[ -f /root/.ssh/id_rsa.pub ] && sudo chmod 644 /root/.ssh/id_rsa.pub

# Copy to homestak
sudo mkdir -p /home/homestak/.ssh
sudo chmod 700 /home/homestak/.ssh
sudo chown homestak:homestak /home/homestak/.ssh
echo "$PRIVKEY" | sudo tee /home/homestak/.ssh/id_rsa > /dev/null
sudo chmod 600 /home/homestak/.ssh/id_rsa
sudo chown homestak:homestak /home/homestak/.ssh/id_rsa
[ -n "$PUBKEY" ] && echo "$PUBKEY" | sudo tee /home/homestak/.ssh/id_rsa.pub > /dev/null
[ -f /home/homestak/.ssh/id_rsa.pub ] && sudo chmod 644 /home/homestak/.ssh/id_rsa.pub && sudo chown homestak:homestak /home/homestak/.ssh/id_rsa.pub

echo "SSH key copied to root and homestak"
'''

        rc, out, err = run_ssh(host, copy_script, user=config.automation_user, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to copy SSH key: {err or out}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] SSH private key copied to {host}")
        return ActionResult(
            success=True,
            message=f"SSH private key copied to {host}",
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
        encoded = base64.b64encode(python_script.encode()).decode()
        inject_script = f"echo '{encoded}' | base64 -d | sudo python3 - {self.key_name}"

        rc, out, err = run_ssh(host, inject_script, user=config.automation_user, timeout=self.timeout)
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
        rc, out, err = run_ssh(host, check_cmd, user=config.automation_user, timeout=30)
        if rc == 0:
            logger.info(f"[{self.name}] vmbr0 already exists on {host}")
            return ActionResult(
                success=True,
                message=f"vmbr0 already configured on {host}",
                duration=time.time() - start
            )

        # Script to create vmbr0 bridge from eth0 with DHCP
        # This preserves the current IP during transition
        # Uses sudo for privileged operations
        bridge_script = '''
set -e

# Get current interface info
IFACE=$(ip -o route get 8.8.8.8 2>/dev/null | grep -oP 'dev \\K\\S+' || echo eth0)
echo "Detected interface: $IFACE"

# Backup interfaces
sudo cp /etc/network/interfaces /etc/network/interfaces.backup.$(date +%s) 2>/dev/null || true

# Create bridge config with DHCP
sudo tee /etc/network/interfaces > /dev/null << 'IFACE_EOF'
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
sudo systemctl restart networking 2>/dev/null || (sudo ifdown eth0; sudo ifup vmbr0)

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

        rc, out, err = run_ssh(host, bridge_script, user=config.automation_user, timeout=self.timeout)
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
        cmd = 'cd /usr/local/etc/homestak && sudo make node-config FORCE=1'
        rc, out, err = run_ssh(host, cmd, user=config.automation_user, timeout=self.timeout)

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
