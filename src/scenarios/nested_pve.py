"""Nested PVE E2E test scenario.

Tests the full stack: VM provisioning -> PVE installation -> nested VM creation.
"""

from actions import (
    TofuApplyAction,
    TofuApplyRemoteAction,
    TofuDestroyAction,
    TofuDestroyRemoteAction,
    AnsiblePlaybookAction,
    StartVMAction,
    WaitForGuestAgentAction,
    StartVMRemoteAction,
    WaitForGuestAgentRemoteAction,
    DownloadGitHubReleaseAction,
    SyncReposToVMAction,
    VerifySSHChainAction,
)
from config import HostConfig, get_sibling_dir
from scenarios import register_scenario
from scenarios.cleanup_nested_pve import StopVMAction


@register_scenario
class NestedPVEConstructor:
    """E2E test for nested Proxmox VE installation."""

    name = 'nested-pve-constructor'
    description = 'Provision inner PVE, install Proxmox VE, create test VM, verify SSH'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for nested PVE E2E test."""
        return [
            # Phase 1: Provision inner PVE VM
            ('provision', TofuApplyAction(
                name='provision-inner-pve',
                env_name='nested-pve',
            ), 'Provision inner PVE VM'),

            # Phase 2: Start VM (tofu creates it stopped)
            ('start_vm', StartVMAction(
                name='start-inner-pve',
                vm_id_attr='nested-pve_vm_id',
                pve_host_attr='ssh_host',
            ), 'Start inner PVE VM'),

            # Phase 3: Wait for guest agent and get IP
            ('wait_ip', WaitForGuestAgentAction(
                name='wait-for-inner-ip',
                vm_id_attr='nested-pve_vm_id',
                pve_host_attr='ssh_host',
                ip_context_key='inner_ip',
                timeout=300,
            ), 'Wait for inner PVE IP'),

            # Phase 4: Install Proxmox VE
            ('install_pve', AnsiblePlaybookAction(
                name='install-pve',
                playbook='playbooks/pve-install.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={'pve_hostname': 'pve-deb', 'ansible_user': 'root'},
                host_key='inner_ip',
                wait_for_ssh_before=True,
                wait_for_ssh_after=True,
                ssh_timeout=120,
                timeout=1200,  # 20 min for PVE install + reboot
            ), 'Install Proxmox VE'),

            # Phase 5: Configure inner PVE
            ('configure', AnsiblePlaybookAction(
                name='configure-inner-pve',
                playbook='playbooks/nested-pve-setup.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={
                    'ansible_user': 'root',
                    'packer_src_dir': str(get_sibling_dir('packer')),
                    'tofu_src_dir': str(get_sibling_dir('tofu')),
                    'site_config_src_dir': str(get_sibling_dir('site-config')),
                    'iac_driver_src_dir': str(get_sibling_dir('iac-driver')),
                },
                host_key='inner_ip',
                wait_for_ssh_before=True,
                timeout=600,
            ), 'Configure inner PVE'),

            # Phase 6: Download packer image from release
            ('download_image', DownloadGitHubReleaseAction(
                name='download-packer-image',
                asset_name=config.packer_image,
                dest_dir='/var/lib/vz/template/iso',
                host_key='inner_ip',
                rename_ext='.img',
                timeout=300,
            ), 'Download packer image from release'),

            # Phase 7: Provision test VM on inner PVE
            ('test_vm_apply', TofuApplyRemoteAction(
                name='provision-test-vm',
                env_name='test',
                node_name='nested-pve',
                host_key='inner_ip',
                timeout_init=120,
                timeout_apply=300,
            ), 'Provision test VM'),

            # Phase 8: Start test VM
            ('test_vm_start', StartVMRemoteAction(
                name='start-test-vm',
                vm_id_attr='test_vm_id',
                pve_host_key='inner_ip',
            ), 'Start test VM'),

            # Phase 9: Wait for test VM IP
            ('test_vm_wait', WaitForGuestAgentRemoteAction(
                name='wait-for-test-ip',
                vm_id_attr='test_vm_id',
                pve_host_key='inner_ip',
                ip_context_key='test_ip',
                timeout=300,
            ), 'Wait for test VM IP'),

            # Phase 10: Sync repos to test VM
            ('sync_repos', SyncReposToVMAction(
                name='sync-repos-to-test-vm',
                target_host_key='test_ip',
                intermediate_host_key='inner_ip',
                timeout=300,
            ), 'Sync /opt/homestak to test VM'),

            # Phase 11: Verify SSH chain
            ('verify', VerifySSHChainAction(
                name='verify-ssh-chain',
                target_host_key='test_ip',
                jump_host_key='inner_ip',
                timeout=300,
            ), 'Verify SSH chain'),
        ]


@register_scenario
class NestedPVERoundtrip:
    """Full E2E roundtrip: construct, verify, destruct."""

    name = 'nested-pve-roundtrip'
    description = 'Full cycle: provision, install PVE, test VM, verify, cleanup, destroy'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for full roundtrip test."""
        return [
            # === CONSTRUCT ===
            ('provision', TofuApplyAction(
                name='provision-inner-pve',
                env_name='nested-pve',
            ), 'Provision inner PVE VM'),

            ('start_vm', StartVMAction(
                name='start-inner-pve',
                vm_id_attr='nested-pve_vm_id',
                pve_host_attr='ssh_host',
            ), 'Start inner PVE VM'),

            ('wait_ip', WaitForGuestAgentAction(
                name='wait-for-inner-ip',
                vm_id_attr='nested-pve_vm_id',
                pve_host_attr='ssh_host',
                ip_context_key='inner_ip',
                timeout=300,
            ), 'Wait for inner PVE IP'),

            ('install_pve', AnsiblePlaybookAction(
                name='install-pve',
                playbook='playbooks/pve-install.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={'pve_hostname': 'pve-deb', 'ansible_user': 'root'},
                host_key='inner_ip',
                wait_for_ssh_before=True,
                wait_for_ssh_after=True,
                ssh_timeout=120,
                timeout=1200,
            ), 'Install Proxmox VE'),

            ('configure', AnsiblePlaybookAction(
                name='configure-inner-pve',
                playbook='playbooks/nested-pve-setup.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={
                    'ansible_user': 'root',
                    'packer_src_dir': str(get_sibling_dir('packer')),
                    'tofu_src_dir': str(get_sibling_dir('tofu')),
                    'site_config_src_dir': str(get_sibling_dir('site-config')),
                    'iac_driver_src_dir': str(get_sibling_dir('iac-driver')),
                },
                host_key='inner_ip',
                wait_for_ssh_before=True,
                timeout=600,
            ), 'Configure inner PVE'),

            ('download_image', DownloadGitHubReleaseAction(
                name='download-packer-image',
                asset_name=config.packer_image,
                dest_dir='/var/lib/vz/template/iso',
                host_key='inner_ip',
                rename_ext='.img',
                timeout=300,
            ), 'Download packer image'),

            ('test_vm_apply', TofuApplyRemoteAction(
                name='provision-test-vm',
                env_name='test',
                node_name='nested-pve',
                host_key='inner_ip',
                timeout_init=120,
                timeout_apply=300,
            ), 'Provision test VM'),

            ('test_vm_start', StartVMRemoteAction(
                name='start-test-vm',
                vm_id_attr='test_vm_id',
                pve_host_key='inner_ip',
            ), 'Start test VM'),

            ('test_vm_wait', WaitForGuestAgentRemoteAction(
                name='wait-for-test-ip',
                vm_id_attr='test_vm_id',
                pve_host_key='inner_ip',
                ip_context_key='test_ip',
                timeout=300,
            ), 'Wait for test VM IP'),

            ('sync_repos', SyncReposToVMAction(
                name='sync-repos-to-test-vm',
                target_host_key='test_ip',
                intermediate_host_key='inner_ip',
                timeout=300,
            ), 'Sync /opt/homestak to test VM'),

            # === VERIFY ===
            ('verify', VerifySSHChainAction(
                name='verify-ssh-chain',
                target_host_key='test_ip',
                jump_host_key='inner_ip',
                timeout=300,
            ), 'Verify SSH chain'),

            # === DESTRUCT ===
            ('cleanup_remote', TofuDestroyRemoteAction(
                name='cleanup-remote-vm',
                env_name='test',
                node_name='nested-pve',
                host_key='inner_ip',
            ), 'Cleanup test VM'),

            ('stop_inner', StopVMAction(
                name='stop-inner-pve',
                vm_id_attr='nested-pve_vm_id',
                pve_host_attr='ssh_host',
            ), 'Stop inner PVE VM'),

            ('destroy_inner', TofuDestroyAction(
                name='destroy-inner-pve',
                env_name='nested-pve',
            ), 'Destroy inner PVE VM'),
        ]
