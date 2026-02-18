"""Pre-flight readiness checks for scenarios.

Validates infrastructure prerequisites before executing actions:
- API token validity
- Host resolution and reachability
"""

import socket

try:
    import requests
    import urllib3
    # Suppress SSL warnings for self-signed certs
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


def validate_api_token(api_endpoint: str, api_token: str) -> tuple[bool, str]:
    """Validate Proxmox API token before running tofu.

    Makes a lightweight API call to verify credentials are valid.

    Args:
        api_endpoint: PVE API URL (e.g., https://198.51.100.61:8006)
        api_token: Full token string (e.g., root@pam!homestak=uuid)

    Returns:
        (success, message) tuple
    """
    if not REQUESTS_AVAILABLE:
        return True, "Skipping API validation (requests not installed)"

    try:
        resp = requests.get(
            f"{api_endpoint}/api2/json/version",
            headers={"Authorization": f"PVEAPIToken={api_token}"},
            verify=False,  # Self-signed cert
            timeout=10
        )

        if resp.status_code == 401:
            return False, (
                "Invalid API token. "
                "Regenerate with: pveum user token add root@pam homestak --privsep 0\n"
                "Then update secrets.yaml and run: make encrypt"
            )

        if resp.status_code == 200:
            data = resp.json().get("data", {})
            version = data.get("version", "unknown")
            return True, f"PVE API accessible (version {version})"

        return False, f"Unexpected API response: {resp.status_code} - {resp.text[:100]}"

    except requests.exceptions.ConnectionError as e:
        return False, f"Cannot connect to {api_endpoint}: {e}"
    except requests.exceptions.Timeout:
        return False, f"Timeout connecting to {api_endpoint}"
    except Exception as e:
        return False, f"Error validating token: {e}"


def validate_host_resolvable(hostname: str) -> tuple[bool, str]:
    """Check if hostname resolves to an IP address.

    Args:
        hostname: Hostname or IP to resolve

    Returns:
        (success, message) tuple
    """
    try:
        ip = socket.gethostbyname(hostname)
        return True, f"{hostname} resolves to {ip}"
    except socket.gaierror:
        return False, (
            f"Cannot resolve hostname '{hostname}'. "
            f"Check nodes/{hostname}.yaml or use --host with a resolvable hostname."
        )


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
        return True, f"Host {host} reachable on port {port}"
    except socket.timeout:
        return False, f"Timeout connecting to {host}:{port}"
    except socket.error as e:
        return False, f"Cannot connect to {host}:{port}: {e}"


def validate_host(hostname: str, check_ssh: bool = True, check_api: bool = False) -> tuple[bool, str]:
    """Combined host validation.

    Args:
        hostname: Hostname or IP to validate
        check_ssh: Check SSH port 22 (for ansible)
        check_api: Check HTTPS port 8006 (for API)

    Returns:
        (success, message) tuple
    """
    # First check resolution
    success, message = validate_host_resolvable(hostname)
    if not success:
        return False, message

    ip = socket.gethostbyname(hostname)

    # Check SSH
    if check_ssh:
        success, ssh_msg = validate_host_reachable(ip, port=22)
        if not success:
            return False, f"Host {hostname} ({ip}) SSH not reachable: {ssh_msg}"

    # Check API
    if check_api:
        success, api_msg = validate_host_reachable(ip, port=8006)
        if not success:
            return False, f"Host {hostname} ({ip}) API port not reachable: {api_msg}"

    return True, f"Host {hostname} ({ip}) reachable"
