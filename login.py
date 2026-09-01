"""One-off: log in interactively and print a reusable TG_SESSION string.

Run this on your own machine, not on the server. Paste the output into .env
(and into the Railway variables) as TG_SESSION.
"""
import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

from tgfinder.config import load_config


async def main() -> None:
    cfg = load_config()
    api_id = cfg.api_id or int(input("api_id: ").strip())
    api_hash = cfg.api_hash or input("api_hash: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        print("\nLogged in as:", me.username or me.first_name)
        print("\nTG_SESSION=" + client.session.save())
        print("\nKeep this string secret - it is full access to the account.")


if __name__ == "__main__":
    asyncio.run(main())
