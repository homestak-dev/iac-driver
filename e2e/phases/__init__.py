# E2E Test Phases
from . import provision, install_pve, configure, download_image, test_vm, verify

__all__ = ['provision', 'install_pve', 'configure', 'download_image', 'test_vm', 'verify']
