"""PVE setup scenario.

Installs PVE (if needed) and configures a Proxmox VE host.
Supports both local and remote execution.

After PVE is installed and configured, generates nodes/{hostname}.yaml
to enable the host for use with vm-constructor and other scenarios.
"""

import json
import logging
import re
import subprocess
import time

from actions import AnsiblePlaybookAction, AnsibleLocalPlaybookAction, EnsurePVEAction
from common import ActionResult, run_command, run_ssh, wait_for_ssh
from config import HostConfig, get_sibling_dir, get_site_config_dir
from scenarios import register_scenario

logger = logging.getLogger(__name__)


@register_scenario
class PVESetup:
    """Install and configure a PVE host."""

    name = 'pve-setup'
    description = 'Install PVE (if needed) and configure host'
    requires_root = True
    requires_host_config = False
    requires_api = False  # pve-setup installs PVE — no API available yet
    expected_runtime = 180  # ~3 min (skip if PVE already installed)

    def get_phases(self, _config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for PVE setup.

        Uses local or remote actions based on context:
        - context['local_mode'] = True: Run locally
        - context['remote_ip'] set: Run on remote host
        """
        return [
            ('ensure_pve', _EnsurePVEPhase(), 'Ensure PVE installed'),
            ('setup_pve', _PVESetupPhase(), 'Run pve-setup.yml'),
            ('generate_node_config', _GenerateNodeConfigPhase(), 'Generate node config'),
            ('create_api_token', _CreateApiTokenPhase(), 'Create API token'),
        ]


class _EnsurePVEPhase:
    """Phase that ensures PVE is installed locally or remotely.

    Local mode uses split playbooks (kernel → reboot → packages) to work
    around Ansible 2.20 blocking ansible.builtin.reboot with local connection.
    Remote mode uses the combined pve-install.yml where reboot module works.
    """

    def run(self, config: HostConfig, context: dict):
        """Ensure PVE is installed locally or remotely."""
        start = time.time()

        if context.get('local_mode'):
            return self._run_local(config, context, start)

        return self._run_remote(config, context, start)

    def _run_local(self, _config: HostConfig, _context: dict, start: float):
        """Install PVE locally with scenario-managed reboot."""
        # Check locally if PVE is running
        result = subprocess.run(
            ['systemctl', 'is-active', 'pveproxy'],
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )
        if result.returncode == 0 and 'active' in result.stdout:
            return ActionResult(
                success=True,
                message="PVE already installed and running - skipped",
                duration=time.time() - start
            )

        ansible_dir = get_sibling_dir('ansible')
        if not ansible_dir.exists():
            return ActionResult(
                success=False,
                message=f"Ansible directory not found: {ansible_dir}",
                duration=time.time() - start
            )

        # Check if Proxmox kernel is already installed (post-reboot re-run)
        kernel_check = subprocess.run(
            ['dpkg', '-l', 'proxmox-default-kernel'],
            capture_output=True, text=True, timeout=30, check=False
        )
        kernel_installed = kernel_check.returncode == 0 and 'ii' in kernel_check.stdout

        pve_pkg_check = subprocess.run(
            ['dpkg', '-l', 'proxmox-ve'],
            capture_output=True, text=True, timeout=30, check=False
        )
        pve_installed = pve_pkg_check.returncode == 0 and 'ii' in pve_pkg_check.stdout

        # Determine hostname for ansible extra-vars (inventory uses 'localhost')
        import socket
        hostname = socket.gethostname()

        if kernel_installed and not pve_installed:
            # Kernel installed but PVE packages not yet — skip to phase 2
            logger.info("Proxmox kernel installed, running phase 2 (packages)...")
        elif not kernel_installed:
            # Phase 1: Install Proxmox kernel
            logger.info("Phase 1: Installing Proxmox kernel...")
            cmd = [
                'ansible-playbook',
                '-i', 'inventory/local.yml',
                'playbooks/pve-install-kernel.yml',
                '-e', f'pve_hostname={hostname}',
            ]
            rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=1200)
            if rc != 0:
                error_msg = err[-500:] if err else out[-500:]
                return ActionResult(
                    success=False,
                    message=f"pve-install-kernel.yml failed: {error_msg}",
                    duration=time.time() - start
                )

            # Reboot to load Proxmox kernel
            logger.info("Rebooting to load Proxmox kernel...")
            subprocess.run(
                ['systemctl', 'reboot'],
                check=False, timeout=30
            )
            # This process will be killed by the reboot.
            # On restart, pve-setup will be re-invoked and resume at phase 2
            # because kernel_installed=True and pve_installed=False.
            time.sleep(300)  # Wait for reboot to kill us
            return ActionResult(
                success=False,
                message="Reboot did not occur within timeout",
                duration=time.time() - start
            )

        # Phase 2: Install PVE packages (after reboot)
        logger.info("Phase 2: Installing PVE packages...")
        cmd = [
            'ansible-playbook',
            '-i', 'inventory/local.yml',
            'playbooks/pve-install-packages.yml',
            '-e', f'pve_hostname={hostname}',
        ]
        rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=1200)
        if rc != 0:
            error_msg = err[-500:] if err else out[-500:]
            return ActionResult(
                success=False,
                message=f"pve-install-packages.yml failed: {error_msg}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message="PVE installed successfully",
            duration=time.time() - start
        )

    def _run_remote(self, config: HostConfig, context: dict, start: float):
        """Install PVE on remote host (reboot handled by ansible)."""
        remote_ip = context.get('remote_ip') or config.ssh_host
        if not remote_ip:
            return ActionResult(
                success=False,
                message="No target host: use --local, --remote <IP>, or configure ssh_host",
                duration=time.time() - start
            )
        context['remote_ip'] = remote_ip

        # Wait for SSH first
        if not wait_for_ssh(remote_ip, timeout=120):
            return ActionResult(
                success=False,
                message=f"SSH not available on {remote_ip}",
                duration=time.time() - start
            )

        action = EnsurePVEAction(
            name='ensure-pve-remote',
            host_key='remote_ip',
            pve_hostname=config.name or 'pve',
        )
        return action.run(config, context)


class _PVESetupPhase:
    """Phase that runs pve-setup.yml locally or remotely."""

    def run(self, config: HostConfig, context: dict):
        """Run pve-setup.yml locally or remotely."""
        if context.get('local_mode'):
            action = AnsibleLocalPlaybookAction(
                name='pve-setup-local',
                playbook='playbooks/pve-setup.yml',
            )
        else:
            # Use remote_ip from context, or fall back to config.ssh_host
            remote_ip = context.get('remote_ip') or config.ssh_host
            if not remote_ip:
                return ActionResult(
                    success=False,
                    message="No target host: use --local, --remote <IP>, or configure ssh_host",
                    duration=0
                )
            # Ensure remote_ip is in context for AnsiblePlaybookAction
            context['remote_ip'] = remote_ip
            action = AnsiblePlaybookAction(
                name='pve-setup-remote',
                playbook='playbooks/pve-setup.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={'ansible_user': config.ssh_user},
                host_key='remote_ip',
                wait_for_ssh_before=True,
            )
        return action.run(config, context)


class _GenerateNodeConfigPhase:
    """Phase that generates nodes/{hostname}.yaml after PVE setup.

    Creates the node configuration file that enables the host for use
    with vm-constructor and other PVE-dependent scenarios.

    In remote mode, also copies the generated config back to local site-config.
    """

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Generate node config locally or remotely."""
        start = time.time()

        if context.get('local_mode'):
            return self._run_local(config, context, start)
        return self._run_remote(config, context, start)

    def _run_local(self, _config: HostConfig, _context: dict, start: float) -> ActionResult:
        """Generate node config locally."""
        try:
            site_config_dir = get_site_config_dir()
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Cannot find site-config: {e}",
                duration=time.time() - start
            )

        logger.info("Generating node config locally...")
        rc, out, err = run_command(
            ['make', 'node-config', 'FORCE=1'],
            cwd=site_config_dir,
            timeout=60
        )

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"make node-config failed: {err or out}",
                duration=time.time() - start
            )

        # Extract hostname from output or detect it
        import socket
        hostname = socket.gethostname()
        node_file = site_config_dir / 'nodes' / f'{hostname}.yaml'

        return ActionResult(
            success=True,
            message=f"Generated {node_file}",
            duration=time.time() - start,
            context_updates={'generated_node_config': str(node_file)}
        )

    def _run_remote(self, config: HostConfig, context: dict, start: float) -> ActionResult:
        """Generate node config on remote host and sync back."""
        remote_ip = context.get('remote_ip') or config.ssh_host
        if not remote_ip:
            return ActionResult(
                success=False,
                message="No remote_ip in context",
                duration=time.time() - start
            )

        # Determine site-config path on remote (FHS or legacy)
        # Try FHS first, fall back to legacy
        detect_cmd = '''
if [ -d /usr/local/etc/homestak ]; then
    echo "/usr/local/etc/homestak"
elif [ -d /opt/homestak/site-config ]; then
    echo "/opt/homestak/site-config"
else
    echo "NOT_FOUND"
fi
'''
        rc, remote_site_config, _ = run_ssh(remote_ip, detect_cmd, timeout=10)
        remote_site_config = remote_site_config.strip()

        if rc != 0 or remote_site_config == "NOT_FOUND":
            return ActionResult(
                success=False,
                message="site-config not found on remote host. Is it bootstrapped?",
                duration=time.time() - start
            )

        # Generate node config on remote
        logger.info(f"Generating node config on {remote_ip}...")
        rc, out, err = run_ssh(
            remote_ip,
            f'cd {remote_site_config} && make node-config FORCE=1',
            timeout=60
        )

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Remote make node-config failed: {err or out}",
                duration=time.time() - start
            )

        # Get hostname from remote
        rc, remote_hostname, _ = run_ssh(remote_ip, 'hostname', timeout=10)
        remote_hostname = remote_hostname.strip()

        if not remote_hostname:
            return ActionResult(
                success=False,
                message="Could not determine remote hostname",
                duration=time.time() - start
            )

        # Copy generated node config back to local site-config
        try:
            local_site_config = get_site_config_dir()
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Cannot find local site-config: {e}",
                duration=time.time() - start
            )

        remote_node_file = f'{remote_site_config}/nodes/{remote_hostname}.yaml'
        local_node_file = local_site_config / 'nodes' / f'{remote_hostname}.yaml'

        logger.info(f"Copying {remote_node_file} to {local_node_file}...")

        # Use scp to copy the file
        scp_cmd = [
            'scp',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            f'root@{remote_ip}:{remote_node_file}',
            str(local_node_file)
        ]

        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            return ActionResult(
                success=False,
                message=f"scp failed: {result.stderr}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Generated and synced nodes/{remote_hostname}.yaml",
            duration=time.time() - start,
            context_updates={
                'generated_node_config': str(local_node_file),
                'remote_hostname': remote_hostname
            }
        )


class _CreateApiTokenPhase:
    """Phase that creates PVE API token and injects into secrets.yaml.

    Creates a 'tofu' API token via pveum, injects the token value into
    secrets.yaml (both local and remote in remote mode), and verifies
    it works against the PVE API.

    Idempotent: if a working token for this hostname already exists
    in local secrets.yaml, the phase is skipped.
    """

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Create API token locally or remotely."""
        start = time.time()

        if context.get('local_mode'):
            return self._run_local(config, context, start)
        return self._run_remote(config, context, start)

    def _run_local(self, _config: HostConfig, _context: dict, start: float) -> ActionResult:
        """Create API token on local PVE host."""
        import socket
        hostname = socket.gethostname()
        api_url = 'https://127.0.0.1:8006'

        try:
            site_config_dir = get_site_config_dir()
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Cannot find site-config: {e}",
                duration=time.time() - start
            )

        # Check for existing working token
        existing = self._get_existing_token(site_config_dir, hostname)
        if existing and self._verify_token(api_url, existing):
            return ActionResult(
                success=True,
                message=f"API token for {hostname} already works — skipped",
                duration=time.time() - start
            )

        # Wait for pvedaemon to be ready (pveum talks to it)
        if not self._wait_for_pvedaemon_local():
            return ActionResult(
                success=False,
                message="pvedaemon not running — cannot create API token",
                duration=time.time() - start
            )

        # Regenerate SSL certs and restart pveproxy before token creation
        # Fixes IPv6-related SSL issues on fresh PVE installs
        logger.debug("Regenerating PVE SSL certificates...")
        subprocess.run(
            'sysctl -w net.ipv6.conf.all.disable_ipv6=1 && '
            'sysctl -w net.ipv6.conf.default.disable_ipv6=1 && '
            'pvecm updatecerts --force 2>/dev/null; '
            'sysctl -w net.ipv6.conf.all.disable_ipv6=0 && '
            'sysctl -w net.ipv6.conf.default.disable_ipv6=0 && '
            'systemctl restart pveproxy && sleep 2',
            shell=True, capture_output=True, timeout=60, check=False
        )

        # Create token via pveum (remove old if exists, since we can't
        # retrieve the value of an existing token)
        logger.info("Creating API token locally...")
        subprocess.run(
            ['pveum', 'user', 'token', 'remove', 'root@pam', 'tofu'],
            capture_output=True, timeout=30, check=False
        )
        result = subprocess.run(
            ['pveum', 'user', 'token', 'add', 'root@pam', 'tofu',
             '--privsep', '0', '--output-format', 'json'],
            capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            return ActionResult(
                success=False,
                message=f"pveum token add failed: {result.stderr or result.stdout}",
                duration=time.time() - start
            )

        full_token = self._parse_token(result.stdout)
        if not full_token:
            return ActionResult(
                success=False,
                message="Failed to parse token from pveum output",
                duration=time.time() - start
            )

        # Inject into local secrets.yaml
        if not self._inject_token_local(site_config_dir, hostname, full_token):
            return ActionResult(
                success=False,
                message="Failed to inject token into secrets.yaml",
                duration=time.time() - start
            )

        # Verify token works against PVE API
        if not self._verify_token(api_url, full_token):
            return ActionResult(
                success=False,
                message="Token created but API verification failed after retries",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"API token created and verified for {hostname}",
            duration=time.time() - start,
            context_updates={'api_token_created': hostname}
        )

    def _run_remote(self, config: HostConfig, context: dict, start: float) -> ActionResult:
        """Create API token on remote PVE host and sync to local secrets."""
        remote_ip = context.get('remote_ip') or config.ssh_host
        if not remote_ip:
            return ActionResult(
                success=False,
                message="No remote_ip in context",
                duration=time.time() - start
            )

        hostname = context.get('remote_hostname')
        if not hostname:
            rc, out, _ = run_ssh(remote_ip, 'hostname', timeout=10)
            if rc != 0 or not out.strip():
                return ActionResult(
                    success=False,
                    message="Could not determine remote hostname",
                    duration=time.time() - start
                )
            hostname = out.strip()

        api_url = f'https://{remote_ip}:8006'

        try:
            site_config_dir = get_site_config_dir()
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Cannot find local site-config: {e}",
                duration=time.time() - start
            )

        # Check for existing working token
        existing = self._get_existing_token(site_config_dir, hostname)
        if existing and self._verify_token(api_url, existing):
            return ActionResult(
                success=True,
                message=f"API token for {hostname} already works — skipped",
                duration=time.time() - start
            )

        # Wait for pvedaemon to be ready on remote
        if not self._wait_for_pvedaemon_remote(remote_ip):
            return ActionResult(
                success=False,
                message=f"pvedaemon not running on {remote_ip} — cannot create API token",
                duration=time.time() - start
            )

        # Create token on remote via SSH
        logger.info(f"Creating API token on {remote_ip}...")
        create_cmd = (
            'pveum user token remove root@pam tofu 2>/dev/null || true; '
            'pveum user token add root@pam tofu --privsep 0 --output-format json'
        )
        rc, out, err = run_ssh(remote_ip, create_cmd, timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"pveum token add failed: {err or out}",
                duration=time.time() - start
            )

        full_token = self._parse_token(out)
        if not full_token:
            return ActionResult(
                success=False,
                message="Failed to parse token from pveum output",
                duration=time.time() - start
            )

        # Inject into remote secrets.yaml
        self._inject_token_remote(remote_ip, hostname, full_token)

        # Inject into local secrets.yaml
        if not self._inject_token_local(site_config_dir, hostname, full_token):
            return ActionResult(
                success=False,
                message="Failed to inject token into local secrets.yaml",
                duration=time.time() - start
            )

        # Verify token works
        if not self._verify_token(api_url, full_token):
            return ActionResult(
                success=False,
                message="Token created but API verification failed after retries",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"API token created and verified for {hostname}",
            duration=time.time() - start,
            context_updates={'api_token_created': hostname}
        )

    @staticmethod
    def _parse_token(pveum_output):
        """Parse full token string from pveum JSON output."""
        try:
            token_data = json.loads(pveum_output.strip())
            return f"{token_data['full-tokenid']}={token_data['value']}"
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse pveum token output: {e}")
            return None

    @staticmethod
    def _get_existing_token(site_config_dir, hostname):
        """Read existing token for hostname from local secrets.yaml.

        Scopes the search to the api_tokens: section to avoid matching
        the same hostname under ssh_keys: or other sections.
        """
        secrets_file = site_config_dir / 'secrets.yaml'
        if not secrets_file.exists():
            return None
        content = secrets_file.read_text()
        # Extract the api_tokens section (indented block after "api_tokens:")
        section_match = re.search(
            r'^api_tokens:\s*\n((?:[ \t]+.+\n)*)',
            content, re.MULTILINE
        )
        if not section_match:
            return None
        section = section_match.group(1)
        # Match hostname within the api_tokens section only
        token_match = re.search(
            rf'^\s*{re.escape(hostname)}:\s*"?([^"\n]+)"?\s*$',
            section, re.MULTILINE
        )
        return token_match.group(1).strip() if token_match else None

    @staticmethod
    def _verify_token(api_url, token, retries=3, delay=5):
        """Verify token works against PVE API with retries.

        Uses stdlib urllib (no curl dependency). Retries handle the case
        where pveproxy hasn't fully started after PVE installation.
        """
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            f'{api_url}/api2/json/version',
            headers={'Authorization': f'PVEAPIToken={token}'}
        )
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            if attempt < retries - 1:
                logger.debug("API verification attempt %d/%d failed, "
                             "retrying in %ds...", attempt + 1, retries, delay)
                time.sleep(delay)
        return False

    @staticmethod
    def _wait_for_pvedaemon_local():
        """Wait for pvedaemon to be active (single retry with 10s sleep)."""
        result = subprocess.run(
            ['systemctl', 'is-active', 'pvedaemon'],
            capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode == 0:
            return True
        logger.debug("pvedaemon not yet active, waiting 10s...")
        time.sleep(10)
        result = subprocess.run(
            ['systemctl', 'is-active', 'pvedaemon'],
            capture_output=True, text=True, timeout=10, check=False
        )
        return result.returncode == 0

    @staticmethod
    def _wait_for_pvedaemon_remote(remote_ip):
        """Wait for pvedaemon to be active on remote host."""
        rc, _, _ = run_ssh(remote_ip, 'systemctl is-active pvedaemon', timeout=10)
        if rc == 0:
            return True
        logger.debug("pvedaemon not yet active on %s, waiting 10s...", remote_ip)
        time.sleep(10)
        rc, _, _ = run_ssh(remote_ip, 'systemctl is-active pvedaemon', timeout=10)
        return rc == 0

    @staticmethod
    def _inject_token_local(site_config_dir, hostname, full_token):
        """Inject token into local secrets.yaml."""
        secrets_file = site_config_dir / 'secrets.yaml'

        # Initialize secrets if needed (decrypt .enc or copy .example)
        if not secrets_file.exists():
            run_command(
                ['make', 'init-secrets'], cwd=site_config_dir, timeout=30
            )
            if not secrets_file.exists():
                logger.error("secrets.yaml not found — no .enc or .example available")
                return False

        content = secrets_file.read_text()
        new_line = f'{hostname}: "{full_token}"'

        # Update existing or add new token entry
        # Scope replacement to api_tokens section by matching indented lines
        pattern = re.compile(
            rf'^(\s*){re.escape(hostname)}:.*$', re.MULTILINE
        )
        if pattern.search(content):
            # Use lambda to avoid regex replacement escaping issues
            # (token value could theoretically contain \, &, etc.)
            content = pattern.sub(
                lambda m: f'{m.group(1)}{new_line}', content
            )
        elif 'api_tokens:' in content:
            content = content.replace(
                'api_tokens:\n',
                f'api_tokens:\n  {new_line}\n',
                1
            )
        else:
            content += f'\napi_tokens:\n  {new_line}\n'

        secrets_file.write_text(content)
        logger.info(f"Injected API token for {hostname} into {secrets_file}")
        return True

    @staticmethod
    def _inject_token_remote(remote_ip, hostname, full_token):
        """Inject token into remote host's secrets.yaml."""
        # Validate hostname is safe for shell interpolation (RFC 952)
        if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$', hostname):
            logger.error(f"Invalid hostname format, skipping remote injection: {hostname}")
            return

        # Escape token value for sed replacement (| delimiter)
        # Special chars in sed replacement: \, &, |
        safe_token = full_token.replace('\\', '\\\\').replace('&', '\\&').replace('|', '\\|')

        secrets_file = '/usr/local/etc/homestak/secrets.yaml'
        inject_cmd = f'''
if [ -f {secrets_file} ]; then
    if grep -q "^\\s*{hostname}:" {secrets_file}; then
        sed -i 's|^\\(\\s*\\){hostname}:.*$|\\1{hostname}: "{safe_token}"|' {secrets_file}
    elif grep -q "^api_tokens:" {secrets_file}; then
        sed -i '/^api_tokens:/a\\  {hostname}: "{safe_token}"' {secrets_file}
    fi
fi
'''
        rc, _, err = run_ssh(remote_ip, inject_cmd, timeout=30)
        if rc != 0:
            logger.warning(f"Failed to inject token on remote: {err}")
