"""Pull contract addresses and Telegram handles out of free-form message text."""
from __future__ import annotations

import re
from dataclasses import dataclass

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_INDEX = {c: i for i, c in enumerate(B58_ALPHABET)}

SOLANA_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
EVM_RE = re.compile(r"(?<![0-9a-fA-FxX])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")

# Telegram handles: t.me/name, https://t.me/name, @name. Invite links (t.me/+hash,
# t.me/joinchat/hash) are captured separately because they cannot be resolved by name.
TME_RE = re.compile(
    r"(?:https?://)?t\.me/(?P<invite>(?:joinchat/|\+))?(?P<name>[A-Za-z0-9_+\-]{4,64})",
    re.IGNORECASE,
)
AT_HANDLE_RE = re.compile(r"(?<![\w/@])@([A-Za-z][A-Za-z0-9_]{4,31})\b")

# Chain-native / infrastructure addresses that show up constantly but are never a call.
DENYLIST = {
    "So11111111111111111111111111111111111111112",  # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC (sol)
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT (sol)
    "11111111111111111111111111111111",              # system program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL token program
    "ComputeBudget111111111111111111111111111111",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",    # USDT (eth)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",    # USDC (eth)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",    # WETH
    "0x0000000000000000000000000000000000000000",
}
DENYLIST_LOWER = {a.lower() for a in DENYLIST}

# Telegram usernames that are noise rather than a channel worth tracking.
HANDLE_DENYLIST = {
    "share", "joinchat", "telegram", "proxy", "socks", "addstickers", "addtheme",
    "iv", "share/url", "s", "c", "bot", "telegramtips",
}

# Well-known chart/trade sites: the path segment right after the host is an address.
URL_ADDRESS_RE = re.compile(
    r"(?:dexscreener\.com|birdeye\.so|pump\.fun|gmgn\.ai|photon-sol\.tinyastro\.io|"
    r"solscan\.io|dextools\.io|axiom\.trade|bullx\.io|neo\.bullx\.io|jup\.ag|geckoterminal\.com)"
    r"/[A-Za-z0-9_\-/]*?([1-9A-HJ-NP-Za-km-z]{32,44}|0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)


def b58_decode(value: str) -> bytes | None:
    """Decode base58; return None when the string is not valid base58."""
    num = 0
    for char in value:
        idx = B58_INDEX.get(char)
        if idx is None:
            return None
        num = num * 58 + idx
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(value) - len(value.lstrip("1"))
    return b"\x00" * pad + raw


def is_solana_address(value: str) -> bool:
    """A Solana mint is a 32-byte base58 public key."""
    if not 32 <= len(value) <= 44:
        return False
    decoded = b58_decode(value)
    return decoded is not None and len(decoded) == 32


@dataclass(frozen=True)
class Address:
    chain: str          # "solana" | "evm"
    address: str        # original casing (EVM stored lowercase)
    from_url: bool      # found inside a chart/trade link rather than bare text


def extract_addresses(text: str) -> list[Address]:
    """Return every plausible token address in `text`, de-duplicated, in order."""
    if not text:
        return []

    url_hits = {m.group(1) for m in URL_ADDRESS_RE.finditer(text)}
    url_hits_lower = {h.lower() for h in url_hits}

    found: dict[str, Address] = {}

    for match in EVM_RE.finditer(text):
        addr = match.group(0).lower()
        if addr in DENYLIST_LOWER or addr in found:
            continue
        found[addr] = Address("evm", addr, addr in url_hits_lower)

    for match in SOLANA_RE.finditer(text):
        addr = match.group(0)
        if addr in DENYLIST or addr in found or not is_solana_address(addr):
            continue
        found[addr] = Address("solana", addr, addr in url_hits)

    return list(found.values())


@dataclass(frozen=True)
class Handle:
    name: str
    is_invite: bool


def extract_handles(text: str) -> list[Handle]:
    """Return referenced Telegram channels/groups (public names and invite hashes)."""
    if not text:
        return []
    found: dict[str, Handle] = {}

    for match in TME_RE.finditer(text):
        name = match.group("name")
        invite = bool(match.group("invite")) or name.startswith("+")
        key = name if invite else name.lower()
        if not invite and key in HANDLE_DENYLIST:
            continue
        found.setdefault(key, Handle(name.lstrip("+"), invite))

    for match in AT_HANDLE_RE.finditer(text):
        key = match.group(1).lower()
        if key in HANDLE_DENYLIST or key in found:
            continue
        found[key] = Handle(match.group(1), False)

    return list(found.values())
