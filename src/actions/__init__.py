"""Reusable infrastructure actions."""

from actions.tofu import (
    TofuApplyAction,
    TofuApplyInlineAction,
    TofuDestroyAction,
    TofuDestroyInlineAction,
    TofuApplyRemoteAction,
    TofuDestroyRemoteAction,
)
from actions.ansible import AnsiblePlaybookAction, AnsibleLocalPlaybookAction, EnsurePVEAction
from actions.ssh import SSHCommandAction, WaitForSSHAction, SyncReposToVMAction, VerifySSHChainAction
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

__all__ = [
    'TofuApplyAction',
    'TofuApplyInlineAction',
    'TofuDestroyAction',
    'TofuDestroyInlineAction',
    'TofuApplyRemoteAction',
    'TofuDestroyRemoteAction',
    'AnsiblePlaybookAction',
    'AnsibleLocalPlaybookAction',
    'EnsurePVEAction',
    'SSHCommandAction',
    'WaitForSSHAction',
    'SyncReposToVMAction',
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
]
