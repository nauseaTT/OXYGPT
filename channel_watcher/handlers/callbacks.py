"""Callback handlers for Ask AI conversations and monitor management.

Ask AI lets users ask follow-up questions about a specific analysed
message. The conversation history is stored per (user, monitor, message)
triple in the ``chat_sessions`` table.

UX overview (see ``ui/keyboards.ask_ai_keyboard`` / ``ui/formatter``):
  - Opening Ask AI shows a context header (badge + summary/full text) and
    a row of one-tap deterministic quick-questions, so most users never
    need to type anything for the common "explain more" / "risks?" asks.
  - A live "🤖 در حال فکر کردن…" placeholder (edited in place with the
    real answer) plus a native Telegram "typing…" indicator cover the
    few seconds of model latency.
  - Each turn shows a running counter and nudges the user toward
    "🧹 پاک‌سازی گفتگو" as the per-session history cap approaches, instead
    of silently trimming old turns with no explanation.
  - "🗒 نمایش متن کامل پست" toggles between the AI summary and the raw
    original text, entirely client-side (no DB write, no AI call).
"""

import json
import logging
from typing import Any, Optional

from telethon import TelegramClient, events, Button

from ..database import (
    get_monitor,
    get_analysis_by_id,
    get_session,
    create_session,
    append_to_history,
    get_history,
    count_turns,
    reset_session_history,
    MAX_HISTORY_LENGTH,
)
from ..services.analyzer import ASK_AI_SYSTEM_PROMPT, sanitize_for_ask_ai_fence
from ..states import UserState, get_state_manager
from ..ui.keyboards import ask_ai_keyboard, ask_ai_suggestions, ask_ai_suggestions_keyboard, back_keyboard
from ..ui.formatter import format_ask_ai_intro, format_ask_ai_answer, THINKING_TEXT
from ..ui.safe_edit import safe_edit

logger = logging.getLogger(__name__)

_state_mgr = get_state_manager()

_client: Optional[TelegramClient] = None
_bot: Any = None

# A "turn" is one user question. Beyond this, further questions are still
# answered, but the UI nudges the user to clear the conversation — keeps
# each session's history (and therefore the tokens re-sent every turn)
# bounded to a sane conversation length rather than growing forever.
MAX_ASK_AI_TURNS = MAX_HISTORY_LENGTH // 2


def _extract_ask_ids(data: bytes) -> tuple:
    """Parse ``cw_ask_{mon_id}_{msg_id}`` callback data."""
    decoded = data.decode()
    parts = decoded.split("_")
    return int(parts[2]), int(parts[3])


def _build_system_context(analysis: dict) -> str:
    """Build the guarded Ask AI system prompt for a specific analysis row."""
    key_points_text = "\n".join(
        f"- {p}" for p in json.loads(analysis.get("key_points", "[]"))
    )
    return (
        ASK_AI_SYSTEM_PROMPT
        .replace("{original_message}", sanitize_for_ask_ai_fence(analysis.get("message_text", "")))
        .replace("{summary}", sanitize_for_ask_ai_fence(analysis.get("summary", "")))
        .replace("{key_points}", sanitize_for_ask_ai_fence(key_points_text))
    )


async def _ask_model(uid: int, monitor_id: int, session_id: int, question: str) -> Optional[str]:
    """Persist *question*, call the AI with full turn history, persist the answer.

    Returns the answer text, or ``None`` on failure (caller shows an
    error message and keeps the session intact for retry).
    """
    await append_to_history(session_id, "user", question)
    history = await get_history(session_id)

    prompt = []
    system_role = None
    for msg in history:
        msg_role = msg.get("role")
        if msg_role == "system":
            system_role = msg.get("content", "")
            continue
        role = "model" if msg_role in ("assistant", "model") else "user"
        prompt.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

    if system_role is None:
        # Should not happen — every session is seeded with a system
        # message on creation — but fall back to the bare prompt rather
        # than crash if it's somehow missing.
        system_role = ASK_AI_SYSTEM_PROMPT

    try:
        ai_service = _bot.get_ai_service(uid)
        model = getattr(ai_service, "fallback_model", None) or "gemini-2.0-flash"
        response_text, _ = await ai_service.generate_response(
            prompt=prompt,
            role=system_role,
            model_name=model,
            allow_market_tool=False,
            allow_image_tool=False,
        )
    except Exception as exc:
        logger.warning("Ask AI generation failed for user %s: %s", uid, exc)
        return None

    if not response_text:
        return None

    await append_to_history(session_id, "assistant", response_text)
    return response_text


# ── Exported handler (callable from pending_message_handler) ────────


async def cw_handle_ask_ai_message(event) -> None:
    """Process user's question text while in AWAITING_ASK_AI state.

    Sends the question to the AI model along with the message context,
    stores the response, and keeps the user in AWAITING_ASK_AI so they
    can ask further questions.
    """
    uid = event.sender_id
    state, data = await _state_mgr.get_state(uid)
    if state != UserState.AWAITING_ASK_AI or not data:
        return

    question = (event.text or "").strip()
    if not question:
        return

    monitor_id = data.get("monitor_id")
    message_id = data.get("message_id")
    session_id = data.get("session_id")
    show_full_text = bool(data.get("show_full_text", False))

    analysis = await get_analysis_by_id(message_id)
    if not analysis:
        await event.reply("❌ پیام مورد نظر یافت نشد.")
        return

    # Live feedback: a typing indicator plus an editable placeholder —
    # Ask AI answers take a few seconds, and silence during that window
    # reads as the bot being broken.
    placeholder = await event.reply(THINKING_TEXT, parse_mode="html")
    try:
        async with _client.action(uid, "typing"):
            response_text = await _ask_model(uid, monitor_id, session_id, question)
    except Exception:
        logger.exception("Ask AI turn crashed for user %s", uid)
        response_text = None

    if not response_text:
        await safe_edit(
            placeholder,
            "❌ خطا در ارتباط با مدل. لطفاً دوباره تلاش کنید.",
            buttons=ask_ai_keyboard(monitor_id, message_id, await count_turns(session_id), show_full_text),
        )
        return

    turn_count = await count_turns(session_id)
    text = format_ask_ai_answer(question, response_text, turn_count, MAX_ASK_AI_TURNS)
    kb = ask_ai_keyboard(monitor_id, message_id, turn_count, show_full_text)
    await safe_edit(placeholder, text, buttons=kb, parse_mode="html")


# ── Callback registrations ────────────────────────────────────────────


def register_callback_handlers(client: TelegramClient, bot_instance: Any) -> None:
    """Register Ask AI and monitor-management callback handlers."""
    global _client, _bot
    _client = client
    _bot = bot_instance

    ASK_RE = r"^cw_ask_\d+_\d+$"
    EXIT_RE = r"^cw_exit_ask_\d+$"
    SUGGEST_RE = r"^cw_asksug_\d+_\d+_\d+$"
    CLEAR_RE = r"^cw_ask_clear_\d+_\d+$"
    FULLTEXT_RE = r"^cw_ask_fulltext_\d+_\d+$"

    async def _open_ask_ai(event, uid: int, monitor_id: int, msg_id: int, show_full_text: bool = False) -> None:
        """Shared entry point: opens/refreshes the Ask AI screen for a post."""
        monitor = await get_monitor(monitor_id)
        if not monitor or monitor["user_id"] != uid:
            await event.answer("دسترسی غیرمجاز!", alert=True)
            return

        analysis = await get_analysis_by_id(msg_id)
        if not analysis or analysis.get("monitor_id") != monitor_id:
            await event.answer("تحلیل یافت نشد.", alert=True)
            return

        session = await get_session(uid, monitor_id, msg_id)
        if session:
            session_id = session["id"]
        else:
            session_id = await create_session(uid, monitor_id, msg_id)
            await append_to_history(session_id, "system", _build_system_context(analysis))

        await _state_mgr.set_state(
            uid,
            UserState.AWAITING_ASK_AI,
            {
                "monitor_id": monitor_id,
                "message_id": msg_id,
                "session_id": session_id,
                "show_full_text": show_full_text,
            },
        )

        topics = json.loads(analysis.get("topics", "[]"))
        turn_count = await count_turns(session_id)
        intro = format_ask_ai_intro(analysis, show_full_text)
        kb = ask_ai_suggestions_keyboard(monitor_id, msg_id, topics) + ask_ai_keyboard(
            monitor_id, msg_id, turn_count, show_full_text
        )
        await safe_edit(event, intro, buttons=kb, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=ASK_RE))
    async def cw_ask_ai_start(event):
        """Start an Ask AI conversation on a specific analysed message."""
        uid = event.sender_id
        try:
            monitor_id, msg_id = _extract_ask_ids(event.data)
        except (IndexError, ValueError):
            await event.answer("داده نامعتبر.", alert=True)
            return
        await _open_ask_ai(event, uid, monitor_id, msg_id)

    @client.on(events.CallbackQuery(pattern=SUGGEST_RE))
    async def cw_ask_ai_suggestion(event):
        """One-tap quick question: run the exact same pipeline as free-text input."""
        uid = event.sender_id
        try:
            parts = event.data.decode().split("_")
            monitor_id, msg_id, idx = int(parts[1]), int(parts[2]), int(parts[3])
        except (IndexError, ValueError):
            await event.answer("داده نامعتبر.", alert=True)
            return

        monitor = await get_monitor(monitor_id)
        if not monitor or monitor["user_id"] != uid:
            await event.answer("دسترسی غیرمجاز!", alert=True)
            return

        analysis = await get_analysis_by_id(msg_id)
        if not analysis or analysis.get("monitor_id") != monitor_id:
            await event.answer("تحلیل یافت نشد.", alert=True)
            return

        session = await get_session(uid, monitor_id, msg_id)
        if not session:
            # Session should already exist (created when Ask AI was opened);
            # this is only a defensive fallback for a stale/expired card.
            session_id = await create_session(uid, monitor_id, msg_id)
            await append_to_history(session_id, "system", _build_system_context(analysis))
        else:
            session_id = session["id"]

        topics = json.loads(analysis.get("topics", "[]"))
        suggestions = ask_ai_suggestions(topics)
        if idx >= len(suggestions):
            await event.answer("این پیشنهاد دیگر در دسترس نیست.", alert=True)
            return
        question = suggestions[idx][1]

        await event.answer("🤖 در حال بررسی…")

        await safe_edit(event, THINKING_TEXT, parse_mode="html", fallback_reply=False)

        try:
            async with client.action(uid, "typing"):
                response_text = await _ask_model(uid, monitor_id, session_id, question)
        except Exception:
            logger.exception("Ask AI suggestion turn crashed for user %s", uid)
            response_text = None

        await _state_mgr.set_state(
            uid,
            UserState.AWAITING_ASK_AI,
            {"monitor_id": monitor_id, "message_id": msg_id, "session_id": session_id, "show_full_text": False},
        )

        if not response_text:
            text = "❌ خطا در ارتباط با مدل. لطفاً دوباره تلاش کنید."
            kb = ask_ai_keyboard(monitor_id, msg_id, await count_turns(session_id), False)
        else:
            turn_count = await count_turns(session_id)
            text = format_ask_ai_answer(question, response_text, turn_count, MAX_ASK_AI_TURNS)
            kb = ask_ai_keyboard(monitor_id, msg_id, turn_count, False)

        await safe_edit(event, text, buttons=kb, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=CLEAR_RE))
    async def cw_ask_ai_clear(event):
        """Wipe the Q&A history for this session, keeping the pinned post context."""
        uid = event.sender_id
        try:
            parts = event.data.decode().split("_")
            monitor_id, msg_id = int(parts[-2]), int(parts[-1])
        except (IndexError, ValueError):
            await event.answer("داده نامعتبر.", alert=True)
            return

        monitor = await get_monitor(monitor_id)
        if not monitor or monitor["user_id"] != uid:
            await event.answer("دسترسی غیرمجاز!", alert=True)
            return

        session = await get_session(uid, monitor_id, msg_id)
        if session:
            await reset_session_history(session["id"])

        await event.answer("🧹 گفتگو پاک شد.")
        await _open_ask_ai(event, uid, monitor_id, msg_id)

    @client.on(events.CallbackQuery(pattern=FULLTEXT_RE))
    async def cw_ask_ai_fulltext(event):
        """Toggle between the AI summary and the raw original post text."""
        uid = event.sender_id
        try:
            parts = event.data.decode().split("_")
            monitor_id, msg_id = int(parts[-2]), int(parts[-1])
        except (IndexError, ValueError):
            await event.answer("داده نامعتبر.", alert=True)
            return

        state, data = await _state_mgr.get_state(uid)
        show_full_text = not bool((data or {}).get("show_full_text", False)) if state == UserState.AWAITING_ASK_AI else True
        await _open_ask_ai(event, uid, monitor_id, msg_id, show_full_text)

    @client.on(events.CallbackQuery(pattern=EXIT_RE))
    async def cw_exit_ask_ai(event):
        """Exit Ask AI mode and return to the monitor settings."""
        uid = event.sender_id
        try:
            parts = event.data.decode().split("_")
            monitor_id = int(parts[-1])
        except (IndexError, ValueError):
            await _state_mgr.clear_state(uid)
            await safe_edit(event, "از حالت پرسش خارج شدید.", buttons=back_keyboard("cw_list_monitors"))
            return

        await _state_mgr.clear_state(uid)
        await safe_edit(
            event,
            "👋 از حالت پرسش‌وپاسخ خارج شدید.",
            buttons=[[Button.inline("⚙️ تنظیمات", f"cw_settings_{monitor_id}")]],
        )
