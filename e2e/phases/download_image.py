"""Phase 4: Download packer image from GitHub release."""

import logging
import time

from ..common import PhaseResult, run_ssh
from ..config import HostConfig

logger = logging.getLogger(__name__)


def run(config: HostConfig, context: dict) -> PhaseResult:
    """Download packer image to inner PVE."""
    start = time.time()

    inner_ip = context.get('inner_ip')
    if not inner_ip:
        return PhaseResult(
            success=False,
            message="No inner_ip in context",
            duration=time.time() - start
        )

    repo = config.packer_release_repo
    tag = config.packer_release_tag
    image = config.packer_image

    # Download image using gh CLI on inner PVE
    logger.info(f"Downloading {image} from {repo} release {tag}...")

    # Create target directory
    rc, out, err = run_ssh(inner_ip, 'mkdir -p /var/lib/vz/template/iso', timeout=30)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"Failed to create iso directory: {err}",
            duration=time.time() - start
        )

    # Download using gh release download
    if tag == 'latest':
        dl_cmd = f'gh release download --repo {repo} --pattern "{image}" --dir /var/lib/vz/template/iso --clobber'
    else:
        dl_cmd = f'gh release download {tag} --repo {repo} --pattern "{image}" --dir /var/lib/vz/template/iso --clobber'

    rc, out, err = run_ssh(inner_ip, dl_cmd, timeout=300)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"Failed to download image: {err}",
            duration=time.time() - start
        )

    # Rename .qcow2 to .img (Proxmox convention)
    img_name = image.replace('.qcow2', '.img')
    rename_cmd = f'mv /var/lib/vz/template/iso/{image} /var/lib/vz/template/iso/{img_name} 2>/dev/null || true'
    run_ssh(inner_ip, rename_cmd, timeout=30)

    # Verify file exists
    verify_cmd = f'ls -la /var/lib/vz/template/iso/{img_name}'
    rc, out, err = run_ssh(inner_ip, verify_cmd, timeout=30)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"Image not found after download: {err}",
            duration=time.time() - start
        )

    return PhaseResult(
        success=True,
        message=f"Downloaded {img_name} to inner PVE",
        duration=time.time() - start,
        context_updates={'packer_image': img_name}
    )
