"""Tests for `telethon_compat.py` — the v1→v2 compatibility shim.

Focus on the pure, side-effect-free helpers: id/mark manipulation, peer refs,
button construction, keyboard normalization, parse-mode resolution, and the
`data_regex` / `text_regex` event filters.
"""

import re

import pytest

import telethon_compat as C


def _ref_id(ref):
    """Extract the numeric id from a v2 *Ref object via its repr.

    v2 Ref objects (`UserRef(123, None)` / `ChannelRef(456, None)`) do not
    expose a public `.id`; the numeric id is the first repr argument.
    """
    m = re.search(r"\((\d+)", repr(ref))
    assert m, f"could not extract id from {ref!r}"
    return int(m.group(1))


# ── strip_channel_mark ─────────────────────────────────────────────────
class TestStripChannelMark:
    def test_dash100_prefix(self):
        assert C.strip_channel_mark(-1001234567890) == 1234567890

    def test_dash_prefix(self):
        assert C.strip_channel_mark(-4242) == 4242

    def test_positive_unchanged(self):
        assert C.strip_channel_mark(555) == 555

    def test_none_passthrough(self):
        assert C.strip_channel_mark(None) is None

    def test_zero(self):
        assert C.strip_channel_mark(0) == 0

    def test_returns_int(self):
        assert isinstance(C.strip_channel_mark(-100999), int)


# ── user_ref / channel_ref / peer_ref ──────────────────────────────────
class TestPeerRefs:
    def test_user_ref_id(self):
        ref = C.user_ref(12345)
        assert _ref_id(ref) == 12345

    def test_channel_ref_strips_mark(self):
        ref = C.channel_ref_from_stored_id(-1009999)
        assert _ref_id(ref) == 9999

    def test_channel_ref_bare(self):
        ref = C.channel_ref_from_stored_id(777)
        assert _ref_id(ref) == 777

    def test_peer_ref_positive_is_user(self):
        from telethon.types import UserRef
        ref = C.peer_ref_from_stored_id(500)
        assert isinstance(ref, UserRef)
        assert _ref_id(ref) == 500

    def test_peer_ref_marked_is_channel(self):
        from telethon.types import ChannelRef
        ref = C.peer_ref_from_stored_id(-1001111)
        assert isinstance(ref, ChannelRef)
        assert _ref_id(ref) == 1111

    def test_peer_ref_dash_is_channel(self):
        from telethon.types import ChannelRef
        ref = C.peer_ref_from_stored_id(-99)
        assert isinstance(ref, ChannelRef)
        assert _ref_id(ref) == 99

    def test_peer_ref_none(self):
        assert C.peer_ref_from_stored_id(None) is None


# ── Button shim ────────────────────────────────────────────────────────
class TestButton:
    def test_inline_encodes_str_data(self):
        b = C.Button.inline("Click", "payload")
        assert b.data == b"payload"

    def test_inline_default_data_is_text(self):
        b = C.Button.inline("Hello")
        assert b.data == b"Hello"

    def test_inline_bytes_data_unchanged(self):
        b = C.Button.inline("x", b"raw")
        assert b.data == b"raw"

    def test_inline_ignores_extra_kwargs(self):
        # v1 style style="danger" must be swallowed.
        b = C.Button.inline("x", "d", style="danger")
        assert b.data == b"d"

    def test_url_button(self):
        b = C.Button.url("Open", "https://example.com")
        # v2 Url button stores the url
        assert getattr(b, "url", None) == "https://example.com"

    def test_switch_inline(self):
        b = C.Button.switch_inline("Share", "q")
        assert b is not None

    def test_text_button(self):
        b = C.Button.text("Reply")
        assert b is not None


# ── _normalize_keyboard ────────────────────────────────────────────────
class TestNormalizeKeyboard:
    def test_none_returns_none(self):
        assert C._normalize_keyboard(None) is None

    def test_empty_list_returns_none(self):
        assert C._normalize_keyboard([]) is None

    def test_single_button_wrapped(self):
        b = C.Button.inline("x", "d")
        kb = C._normalize_keyboard(b)
        assert isinstance(kb, C.InlineKeyboard)

    def test_single_row(self):
        row = [C.Button.inline("a", "1"), C.Button.inline("b", "2")]
        kb = C._normalize_keyboard(row)
        assert isinstance(kb, C.InlineKeyboard)

    def test_list_of_rows(self):
        rows = [[C.Button.inline("a", "1")], [C.Button.inline("b", "2")]]
        kb = C._normalize_keyboard(rows)
        assert isinstance(kb, C.InlineKeyboard)

    def test_existing_keyboard_passthrough(self):
        rows = [[C.Button.inline("a", "1")]]
        kb1 = C._normalize_keyboard(rows)
        kb2 = C._normalize_keyboard(kb1)
        assert kb2 is kb1


# ── _resolve_parse_mode ────────────────────────────────────────────────
class TestResolveParseMode:
    def test_none_text_returns_empty(self):
        assert C._resolve_parse_mode(None, "html") == {}

    def test_no_parse_mode_is_plain(self):
        assert C._resolve_parse_mode("hi", None) == {"text": "hi"}

    def test_html(self):
        assert C._resolve_parse_mode("<b>x</b>", "html") == {"html": "<b>x</b>"}

    def test_html_uppercase(self):
        assert C._resolve_parse_mode("x", "HTML") == {"html": "x"}

    def test_markdown(self):
        assert C._resolve_parse_mode("*x*", "markdown") == {"markdown": "*x*"}

    def test_md_alias(self):
        assert C._resolve_parse_mode("x", "md") == {"markdown": "x"}

    def test_unknown_falls_back_to_plain(self):
        assert C._resolve_parse_mode("x", "weird") == {"text": "x"}


# ── _resolve_caption_parse_mode ────────────────────────────────────────
class TestResolveCaptionParseMode:
    def test_none_caption(self):
        assert C._resolve_caption_parse_mode(None, "html") == {}

    def test_plain(self):
        assert C._resolve_caption_parse_mode("cap", None) == {"caption": "cap"}

    def test_html(self):
        assert C._resolve_caption_parse_mode("c", "html") == {"caption_html": "c"}

    def test_markdown(self):
        assert C._resolve_caption_parse_mode("c", "md") == {"caption_markdown": "c"}


# ── photo_dedup_key ────────────────────────────────────────────────────
class TestPhotoDedupKey:
    def test_uses_input_media_repr(self):
        class Fake:
            _input_media = "MEDIA123"
        key = C.photo_dedup_key(Fake())
        assert key == repr("MEDIA123")

    def test_falls_back_to_object_id(self):
        class Fake:
            _input_media = None
        f = Fake()
        assert C.photo_dedup_key(f) == id(f)

    def test_distinct_media_distinct_keys(self):
        class A:
            _input_media = "A"
        class B:
            _input_media = "B"
        assert C.photo_dedup_key(A()) != C.photo_dedup_key(B())


# ── data_regex ─────────────────────────────────────────────────────────
class TestDataRegex:
    def _event(self, data):
        class E:
            pass
        e = E()
        e.data = data
        return e

    def test_matches_prefix(self):
        f = C.data_regex(r"service_delete:")
        assert f(self._event(b"service_delete:5")) is True

    def test_anchored_at_start(self):
        f = C.data_regex(r"delete:")
        # re.match is anchored; "x_delete:" should not match.
        assert f(self._event(b"x_delete:5")) is False

    def test_negative_lookahead(self):
        f = C.data_regex(r"^service_delete:(?!confirm)")
        assert f(self._event(b"service_delete:5")) is True
        assert f(self._event(b"service_delete:confirm")) is False

    def test_non_bytes_data_false(self):
        f = C.data_regex(r"x")
        assert f(self._event("x")) is False

    def test_missing_data_false(self):
        f = C.data_regex(r"x")
        class E:
            pass
        assert f(E()) is False

    def test_accepts_compiled_pattern(self):
        f = C.data_regex(re.compile(r"abc"))
        assert f(self._event(b"abc123")) is True

    def test_filter_has_name(self):
        f = C.data_regex(r"x")
        assert "data_regex" in f.__name__


# ── text_regex ─────────────────────────────────────────────────────────
class TestTextRegex:
    def _event(self, text):
        class E:
            pass
        e = E()
        e.text = text
        return e

    def test_matches_command(self):
        f = C.text_regex(r"/start")
        assert f(self._event("/start")) is True

    def test_anchored_at_start(self):
        f = C.text_regex(r"/w")
        # match is anchored — "hello /w" should NOT match.
        assert f(self._event("hello /w")) is False

    def test_non_ascii_trigger(self):
        f = C.text_regex(r"اکسی")
        assert f(self._event("اکسی سلام")) is True

    def test_alternation(self):
        f = C.text_regex(r"/start|اکسی")
        assert f(self._event("/start now")) is True
        assert f(self._event("اکسی")) is True

    def test_no_text_false(self):
        f = C.text_regex(r"x")
        class E:
            pass
        assert f(E()) is False

    def test_non_str_text_false(self):
        f = C.text_regex(r"x")
        assert f(self._event(123)) is False


# ── re-exports present ──────────────────────────────────────────────────
class TestReExports:
    def test_telegram_client_alias(self):
        assert C.TelegramClient is C.Client

    def test_error_aliases_exist(self):
        assert C.FloodWaitError is not None
        assert C.MessageNotModifiedError is not None

    def test_events_callbackquery_compat(self):
        # events.CallbackQuery.Event -> ButtonCallback
        assert C.events.CallbackQuery.Event is C.events.ButtonCallback

    def test_all_exports_defined(self):
        for name in C.__all__:
            assert hasattr(C, name), f"missing export {name}"


# ── download_media compat (v2: Client.download) ────────────────────────────
class TestBytesSink:
    def test_accumulates_chunks(self):
        s = C._BytesSink()
        s.write(b"foo")
        s.write(b"bar")
        assert s.getvalue() == b"foobar"

    def test_empty(self):
        assert C._BytesSink().getvalue() == b""

    def test_write_accepts_bytearray(self):
        s = C._BytesSink()
        s.write(bytearray(b"xy"))
        assert s.getvalue() == b"xy"


class TestDownloadMediaShim:
    def test_attached_to_message(self):
        from telethon._impl.client.types import Message
        assert hasattr(Message, "download_media")

    async def test_returns_none_when_no_media(self):
        class FakeMsg:
            file = None
            photo = None
            video = None
            _client = object()
        # Bound-method style invocation of the shim.
        result = await C._msg_download_media(FakeMsg())
        assert result is None

    async def test_bytes_download_returns_joined_buffer(self):
        class FakeClient:
            async def download(self, media, sink):
                await_write = sink.write(b"hello ")
                sink.write(b"world")

        class FakeMsg:
            file = object()
            photo = None
            video = None
            _client = FakeClient()

        data = await C._msg_download_media(FakeMsg(), bytes)
        assert data == b"hello world"

    async def test_path_download_returns_target(self):
        recorded = {}

        class FakeClient:
            async def download(self, media, target):
                recorded["target"] = target

        class FakeMsg:
            file = object()
            photo = None
            video = None
            _client = FakeClient()

        out = await C._msg_download_media(FakeMsg(), "/tmp/pic.jpg")
        assert out == "/tmp/pic.jpg"
        assert recorded["target"] == "/tmp/pic.jpg"

    async def test_uses_photo_when_file_absent(self):
        class FakeClient:
            async def download(self, media, sink):
                sink.write(b"P")

        class FakeMsg:
            file = None
            photo = object()
            video = None
            _client = FakeClient()

        assert await C._msg_download_media(FakeMsg(), bytes) == b"P"


# ── get_messages compat (v2: get_messages / get_messages_with_ids split) ────
class _FakeAsyncList:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        self._it = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeMsg:
    def __init__(self, i):
        self.id = i


class TestGetMessagesShim:
    def test_wrapper_is_plain_function(self):
        # Must NOT be a coroutine function, so the id-less path can return an
        # AsyncList that history call sites iterate with `async for`.
        import inspect
        assert not inspect.iscoroutinefunction(C._client_get_messages_wrapper)

    async def test_scalar_id_returns_single_message(self):
        class Client:
            def get_messages_with_ids(self, peer, id_list):
                return _FakeAsyncList([_FakeMsg(i) for i in id_list])

        msg = await C._client_get_messages_wrapper(Client(), 111, ids=7)
        assert msg.id == 7

    async def test_list_ids_returns_list(self):
        class Client:
            def get_messages_with_ids(self, peer, id_list):
                return _FakeAsyncList([_FakeMsg(i) for i in id_list])

        msgs = await C._client_get_messages_wrapper(Client(), 111, ids=[7, 8])
        assert [m.id for m in msgs] == [7, 8]

    async def test_missing_scalar_id_returns_none(self):
        class Client:
            def get_messages_with_ids(self, peer, id_list):
                return _FakeAsyncList([])

        assert await C._client_get_messages_wrapper(Client(), 111, ids=99) is None

    async def test_get_messages_by_ids_helper(self):
        class Client:
            def get_messages_with_ids(self, peer, id_list):
                return _FakeAsyncList([_FakeMsg(i) for i in id_list])

        one = await C._get_messages_by_ids(Client(), object(), 5)
        assert one.id == 5
        many = await C._get_messages_by_ids(Client(), object(), [1, 2, 3])
        assert [m.id for m in many] == [1, 2, 3]

    def test_idless_passthrough_returns_asynclist_not_coroutine(self):
        # The passthrough must return whatever the native method returns (an
        # AsyncList), never a coroutine — otherwise `async for` breaks.
        sentinel = _FakeAsyncList([_FakeMsg(1)])

        class Client:
            def _native(self, peer, *a, **k):
                return sentinel

        # Temporarily point the captured original at our fake native.
        import telethon_compat as tc
        orig = tc._orig_client_get_messages
        tc._orig_client_get_messages = lambda self, peer, *a, **k: sentinel
        try:
            result = C._client_get_messages_wrapper(Client(), 1)
            assert result is sentinel
        finally:
            tc._orig_client_get_messages = orig
