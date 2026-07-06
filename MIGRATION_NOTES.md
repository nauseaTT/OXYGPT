# OXYGPT — Telethon v1 to v2 Migration Notes

Date: 2026-07-05
Target library: **Telethon 2.0.0a0** (installed from the official `v2` branch;
v2 is not yet published to PyPI as a stable release).

This document is the design record for the migration of the OXYGPT Telegram
bot from Telethon **v1.x** to Telethon **v2**. It captures the decisions taken,
the mapping of every v1 idiom to its v2 replacement, the risks, and the
behavioral nuances that had to be preserved 1:1.

Read this together with the exhaustive inline comments added throughout the
code and the rewritten `README.md`.

---

## 0. Executive summary

Telethon v2 is a **full rewrite** (a port of the Rust `grammers` library back
to Python). The public surface changed radically and there is **no v1
compatibility bridge** shipped by the library. Because this codebase is large
(~31 files importing Telethon, ~575 message-producing calls, ~297 keyboard
sites, ~215 callback registrations), a **thin, fully-documented compatibility
layer** — `telethon_compat.py` — is introduced. It lets the vast majority of
the existing v1-style call sites keep working *verbatim* while actually
executing against the v2 API. Everything the shim cannot express transparently
(client construction, login, event/filter registration, peer resolution,
`iter_messages`, raw API, error field renames, typing indicators) was migrated
explicitly, module by module, with migration-aware comments.

The shim is not "leaving v1 idioms in place": it is a single, well-understood
translation point. All Telethon traffic goes through v2 objects; the shim only
adapts *argument shapes* (e.g. `parse_mode="html"` -> `html=`, `buttons=` ->
`keyboard=`) and re-adds a few convenience accessors that v2 dropped
(`ButtonCallback.edit/respond/sender_id/chat_id`). Each adaptation is
documented at its definition.

---

## 1. Import strategy for `Client`

- v1: `from telethon import TelegramClient`
- v2: `TelegramClient` was renamed to `telethon.Client`.

**Decision:** import the v2 class but keep the old name via an alias to
minimize churn across ~61 references and all `client: TelegramClient` type
hints:

```python
from telethon_compat import TelegramClient, events, Button   # v2 under the hood
# telethon_compat re-exports: TelegramClient = telethon.Client
```

Type hints stay written as `TelegramClient` but now resolve to `telethon.Client`.
Where a file previously did `from telethon import TelegramClient`, it now does
`from telethon_compat import TelegramClient`.

---

## 2. The compatibility layer (`telethon_compat.py`)

`telethon_compat` re-exports the v2 names the codebase expects and installs a
small set of **monkeypatches / adapters** (all documented at their definition):

| v1 idiom used in code                              | v2 reality                                         | How the shim bridges it |
|----------------------------------------------------|----------------------------------------------------|-------------------------|
| `TelegramClient`                                   | `telethon.Client`                                  | alias re-export |
| `Button.inline/url/switch_inline/text`             | `types.Button.Callback/Url/SwitchInline/Text`      | `Button` shim class with the same classmethods |
| `send_message(..., parse_mode="html", buttons=..)` | `send_message(..., html=..., keyboard=..)`         | wrapper translates kwargs |
| `event.reply/respond/edit(..., parse_mode=, buttons=)` | `Message.reply/respond/edit(..., html=, keyboard=)` | `Message` methods wrapped |
| `event.edit/respond/delete` on a callback event    | `ButtonCallback` has only `answer/get_message`     | methods added to `ButtonCallback` |
| `event.sender_id / chat_id` on a callback event    | not present on `ButtonCallback`                    | properties added |
| `link_preview=<bool>`                              | `link_preview=<bool>` (kept, default `False`)      | passed through |
| `FloodWaitError.seconds`                           | `FloodWaitError.value`                             | handled at each call site (see sec. 7) |

### Parse-mode conversion rule (applies to ~575 call sites)

Telethon v2 removed the global `client.parse_mode`. Each call must state how
the text is to be interpreted:

- `parse_mode="html"` (or `"HTML"`)  ->  pass the text as `html=<text>`
- `parse_mode="md"` / `"markdown"`   ->  pass the text as `markdown=<text>`
- no `parse_mode` / `parse_mode=None`->  pass the text as `text=<text>` (plain)

For file sends the equivalent caption parameters are used
(`caption` / `caption_html` / `caption_markdown`).

The shim reads the positional/`message=` text plus the `parse_mode=` kwarg and
routes them to the correct v2 keyword. This keeps HTML-vs-plain semantics
identical: messages that were plain stay plain, HTML stays HTML.

### Keyboard conversion rule (applies to ~297 sites)

- v1: `buttons=[[Button.inline("a","x")], [Button.url("b","http://..")]]`
- v2: `keyboard=InlineKeyboard([[Callback("a", b"x")], [Url("b","http://..")]])`

The `Button` shim returns v2 button instances, and the shim's message wrappers
accept the v1 `buttons=` argument (a button, a row, or a list of rows) and wrap
it into an `InlineKeyboard` automatically. A lone reply keyboard of `Text`
buttons is wrapped appropriately as well.

---

## 3. Client construction, login and lifecycle

- **No `client.start()`.** v2 requires `await client.connect()` followed by an
  explicit login.
  - Bot: `await client.bot_sign_in(bot_token)` (after `connect()`).
  - User (Channel Watcher): `await client.connect()` then
    `token = await client.request_login_code(phone)` then
    `await client.sign_in(token, code)` where `code` is read from the terminal
    (preserving the old `code_callback` prompt behavior). A `PasswordToken`
    return means 2FA is enabled -> `await client.check_password(pw)`.
  - Both flows first check `await client.is_authorized()` so an already-authed
    session skips re-login.
- **No `client.loop`.** Background tasks use `asyncio.create_task(...)` / a
  captured `asyncio.get_running_loop()` from *inside* the running loop.
  The two background loops (`_daily_reset_loop`, `_cleanup_stale_data_loop`) and
  `run_until_disconnected()` are preserved.
- **v1 sessions are NOT compatible with v2.** The old `bot.session` /
  `channel_watcher_user.session` files store data in a format v2 cannot read.
  **A re-login is required on first v2 run** (bot-token re-auth, and interactive
  phone+code re-auth for the Channel Watcher user account). The session file
  *names* are preserved so the code does not change, but the files themselves
  will be re-created by v2. This is documented in the README.

---

## 4. Events and filters (separation of concerns)

v2 splits events (the objects handlers receive) from filters (standalone
combinable predicates).

- `events.CallbackQuery` -> **`events.ButtonCallback`** (and it no longer also
  fires for inline callbacks — that v1 dual behavior is gone). Audited: this
  codebase has exactly one inline handler (`InlineQuery`), and its results are
  articles/switch-pm, not inline callback buttons, so the dropped dual behavior
  is a no-op here.
- **Command routing:** v1 `events.NewMessage(incoming=True, pattern="/start|اکسی")`
  becomes `events.NewMessage`, filtered by
  `filters.Incoming() & filters.Text(<regex>)`. `filters.Command("start")`
  exists but only matches a single ASCII slash-command and cannot express the
  `/start|اکسی` alternation or the `اکسی` non-ASCII trigger, so we use
  `filters.Text(<anchored regex>)` to reproduce v1's `re.match` semantics
  exactly (anchored at start).
- **Callback routing:** v1 `events.CallbackQuery(data=b"x")` becomes
  `events.ButtonCallback` + `filters.Data(b"x")` (exact match). v1
  `events.CallbackQuery(pattern=r"prefix:")` (a `re.match` prefix test) becomes
  `events.ButtonCallback` + a **custom `data_regex(...)` filter** (added in the
  compat layer) that runs `re.match` against the callback bytes decoded as
  UTF-8. This precisely reproduces v1 behavior, **including the negative
  lookahead prefix-collision cases** such as `^service_delete:(?!confirm)` vs
  `service_delete_confirm:` and `^service_remove_key:(?!confirm)` vs
  `service_remove_key_confirm:`. Registration order is preserved.

### StopPropagation / multi-handler behavior

- `events.StopPropagation` no longer exists. In v2, once a handler's filter
  returns `True`, later handlers are skipped **unless** the client is created
  with `check_all_handlers=True`.
- This codebase registers **two** `NewMessage(incoming=True)` catch-all
  handlers (`pending_message_handler` and `inline_handler`) that are *both*
  expected to run on the same message. To preserve that, the main bot `Client`
  is constructed with **`check_all_handlers=True`**. Every handler already
  guards its own preconditions (state checks) and returns early when not
  applicable, so running all handlers is safe and matches v1 semantics
  (where all handlers always ran unless `StopPropagation` was raised — and this
  code never raised it: `StopPropagation` count = 0).

---

## 5. Messages, replies and forwards

| v1                                   | v2                                            |
|--------------------------------------|-----------------------------------------------|
| `msg.raw_text` / `msg.message`       | `msg.text` (also `text_html`, `text_markdown`)|
| `event.is_reply`                     | `event.replied_message_id is not None`        |
| `event.reply_to_msg_id` / `reply_to` | `event.replied_message_id`                    |
| `event.get_reply_message()`          | `event.get_replied_message()`                 |
| `msg.forward`                        | `msg.forward_info`                            |
| `msg.forward_to(chat)`               | `msg.forward(chat)`                           |

`raw_text` count in this codebase is 0. The `is_reply` / `reply_to` /
`get_reply_message` sites are migrated explicitly with comments.

---

## 6. Peers, `PeerRef`, and the removal of the entity cache

v2 removes the on-disk **entity cache** and the concept of **marked IDs**
(`-100...` prefixes). Everything is referenced through `types.PeerRef` /
`UserRef` / `ChannelRef` / `GroupRef`, constructed from the **bare** id.

**Storage strategy:** the app already stores raw numeric IDs in its database
(user ids, channel ids). Those stay as-is. At the Telethon boundary we wrap
them:

- A stored **user id** `uid`      -> `UserRef(uid)`
- A stored **channel id** `cid`   -> `ChannelRef(strip_mark(cid))` where
  `strip_mark` removes any legacy `-100` / `-` prefix that may exist in old
  stored values (helper `channel_ref_from_stored_id()` in the compat layer).
- Event objects already expose `.chat` / `.sender` (each at least an `.id` and
  a `.ref`), so handlers that reply into the current chat use `event.chat` /
  the shimmed `event.respond`, no manual peer building needed.

Specific migrations:
- `bot.py::check_user_joined()` used
  `client(GetParticipantRequest(channel=..., participant=...))`. v2:
  `await client.get_participants(channel_ref)` iterated, or the raw
  `tl.functions.channels.get_participant(...)` with `PeerRef._to_input_peer()`
  when a single-participant lookup is needed. We use the raw single-participant
  request wrapped by a documented helper, matching the original 1-call cost.
- `channel_watcher/services/fetcher.py` `get_entity` + `-100...` candidate
  logic -> `resolve_username` / `ChannelRef`, documented.
- `trade_journal/services/channel_service.py` `resolve_channel_input`,
  `-10000000000 - num` marked-id logic, `PeerChannel`,
  `InputMediaPhoto`/`InputPhoto` -> `ChannelRef` + v2 `send_file`/`send_photo`
  and album helpers.

`StringSession` (removed in v2) is not used anywhere in this codebase (count 0).

---

## 7. Errors (`telethon.errors` is now a factory)

- `telethon.errors` is no longer a static module; error classes are generated
  on demand: `from telethon import errors; except errors.FloodWait:`.
- **`FloodWaitError.seconds` is gone** -> use **`.value`** (the wait in
  seconds). Both `.seconds` sites (`animator.py`,
  `channel_watcher/ui/safe_edit.py`) are updated.
- Error names: v2 tends to drop the `Error` suffix in some spots, but the
  factory accepts the RPC name. We import the specific names used
  (`FloodWait`, `MessageNotModified`, `MessageIdInvalid`, `MessageEmpty`,
  etc.) via `from telethon import errors` and reference `errors.<Name>`.
  Where a v1 name has no direct v2 equivalent we catch the base `RpcError` and
  branch on `.name` / `.code`, with a comment.

The compat layer re-exports an `errors` object and a set of the specific
exception aliases the code catches, so `except FloodWaitError:` style code
keeps working while resolving to the v2 error type.

---

## 8. Raw API is private (`telethon._tl`) and `snake_case`

- `from telethon.tl.functions...` / `telethon.tl.types...` -> `from telethon
  import _tl as tl` then `tl.functions.<ns>.<snake_case>` /
  `tl.types.<Name>` / `tl.abcs.<Name>`.
- Migrated raw calls:
  - `GetParticipantRequest` -> `tl.functions.channels.get_participant(...)`
  - `JoinChannelRequest` -> `tl.functions.channels.join_channel(...)`
  - `SetTypingRequest` + `SendMessageTypingAction` ->
    `tl.functions.messages.set_typing(peer=..., action=tl.types.SendMessageTypingAction())`
  - `InputMediaPhoto` / `InputPhoto` / `PeerChannel` / participant types ->
    prefer public v2 wrappers (`send_file`, `Participant`, `ChannelRef`); fall
    back to `tl.types.*` only where no public API exists, clearly commented as
    private/raw.

### `client.action(...)` removed

Used in `channel_watcher/handlers/callbacks.py` as
`async with client.action(uid, "typing")`. Replaced by a documented async
context manager `typing_action(client, peer)` (compat layer) that periodically
sends `tl.functions.messages.set_typing(...)` while active — reproducing the v1
behavior with the v2 raw API.

---

## 9. `iter_messages` unified into `get_messages`

- v1 `client.iter_messages(chat, limit=n)` -> v2
  `client.get_messages(chat, n)` which is *both* awaitable and async-iterable:
  `async for m in client.get_messages(chat, n): ...`.
- v2's `limit` no longer has v1's "funny rules" (get defaulted to 1, iter to
  none). We pass explicit limits everywhere the old code did, and translate
  `offset_id` / `offset_date`. `channel_watcher/services/fetcher.py` migrated.

---

## 10. Buttons and inline

- `Button` construction unified through the compat `Button` shim
  (`Button.inline -> Callback`, `Button.url -> Url`,
  `Button.switch_inline -> SwitchInline`, `Button.text -> Text`). `style=`
  (e.g. `"danger"`) does not exist in the v2 button API; such kwargs are
  accepted and ignored by the shim with a comment (Telegram bot inline buttons
  never rendered a color anyway).
- Inline query: v1 `events.InlineQuery` + `event.builder.article(...)` +
  `event.answer(results)` -> v2 `events.InlineQuery` with the v2 inline-result
  builder. Migrated in `misc.py` with comments; if the exact v2 article
  builder differs, a documented helper adapts it.

---

## 11. Verification plan

- `python -c "import telegram; from telegram import TelegramBot"` imports clean.
- Construct `TelegramBot(...)` offline (no login) without error.
- `mypy` on migrated modules (v2 ships type hints).
- Phase 8 grep gate: no residual v1 idioms except intentional, commented `_tl`
  raw-API usages.

## 12. Open questions / TODO(v2)

Any `# TODO(v2): verify` markers left in code are collected in the final phase
report. The v2 migration guide explicitly states its change list is not
exhaustive; anything not covered above was checked against the live v2 module
docs and the installed 2.0.0a0 source before use.
