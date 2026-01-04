"""Reusable infrastructure actions."""

from actions.tofu import TofuApplyAction, TofuDestroyAction, TofuApplyRemoteAction
from actions.ansible import AnsiblePlaybookAction, AnsibleLocalPlaybookAction
from actions.ssh import SSHCommandAction, WaitForSSHAction, VerifySSHChainAction
from actions.proxmox import (
    StartVMAction,
    WaitForGuestAgentAction,
    StartVMRemoteAction,
    WaitForGuestAgentRemoteAction,
)
from actions.file import RemoveImageAction, DownloadFileAction, DownloadGitHubReleaseAction

__all__ = [
    'TofuApplyAction',
    'TofuDestroyAction',
    'TofuApplyRemoteAction',
    'AnsiblePlaybookAction',
    'AnsibleLocalPlaybookAction',
    'SSHCommandAction',
    'WaitForSSHAction',
    'VerifySSHChainAction',
    'StartVMAction',
    'WaitForGuestAgentAction',
    'StartVMRemoteAction',
    'WaitForGuestAgentRemoteAction',
    'RemoveImageAction',
    'DownloadFileAction',
    'DownloadGitHubReleaseAction',
]
