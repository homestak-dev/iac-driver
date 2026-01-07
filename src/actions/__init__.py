"""Reusable infrastructure actions."""

from actions.tofu import TofuApplyAction, TofuDestroyAction, TofuApplyRemoteAction, TofuDestroyRemoteAction
from actions.ansible import AnsiblePlaybookAction, AnsibleLocalPlaybookAction, EnsurePVEAction
from actions.ssh import SSHCommandAction, WaitForSSHAction, SyncReposToVMAction, VerifySSHChainAction
from actions.proxmox import (
    StartVMAction,
    WaitForGuestAgentAction,
    StartProvisionedVMsAction,
    WaitForProvisionedVMsAction,
    StartVMRemoteAction,
    WaitForGuestAgentRemoteAction,
)
from actions.file import RemoveImageAction, DownloadFileAction, DownloadGitHubReleaseAction

__all__ = [
    'TofuApplyAction',
    'TofuDestroyAction',
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
    'StartProvisionedVMsAction',
    'WaitForProvisionedVMsAction',
    'StartVMRemoteAction',
    'WaitForGuestAgentRemoteAction',
    'RemoveImageAction',
    'DownloadFileAction',
    'DownloadGitHubReleaseAction',
]
