// Cloudflare Worker: Telegram webhook -> triggers the GitHub Actions upload workflow.
const reply = (chat_id, text, reply_to_message_id) =>
  new Response(JSON.stringify({ method: "sendMessage", chat_id, text, reply_to_message_id }),
    { headers: { "content-type": "application/json" } });

export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("ok");
    if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET)
      return new Response("forbidden", { status: 403 });

    const m = (await req.json()).message;
    if (!m || String(m.from?.id) !== String(env.ALLOWED_USER_ID)) return new Response("ok");

    const isVideo = m.video || m.animation || m.document?.mime_type?.startsWith("video/");
    if (!isVideo) return reply(m.chat.id, "Send or forward a video file. Add a caption for the title (first line), description, and #public / #unlisted / #private.", m.message_id);

    const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/upload.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tg-yt-uploader",
      },
      body: JSON.stringify({ ref: "main", inputs: { chat_id: String(m.chat.id), message_id: String(m.message_id) } }),
    });
    return reply(m.chat.id, r.status === 204 ? "⏳ Queued. The upload runner is starting…" : `❌ Couldn't start the job (GitHub ${r.status}).`, m.message_id);
  },
};
