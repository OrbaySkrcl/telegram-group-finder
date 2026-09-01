"""Free market-data clients: DexScreener for lookups, GeckoTerminal for candles.

Neither needs an API key, which is what keeps the whole system free to run.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

DEXSCREENER = "https://api.dexscreener.com"
GECKOTERMINAL = "https://api.geckoterminal.com/api/v2"

# DexScreener chainId -> GeckoTerminal network slug.
NETWORK_MAP = {
    "solana": "solana",
    "ethereum": "eth",
    "bsc": "bsc",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "sui": "sui-network",
    "blast": "blast",
    "tron": "tron",
    "abstract": "abstract",
    "hyperliquid": "hyperevm",
}


class RateLimiter:
    """Simple async token bucket: at most `rate` calls per `per` seconds."""

    def __init__(self, rate: int, per: float = 60.0):
        self.rate = rate
        self.per = per
        self._allowance = float(rate)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._allowance += (now - self._last) * (self.rate / self.per)
                self._last = now
                if self._allowance > self.rate:
                    self._allowance = float(self.rate)
                if self._allowance >= 1.0:
                    self._allowance -= 1.0
                    return
                await asyncio.sleep((1.0 - self._allowance) * (self.per / self.rate))


@dataclass
class PairInfo:
    chain: str
    pair_address: str
    base_address: str
    symbol: str | None
    name: str | None
    price_usd: float | None
    market_cap: float | None
    liquidity_usd: float | None
    pair_created_at: int | None   # unix seconds


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_pair(raw: dict) -> PairInfo | None:
    base = raw.get("baseToken") or {}
    if not raw.get("pairAddress") or not base.get("address"):
        return None
    created_ms = raw.get("pairCreatedAt")
    return PairInfo(
        chain=raw.get("chainId", ""),
        pair_address=raw["pairAddress"],
        base_address=base["address"],
        symbol=base.get("symbol"),
        name=base.get("name"),
        price_usd=_to_float(raw.get("priceUsd")),
        # marketCap is missing on some pairs; fdv is the usable stand-in.
        market_cap=_to_float(raw.get("marketCap")) or _to_float(raw.get("fdv")),
        liquidity_usd=_to_float((raw.get("liquidity") or {}).get("usd")),
        pair_created_at=int(created_ms // 1000) if isinstance(created_ms, (int, float)) else None,
    )


def _best_pair(pairs: list[dict], address: str) -> PairInfo | None:
    """Pick the deepest pool whose base token is the address we asked about."""
    want = address.lower()
    best: PairInfo | None = None
    for raw in pairs:
        info = _parse_pair(raw)
        if info is None or info.base_address.lower() != want:
            continue
        if best is None or (info.liquidity_usd or 0) > (best.liquidity_usd or 0):
            best = info
    return best


class MarketData:
    def __init__(self, timeout: float = 20.0):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "tgfinder/0.1 (+https://github.com/OrbaySkrcl/telegram-group-finder)"},
        )
        # DexScreener publishes ~300 req/min on the token endpoints; GeckoTerminal's
        # free tier is 30 req/min. Stay comfortably underneath both.
        self._ds_limit = RateLimiter(240, 60.0)
        self._gt_limit = RateLimiter(25, 60.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, limiter: RateLimiter, params: dict | None = None,
                   attempts: int = 3) -> dict | None:
        for attempt in range(attempts):
            await limiter.acquire()
            try:
                resp = await self._client.get(url, params=params)
            except httpx.HTTPError:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code == 429:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    async def lookup_tokens(self, addresses: list[str]) -> dict[str, PairInfo]:
        """Resolve up to 30 token addresses per request into their deepest pool."""
        out: dict[str, PairInfo] = {}
        for i in range(0, len(addresses), 30):
            chunk = addresses[i:i + 30]
            data = await self._get(
                f"{DEXSCREENER}/latest/dex/tokens/{','.join(chunk)}", self._ds_limit
            )
            pairs = (data or {}).get("pairs") or []
            for address in chunk:
                info = _best_pair(pairs, address)
                if info is not None:
                    out[address] = info
        return out

    async def search(self, address: str) -> PairInfo | None:
        """Fallback for addresses that are a pool rather than a token."""
        data = await self._get(
            f"{DEXSCREENER}/latest/dex/search", self._ds_limit, params={"q": address}
        )
        pairs = (data or {}).get("pairs") or []
        info = _best_pair(pairs, address)
        if info is not None:
            return info
        # Address was probably the pool itself; take that pool directly.
        for raw in pairs:
            if (raw.get("pairAddress") or "").lower() == address.lower():
                return _parse_pair(raw)
        return None

    async def resolve(self, address: str) -> PairInfo | None:
        found = await self.lookup_tokens([address])
        return found.get(address) or await self.search(address)

    async def ohlcv(self, chain: str, pair_address: str, timeframe: str = "minute",
                    before_ts: int | None = None, limit: int = 1000) -> list[tuple]:
        """Return [(ts, open, high, low, close, volume), ...] ascending by time."""
        network = NETWORK_MAP.get(chain)
        if not network:
            return []
        params: dict[str, object] = {"aggregate": 1, "limit": min(limit, 1000), "currency": "usd"}
        if before_ts:
            params["before_timestamp"] = before_ts
        data = await self._get(
            f"{GECKOTERMINAL}/networks/{network}/pools/{pair_address}/ohlcv/{timeframe}",
            self._gt_limit,
            params=params,
        )
        raw = (((data or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = []
        for row in raw:
            if not row or len(row) < 5:
                continue
            try:
                candles.append((
                    int(row[0]), float(row[1]), float(row[2]),
                    float(row[3]), float(row[4]),
                    float(row[5]) if len(row) > 5 and row[5] is not None else 0.0,
                ))
            except (TypeError, ValueError):
                continue
        candles.sort(key=lambda c: c[0])
        return candles

    async def candles_covering(self, chain: str, pair_address: str, start_ts: int,
                               end_ts: int) -> tuple[list[tuple], str]:
        """Fetch candles spanning [start_ts, end_ts], preferring minute resolution.

        GeckoTerminal caps a response at 1000 candles and only retains minute data
        for a limited window, so fall back to hourly for older calls.
        """
        for timeframe, step in (("minute", 60), ("hour", 3600)):
            needed = (end_ts - start_ts) // step + 2
            if needed > 1000 and timeframe == "minute":
                continue
            # `before_timestamp` is exclusive-ish; ask for the window end plus a margin.
            candles = await self.ohlcv(chain, pair_address, timeframe,
                                       before_ts=end_ts + step * 2, limit=1000)
            if candles and candles[0][0] <= start_ts + step:
                return candles, timeframe
        return [], "none"
