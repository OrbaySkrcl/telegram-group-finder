"""Drive the whole system from Telegram, so no terminal is ever required.

You message the control chat (your own Saved Messages by default) and the running
service answers there. Everything the CLI does is reachable this way.
"""
from __future__ import annotations

import asyncio
import logging
import time

from telethon import events

from . import discovery, report
from .scoring import compute_stats

log = logging.getLogger("tgfinder.control")

TELEGRAM_MAX = 4000          # real limit is 4096; leave room for the code fence
PREFIXES = ("/", "!")

HELP = """tgfinder — komutlar

Buraya yazdığın komutlara cevap veririm.

BAŞLARKEN
  /backfill @kanal 30   kanalın son 30 gününü çek ve puanla
  /score                liderlik tablosu
  /detail @kanal        o kanalın çağrı çağrı defteri

KANAL YÖNETİMİ
  /channels             izlenen kanallar
  /monitor @kanal       canlı dinlemeye al
  /unmonitor @kanal     dinlemeyi bırak

KEŞİF
  /discover solana calls   Telegram dizininde ara
  /candidates              aday havuzu
  /approve @kanal          adayı onayla
  /reject @kanal           adayı ele
  /join                    onaylı adaylara katıl

DURUM
  /status               sistem özeti
  /chains               hangi zincirlerde veri toplandı
  /help                 bu liste

Not: @kanal yerine kanal linkini de yapıştırabilirsin."""


def _chunks(text: str, size: int = TELEGRAM_MAX) -> list[str]:
    """Split on line boundaries so tables never break mid-row."""
    out: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:                 # pathological single long line
            out.append(current + line[:size])
            current, line = "", line[size:]
        if len(current) + len(line) > size:
            out.append(current)
            current = line
        else:
            current += line
    if current:
        out.append(current)
    return out or [""]


def _clean_handle(token: str) -> str:
    token = token.strip().strip(",")
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if token.lower().startswith(prefix.lower()):
            token = token[len(prefix):]
    return token.split("?")[0].strip("/")


class Control:
    """Command surface for the service.

    Two transports. With TG_BOT_TOKEN set, a BotFather bot gives the tool its own
    chat window, which keeps it out of Saved Messages. Without one, it falls back
    to listening in whatever chat REPORT_CHAT names (Saved Messages by default).
    """

    def __init__(self, db, client, market, cfg, collector, tracker, bot=None):
        self.db = db
        self.client = client
        self.market = market
        self.cfg = cfg
        self.collector = collector
        self.tracker = tracker
        self.bot = bot
        self.chat_id: int | None = None
        self.owner_id: int | None = None
        self._own_messages: set[int] = set()
        self._busy = False

    @property
    def uses_bot(self) -> bool:
        return self.bot is not None

    async def start(self) -> None:
        me = await self.client.get_me()
        self.owner_id = self.cfg.owner_id or int(me.id)

        if self.uses_bot:
            self.bot.add_event_handler(self._on_bot_message, events.NewMessage())
            try:
                await self.send("tgfinder çalışıyor. Komutlar için /help yaz.")
            except Exception:
                # A bot cannot message someone who has not started it yet. That is
                # not an error - the first /start from the owner opens the channel.
                log.warning("bot cannot message the owner yet - open the bot in "
                            "Telegram and press Start")
            return

        entity = await self.client.get_entity(self.cfg.report_chat)
        self.chat_id = int(entity.id)
        self.client.add_event_handler(self._on_message, events.NewMessage())
        await self.send("tgfinder çalışıyor. Komutlar için /help yaz.")

    # ---- sending ----------------------------------------------------------

    async def send(self, text: str, code: bool = False) -> None:
        for part in _chunks(text):
            body = f"```\n{part}\n```" if code else part
            kwargs = {"parse_mode": "md" if code else None, "link_preview": False}
            if self.uses_bot:
                await self.bot.send_message(self.owner_id, body, **kwargs)
                continue
            msg = await self.client.send_message(self.chat_id, body, **kwargs)
            # Our own replies land back in the control chat as new events.
            self._own_messages.add(int(msg.id))

    # ---- dispatch ---------------------------------------------------------

    async def _on_bot_message(self, event) -> None:
        # The bot is private to one person: anyone else gets no response at all.
        if int(getattr(event, "sender_id", 0) or 0) != self.owner_id:
            return
        await self._handle_text((event.message.message or "").strip())

    async def _on_message(self, event) -> None:
        if self.chat_id is None or int(getattr(event, "chat_id", 0) or 0) not in (
            self.chat_id, -self.chat_id
        ):
            return
        if int(event.message.id) in self._own_messages:
            return
        await self._handle_text((event.message.message or "").strip())

    async def _handle_text(self, text: str) -> None:
        if not text or text[0] not in PREFIXES:
            return
        try:
            await self.dispatch(text[1:])
        except Exception as exc:
            log.exception("command failed: %s", text)
            await self.send(f"Hata: {exc}")

    async def dispatch(self, body: str) -> None:
        parts = body.split()
        command, args = parts[0].lower(), parts[1:]
        handler = getattr(self, f"_cmd_{command}", None)
        if handler is None:
            await self.send(f"Bilinmeyen komut: /{command}\n/help yazabilirsin.")
            return
        await handler(args)

    # ---- commands ---------------------------------------------------------

    async def _cmd_help(self, args) -> None:
        await self.send(HELP, code=True)

    async def _cmd_start(self, args) -> None:
        await self._cmd_help(args)

    async def _cmd_status(self, args) -> None:
        row = self.db.one(
            "SELECT (SELECT COUNT(*) FROM channels WHERE monitored=1) AS monitored,"
            "       (SELECT COUNT(*) FROM channels) AS known,"
            "       (SELECT COUNT(*) FROM calls) AS calls,"
            "       (SELECT COUNT(*) FROM calls WHERE status='pending') AS pending,"
            "       (SELECT COUNT(*) FROM calls WHERE status='done') AS scored,"
            "       (SELECT COUNT(*) FROM candidates WHERE status='new') AS leads"
        )
        await self.send(
            "tgfinder durumu\n"
            f"  izlenen kanal   : {row['monitored']} (toplam kayıtlı {row['known']})\n"
            f"  toplam çağrı    : {row['calls']}\n"
            f"  puanlanmış      : {row['scored']}\n"
            f"  sırada bekleyen : {row['pending']}\n"
            f"  aday havuzu     : {row['leads']}\n"
            f"  simülasyon      : {self.cfg.tp_multiple:g}x sat / "
            f"-%{self.cfg.sl_drop * 100:.0f} kes / {self.cfg.horizon_hours}s takip, "
            f"%{self.cfg.slippage * 100:.0f} slipaj",
            code=True,
        )

    async def _cmd_score(self, args) -> None:
        days = int(args[0]) if args and args[0].isdigit() else self.cfg.window_days
        stats = compute_stats(self.db, days, self.cfg.min_calls)
        if not stats:
            await self.send(
                "Henüz yeterli veri yok.\n"
                f"En az {self.cfg.min_calls} çağrısı olan kanal gerekiyor.\n"
                "Başlamak için: /backfill @birkanal 30"
            )
            return
        await self.send(report.render_telegram(stats, days))

    async def _cmd_detail(self, args) -> None:
        if not args:
            await self.send("Kullanım: /detail @kanal")
            return
        handle = _clean_handle(args[0])
        row = self.db.one(
            "SELECT id, title FROM channels WHERE id=? OR lower(username)=lower(?)",
            (int(handle) if handle.isdigit() else -1, handle),
        )
        if row is None:
            await self.send(f"Bu kanal kayıtlı değil: {handle}\n"
                            "Önce: /backfill @kanal 30")
            return
        await self.send(report.render_channel_detail(self.db, int(row["id"])), code=True)

    async def _cmd_channels(self, args) -> None:
        rows = self.db.query(
            "SELECT c.username, c.title, c.monitored,"
            "  (SELECT COUNT(*) FROM calls WHERE channel_id=c.id) AS calls "
            "FROM channels c ORDER BY calls DESC LIMIT 60"
        )
        if not rows:
            await self.send("Hiç kanal yok. /backfill @kanal 30 ile başla.")
            return
        lines = [f"{'KANAL':30} {'ÇAĞRI':6} DİNLE", "-" * 46]
        for r in rows:
            name = (r["username"] or r["title"] or "?")[:30]
            lines.append(f"{name:30} {r['calls']:<6} {'evet' if r['monitored'] else 'hayır'}")
        await self.send("\n".join(lines), code=True)

    async def _cmd_monitor(self, args) -> None:
        await self._set_monitored(args, 1)

    async def _cmd_unmonitor(self, args) -> None:
        await self._set_monitored(args, 0)

    async def _set_monitored(self, args, flag: int) -> None:
        if not args:
            await self.send("Kullanım: /monitor @kanal")
            return
        changed = 0
        for token in args:
            handle = _clean_handle(token)
            cur = self.db.execute(
                "UPDATE channels SET monitored=? WHERE id=? OR lower(username)=lower(?)",
                (flag, int(handle) if handle.isdigit() else -1, handle),
            )
            changed += cur.rowcount
        self.collector.refresh_monitored(force=True)
        await self.send(f"{changed} kanal güncellendi. "
                        f"Şu an {self.collector.monitored_count} kanal dinleniyor.")

    async def _cmd_backfill(self, args) -> None:
        if not args:
            await self.send("Kullanım: /backfill @kanal 30\n"
                            "Gün sayısı serbest: 7, 30, 90...")
            return
        if self._busy:
            await self.send("Zaten bir backfill çalışıyor, bitince haber veririm.")
            return
        days = self.cfg.window_days
        handles = []
        for token in args:
            if token.isdigit():
                days = int(token)
            else:
                handles.append(_clean_handle(token))
        if not handles:
            await self.send("Kanal adı vermedin. Örnek: /backfill @kanal 30")
            return
        asyncio.create_task(self._run_backfill(handles, days))
        await self.send(f"{len(handles)} kanal için son {days} gün çekiliyor. "
                        "Bu birkaç dakika sürebilir, bitince yazacağım.")

    async def _run_backfill(self, handles: list[str], days: int) -> None:
        self._busy = True
        try:
            summary = []
            not_joined = []
            for i, handle in enumerate(handles):
                if i:
                    # Reading many channels back to back is what triggers
                    # Telegram's rate limiter. Space them out.
                    await asyncio.sleep(5)
                try:
                    entity = await self.client.get_entity(handle)
                except Exception as exc:
                    summary.append(f"  {handle}: bulunamadı ({exc})")
                    continue
                info = await self.collector.backfill(entity, days)
                line = (f"  {handle}: {info['messages']} mesaj, "
                        f"{info['new_calls']} yeni çağrı")
                if info.get("flood_wait"):
                    line += f"  [Telegram {info['flood_wait']}sn yavaşlat dedi]"
                elif info.get("truncated"):
                    line += "  [mesaj limitine takıldı]"
                if info.get("member") is False:
                    line += "  [üye değilsin]"
                    not_joined.append(handle)
                summary.append(line)
            self.collector.refresh_monitored(force=True)

            note = ""
            if not_joined:
                note = ("\n\nÜye olmadığın kanallar: " + ", ".join(not_joined) +
                        "\nGeçmişleri okundu ve puanlanacak, ama CANLI takip için "
                        "üye olman gerekiyor (Telegram sadece üye olduğun "
                        "sohbetlerin yeni mesajlarını gönderiyor). Beğenirsen "
                        "elle katıl, sonra /monitor @kanal yaz.")
            await self.send("Mesajlar çekildi:\n" + "\n".join(summary) + note
                            + "\n\nŞimdi fiyat geçmişi işleniyor...")

            totals = await self.tracker.drain()
            await self.send(f"{totals['resolved']} token çözüldü, "
                            f"{totals['scored']} çağrı puanlandı.")

            stats = compute_stats(self.db, max(days, self.cfg.window_days),
                                  self.cfg.min_calls)
            if stats:
                await self.send(report.render_telegram(stats, days))
            else:
                await self.send(
                    f"Puanlanacak kadar çağrı çıkmadı (kanal başına en az "
                    f"{self.cfg.min_calls} gerekiyor). Daha uzun bir aralık dene: "
                    f"/backfill {handles[0]} 30"
                )
        except Exception as exc:
            log.exception("backfill failed")
            await self.send(f"Backfill hatası: {exc}")
        finally:
            self._busy = False

    async def _cmd_chains(self, args) -> None:
        """Which chains the collected calls are on, and how many were scorable.

        This is the honest answer to "does it handle my chains?" - measured on
        your own data rather than promised.
        """
        rows = self.db.query(
            """
            SELECT COALESCE(t.market_chain, c.chain || ' (çözülmedi)') AS net,
                   COUNT(*) AS n_calls,
                   SUM(CASE WHEN c.status = 'done'    THEN 1 ELSE 0 END) AS scored,
                   SUM(CASE WHEN c.status = 'nochain' THEN 1 ELSE 0 END) AS nochain,
                   SUM(CASE WHEN c.status = 'pending' THEN 1 ELSE 0 END) AS pending
              FROM calls c
              LEFT JOIN tokens t ON t.chain = c.chain AND t.address = c.address
             GROUP BY net
             ORDER BY n_calls DESC
            """
        )
        if not rows:
            await self.send("Henüz hiç çağrı toplanmadı. /backfill @kanal 30 dene.")
            return
        lines = [f"{'ZİNCİR':22} {'ÇAĞRI':7} {'PUANLI':7} {'BEKLİYOR':9} DESTEKSİZ",
                 "-" * 62]
        for r in rows:
            lines.append(f"{r['net'][:22]:22} {r['n_calls']:<7} {r['scored']:<7} "
                         f"{r['pending']:<9} {r['nochain']}")
        lines.append("")
        lines.append("DESTEKSİZ = o zincir için mum verisi bulunamadı; bu çağrılar")
        lines.append("hiçbir kanalın puanına dahil edilmez (bizim eksiğimiz).")
        await self.send("\n".join(lines), code=True)

    async def _cmd_discover(self, args) -> None:
        keywords = [" ".join(args)] if args else discovery.DEFAULT_KEYWORDS
        await self.send(f"{len(keywords)} anahtar kelime aranıyor...")
        results = await discovery.search_public_channels(self.client, keywords)
        discovery.record_search_results(self.db, results)
        await self.send(f"{len(results)} kanal aday havuzuna eklendi. "
                        "Bakmak için: /candidates")

    async def _cmd_candidates(self, args) -> None:
        status = args[0].lower() if args else "new"
        rows = discovery.rank_candidates(self.db, 40, status)
        if not rows:
            await self.send(f"'{status}' durumunda aday yok. "
                            "/discover ile arama yapabilirsin.")
            return
        lines = [f"{'ADAY':30} {'BAHİS':6} {'FWD':5} KAYNAK", "-" * 60]
        for r in rows:
            lines.append(f"{r['handle'][:30]:30} {r['mentions']:<6} "
                         f"{r['forwards']:<5} {(r['source'] or '')[:18]}")
        lines.append("")
        lines.append("Onaylamak için: /approve @aday")
        await self.send("\n".join(lines), code=True)

    async def _cmd_approve(self, args) -> None:
        await self._set_candidate_status(args, "approved",
                                         "onaylandı. Katılmak için: /join")

    async def _cmd_reject(self, args) -> None:
        await self._set_candidate_status(args, "rejected", "elendi.")

    async def _set_candidate_status(self, args, status: str, suffix: str) -> None:
        if not args:
            await self.send(f"Kullanım: /{status[:7]} @aday")
            return
        for token in args:
            discovery.set_status(self.db, _clean_handle(token), status)
        await self.send(f"{len(args)} aday {suffix}")

    async def _cmd_join(self, args) -> None:
        await self.send("Onaylı adaylara katılıyorum (aralarda bekliyorum, "
                        "Telegram toplu katılımı cezalandırıyor)...")
        joined = await discovery.join_approved(self.db, self.client,
                                               self.cfg.max_joins_per_day)
        self.collector.refresh_monitored(force=True)
        if not joined:
            await self.send("Katılınacak onaylı aday yok ya da günlük limit doldu.")
            return
        await self.send(f"Katıldım: {', '.join(joined)}\n"
                        f"Geçmişlerini de çekmek için: /backfill "
                        f"{' '.join('@' + h for h in joined[:3])} 30")
