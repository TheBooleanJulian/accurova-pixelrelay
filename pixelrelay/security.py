"""Secrets handling: random tokens, keyed hashes and the Room PIN.

Nothing here logs or returns a secret except the functions whose purpose is
to hand one to its rightful holder (the owner seeing their PIN, a cookie).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PIN_DIGITS = 6
OWNER_COOKIE = "pr_owner"
ROOM_COOKIE_PREFIX = "pr_room_"


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(secret_key: str, token: str) -> str:
    """Keyed hash used to store session and invitation tokens."""
    return hmac.new(secret_key.encode(), b"token:" + token.encode(), hashlib.sha256).hexdigest()


def ip_hash(secret_key: str, ip: str) -> str:
    """IPs are stored only as a keyed hash, for rate limiting."""
    return hmac.new(secret_key.encode(), b"ip:" + ip.encode(), hashlib.sha256).hexdigest()[:32]


def new_pin_seed() -> str:
    return secrets.token_hex(32)


def derive_pin(secret_key: str, project_id: str, seed: str) -> str:
    """PIN = HMAC(SECRET_KEY, project || seed) mod 10^6, zero-padded.

    The seed is 256 random bits, so the PIN is uniformly random in practice
    (the modulo bias is about 2^-236). Without SECRET_KEY the stored seed says
    nothing about the PIN, so a database leak alone does not expose PINs.
    """
    digest = hmac.new(secret_key.encode(), f"pin:{project_id}:{seed}".encode(), hashlib.sha256).digest()
    return str(int.from_bytes(digest, "big") % 10**PIN_DIGITS).zfill(PIN_DIGITS)


def pin_matches(secret_key: str, project_id: str, seed: str, candidate: str) -> bool:
    expected = derive_pin(secret_key, project_id, seed)
    return hmac.compare_digest(expected.encode(), candidate.encode())


def normalise_pin(raw: str) -> str | None:
    digits = "".join(ch for ch in raw if not ch.isspace() and ch != "-")
    if len(digits) != PIN_DIGITS or not digits.isascii() or not digits.isdigit():
        return None
    return digits


def room_cookie_name(slug: str) -> str:
    return ROOM_COOKIE_PREFIX + slug


def new_slug() -> str:
    """Public project identifier for URLs: 10 chars of lowercase base32 (50 bits)."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def device_label(user_agent: str) -> str:
    """A coarse, human label for a remembered device (no fingerprinting)."""
    ua = user_agent or ""
    browser = next(
        (
            name
            for key, name in (
                ("Edg/", "Edge"),
                ("OPR/", "Opera"),
                ("Firefox/", "Firefox"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if key in ua
        ),
        "Browser",
    )
    system = next(
        (
            name
            for key, name in (
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Android", "Android"),
                ("Windows", "Windows"),
                ("Mac OS X", "Mac"),
                ("Linux", "Linux"),
            )
            if key in ua
        ),
        "device",
    )
    return f"{browser} on {system}"
