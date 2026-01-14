"""Pre-flight validation checks for scenarios.

This module provides readiness checks that run before scenarios execute,
catching configuration issues early with actionable error messages.
"""

import logging
import socket

import requests
import urllib3

# Suppress SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# API Token Validation
# -----------------------------------------------------------------------------

def validate_api_token(api_endpoint: str, api_token: str, node_name: str) -> list[str]:
    """Validate Proxmox API token is present and valid.

    Args:
        api_endpoint: PVE API URL (e.g., https://10.0.12.61:8006)
        api_token: Full token string (e.g., root@pam!homestak=uuid)
        node_name: Node name for error messages

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Check API endpoint is configured
    if not api_endpoint:
        errors.append(
            f"API endpoint not configured for node '{node_name}'\n"
            f"  Add 'api_endpoint' to site-config/nodes/{node_name}.yaml"
        )
        return errors

    # Check token is present
    if not api_token:
        errors.append(
            f"API token not found for node '{node_name}'\n"
            f"  Ensure secrets.yaml is decrypted: cd ../site-config && make decrypt\n"
            f"  Ensure token exists: secrets.api_tokens.{node_name}"
        )
        return errors

    # Check token format (PVE format: user@realm!tokenname=tokenvalue)
    if '!' not in api_token or '=' not in api_token:
        errors.append(
            f"API token for '{node_name}' has invalid format\n"
            f"  Expected: user@realm!tokenname=tokenvalue\n"
            f"  Got: {api_token[:20]}..."
        )
        return errors

    # Validate token against API
    try:
        resp = requests.get(
            f"{api_endpoint}/api2/json/version",
            headers={"Authorization": f"PVEAPIToken={api_token}"},
            verify=False,  # Self-signed cert
            timeout=10
        )

        if resp.status_code == 401:
            errors.append(
                f"API token invalid for node '{node_name}'\n"
                f"  Regenerate: pveum user token add root@pam homestak --privsep 0\n"
                f"  Then update secrets.yaml and run: cd ../site-config && make encrypt"
            )
        elif resp.status_code != 200:
            errors.append(
                f"Unexpected API response for node '{node_name}': {resp.status_code}\n"
                f"  Response: {resp.text[:100]}"
            )
        else:
            data = resp.json().get("data", {})
            version = data.get("version", "unknown")
            logger.info(f"API token valid for {node_name} (PVE {version})")

    except requests.exceptions.ConnectionError:
        errors.append(
            f"Cannot connect to {api_endpoint}\n"
            f"  Check: host is online, port 8006 is open, firewall allows access"
        )
    except requests.exceptions.Timeout:
        errors.append(f"Timeout connecting to {api_endpoint}")
    except Exception as e:
        errors.append(f"Error validating token: {e}")

    return errors


# -----------------------------------------------------------------------------
# Host Availability Validation
# -----------------------------------------------------------------------------

def validate_host_resolvable(hostname: str) -> tuple[bool, str]:
    """Check if hostname resolves to an IP address.

    Args:
        hostname: Hostname or IP to resolve

    Returns:
        (success, ip_or_error) tuple
    """
    try:
        ip = socket.gethostbyname(hostname)
        return True, ip
    except socket.gaierror:
        return False, f"Cannot resolve hostname '{hostname}'"


def validate_host_reachable(host: str, port: int = 22, timeout: float = 5.0) -> tuple[bool, str]:
    """Check if host is reachable on specified port.

    Args:
        host: Hostname or IP
        port: Port to check (default: 22 for SSH)
        timeout: Connection timeout in seconds

    Returns:
        (success, message) tuple
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, f"Port {port} reachable"
    except socket.timeout:
        return False, f"Timeout connecting to {host}:{port}"
    except socket.error as e:
        return False, f"Cannot connect to {host}:{port}: {e}"


def validate_host_availability(ssh_host: str, node_name: str,
                               check_ssh: bool = True,
                               check_api: bool = True,
                               timeout: float = 5.0) -> list[str]:
    """Validate host is resolvable and reachable.

    Args:
        ssh_host: Hostname or IP to validate
        node_name: Node name for error messages
        check_ssh: Check SSH port 22
        check_api: Check API port 8006
        timeout: Connection timeout per check

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    if not ssh_host:
        errors.append(
            f"SSH host not configured for node '{node_name}'\n"
            f"  Derived from api_endpoint or set explicitly in nodes/{node_name}.yaml"
        )
        return errors

    # Check hostname resolution
    success, result = validate_host_resolvable(ssh_host)
    if not success:
        errors.append(
            f"{result}\n"
            f"  Check: nodes/{node_name}.yaml has correct api_endpoint or ip\n"
            f"  Or use --host with a resolvable hostname/IP"
        )
        return errors  # Can't check ports if hostname doesn't resolve

    ip = result
    logger.info(f"Host {ssh_host} resolves to {ip}")

    # Check SSH port
    if check_ssh:
        success, message = validate_host_reachable(ip, port=22, timeout=timeout)
        if not success:
            errors.append(
                f"SSH not available on {ssh_host} ({ip})\n"
                f"  {message}\n"
                f"  Check: host is online, SSH is enabled, firewall allows port 22"
            )

    # Check API port
    if check_api:
        success, message = validate_host_reachable(ip, port=8006, timeout=timeout)
        if not success:
            errors.append(
                f"PVE API not available on {ssh_host} ({ip})\n"
                f"  {message}\n"
                f"  Check: pveproxy service is running, firewall allows port 8006"
            )

    return errors


# -----------------------------------------------------------------------------
# Combined Validation
# -----------------------------------------------------------------------------

def validate_readiness(config, scenario_class, timeout: float = 10.0) -> list[str]:
    """Run all readiness checks for a scenario.

    Args:
        config: HostConfig instance
        scenario_class: Scenario class with requirement attributes
        timeout: Connection timeout for network checks

    Returns:
        Combined list of all validation errors
    """
    errors = []

    # Check scenario requirements
    requires_api = getattr(scenario_class, 'requires_api', True)
    requires_host_ssh = getattr(scenario_class, 'requires_host_ssh', True)

    # API token validation
    if requires_api:
        api_token = getattr(config, '_api_token', None) or getattr(config, 'api_token', None)
        if callable(api_token):
            api_token = api_token()
        errors.extend(validate_api_token(
            api_endpoint=config.api_endpoint,
            api_token=api_token,
            node_name=config.name
        ))

    # Host availability validation (skip if API validation already failed on connection)
    if requires_host_ssh and not any("Cannot connect" in e for e in errors):
        ssh_host = getattr(config, 'ssh_host', None) or getattr(config, 'ip', None)
        errors.extend(validate_host_availability(
            ssh_host=ssh_host,
            node_name=config.name,
            check_ssh=requires_host_ssh,
            check_api=requires_api,
            timeout=timeout
        ))

    return errors
