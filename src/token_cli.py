"""Token introspection CLI.

Usage:
    ./run.sh token inspect <token> [--verify]
"""

import argparse
import base64
import datetime
import hashlib
import hmac as hmac_mod
import json
import sys


def _base64url_decode(s: str) -> bytes:
    """Decode base64url string (padding-free)."""
    s += '=' * (4 - len(s) % 4) if len(s) % 4 else ''
    return base64.urlsafe_b64decode(s)


def inspect_token(token: str, signing_key: str = None) -> int:
    """Decode and optionally verify a provisioning token.

    Args:
        token: The provisioning token string
        signing_key: Hex-encoded signing key (for --verify)

    Returns:
        Exit code (0=success, 1=error)
    """
    parts = token.split(".")
    if len(parts) != 2:
        print(f"Error: Expected 2 dot-separated segments, got {len(parts)}")
        return 1

    payload_b64, sig_b64 = parts

    # Decode payload
    try:
        payload_bytes = _base64url_decode(payload_b64)
        claims = json.loads(payload_bytes)
    except Exception as e:
        print(f"Error: Cannot decode payload: {e}")
        return 1

    # Display claims
    print("Claims:")
    print(f"  version (v): {claims.get('v', '?')}")
    print(f"  node    (n): {claims.get('n', '?')}")
    print(f"  spec    (s): {claims.get('s', '?')}")
    iat = claims.get('iat')
    if iat:
        ts = datetime.datetime.fromtimestamp(iat, tz=datetime.timezone.utc)
        print(f"  issued  (iat): {iat} ({ts.isoformat()})")
    else:
        print(f"  issued  (iat): (not set)")

    # Show any extra claims
    known = {'v', 'n', 's', 'iat'}
    extra = {k: v for k, v in claims.items() if k not in known}
    if extra:
        for k, v in extra.items():
            print(f"  {k}: {v}")

    # Verify signature if requested
    if signing_key is not None:
        try:
            expected_sig = hmac_mod.new(
                bytes.fromhex(signing_key),
                payload_b64.encode(),
                hashlib.sha256,
            ).digest()
        except ValueError:
            print("\nSignature: INVALID (bad signing key)")
            return 1

        try:
            actual_sig = _base64url_decode(sig_b64)
        except Exception:
            print("\nSignature: INVALID (cannot decode)")
            return 1

        if hmac_mod.compare_digest(expected_sig, actual_sig):
            print("\nSignature: VALID")
        else:
            print("\nSignature: INVALID")
            return 1

    return 0


def main(argv: list) -> int:
    """Token CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="./run.sh token",
        description="Provisioning token utilities",
    )
    sub = parser.add_subparsers(dest="action")

    inspect_parser = sub.add_parser("inspect", help="Decode and inspect a token")
    inspect_parser.add_argument("token", help="Provisioning token to inspect")
    inspect_parser.add_argument(
        "--verify", action="store_true",
        help="Verify HMAC signature using signing key from secrets.yaml",
    )

    args = parser.parse_args(argv)

    if not args.action:
        parser.print_help()
        return 1

    if args.action == "inspect":
        signing_key = None
        if args.verify:
            # Load signing key from secrets.yaml
            try:
                from resolver.base import ResolverBase
                resolver = ResolverBase()
                signing_key = resolver.get_signing_key()
                if not signing_key:
                    print("Error: No signing key found in secrets.yaml (auth.signing_key)")
                    return 1
            except Exception as e:
                print(f"Error: Cannot load signing key: {e}")
                return 1

        return inspect_token(args.token, signing_key)

    return 1
