"""Animated waiting-message manager for Telegram bot.

Provides a professional, phase-based animation system that keeps users
informed and engaged while the AI processes their request.  Each tool
(search, image, market, HTML) gets its own animation style, and the
display adapts based on elapsed time and tool-specific progress.

Key features:
- Phase-based animation: each tool renders a unique visual style.
- Streaming text: image prompts appear word-by-word in a scrolling line.
- Creative progress bar: uses ▓░ characters with tool-specific prefixes.
- Context-aware tips: skill/mentor-specific tips rotate during the wait.
- Pipeline indicator: shows completed → active → pending phases.
- Production hardening: FloodWaitError handling, deleted-message guard,
  adaptive throttle, and safe cleanup.
"""

import asyncio
import random
import time
import logging
from typing import Any, Optional, List, Dict

from telethon import Button
from telethon.errors import FloodWaitError, MessageIdInvalidError, BadRequestError, MessageNotModifiedError

from .constants import (
    ToolEvent,
    TIPS_QUICKASK,
    TIPS_CODING,
    TIPS_LEARN,
    TIPS_DEEPTHINK,
    TIPS_MENTOR,
    PROCESSING_PHASES,
    PATIENCE_MESSAGES,
    TOOL_EXPECTED_DURATIONS,
    STREAMING_MAX_VISIBLE_CHARS,
    STREAMING_WORD_INTERVAL,
    PHASE_ICONS,
)

tlogger = logging.getLogger("telegram")

# Map tool identifiers to their PROCESSING_PHASES index for phase tracking.
_TOOL_TO_PHASE: Dict[str, int] = {
    "search": 2,   # "جستجوی وب"
    "image": 5,    # "تولید تصویر"
    "market": 4,   # "دریافت داده بازار"
    "html": 3,     # "آنالیز نتایج" (closest match)
}


class StreamingText:
    """Typewriter-style text reveal for long prompts.

    Words appear one at a time.  Once the visible text exceeds
    ``max_visible`` characters, the oldest words are dropped and an
    ellipsis is prepended so the display stays on a single line.

    Args:
        full_text: The complete text to stream.
        max_visible: Maximum visible characters before truncation.
    """

    def __init__(self, full_text: str, max_visible: int = STREAMING_MAX_VISIBLE_CHARS) -> None:
        self.words: List[str] = full_text.split()
        self.max_visible: int = max_visible
        self._current_index: int = 0

    def advance(self) -> str:
        """Advance one word and return the currently visible substring."""
        if not self.words:
            return ""
        self._current_index = min(self._current_index + 1, len(self.words))
        return self.get_visible()

    def get_visible(self) -> str:
        """Return the currently visible portion of the text."""
        if not self.words or self._current_index == 0:
            return ""
        selected = self.words[: self._current_index]
        text = " ".join(selected)
        if len(text) > self.max_visible:
            # Keep the tail that fits within max_visible
            trimmed = text[-self.max_visible :]
            # Start at a word boundary
            space_idx = trimmed.find(" ")
            if space_idx != -1:
                trimmed = trimmed[space_idx + 1 :]
            return "... " + trimmed
        return text


class StatusAnimator:
    """Manages a professional, phase-based animated waiting message.

    The animator edits a Telegram message in-place with spinners,
    progress bars, tool-specific details, and rotating tips.

    **Production hardening:**
    - Catches ``FloodWaitError`` and respects the wait period.
    - Catches ``MessageIdInvalidError`` / ``BadRequestError`` to stop
      gracefully when the user deletes the message.
    - Adaptive throttle: increases the edit interval after consecutive
      failures to avoid hammering the Telegram API.

    Args:
        msg: The Telethon message object to animate.
        mentor_key: Optional mentor identifier for mentor-specific tips.
        skill_key: Optional skill identifier for skill-specific tips.
        max_duration: Hard safety-net timeout in seconds (default 300).
    """

    def __init__(
        self,
        msg: Any,
        mentor_key: Optional[str] = None,
        skill_key: Optional[str] = None,
        max_duration: int = 300,
    ) -> None:
        self.msg: Any = msg
        self.mentor_key: Optional[str] = mentor_key
        self.skill_key: Optional[str] = skill_key
        self.max_duration: int = max_duration
        self.is_running: bool = True
        self.task: Optional[asyncio.Task] = None

        # Phase tracking
        self.phase_index: int = 0
        self.completed_phases: List[str] = []
        self.current_phase_label: str = "تحلیل پیام"

        # Tool state
        self._current_tool: str = ""
        self._tool_event: Optional[ToolEvent] = None
        self._tool_start_time: float = 0.0
        self._streaming: Optional[StreamingText] = None
        self._streaming_last_advance: float = 0.0

        # Tips
        self._tip_pool: List[str] = self._build_tip_pool()
        self._current_tip: str = random.choice(self._tip_pool)
        self._tip_counter: int = 0

        # Adaptive throttle
        self._base_interval: float = 1.5
        self._edit_fail_count: int = 0

    # ── Tip pool construction ──────────────────────────────────

    def _build_tip_pool(self) -> List[str]:
        """Build the context-aware tip pool based on skill/mentor key."""
        if self.mentor_key and self.mentor_key in TIPS_MENTOR:
            return list(TIPS_MENTOR[self.mentor_key])
        if self.skill_key == "coding":
            return list(TIPS_CODING)
        if self.skill_key == "learn":
            return list(TIPS_LEARN)
        if self.skill_key == "deepthink":
            return list(TIPS_DEEPTHINK)
        return list(TIPS_QUICKASK)

    # ── Public API ─────────────────────────────────────────────

    async def start(self) -> None:
        """Start the animation loop as a background task."""
        self.task = asyncio.create_task(self._animate())

    async def stop(self) -> None:
        """Stop the animation loop and cancel the background task.

        Safe to call multiple times — no-op if already stopped.
        """
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def update_step(self, step_text: str) -> None:
        """Update the current phase label (legacy interface).

        Matches the step text against ``PROCESSING_PHASES`` to set
        the phase index.  Used by ``on_status_change`` callbacks that
        pass plain strings.
        """
        self.current_phase_label = step_text
        for i, phase in enumerate(PROCESSING_PHASES):
            if phase in step_text or step_text in phase:
                self.phase_index = i
                return

    def update_tool(self, event: ToolEvent) -> None:
        """Update the animator with structured tool execution data.

        Called by ``on_tool_call`` when a tool starts or progresses.
        Sets up tool-specific animation state (streaming text, progress
        tracking, source display).
        """
        self._current_tool = event.tool
        self._tool_event = event

        # Map tool to phase index
        if event.tool in _TOOL_TO_PHASE:
            new_phase = _TOOL_TO_PHASE[event.tool]
            if new_phase != self.phase_index:
                phase_label = PROCESSING_PHASES[min(self.phase_index, len(PROCESSING_PHASES) - 1)]
                if phase_label not in self.completed_phases:
                    self.completed_phases.append(phase_label)
            self.phase_index = new_phase
            self.current_phase_label = PROCESSING_PHASES[new_phase]

        # Set up streaming text for image prompts
        if event.tool == "image" and event.query and not self._streaming:
            self._streaming = StreamingText(event.query)
            self._streaming_last_advance = time.time()

        # Update query display for search
        if event.tool == "search" and event.query:
            # Query is shown directly, no streaming needed
            pass

        # Track tool start time for progress estimation
        if self._tool_start_time == 0:
            self._tool_start_time = time.time()

    # ── Animation loop ─────────────────────────────────────────

    async def _animate(self) -> None:
        """Main animation loop with production-grade error handling.

        Edits the message at adaptive intervals with:
        - Braille spinner animation
        - Tool-specific progress bars (▓░ style)
        - Phase pipeline indicator (completed → active → pending)
        - Streaming text for image prompts
        - Search query + source links
        - Context-aware rotating tips

        Handles ``FloodWaitError`` by respecting the wait period,
        and stops gracefully on deleted-message errors.
        """
        spinners: List[str] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner_index: int = 0
        start_time: float = time.time()

        while self.is_running:
            try:
                elapsed: int = int(time.time() - start_time)

                # Safety net: hard stop after max_duration seconds
                if elapsed >= self.max_duration:
                    await self._render_timeout()
                    self.is_running = False
                    break

                spinner = spinners[spinner_index % len(spinners)]
                text = self._build_frame(spinner, elapsed)

                await self._safe_edit(text)

                spinner_index += 1
                self._tip_counter += 1
                if self._tip_counter % 10 == 0:
                    self._current_tip = random.choice(self._tip_pool)

                # Adaptive sleep
                interval = self._get_interval(elapsed)
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except FloodWaitError as e:
                tlogger.warning(f"StatusAnimator: FloodWaitError, waiting {e.seconds}s")
                await asyncio.sleep(e.seconds + 1)
            except (MessageIdInvalidError, BadRequestError):
                tlogger.info("StatusAnimator: message deleted, stopping")
                self.is_running = False
                break
            except Exception as e:
                tlogger.warning(f"StatusAnimator: unexpected error: {e}")
                await asyncio.sleep(2)

    # ── Frame builder ──────────────────────────────────────────

    def _build_frame(self, spinner: str, elapsed: int) -> str:
        """Build the complete animation frame text.

        Returns an HTML-formatted string ready for ``msg.edit()``.
        """
        lines: List[str] = []

        # Pipeline indicator (completed phases)
        if self.completed_phases:
            pipeline_parts = []
            for phase in self.completed_phases:
                icon = self._get_phase_icon(phase)
                pipeline_parts.append(f"✅ {phase}")
            current_icon = self._get_phase_icon(self.current_phase_label)
            pipeline_parts.append(f"{spinner} {self.current_phase_label}")
            lines.append(" → ".join(pipeline_parts))
        else:
            icon = self._get_phase_icon(self.current_phase_label)
            lines.append(f"{spinner} <b>{self.current_phase_label}</b>")

        # Progress bar
        progress = self._calculate_progress(elapsed)
        bar = self._make_progress_bar(progress, style=self._current_tool or "default")
        pct = int(progress * 100)
        lines.append(f"{bar}  {pct}%")

        # Tool-specific details
        detail_lines = self._build_tool_details()
        lines.extend(detail_lines)

        # Patience message
        patience = self._get_patience_message(elapsed)
        if patience:
            lines.append(patience)

        # Tip
        lines.append(f"💡 {self._current_tip}")

        return "\n".join(lines)

    def _build_tool_details(self) -> List[str]:
        """Build tool-specific detail lines for the current tool."""
        details: List[str] = []
        event = self._tool_event
        if not event:
            return details

        if event.tool == "search":
            # Show query (truncated)
            if event.query:
                query_display = event.query[:50] + ("..." if len(event.query) > 50 else "")
                details.append(f'🔍 <i>"{query_display}"</i>')
            # Show sources with HTML links
            if event.sources:
                source_links = []
                for src in event.sources[:3]:
                    title = src.get("title", "")[:30]
                    uri = src.get("uri", "")
                    if title and uri:
                        source_links.append(f'<a href="{uri}">{title}</a>')
                if source_links:
                    details.append("📖 " + " • ".join(source_links))

        elif event.tool == "image":
            # Streaming text for image prompt
            if self._streaming:
                now = time.time()
                if now - self._streaming_last_advance >= STREAMING_WORD_INTERVAL:
                    self._streaming.advance()
                    self._streaming_last_advance = now
                visible = self._streaming.get_visible()
                if visible:
                    details.append(f"🎨 <i>{visible}</i>")
            elif event.query:
                # Fallback: show truncated query
                short = event.query[:STREAMING_MAX_VISIBLE_CHARS]
                if len(event.query) > STREAMING_MAX_VISIBLE_CHARS:
                    short += "..."
                details.append(f"🎨 <i>{short}</i>")

        elif event.tool == "market":
            if event.detail:
                details.append(f"💱 {event.detail}")
            elif event.query:
                details.append(f"💱 {event.query}")

        elif event.tool == "html":
            if event.query:
                title = event.query[:40]
                details.append(f"📝 {title}")

        return details

    # ── Progress calculation ───────────────────────────────────

    def _calculate_progress(self, elapsed: int) -> float:
        """Calculate normalized progress (0.0–1.0) based on tool and elapsed time.

        Uses tool-specific expected durations for accurate estimation.
        Capped at 0.95 until the tool actually completes.
        """
        if self._current_tool and self._tool_start_time > 0:
            tool_elapsed = time.time() - self._tool_start_time
            expected = TOOL_EXPECTED_DURATIONS.get(self._current_tool, 15)
            return min(tool_elapsed / expected, 0.95)

        # Default: use overall elapsed time
        expected = TOOL_EXPECTED_DURATIONS.get("default", 15)
        return min(elapsed / expected, 0.95)

    # ── Progress bar ───────────────────────────────────────────

    @staticmethod
    def _make_progress_bar(progress: float, width: int = 10, style: str = "default") -> str:
        """Render a creative progress bar using ▓░ characters.

        Args:
            progress: Normalized progress 0.0–1.0.
            width: Number of bar segments.
            style: Tool identifier for prefix icon.

        Returns:
            Formatted string like ``🔍 ▓▓▓░░░░░░░``.
        """
        filled = int(progress * width)
        empty = width - filled
        bar = "▓" * filled + "░" * empty

        prefix = ""
        if style == "search":
            prefix = "🔍 "
        elif style == "image":
            prefix = "🎨 "
        elif style == "market":
            prefix = "📊 "
        elif style == "html":
            prefix = "📄 "

        return f"{prefix}{bar}"

    # ── Phase icon lookup ──────────────────────────────────────

    @staticmethod
    def _get_phase_icon(phase_label: str) -> str:
        """Return the emoji icon for a given phase label."""
        for key, icon in PHASE_ICONS.items():
            if key in phase_label:
                return icon
        return "💭"

    # ── Patience messages ──────────────────────────────────────

    def _get_patience_message(self, elapsed: int) -> str:
        """Select a time-appropriate patience message for the current tool."""
        tool = self._current_tool if self._current_tool else "thinking"
        messages = PATIENCE_MESSAGES.get(tool, PATIENCE_MESSAGES.get("thinking", []))

        selected = ""
        for min_secs, msg in messages:
            if elapsed >= min_secs:
                selected = msg
        return selected

    # ── Adaptive throttle ──────────────────────────────────────

    def _get_interval(self, elapsed: int) -> float:
        """Calculate the adaptive edit interval based on elapsed time and error count.

        Slows down edits during long waits and after consecutive failures
        to respect Telegram rate limits.
        """
        # Base interval scales with elapsed time
        if elapsed < 5:
            base = 1.2
        elif elapsed < 20:
            base = 1.5
        else:
            base = 2.0

        # Apply error backoff
        if self._edit_fail_count > 0:
            backoff = min(base * (1.5 ** self._edit_fail_count), 10.0)
            return backoff

        return base

    # ── Safe message edit ──────────────────────────────────────

    async def _safe_edit(self, text: str) -> None:
        """Edit the message with error handling for rate limits and deleted messages.

        On success, resets the failure counter.  On ``FloodWaitError``,
        the exception is re-raised for the main loop to handle.
        On deleted-message errors, re-raised for graceful shutdown.
        """
        try:
            await self.msg.edit(
                text,
                buttons=[Button.inline("لغو درخواست", b"clear_processing", style="danger")],
                parse_mode="html",
            )
            self._edit_fail_count = 0
        except FloodWaitError:
            raise
        except (MessageIdInvalidError, BadRequestError):
            raise
        except MessageNotModifiedError:
            # Content unchanged — not a real failure, but reset the
            # failure counter so a prior streak of real failures doesn't
            # keep the backoff multiplier elevated forever (Bug #13).
            self._edit_fail_count = 0
        except Exception as e:
            self._edit_fail_count += 1
            tlogger.debug(f"StatusAnimator edit failed (count={self._edit_fail_count}): {e}")

    # ── Timeout renderer ───────────────────────────────────────

    async def _render_timeout(self) -> None:
        """Render the timeout message when max_duration is exceeded."""
        try:
            await self.msg.edit(
                "⏱ زمان پردازش بیش از حد انتظار طول کشید.\n"
                "لطفا مجددا تلاش کنید.",
                buttons=[Button.inline("🔄 تلاش مجدد", b"clear_processing")],
                parse_mode="html",
            )
        except Exception:
            pass
