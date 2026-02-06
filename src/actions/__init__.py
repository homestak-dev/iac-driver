"""Reusable infrastructure actions."""

from actions.tofu import (
    TofuApplyAction,
    TofuApplyInlineAction,
    TofuDestroyAction,
    TofuDestroyInlineAction,
)
from actions.ansible import AnsiblePlaybookAction, AnsibleLocalPlaybookAction, EnsurePVEAction
from actions.ssh import SSHCommandAction, WaitForSSHAction, WaitForFileAction, VerifySSHChainAction
from actions.proxmox import (
    StartVMAction,
    WaitForGuestAgentAction,
    LookupVMIPAction,
    StartProvisionedVMsAction,
    WaitForProvisionedVMsAction,
    StartVMRemoteAction,
    WaitForGuestAgentRemoteAction,
)
from actions.file import RemoveImageAction, DownloadFileAction, DownloadGitHubReleaseAction
from actions.recursive import RecursiveScenarioAction
from actions.pve_lifecycle import (
    EnsureImageAction,
    CreateApiTokenAction,
    BootstrapAction,
    CopySecretsAction,
    InjectSSHKeyAction,
    CopySSHPrivateKeyAction,
    InjectSelfSSHKeyAction,
    ConfigureNetworkBridgeAction,
    GenerateNodeConfigAction,
)

__all__ = [
    'TofuApplyAction',
    'TofuApplyInlineAction',
    'TofuDestroyAction',
    'TofuDestroyInlineAction',
    'AnsiblePlaybookAction',
    'AnsibleLocalPlaybookAction',
    'EnsurePVEAction',
    'SSHCommandAction',
    'WaitForSSHAction',
    'WaitForFileAction',
    'VerifySSHChainAction',
    'StartVMAction',
    'WaitForGuestAgentAction',
    'LookupVMIPAction',
    'StartProvisionedVMsAction',
    'WaitForProvisionedVMsAction',
    'StartVMRemoteAction',
    'WaitForGuestAgentRemoteAction',
    'RemoveImageAction',
    'DownloadFileAction',
    'DownloadGitHubReleaseAction',
    'RecursiveScenarioAction',
    'EnsureImageAction',
    'CreateApiTokenAction',
    'BootstrapAction',
    'CopySecretsAction',
    'InjectSSHKeyAction',
    'CopySSHPrivateKeyAction',
    'InjectSelfSSHKeyAction',
    'ConfigureNetworkBridgeAction',
    'GenerateNodeConfigAction',
]
