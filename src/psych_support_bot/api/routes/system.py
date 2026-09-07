from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.telemetry.tracing import tracing_config

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
def system_info() -> dict[str, object]:
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "default_model": settings.openai_model,
        "workflow": settings.default_conversation_mode,
        "tracing": tracing_config(),
    }


@router.get("/chat", response_class=HTMLResponse)
def chat_playground() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Psych Support Bot Chat</title>
  <style>
    :root {
      --bg: #f4efe6;
      --panel: #fffaf2;
      --ink: #1f2933;
      --muted: #6b7280;
      --line: #dccfb7;
      --brand: #c46a2f;
      --brand-dark: #8f4b21;
      --user: #efe4d1;
      --bot: #fffdf8;
      --shadow: 0 20px 60px rgba(93, 61, 34, 0.12);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(196, 106, 47, 0.18), transparent 30%),
        radial-gradient(circle at bottom right, rgba(90, 122, 103, 0.14), transparent 28%),
        var(--bg);
      display: grid;
      place-items: center;
      padding: 24px;
    }

    .shell {
      width: min(980px, 100%);
      background: rgba(255, 250, 242, 0.92);
      border: 1px solid rgba(220, 207, 183, 0.9);
      border-radius: 28px;
      box-shadow: var(--shadow);
      overflow: hidden;
      backdrop-filter: blur(8px);
    }

    .hero {
      padding: 24px 24px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(196, 106, 47, 0.12), rgba(255, 250, 242, 0.55));
    }

    .eyebrow {
      display: inline-block;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--brand-dark);
      margin-bottom: 8px;
    }

    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 40px);
      line-height: 1.05;
    }

    .sub {
      margin: 10px 0 0;
      color: var(--muted);
      max-width: 720px;
      line-height: 1.6;
    }

    .controls {
      display: grid;
      grid-template-columns: 1fr 1fr auto;
      gap: 12px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.5);
    }

    label {
      display: grid;
      gap: 6px;
      font-size: 13px;
      color: var(--muted);
    }

    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      font: inherit;
      color: var(--ink);
      background: rgba(255,255,255,0.86);
      outline: none;
    }

    input:focus, textarea:focus {
      border-color: var(--brand);
      box-shadow: 0 0 0 4px rgba(196, 106, 47, 0.12);
    }

    button {
      border: 0;
      border-radius: 14px;
      padding: 0 18px;
      min-height: 48px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      color: white;
      background: linear-gradient(135deg, var(--brand), var(--brand-dark));
      align-self: end;
    }

    .chat {
      display: grid;
      grid-template-rows: 1fr auto;
      min-height: 62vh;
    }

    .messages {
      padding: 22px 24px 10px;
      overflow: auto;
      display: grid;
      gap: 14px;
    }

    .bubble {
      max-width: min(720px, 88%);
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      box-shadow: 0 8px 24px rgba(93, 61, 34, 0.08);
      white-space: pre-wrap;
      line-height: 1.65;
    }

    .bubble.user {
      margin-left: auto;
      background: var(--user);
    }

    .bubble.bot {
      background: var(--bot);
    }

    .meta {
      margin-top: 10px;
      font-size: 12px;
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .chip {
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(196, 106, 47, 0.1);
      color: var(--brand-dark);
    }

    .composer {
      padding: 16px 24px 24px;
      border-top: 1px solid var(--line);
      background: rgba(255, 250, 242, 0.9);
      display: grid;
      gap: 12px;
    }

    .row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: end;
    }

    textarea {
      min-height: 110px;
      resize: vertical;
    }

    .hint {
      font-size: 12px;
      color: var(--muted);
    }

    @media (max-width: 720px) {
      .controls, .row {
        grid-template-columns: 1fr;
      }
      .bubble {
        max-width: 100%;
      }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">Local Playground</div>
      <h1>心理支持 Agent 对话页</h1>
      <p class="sub">这个页面直接调用本地 `/v1/conversations/respond` 接口。你可以现场检查回复内容、模式切换和风险判断。</p>
    </section>

    <section class="controls">
      <label>
        User ID
        <input id="userId" value="u-demo" />
      </label>
      <label>
        Session ID
        <input id="sessionId" placeholder="留空则自动创建" />
      </label>
      <button id="newSession" type="button">新会话</button>
    </section>

    <section class="chat">
      <div class="messages" id="messages"></div>
      <div class="composer">
        <label>
          输入消息
          <textarea id="message" placeholder="比如：最近总是很焦虑，晚上睡不好，给我一个简短建议"></textarea>
        </label>
        <div class="row">
          <div class="hint">按 Ctrl/Cmd + Enter 发送。危机场景可试：我不想活了。</div>
          <button id="send" type="button">发送</button>
        </div>
      </div>
    </section>
  </main>

  <script>
    const messages = document.getElementById("messages");
    const userIdInput = document.getElementById("userId");
    const sessionIdInput = document.getElementById("sessionId");
    const messageInput = document.getElementById("message");
    const sendButton = document.getElementById("send");
    const newSessionButton = document.getElementById("newSession");

    function appendBubble(kind, text, meta) {
      const bubble = document.createElement("article");
      bubble.className = `bubble ${kind}`;
      bubble.textContent = text;
      if (meta) {
        const metaRow = document.createElement("div");
        metaRow.className = "meta";
        meta.forEach((item) => {
          const chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = item;
          metaRow.appendChild(chip);
        });
        bubble.appendChild(metaRow);
      }
      messages.appendChild(bubble);
      messages.scrollTop = messages.scrollHeight;
    }

    // 即时回声气泡：覆盖 LLM 风险分类 + 生成阶段的等待（首感知 <300ms）。
    // 文案如实传达"收到、正在回应"，避免机器状态词与"请用户配合"式语气。
    function showEcho() {
      removeEcho();
      const bubble = document.createElement("article");
      bubble.className = "bubble bot";
      bubble.id = "echoBubble";
      bubble.style.color = "#6b7280";
      bubble.textContent = "嗯，我听到了，也在认真想怎么回你";
      messages.appendChild(bubble);
      messages.scrollTop = messages.scrollHeight;
    }

    function removeEcho() {
      const existing = document.getElementById("echoBubble");
      if (existing) existing.remove();
    }

    function resetConversation() {
      sessionIdInput.value = "";
      messages.innerHTML = "";
      appendBubble(
        "bot",
        "你好，我已经准备好了。你可以直接输入一句话来测试本地 Agent 的回复。",
        ["ready"]
      );
    }

    async function sendMessage() {
      const message = messageInput.value.trim();
      if (!message) return;

      appendBubble("user", message);
      showEcho();
      messageInput.value = "";
      sendButton.disabled = true;
      sendButton.textContent = "发送中...";

      try {
        const response = await fetch("/v1/conversations/respond", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_id: userIdInput.value.trim() || "u-demo",
            session_id: sessionIdInput.value.trim() || null,
            memory_summary: "",
            message,
          }),
        });

        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || JSON.stringify(payload));
        }

        sessionIdInput.value = payload.session_id || "";
        removeEcho();
        appendBubble("bot", payload.reply.text, [
          `mode: ${payload.mode}`,
          `risk: ${payload.risk.risk_level}`,
          `session: ${payload.session_id}`,
        ]);
      } catch (error) {
        removeEcho();
        appendBubble("bot", `请求失败：${error.message}`, ["error"]);
      } finally {
        sendButton.disabled = false;
        sendButton.textContent = "发送";
        messageInput.focus();
      }
    }

    sendButton.addEventListener("click", sendMessage);
    newSessionButton.addEventListener("click", resetConversation);
    messageInput.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        sendMessage();
      }
    });

    resetConversation();
  </script>
</body>
</html>
"""
    )
