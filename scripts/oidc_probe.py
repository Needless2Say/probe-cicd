"""
Scratch probe, GitHub's OIDC token as a proof, minted with a digest as its audience, read and verified.

  mint    asks the runner for a token with the given audience and writes it to a file the job alone reads, never
          printing it
  claims  prints the token's claims a verifier would hold to, its audience against the expected one, and whether its
          RS256 signature verifies against GitHub's published keys
  verify  the same signature check of a token kept since, by a fresh process, an expired token included, so a proof
          can be checked a week after its run and after a signing key turns over

The signature is checked with the standard library alone, the RSA public key from the JWKS, the PKCS#1 v1.5 padding
and SHA-256's DigestInfo compared whole. Never prints a token.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request

ISSUER = "https://token.actions.githubusercontent.com"
JWKS   = ISSUER + "/.well-known/jwks"

# SHA-256's DigestInfo prefix, RFC 8017 section 9.2
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

CLAIMS = (
    "iss", "aud", "sub", "repository", "repository_owner", "ref", "ref_type", "event_name", "workflow_ref",
    "workflow_sha", "job_workflow_ref", "job_workflow_sha", "run_id", "run_attempt", "environment", "actor",
    "actor_id", "runner_environment",
)


def b64url(text: str) -> bytes:
    """
    Decode base64url without padding.

    Args:
        text: the encoded text

    Returns:
        bytes: the decoded bytes
    """
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_rs256(signing_input: bytes, signature: bytes, modulus: int, exponent: int) -> bool:
    """
    Whether an RS256 signature verifies, the whole encoded message compared.

    Args:
        signing_input: the token's header and payload as signed
        signature: the signature's bytes
        modulus: the key's n
        exponent: the key's e

    Returns:
        bool: whether it verifies
    """
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= modulus:
        return False
    message  = pow(value, exponent, modulus).to_bytes(size, "big")
    info     = SHA256_PREFIX + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    return hmac.compare_digest(message, expected)


def fetch_keys() -> dict[str, tuple[int, int]]:
    """
    GitHub's published signing keys by their key id.

    Returns:
        dict[str, tuple[int, int]]: each key id's modulus and exponent
    """
    with urllib.request.urlopen(JWKS, timeout = 30) as response:
        keys = json.loads(response.read()).get("keys") or []
    found: dict[str, tuple[int, int]] = {}
    for key in keys:
        if key.get("kty") == "RSA" and key.get("kid"):
            found[key["kid"]] = (int.from_bytes(b64url(key["n"]), "big"), int.from_bytes(b64url(key["e"]), "big"))
    return found


def read_token(path: str) -> tuple[dict, dict, bytes, bytes]:
    """
    A token's header, claims, signed input and signature.

    Args:
        path: the file holding the token

    Returns:
        tuple[dict, dict, bytes, bytes]: the header, the claims, the signed input and the signature
    """
    with open(path, encoding = "utf-8") as handle:
        token = handle.read().strip()
    head, body, sig = token.split(".")
    return json.loads(b64url(head)), json.loads(b64url(body)), f"{head}.{body}".encode(), b64url(sig)


def when(seconds: object) -> str:
    """
    A token time written as UTC.

    Args:
        seconds: seconds since the epoch

    Returns:
        str: the time, or "none"
    """
    if not isinstance(seconds, int):
        return "none"
    return dt.datetime.fromtimestamp(seconds, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def signature_line(path: str) -> str:
    """
    Whether a kept token's signature verifies against the keys published now.

    Args:
        path: the file holding the token

    Returns:
        str: the key id, whether it is still published, and whether the signature verifies
    """
    header, _claims, signed, signature = read_token(path)
    keys = fetch_keys()
    kid  = header.get("kid", "")
    if header.get("alg") != "RS256" or kid not in keys:
        return f"key {kid or 'none'}, published now: {'yes' if kid in keys else 'no'}, alg {header.get('alg')}"
    modulus, exponent = keys[kid]
    verified = verify_rs256(signed, signature, modulus, exponent)
    return f"key {kid}, published now: yes, signature verifies: {'yes' if verified else 'no'}"


def cmd_mint(args: argparse.Namespace) -> int:
    """
    Mint a token with the given audience into a file, never printing it.
    """
    url   = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not url or not token:
        print("- no OIDC request URL in this job, so it was granted no id-token")
        return 1
    request = urllib.request.Request(url + "&" + urllib.parse.urlencode({"audience": args.audience}))
    request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout = 30) as response:
        value = json.loads(response.read()).get("value", "")
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding = "utf-8") as handle:
        handle.write(value)
    print(f"- minted a token for the audience given, {len(value)} bytes, kept in a file and never printed")
    return 0


def cmd_claims(args: argparse.Namespace) -> int:
    """
    Print the claims a verifier would hold to, and whether the signature verifies.
    """
    _header, claims, _signed, _signature = read_token(args.file)
    for name in CLAIMS:
        print(f"- {name}: {claims.get(name, 'absent')}")
    print(f"- issued {when(claims.get('iat'))}, expires {when(claims.get('exp'))}")
    if args.expect_audience:
        print(f"- the audience is the digest asked for: {'yes' if claims.get('aud') == args.expect_audience else 'no'}")
    print(f"- the issuer is GitHub's: {'yes' if claims.get('iss') == ISSUER else 'no'}")
    print(f"- {signature_line(args.file)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """
    A kept token's signature, checked by a fresh process against the keys published now.
    """
    _header, claims, _signed, _signature = read_token(args.file)
    print(f"- a kept token issued {when(claims.get('iat'))}, expired {when(claims.get('exp'))}")
    print(f"- {signature_line(args.file)}")
    return 0


def main() -> int:
    """
    The three subcommands.
    """
    parser = argparse.ArgumentParser(description = "GitHub's OIDC token as a proof, never printing it.")
    sub    = parser.add_subparsers(dest = "command", required = True)
    mint   = sub.add_parser("mint")
    mint.add_argument("--audience", required = True)
    mint.add_argument("--out", required = True)
    claims = sub.add_parser("claims")
    claims.add_argument("--file", required = True)
    claims.add_argument("--expect-audience", default = "")
    verify = sub.add_parser("verify")
    verify.add_argument("--file", required = True)
    args = parser.parse_args()
    return {"mint": cmd_mint, "claims": cmd_claims, "verify": cmd_verify}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
