"""SQLite storage. Small enough that a single connection with a lock is plenty."""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS channels (
    id           INTEGER PRIMARY KEY,
    tg_id        INTEGER UNIQUE,
    username     TEXT,
    title        TEXT,
    members      INTEGER,
    added_at     INTEGER NOT NULL,
    monitored    INTEGER NOT NULL DEFAULT 1,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_channels_username ON channels(lower(username));

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    channel_id   INTEGER NOT NULL REFERENCES channels(id),
    tg_msg_id    INTEGER NOT NULL,
    ts           INTEGER NOT NULL,
    text         TEXT,
    fwd_from     TEXT,
    UNIQUE(channel_id, tg_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

CREATE TABLE IF NOT EXISTS tokens (
    chain            TEXT NOT NULL,   -- extraction chain: 'solana' | 'evm' (join key)
    market_chain     TEXT,            -- DexScreener chainId: 'solana','base','bsc',...
    address          TEXT NOT NULL,
    symbol           TEXT,
    name             TEXT,
    pair_address     TEXT,
    pair_created_at  INTEGER,
    resolved_at      INTEGER,
    resolve_status   TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|nopair
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY,
    channel_id    INTEGER NOT NULL REFERENCES channels(id),
    message_id    INTEGER REFERENCES messages(id),
    chain         TEXT NOT NULL,
    address       TEXT NOT NULL,
    ts            INTEGER NOT NULL,
    mc_at_call    REAL,
    price_at_call REAL,
    liq_at_call   REAL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|done|unresolved
    UNIQUE(channel_id, chain, address)
);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status, ts);
CREATE INDEX IF NOT EXISTS idx_calls_token ON calls(chain, address, ts);

CREATE TABLE IF NOT EXISTS outcomes (
    call_id          INTEGER PRIMARY KEY REFERENCES calls(id),
    entry_price      REAL,
    exit_price       REAL,
    exit_reason      TEXT,          -- tp|sl|time|no_data
    sim_return       REAL,          -- fractional, after slippage, e.g. 0.42 = +42%
    max_multiple     REAL,          -- best price reached / entry price
    min_multiple     REAL,
    minutes_to_max   INTEGER,
    minutes_to_2x    INTEGER,
    rugged           INTEGER NOT NULL DEFAULT 0,
    candle_source    TEXT,          -- minute|hour|none
    computed_at      INTEGER
);

CREATE TABLE IF NOT EXISTS candidates (
    id            INTEGER PRIMARY KEY,
    handle        TEXT NOT NULL UNIQUE,
    is_invite     INTEGER NOT NULL DEFAULT 0,
    source        TEXT,
    mentions      INTEGER NOT NULL DEFAULT 0,
    forwards      INTEGER NOT NULL DEFAULT 0,
    first_seen_at INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new'  -- new|approved|joined|rejected
);

CREATE TABLE IF NOT EXISTS forward_edges (
    src_handle  TEXT NOT NULL,
    dst_channel INTEGER NOT NULL REFERENCES channels(id),
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (src_handle, dst_channel)
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- small helpers used across modules -------------------------------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def upsert_channel(self, tg_id: int, username: str | None, title: str | None,
                       members: int | None, now: int, monitored: int = 1) -> int:
        self.execute(
            "INSERT INTO channels(tg_id, username, title, members, added_at, monitored) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET "
            "  username = COALESCE(excluded.username, channels.username), "
            "  title    = COALESCE(excluded.title, channels.title), "
            "  members  = COALESCE(excluded.members, channels.members)",
            (tg_id, username, title, members, now, monitored),
        )
        row = self.one("SELECT id FROM channels WHERE tg_id = ?", (tg_id,))
        assert row is not None
        return int(row["id"])

    def add_candidate(self, handle: str, is_invite: bool, source: str, now: int,
                      mentions: int = 0, forwards: int = 0) -> None:
        self.execute(
            "INSERT INTO candidates(handle, is_invite, source, mentions, forwards, "
            "                       first_seen_at, last_seen_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(handle) DO UPDATE SET "
            "  mentions     = candidates.mentions + excluded.mentions, "
            "  forwards     = candidates.forwards + excluded.forwards, "
            "  last_seen_at = excluded.last_seen_at",
            (handle.lower(), int(is_invite), source, mentions, forwards, now, now),
        )
