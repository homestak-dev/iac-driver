"""File download and management actions."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from common import ActionResult, run_ssh
from config import HostConfig

logger = logging.getLogger(__name__)


@dataclass
class RemoveImageAction:
    """Remove packer image from PVE host."""
    name: str
    image_dir: str = '/var/lib/vz/template/iso'
    fail_if_missing: bool = False

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Remove image from PVE host."""
        start = time.time()

        pve_host = config.ssh_host
        image_name = config.packer_image.replace('.qcow2', '.img')
        image_path = f'{self.image_dir}/{image_name}'

        # Check if image exists
        logger.info(f"[{self.name}] Checking for {image_name} on {pve_host}...")
        rc, out, err = run_ssh(pve_host, f'test -f {image_path} && echo exists', timeout=30)

        if rc != 0 or 'exists' not in out:
            if self.fail_if_missing:
                return ActionResult(
                    success=False,
                    message=f"Image {image_name} not found",
                    duration=time.time() - start
                )
            return ActionResult(
                success=True,
                message=f"Image {image_name} already absent",
                duration=time.time() - start
            )

        # Remove image
        logger.info(f"[{self.name}] Removing {image_name} from {pve_host}...")
        rc, out, err = run_ssh(pve_host, f'rm -f {image_path}', timeout=30)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to remove image: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Removed {image_name}",
            duration=time.time() - start
        )


@dataclass
class DownloadFileAction:
    """Download a file from a URL to a remote host."""
    name: str
    url: str
    dest_dir: str
    dest_filename: Optional[str] = None  # if None, use filename from URL
    host_key: str = 'inner_ip'
    rename_ext: Optional[str] = None  # e.g., '.img' to rename .qcow2 files
    timeout: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Download file to remote host."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        # Determine filename
        filename = self.dest_filename or self.url.split('/')[-1]
        dest = f"{self.dest_dir}/{filename}"

        # Create target directory
        logger.info(f"[{self.name}] Creating directory {self.dest_dir}...")
        rc, _, err = run_ssh(host, f'mkdir -p {self.dest_dir}', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to create directory: {err}",
                duration=time.time() - start
            )

        # Download file
        logger.info(f"[{self.name}] Downloading {self.url}...")
        dl_cmd = f'curl -fSL -o {dest} {self.url}'
        rc, _, err = run_ssh(host, dl_cmd, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to download from {self.url}: {err}",
                duration=time.time() - start
            )

        final_filename = filename

        # Rename extension if requested
        if self.rename_ext:
            # Find current extension
            if '.' in filename:
                base = filename.rsplit('.', 1)[0]
                new_filename = base + self.rename_ext
                rename_cmd = f'mv {dest} {self.dest_dir}/{new_filename} 2>/dev/null || true'
                run_ssh(host, rename_cmd, timeout=30)
                final_filename = new_filename

        # Verify file exists
        verify_path = f"{self.dest_dir}/{final_filename}"
        rc, _, err = run_ssh(host, f'ls -la {verify_path}', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"File not found after download: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Downloaded {final_filename}",
            duration=time.time() - start,
            context_updates={'downloaded_file': final_filename}
        )


@dataclass
class DownloadGitHubReleaseAction:
    """Download an asset from a GitHub release."""
    name: str
    asset_name: str  # e.g., "debian-12-custom.qcow2"
    dest_dir: str = '/var/lib/vz/template/iso'
    host_key: str = 'inner_ip'
    rename_ext: Optional[str] = '.img'  # Proxmox convention
    timeout: int = 300

    def _resolve_latest_tag(self, repo: str, host: str) -> Optional[str]:
        """Resolve 'latest' to actual tag name via GitHub API.

        GitHub download URLs require the actual tag name, not 'latest'.
        This queries the API to get the real tag for the latest release.
        """
        api_url = f'https://api.github.com/repos/{repo}/releases/latest'
        # Use curl with jq to extract tag_name
        cmd = f"curl -fsSL '{api_url}' | jq -r '.tag_name'"
        rc, out, err = run_ssh(host, cmd, timeout=30)
        if rc == 0 and out.strip():
            return out.strip()
        return None

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Download release asset."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        repo = config.packer_release_repo
        tag = config.packer_release

        # Resolve 'latest' to actual tag name (GitHub URLs require real tag)
        if tag == 'latest':
            resolved_tag = self._resolve_latest_tag(repo, host)
            if resolved_tag:
                logger.info(f"[{self.name}] Resolved 'latest' to tag {resolved_tag}")
                tag = resolved_tag
            else:
                return ActionResult(
                    success=False,
                    message=f"Failed to resolve 'latest' release tag for {repo}",
                    duration=time.time() - start
                )

        url = f'https://github.com/{repo}/releases/download/{tag}/{self.asset_name}'
        dest = f"{self.dest_dir}/{self.asset_name}"

        # Create target directory
        logger.info(f"[{self.name}] Creating directory {self.dest_dir}...")
        rc, _, err = run_ssh(host, f'mkdir -p {self.dest_dir}', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to create directory: {err}",
                duration=time.time() - start
            )

        # Download file
        logger.info(f"[{self.name}] Downloading {self.asset_name} from {repo} release {tag}...")
        dl_cmd = f'curl -fSL -o {dest} {url}'
        rc, _, err = run_ssh(host, dl_cmd, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to download from {url}: {err}",
                duration=time.time() - start
            )

        final_filename = self.asset_name

        # Rename extension if requested (e.g., .qcow2 -> .img)
        if self.rename_ext and '.' in self.asset_name:
            base = self.asset_name.rsplit('.', 1)[0]
            new_filename = base + self.rename_ext
            rename_cmd = f'mv {dest} {self.dest_dir}/{new_filename} 2>/dev/null || true'
            run_ssh(host, rename_cmd, timeout=30)
            final_filename = new_filename

        # Verify file exists
        verify_path = f"{self.dest_dir}/{final_filename}"
        rc, _, err = run_ssh(host, f'ls -la {verify_path}', timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"File not found after download: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Downloaded {final_filename}",
            duration=time.time() - start,
            context_updates={'packer_image': final_filename}
        )
