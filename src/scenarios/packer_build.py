"""Packer image build scenario.

Builds Debian cloud images using packer. Supports local and remote execution.

Prerequisites for remote builds:
1. Target host must be bootstrapped: curl -fsSL .../install.sh | bash
2. Packer module installed: homestak install packer

For dev workflow (testing uncommitted changes):
- Use packer-sync-build-fetch to sync local changes before building
"""

import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from common import ActionResult, run_command, run_ssh
from config import HostConfig, get_sibling_dir
from scenarios import register_scenario

logger = logging.getLogger(__name__)

# Available templates
TEMPLATES = ['debian-12-custom', 'debian-13-custom']


@dataclass
class SyncPackerAction:
    """Sync local packer repo to remote host (for dev workflow)."""
    name: str
    packer_dir: Optional[str] = None
    remote_path: str = '/opt/homestak/packer'

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Rsync local packer to remote."""
        start = time.time()

        remote_ip = context.get('remote_ip') or config.ssh_host
        if not remote_ip:
            return ActionResult(
                success=False,
                message="No target host: use --remote <IP>",
                duration=time.time() - start
            )

        user = config.ssh_user
        local_packer = Path(self.packer_dir) if self.packer_dir else get_sibling_dir('packer')

        if not local_packer.exists():
            return ActionResult(
                success=False,
                message=f"Local packer directory not found: {local_packer}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Syncing {local_packer} to {user}@{remote_ip}:{self.remote_path}")

        # Rsync with exclusions
        rc, out, err = run_command([
            'rsync', '-av', '--delete',
            '--exclude=.git',
            '--exclude=images',
            '--exclude=logs',
            '--exclude=cache',
            f'{local_packer}/',
            f'{user}@{remote_ip}:{self.remote_path}/'
        ], timeout=120)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Rsync failed: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Synced packer to {remote_ip}:{self.remote_path}",
            duration=time.time() - start
        )


@dataclass
class PackerBuildAction:
    """Build packer images locally or remotely."""
    name: str
    templates: list[str] = None  # None = all templates
    packer_dir: Optional[str] = None  # Override packer directory

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Build packer images."""
        start = time.time()

        # Determine which templates to build
        templates = self.templates or context.get('templates') or TEMPLATES

        # Local or remote execution
        if context.get('local_mode'):
            return self._build_local(config, context, templates, start)
        else:
            return self._build_remote(config, context, templates, start)

    def _build_local(self, config: HostConfig, context: dict, templates: list[str], start: float) -> ActionResult:
        """Build images locally."""
        packer_dir = Path(self.packer_dir) if self.packer_dir else get_sibling_dir('packer')

        if not packer_dir.exists():
            return ActionResult(
                success=False,
                message=f"Packer directory not found: {packer_dir}",
                duration=time.time() - start
            )

        templates_dir = packer_dir / 'templates'
        images_dir = packer_dir / 'images'
        images_dir.mkdir(exist_ok=True)

        # Get SSH key path
        ssh_key = str(Path.home() / '.ssh' / 'id_rsa')
        if context.get('ssh_key'):
            ssh_key = context['ssh_key']

        built = []
        failed = []

        for template in templates:
            template_file = templates_dir / f'{template}.pkr.hcl'
            if not template_file.exists():
                failed.append(f"{template}: template not found")
                continue

            logger.info(f"[{self.name}] Building {template}...")

            # Initialize packer plugins
            rc, out, err = run_command(
                ['packer', 'init', str(template_file)],
                cwd=packer_dir,
                timeout=120
            )
            if rc != 0:
                failed.append(f"{template}: init failed - {err}")
                continue

            # Build image
            rc, out, err = run_command(
                ['packer', 'build', '-force',
                 '-var', f'ssh_private_key_file={ssh_key}',
                 str(template_file)],
                cwd=packer_dir,
                timeout=600,  # 10 minute timeout per image
                capture=False  # Stream output
            )
            if rc != 0:
                failed.append(f"{template}: build failed - {err}")
                continue

            # Check output exists - output dir is debian-{version}, file is {template}.qcow2
            version_dir = template.rsplit('-', 1)[0]  # debian-12-custom -> debian-12
            output_dir = images_dir / version_dir
            qcow2_file = output_dir / f'{template}.qcow2'
            if qcow2_file.exists():
                built.append(template)
                context[f'{template}_image'] = str(qcow2_file)
            else:
                failed.append(f"{template}: output not found")

        # Summary
        if failed:
            return ActionResult(
                success=False,
                message=f"Built {len(built)}/{len(templates)}: {', '.join(failed)}",
                duration=time.time() - start,
                context_updates={'built_images': built}
            )

        return ActionResult(
            success=True,
            message=f"Built {len(built)} images: {', '.join(built)}",
            duration=time.time() - start,
            context_updates={'built_images': built}
        )

    def _build_remote(self, config: HostConfig, context: dict, templates: list[str], start: float) -> ActionResult:
        """Build images on remote host via SSH."""
        # Get remote host
        remote_ip = context.get('remote_ip') or config.ssh_host
        if not remote_ip:
            return ActionResult(
                success=False,
                message="No target host: use --local, --remote <IP>, or configure ssh_host",
                duration=time.time() - start
            )

        user = config.ssh_user
        sudo = '' if user == 'root' else 'sudo '

        # Determine packer path on remote
        packer_path = self.packer_dir or '/opt/homestak/packer'

        built = []
        failed = []

        for template in templates:
            logger.info(f"[{self.name}] Building {template} on {remote_ip}...")

            # Initialize packer
            cmd = f'cd {packer_path} && packer init templates/{template}.pkr.hcl'
            rc, out, err = run_ssh(remote_ip, cmd, user=user, timeout=120)
            if rc != 0:
                failed.append(f"{template}: init failed - {err}")
                continue

            # Build image (longer timeout for actual build)
            cmd = f'cd {packer_path} && packer build -force templates/{template}.pkr.hcl'
            rc, out, err = run_ssh(remote_ip, cmd, user=user, timeout=600)
            if rc != 0:
                failed.append(f"{template}: build failed - {err}")
                continue

            # Verify output - output dir is debian-{version}, file is {template}.qcow2
            # e.g., debian-12-custom -> images/debian-12/debian-12-custom.qcow2
            output_dir = template.rsplit('-', 1)[0]  # debian-12-custom -> debian-12
            cmd = f'test -f {packer_path}/images/{output_dir}/{template}.qcow2 && echo exists'
            rc, out, err = run_ssh(remote_ip, cmd, user=user, timeout=30)
            if rc == 0 and 'exists' in out:
                built.append(template)
            else:
                failed.append(f"{template}: output not found")

        # Summary
        if failed:
            return ActionResult(
                success=False,
                message=f"Built {len(built)}/{len(templates)} on {remote_ip}: {', '.join(failed)}",
                duration=time.time() - start,
                context_updates={'built_images': built, 'remote_ip': remote_ip}
            )

        return ActionResult(
            success=True,
            message=f"Built {len(built)} images on {remote_ip}: {', '.join(built)}",
            duration=time.time() - start,
            context_updates={'built_images': built, 'remote_ip': remote_ip}
        )


@dataclass
class PackerPublishAction:
    """Publish built images to PVE storage."""
    name: str
    packer_dir: Optional[str] = None

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Copy images to /var/lib/vz/template/iso/."""
        start = time.time()
        remote_ip = context.get('remote_ip') or config.ssh_host
        user = config.ssh_user
        sudo = '' if user == 'root' else 'sudo '

        packer_path = self.packer_dir or '/opt/homestak/packer'
        built_images = context.get('built_images', TEMPLATES)

        published = []
        for template in built_images:
            src = f'{packer_path}/images/{template}/{template}.qcow2'
            dst = f'/var/lib/vz/template/iso/{template}.img'

            logger.info(f"[{self.name}] Publishing {template} to PVE storage...")

            cmd = f'{sudo}cp {src} {dst}'
            rc, out, err = run_ssh(remote_ip, cmd, user=user, timeout=120)
            if rc == 0:
                published.append(template)
            else:
                logger.warning(f"Failed to publish {template}: {err}")

        return ActionResult(
            success=len(published) > 0,
            message=f"Published {len(published)} images: {', '.join(published)}",
            duration=time.time() - start
        )


@dataclass
class FetchImagesAction:
    """Fetch built images from remote host to local machine."""
    name: str
    local_dest: str = '/tmp/packer-images'
    packer_dir: Optional[str] = None

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """SCP images from remote host."""
        start = time.time()
        remote_ip = context.get('remote_ip') or config.ssh_host
        user = config.ssh_user

        packer_path = self.packer_dir or '/opt/homestak/packer'
        built_images = context.get('built_images', TEMPLATES)

        # Create local destination
        dest = Path(self.local_dest)
        dest.mkdir(parents=True, exist_ok=True)

        fetched = []
        for template in built_images:
            # Output dir is debian-{version}, file is {template}.qcow2
            output_dir = template.rsplit('-', 1)[0]  # debian-12-custom -> debian-12
            src = f'{user}@{remote_ip}:{packer_path}/images/{output_dir}/{template}.qcow2'
            dst = dest / f'{template}.qcow2'

            logger.info(f"[{self.name}] Fetching {template} from {remote_ip}...")

            rc, out, err = run_command(
                ['scp', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
                 src, str(dst)],
                timeout=300
            )
            if rc == 0:
                fetched.append(template)
                context[f'{template}_local'] = str(dst)
            else:
                logger.warning(f"Failed to fetch {template}: {err}")

        return ActionResult(
            success=len(fetched) > 0,
            message=f"Fetched {len(fetched)} images to {dest}: {', '.join(fetched)}",
            duration=time.time() - start,
            context_updates={'fetched_images': fetched, 'images_dir': str(dest)}
        )


@register_scenario
class PackerBuild:
    """Build packer images locally or remotely."""

    name = 'packer-build'
    description = 'Build Debian cloud images with packer'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for packer build."""
        return [
            ('build', PackerBuildAction(
                name='build-images',
            ), 'Build packer images'),
        ]


@register_scenario
class PackerBuildAndPublish:
    """Build and publish packer images (remote only)."""

    name = 'packer-build-publish'
    description = 'Build and publish images to PVE storage'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for build and publish."""
        return [
            ('build', PackerBuildAction(
                name='build-images',
            ), 'Build packer images'),
            ('publish', PackerPublishAction(
                name='publish-images',
            ), 'Publish to PVE storage'),
        ]


@register_scenario
class PackerBuildAndFetch:
    """Build on remote, fetch to local (for release)."""

    name = 'packer-build-fetch'
    description = 'Build on remote host, fetch images locally'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for build and fetch."""
        return [
            ('build', PackerBuildAction(
                name='build-images',
            ), 'Build packer images'),
            ('fetch', FetchImagesAction(
                name='fetch-images',
            ), 'Fetch images to local'),
        ]


@register_scenario
class PackerSync:
    """Sync local packer repo to remote (dev workflow)."""

    name = 'packer-sync'
    description = 'Sync local packer changes to remote host'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for sync."""
        return [
            ('sync', SyncPackerAction(
                name='sync-packer',
            ), 'Sync packer to remote'),
        ]


@register_scenario
class PackerSyncBuildFetch:
    """Sync local changes, build on remote, fetch results (dev workflow)."""

    name = 'packer-sync-build-fetch'
    description = 'Sync local changes, build remotely, fetch images'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for sync, build, and fetch."""
        return [
            ('sync', SyncPackerAction(
                name='sync-packer',
            ), 'Sync packer to remote'),
            ('build', PackerBuildAction(
                name='build-images',
            ), 'Build packer images'),
            ('fetch', FetchImagesAction(
                name='fetch-images',
            ), 'Fetch images to local'),
        ]
