"""Nested PVE test scenarios.

Tests the full stack: VM provisioning -> PVE installation -> nested VM creation.
"""

from actions import (
    TofuApplyAction,
    TofuApplyRemoteAction,
    TofuDestroyAction,
    TofuDestroyRemoteAction,
    AnsiblePlaybookAction,
    EnsurePVEAction,
    StartVMAction,
    WaitForGuestAgentAction,
    StartVMRemoteAction,
    WaitForGuestAgentRemoteAction,
    DownloadGitHubReleaseAction,
    SyncReposToVMAction,
    VerifySSHChainAction,
)
from config import HostConfig, get_base_dir
from scenarios import register_scenario
from scenarios.cleanup_nested_pve import StopVMAction


@register_scenario
class NestedPVEConstructor:
    """integration test for nested Proxmox VE installation."""

    name = 'nested-pve-constructor'
    description = 'Provision inner PVE, install Proxmox VE, create test VM, verify SSH'
    expected_runtime = 360  # ~6 min (PVE install ~2m with pre-installed image)
    requires_nested_virt = True  # Requires nested virtualization

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for nested PVE integration test."""
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
            ), 'Wait for inner PVE IP'),

            # Phase 4: Ensure PVE installed
            ('ensure_pve', EnsurePVEAction(
                name='ensure-pve',
                host_key='inner_ip',
                pve_hostname='nested-pve',
            ), 'Ensure PVE installed'),

            # Phase 5: Setup network bridge (vmbr0)
            ('setup_network', AnsiblePlaybookAction(
                name='configure-network',
                playbook='playbooks/nested-pve-network.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={},
                host_key='inner_ip',
                wait_for_ssh_before=True,
            ), 'Configure vmbr0 bridge'),

            # Phase 6: Copy SSH keys for nested VM access
            ('setup_ssh', AnsiblePlaybookAction(
                name='copy-ssh-keys',
                playbook='playbooks/nested-pve-ssh.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={},
                host_key='inner_ip',
            ), 'Copy SSH keys'),

            # Phase 7: Sync repos and configure PVE
            ('setup_repos', AnsiblePlaybookAction(
                name='sync-repos-config',
                playbook='playbooks/nested-pve-repos.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={
                    'bootstrap_use_local': True,
                    'homestak_src_dir': str(get_base_dir().parent),
                },
                host_key='inner_ip',
            ), 'Sync repos and configure PVE'),

            # Phase 6: Download packer image from release
            ('download_image', DownloadGitHubReleaseAction(
                name='download-packer-image',
                asset_name=config.packer_image,
                dest_dir='/var/lib/vz/template/iso',
                host_key='inner_ip',
                rename_ext='.img',
            ), 'Download packer image from release'),

            # Phase 7: Provision test VM on inner PVE
            ('test_vm_apply', TofuApplyRemoteAction(
                name='provision-test-vm',
                env_name='test',
                node_name='nested-pve',
                host_key='inner_ip',
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
                ip_context_key='leaf_ip',
            ), 'Wait for test VM IP'),

            # Phase 10: Sync repos to test VM
            ('sync_repos', SyncReposToVMAction(
                name='sync-repos-to-test-vm',
                target_host_key='leaf_ip',
                intermediate_host_key='inner_ip',
            ), 'Sync /opt/homestak to test VM'),

            # Phase 11: Verify SSH chain
            ('verify', VerifySSHChainAction(
                name='verify-ssh-chain',
                target_host_key='leaf_ip',
                jump_host_key='inner_ip',
                timeout=300,  # 5x default: multi-hop SSH chain requires longer timeout
            ), 'Verify SSH chain'),
        ]


@register_scenario
class NestedPVERoundtrip:
    """Full integration roundtrip: construct, verify, destruct."""

    name = 'nested-pve-roundtrip'
    description = 'Full cycle: provision, install PVE, test VM, verify, cleanup, destroy'
    expected_runtime = 540  # ~9 min
    requires_nested_virt = True  # Requires nested virtualization

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
            ), 'Wait for inner PVE IP'),

            ('ensure_pve', EnsurePVEAction(
                name='ensure-pve',
                host_key='inner_ip',
                pve_hostname='nested-pve',
            ), 'Ensure PVE installed'),

            ('setup_network', AnsiblePlaybookAction(
                name='configure-network',
                playbook='playbooks/nested-pve-network.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={},
                host_key='inner_ip',
                wait_for_ssh_before=True,
            ), 'Configure vmbr0 bridge'),

            ('setup_ssh', AnsiblePlaybookAction(
                name='copy-ssh-keys',
                playbook='playbooks/nested-pve-ssh.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={},
                host_key='inner_ip',
            ), 'Copy SSH keys'),

            ('setup_repos', AnsiblePlaybookAction(
                name='sync-repos-config',
                playbook='playbooks/nested-pve-repos.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={
                    'bootstrap_use_local': True,
                    'homestak_src_dir': str(get_base_dir().parent),
                },
                host_key='inner_ip',
            ), 'Sync repos and configure PVE'),

            ('download_image', DownloadGitHubReleaseAction(
                name='download-packer-image',
                asset_name=config.packer_image,
                dest_dir='/var/lib/vz/template/iso',
                host_key='inner_ip',
                rename_ext='.img',
            ), 'Download packer image'),

            ('test_vm_apply', TofuApplyRemoteAction(
                name='provision-test-vm',
                env_name='test',
                node_name='nested-pve',
                host_key='inner_ip',
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
                ip_context_key='leaf_ip',
            ), 'Wait for test VM IP'),

            ('sync_repos', SyncReposToVMAction(
                name='sync-repos-to-test-vm',
                target_host_key='leaf_ip',
                intermediate_host_key='inner_ip',
            ), 'Sync /opt/homestak to test VM'),

            # === VERIFY ===
            ('verify', VerifySSHChainAction(
                name='verify-ssh-chain',
                target_host_key='leaf_ip',
                jump_host_key='inner_ip',
                timeout=300,  # 5x default: multi-hop SSH chain requires longer timeout
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
