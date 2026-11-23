"""Stream one Telegram video straight into YouTube. Nothing is written to disk.

Telegram (MTProto, bot token) -> RAM (16 MiB window) -> YouTube resumable upload.
Env: CHAT_ID, MESSAGE_ID, TG_API_ID, TG_API_HASH, TG_BOT_TOKEN,
     YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, DEFAULT_PRIVACY (optional)
"""
import asyncio, json, math, os, re, time

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession

REQ = 512 * 1024              # Telegram download request size (max allowed)
CHUNK = 16 * 1024 * 1024      # YouTube chunk: must be a multiple of 256 KiB
MAX_FAILS = 10
RETRYABLE = (httpx.TransportError, httpx.TimeoutException, ConnectionError, TimeoutError)


class Retry(Exception):
    pass


class BotAPI:
    """Status messages go through the plain Bot API (no entity lookup needed)."""
    def __init__(self, http, token, chat_id):
        self.http, self.base, self.chat_id, self.mid, self.last = http, f"https://api.telegram.org/bot{token}", chat_id, None, ""

    async def say(self, text):
        if text == self.last:
            return
        self.last = text
        try:
            if self.mid is None:
                r = await self.http.post(f"{self.base}/sendMessage", json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
                self.mid = r.json()["result"]["message_id"]
            else:
                await self.http.post(f"{self.base}/editMessageText", json={"chat_id": self.chat_id, "message_id": self.mid, "text": text, "disable_web_page_preview": True})
        except Exception:
            pass  # status updates are best effort


class YouTube:
    API = "https://www.googleapis.com/upload/youtube/v3/videos"

    def __init__(self, http):
        self.http, self.token, self.exp = http, None, 0

    async def _auth(self):
        if time.time() > self.exp - 300:
            r = await self.http.post("https://oauth2.googleapis.com/token", data={
                "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
                "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"})
            r.raise_for_status()
            j = r.json()
            self.token, self.exp = j["access_token"], time.time() + j["expires_in"]
        return {"Authorization": f"Bearer {self.token}"}

    async def start(self, meta, size, mime):
        h = await self._auth()
        h |= {"Content-Type": "application/json; charset=UTF-8",
              "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": mime}
        r = await self.http.post(self.API, params={"uploadType": "resumable", "part": "snippet,status"},
                                 headers=h, content=json.dumps(meta))
        if r.status_code >= 400:
            print(f"YouTube upload init failed: HTTP {r.status_code}\n{r.text}", flush=True)
        r.raise_for_status()
        return r.headers["Location"]

    @staticmethod
    def _parse(r):
        if r.status_code in (200, 201):
            return "done", r.json()
        if r.status_code == 308:
            rng = r.headers.get("Range")          # e.g. "bytes=0-16777215"
            return "more", (int(rng.split("-")[1]) + 1 if rng else 0)
        if r.status_code in (500, 502, 503, 504, 429):
            raise Retry(f"HTTP {r.status_code}")
        r.raise_for_status()
        raise RuntimeError(f"Unexpected HTTP {r.status_code}")

    async def put(self, session, data, start, total):
        h = {"Content-Length": str(len(data)), "Content-Range": f"bytes {start}-{start + len(data) - 1}/{total}"}
        return self._parse(await self.http.put(session, content=data, headers=h))

    async def status(self, session, total):
        h = {"Content-Length": "0", "Content-Range": f"bytes */{total}"}
        return self._parse(await self.http.put(session, headers=h))


async def read_range(client, media, pos, n):
    buf = bytearray()
    async for part in client.iter_download(media, offset=pos, request_size=REQ, limit=math.ceil(n / REQ)):
        buf += part
    return bytes(buf[:n])


def build_meta(caption, filename):
    privacy = os.environ.get("DEFAULT_PRIVACY", "private")
    for tag in ("public", "unlisted", "private"):
        if re.search(rf"(?i)#{tag}\b", caption):
            privacy = tag
    caption = re.sub(r"(?i)#(public|unlisted|private)\b", "", caption).strip()
    lines = caption.splitlines()
    title = lines[0].strip() if lines and lines[0].strip() else os.path.splitext(filename or "Telegram video")[0]
    title = re.sub(r"[<>]", "", title)[:100] or "Telegram video"
    desc = re.sub(r"[<>]", "", "\n".join(lines[1:]).strip())
    return {"snippet": {"title": title, "description": desc, "categoryId": "22"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}, privacy


async def backoff(fails):
    if fails > MAX_FAILS:
        raise RuntimeError("Too many consecutive failures")
    await asyncio.sleep(min(2 ** fails, 60))


async def main():
    chat_id, message_id = int(os.environ["CHAT_ID"]), int(os.environ["MESSAGE_ID"])
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as http:
        bot = BotAPI(http, os.environ["TG_BOT_TOKEN"], chat_id)
        try:
            client = TelegramClient(StringSession(), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
            await client.start(bot_token=os.environ["TG_BOT_TOKEN"])
            # peer-less lookup: works for a bot's private-chat message ids
            msg = await client.get_messages(None, ids=message_id)
            if not msg or not msg.media or not msg.file:
                raise RuntimeError("Could not read that message's media")
            size, mime = msg.file.size, msg.file.mime_type or "application/octet-stream"
            meta, privacy = build_meta(msg.message or "", msg.file.name)

            yt = YouTube(http)
            session = await yt.start(meta, size, mime)
            await bot.say(f"⬆️ Uploading {size / 1e6:,.0f} MB to YouTube ({privacy})…")

            pos, fails, resync, last_ui = 0, 0, False, 0.0
            while True:
                try:
                    if resync:
                        kind, val = await yt.status(session, size)
                        resync = False
                    else:
                        data = await read_range(client, msg.media, pos, min(CHUNK, size - pos))
                        kind, val = await yt.put(session, data, pos, size)
                    fails = 0
                except (*RETRYABLE, Retry) as e:
                    fails += 1
                    resync = True                 # ask YouTube where we really are
                    print(f"retry {fails}: {type(e).__name__}", flush=True)
                    await backoff(fails)
                    continue
                if kind == "done":
                    vid = val["id"]
                    break
                pos = val                          # next offset; Telegram re-read starts here
                if time.time() - last_ui > 20:
                    last_ui = time.time()
                    await bot.say(f"⬆️ Uploading… {pos / size:.0%}")
                print(f"{pos / size:.0%}", flush=True)

            title = meta["snippet"]["title"]
            await bot.say(f"✅ {title}\n\n📺 https://youtu.be/{vid}\n🔒 {privacy}\n\nYouTube may still be processing it.")
            await client.disconnect()
        except Exception as e:
            await bot.say(f"❌ Upload failed: {type(e).__name__}. Check the GitHub Actions log.")
            raise


if __name__ == "__main__":
    asyncio.run(main())
