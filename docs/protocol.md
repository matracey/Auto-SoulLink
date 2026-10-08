# SLink client ↔ server wire protocol

**Status:** normative. Extracted from the trusted reference implementation at commit `7f52df8`:

| Layer | File | Role |
|---|---|---|
| Framing (client) | `lua/connector.lua` | non-blocking LuaSocket, newline-delimited JSON |
| Framing + admission + display (server) | `server/server.py` | `SLinkServer.handle_client`, `_dispatch_event`, status/HTML builders |
| Rules engine (server) | `server/state.py` | `SoulLinkState.handle_event` and every `_handle_*` |
| Adapter hooks | `server/adapters/base.py`, `server/adapters/__init__.py` | per-generation knobs the protocol relies on |
| Reference client | `lua/gen3/client.lua` (+ `lua/gen3/*.lua`, shared `lua/core/session.lua`/`lua/core/deferred.lua`) | the only client this document treats as correct |

**Authority.** Where the Gen 3 client and the server disagree, the server wins. Every such disagreement is marked **⚠ DISAGREEMENT** inline and collected in [Appendix A](#appendix-a--disagreements-and-ambiguities). `lua/gen1/client.lua` and `lua/gen2/client.lua` were deliberately not consulted.

**Conventions.** `file:line` cites the line where the behaviour is implemented. Field types: `str`, `int`, `bool`, `list[T]`, `hex` (lowercase hex string), `key` (mon key string, format per §3.1). "MUST/SHOULD/MAY" are RFC-2119. All indices are **0-based** unless stated. The two players are `"a"` and `"b"`; the "partner" is the other one (`state.py:158-159`).

---

## 1. Transport

| Property | Value | Cite |
|---|---|---|
| Carrier | TCP, one connection per client, server is `asyncio.start_server` | `server.py:5704-5705` |
| Framing | one JSON object per line, `\n` terminated, both directions | `connector.lua:7-9`, `server.py:1640`, `server.py:1877-1880` |
| Encoding | UTF-8; server decodes with `errors="replace"` and `.strip()`s the line; blank lines are ignored | `server.py:1663-1665` |
| Max inbound line (server) | 4 MiB (`limit=4*1024*1024`); an over-long line is drained to the next `\n`, logged, and the connection stays up. **No reply is sent for the dropped line.** | `server.py:1643-1658`, `server.py:5704-5705` |
| Max inbound line (client) | 4 MiB (`MAX_LINE`); an over-long server line is discarded and the reader resyncs at the next `\n` | `connector.lua:51`, `connector.lua:242-247`, `connector.lua:221-228` |
| Client → server envelope | `{"event": "<type>", "player": "a"\|"b", "seq": N, ...fields}` — `send()` stamps `seq` and `player` on every event | `lua/core/session.lua:78-88` |
| Server → client envelope | **exactly one line per inbound line**: `{"commands": [ {...}, {...} ]}`. Never fewer than one element: an empty queue is `[{"cmd":"noop"}]` | `server.py:1672`, `server.py:1831-1832`, `state.py:715` |
| Reply on malformed JSON | `{"commands":[{"cmd":"noop"}]}` | `server.py:1666-1673` |
| Reply on unknown `player` | `{"commands":[{"cmd":"noop"}]}`; `player` MUST be `"a"` or `"b"` (`VALID_PLAYERS`) | `server.py:79` (the set), `server.py:1681` (the check in `handle_client`), `server.py:1683` (the noop reply) |
| Reply on duplicate `seq` | `{"commands":[{"cmd":"noop"}]}` — the event is **not processed** | `server.py:1815-1818` |
| ACK/NACK envelope | **None.** There is no per-event ack. Command receipt is implicit (the reply line). Command *execution* is acknowledged only for the deferred commands via dedicated events (§5). | — |
| Delivery model | Commands for the sender are returned in the reply to the event that produced them. Commands for the *partner* are queued in `queued_commands[partner]` and flushed in the reply to the partner's **next event of any type** (ticks included). | `state.py:2-8`, `state.py:214`, `state.py:704-715` |
| Reply order | FIFO in queue order; server.py may append one `link_panel` after the state's list | `state.py:715`, `server.py:2148-2157` |
| Client pump cadence | once per emulated frame: `C.pump()` flushes the send queue and reads all complete lines; the client then drains `C.receive()` until nil and dispatches each reply | `connector.lua:150-154`, `lua/core/session.lua:361`, `lua/core/session.lua:406-415` |
| Reconnect | non-blocking connect with exponential backoff: first retry after 30 frames (~0.5 s), doubling to a 1800-frame (~30 s) cap; reset to 30 on success | `connector.lua:55-57`, `connector.lua:161-187`, `connector.lua:78`, `connector.lua:103` |
| `C.connected()` | `true` iff the socket is open **and** the non-blocking connect has completed (a one-byte blank protocol line must be writable; the server ignores it). During an in-progress connect it is `false`; a five-second deadline closes a stalled attempt so normal backoff can retry. | `connector.lua:143-146`, `connector.lua:95-128` |
| On disconnect (client) | socket closed; partial-send offset, partial-receive buffer and oversize flag reset; backoff reset. **The unsent `_send_queue` is cleared** so stale events cannot precede the next connection's hello. This does not claim the received `_line_queue` is cleared. | `lua/connector.lua:262-280` |
| Events while disconnected | `send()` refuses to enqueue when `not C.connected()`: the event is **dropped** with a console log. Nothing is buffered for later. (A connected-but-not-yet-helloed send is a separate case, held and flushed right after hello instead of dropped, up to 64 events — `lua/core/session.lua:89-98`.) | `lua/core/session.lua:78-81` |
| On disconnect (server) | `connected_players[pid].connected = False`; **queued commands survive** and are delivered on the next event after reconnect | `server.py:1849-1861`, `state.py:214` |

### 1.1 `seq` semantics

| Rule | Cite |
|---|---|
| `seq` is a per-client monotonically increasing `int` starting at 1 for the process lifetime; it does **not** reset on TCP reconnect (only on script reload) | `lua/core/session.lua:71`, `lua/core/session.lua:83` |
| The duplicate-event counter is **per connection**, not per player: `last_seq` is a local of `handle_client`, so it is born with the socket and dies with it. An event with `seq <= last_seq` is dropped as a duplicate of one seen on *this* connection; a new connection starts from `-1` by construction, so a client that restarts and counts from 1 again is never mistaken for a duplicate. | `server/server.py:1633-1636` (the per-connection locals), `server.py:1814-1821` (the guard) |
| Each player-slot on a connection, guarded by `handle_client`, is **ignored until that slot says hello**: any non-`hello` event for a slot not yet helloed on this connection is answered `noop` with `refused="no_hello"`, with one WARNING per connection. The per-slot identity gate in `_dispatch_event` is the second line. | `server/server.py:1686-1697`, `server.py:2183-2184` |
| ⚠ RETIRED 2026-09-17 (`0629736`) — kept so the history reads straight: the server used to keep `_last_seq[player]` and treat `seq <= 1 and last > 10` as a client restart, which trapped a client that restarted after sending ≤ 10 events (its first events, `hello` included, were silently dropped) and forced a conformance harness to never reuse a server instance across "restarts". `reconnect_new`'s wrong-save leg hit exactly that trap live. The heuristic is deleted and the constraint no longer exists — a harness may reuse a server across restarts freely. | `0629736`; `server/server.py:1739-1761` |
| Omitting `seq` (`-1` default) disables the guard for that message | `server/server.py:1814-1816` |

### 1.2 Client-side response parsing (RETIRED — kept for history)

⚠ RETIRED at the C4/gen3 client rewrite (`addc9225`, "delete the old Gen 3 client and its modules"): the old (reference) Gen 3 client had **no JSON decoder**; `parse_command_list` was a pattern scraper. That file is gone (archived as `archive/gen3-old-client:lua/clients/gen3_frlge_client.lua`), and every shipped client now decodes with a real JSON codec (`lua/json_codec.lua`; the shared receive loop is `lua/core/session.lua:406-415`, `json.decode` at `:409`; Gen 1 binds the same codec at `lua/gen1/client.lua:1932`). The consequences below described what a spec-conformant server had to tolerate from the old scraper; none of them apply to a client built on `lua/core/session.lua`. Kept, not deleted, so the history reads straight (same convention as Appendix A10):

- Nested objects were only understood for `stats` (`{level,maxHP,attack,defense,speed,spAtk,spDef}`). `pp1..pp4` inside `stats` were **not** extracted although the old client itself sent them.
- String arrays were understood for `rows`, `areas`, `blobs_hex`, `options`.
- `text` had JSON `\n` unescaped to a real newline.
- Booleans were read for the five `config` fields only.
- Any command with a recognised `cmd` but missing required companion fields (e.g. `box_mon` without `key`) fell to the "unknown command" log branch.

A client MAY still use a real JSON decoder (every current client does); it MUST NOT depend on field order.

---

## 2. Session lifecycle

### 2.1 `hello`

Sent on every TCP (re)connect edge — `hello_sent` is cleared on disconnect and re-armed as the first send once the driver says it is safe (`lua/core/session.lua:362-370`). The server treats **every** `hello` as a fresh session start for that player (`state.py:2027-2382`).

| Field | Type | Required | Gen 3 sends | Server reads | Cite |
|---|---|---|---|---|---|
| `event` | `"hello"` | yes | yes | dispatch | `state.py:544-546` |
| `player` | `"a"\|"b"` | yes | yes | `handle_client` | `server.py:1680` |
| `seq` | int | SHOULD | yes | dup guard | `server.py:1814` |
| `rom_type` | str | yes (routing) | yes | adapter selection, set-once commit, `is_rr = rom_type.endswith("_rr")` | `server.py:2230-2240`, `server.py:2321-2324` |
| `party` | list[PartyEntry] (§4.1) | yes (may be `[]`) | yes | identity, `party_size`, `party_keys`, blobs, hp==0 faints, display backfill, `party_details` seed | `state.py:2148-2155`, `state.py:2170-2302`, `server.py:2371-2392` |
| `ot_id` | str | SHOULD | **no** | identity lock (preferred over key-derived OT) | `state.py:2052` |
| `trainer_name` | str | SHOULD | yes | identity display name, committed once per run | `state.py:2061`, `server.py:2364-2371` |
| `player_gender` | int: `0` = MALE, `1` = FEMALE | optional | Emerald only, hello + tick | Per-player trainer-panel hint. Emerald keeps May for a male player and Brendan for a female player. Absent, unreadable or invalid = unknown: show both. Other titles are unchanged. | `lua/gen3/reads.lua` `read_trainer`; generated `gen3_emerald/profile.json` `SB2_PLAYER_GENDER_OFFSET`; pret `pokeemerald@c65e93f2:include/global.h:508-513`, `include/constants/global.h:113-114`; `data/gen3/pret/pokeemerald.sym:962` (`gSaveBlock2Ptr`) |
| `has_pokeballs` | bool | SHOULD | yes | nuzlocke gate; `None` + non-empty party ⇒ `True` (legacy) | `state.py:2159-2161` |
| `ball_count` | int | optional | yes | dashboard | `server.py:2358-2359` |
| `badges` | int **bitmask** (bit i = gym i+1) | optional | yes | dashboard, badges overlay, compact `link_panel` popcount | `server.py:2360-2361`, `server.py:2360-2361` |
| `kanto_badges` | int bitmask (second region, Gen 4) | optional | no | badges overlay bits 8-15 | `server.py:2362-2363`, `server.py:2362-2363` |
| `battle_identity` | bool | optional | yes | capability declaration (card C5-10b): this client mints and enforces the battle request identity, so an identity-less manual rival inject may be refused for it. Absent or `false` = the client does not speak the protocol and keeps the pre-card behaviour. Never inferred from anything else. | `state.py:2063-2067` |
| `area_id` | str | optional | yes | `player_area_id` | `server.py:2317-2318` |
| `loc_name` | str | optional | yes | `player_area` (display) | `server.py:2317-2318` |
| `writes_enabled` | bool | optional | yes | **ignored by server** | — |
| `panel` / `panel_abi` | bool / int | optional | no | per-cartridge native-panel capability; absent ⇒ adapter default (`info_panel_width()==0`) | `server.py:948-966`, `server.py:948-966` |
| `sfx` | bool | optional | no | per-cartridge native-sound capability (Gen 1 companion mailbox caps bit 0); carried like `panel` | `server.py` hello handler |
| `trade_prepare` | bool | optional | no | this client answers `apply_prepare` (§6.1 `preparing`); a trade takes the prepare round only when **both** players declared it, otherwise the direct `apply_trade` is unchanged | `state.py` `trade_prepare` |
| `party_hidden` | bool | optional | no | Gen 3 recovery protocol: this party/box snapshot is withheld, including borrowed-party and unsaved-trade episodes; preserve the last trusted display, but do not use it as fresh evidence or new trade eligibility. Explicit `ot_id` is required on a withheld hello. While hidden, capture/faint/deposit/retrieve/whiteout/release events are refused before state or presentation mutation with `noop{refused:"party_hidden"}` and a warning; the report was not processed. **Exception — `key_change`:** a well-formed pair (both keys non-empty strings, `old_key ≠ new_key`) is answered `key_change_rejected{old_key,new_key,reason:"party hidden"}` instead — still before any mutation and still retiring nothing, so the client's alias survives — while a malformed pair and every other hidden event still get the bare `noop`. An explicit visible empty-party hello releases visibility but supplies no trade evidence: an awaiting trade remains hello-only. Other generations ignore the extension; unsupported Gen 3 foundations refuse it by name | `state.py` `party_snapshot_withheld`; `refuse_hidden_event` (`state.py:473-485`) |
| `trade_outstanding` | list of objects | optional | no | Gen 3 recovery hello: `{token: nonempty string, epoch: integral number 1..4294967295}` records from a durable client journal; omit when empty. A matching pending token becomes uncertain/hello-only before snapshot use. No declaration-bearing hello is evidence. Integral JSON floats are accepted and normalized to integers; booleans, fractions and out-of-range values leave the slot connected but hidden/unrecovered, with a board warning. Recovery barriers persist independently of the pending token; concrete trade_done cannot bypass a hidden/hello-only barrier. Epoch is an opaque lease identity, never proof of a save or reload | `state.py` `_declare_trade_outstanding`, `docs/gen3/research/t4_server_trade_contract.md` |
| `rom_content` | dict (adapter-defined) | required for randomized FR/LG and Emerald; also required under a `rom_contract.json` | no | verified admission fingerprint + per-player encounter/trainer tables. These reports preserve explicit trainer moves but omit default moves: level-up learnsets are not shipped or verified by this report. Multi-set maps such as Altering Cave display set 0; the report does not carry the runtime set selector. FR/LG `rand_overlay` is unreachable with the shipped client: its hook sends content only for `rand`, so a `rand_overlay` hello from that client lacks the required report and is refused (`lua/gen3/client.lua:1078-1099`). | `server.py` `_decide_admission`, `gen3_frlge.py` `ingest_rom_content` |
| `rom_sha1` | str (40 hex, any case) | SHOULD | no | compared to the contract's `rom_sha1` for this player when both exist; mismatch ⇒ rejected (§2.2 step 1) | `_decide_admission` |
| `companion_abi` | int | optional; Gen 3 FR/LG/Emerald/RR clients publish it **only when the cartridge's own companion mailbox is live in RAM** (`lua/gen3/native.lua` `companion_live`: signature at the pack's `native.BASE`, ABI at `BASE+4`) | no | companion evidence for `GameRulesAdapter.companion_refusal` (§2.2 step 1''): it must equal the ABI the title's pack pins in `profile.json` `native.ABI` (2 for FR/LG/Emerald, 1 for Radical Red); the gate never trusts `artifact_kind`, because a randomized cartridge declares `rand` whether or not it is patched. Gen 2 Crystal/Gold/Silver publish it the same way, only while the overlay's own SLNK mailbox signature reads right in RAM (`lua/gen2/panel.lua` `companion_abi`: beacon, version byte and init cookie, read at hello time like Gen 3's; the frame counter gates painting, not this evidence), and it must equal the title's `profile.json` `overlay.abi` (3, generated from `patch/gb/slink_abi.inc`); a clean cartridge has no SLNK mailbox, so it is absent. The evidence is checked at hello time only: a mid-run ROM swap without a fresh hello is not re-checked, and a reconnect hello re-checks | `gen3_frlge.py` `companion_refusal` |
| `artifact_kind` | str (`clean` \| `overlay` \| `rand` \| `rand_overlay` \| `named` \| `companion`) | optional, default `"clean"` | no | committed once per run beside `rom_type`; a later hello of another *pairing* kind is refused (§2.2 step 1') | `_mixed_games_error` |
| `foundation` | str (pack id, e.g. `gen3_frlg` \| `gen3_rr` \| `gen3_emerald` \| `gen1_rby` \| `gen1_purergb` \| `gen2_gsc`) | optional — omit the key, or send the derived string | no | **never trusted**: the server derives the foundation from `rom_type` and refuses a hello that declares anything else. Absent (key missing) ⇒ derived, so a client that does not send it is unaffected; **present** ⇒ validated, and `null`/`""`/`false`/`0`/`[]`/`{}` are refused, not treated as absent | `foundation_for_rom_type` |
| `pc_boxes` | list[BoxEntry] (§4.4) | optional | yes (hello + tick) | `pc_boxes`, memorial contamination scan | `server.py:2379-2385` |
| `pc_boxes_generation` | int ≥ 1 | optional (Gen 1/2, FR/LG, Emerald and RR send it; expansion has no admitted client) | yes (hello + tick) | KEY-SCOPE-5 box census: `pc_boxes` is a **complete** census only when the same message carries this generation, which the client bumps after each successful full box scan (never on a failed one) and which is monotonic per client session. Omitted — like `pc_boxes` — whenever the last scan was incomplete, so an absent generation means "no complete census yet", never "census 0". Sending it on hello advertises the census; see the tick row and `key_change` | `server.py` `_ingest_box_census` |
| `in_battle`, `is_trainer_battle`, `enemy_party` | as in tick | optional | no | seed `battle_state` | `server.py:2394-2402` |

**Declaring clients (client conformance).** Gen 1 and Gen 2 clients always send both pairing fields, so for their foundations (`gen1_rby`, `gen1_purergb`, `gen2_gsc`) a hello that omits `foundation` or `artifact_kind` is non-conformant, and a Gen 2 hello (`Crystal`/`crystal`, `Gold`/`gold`, `Silver`/`silver`) must declare `foundation:"gen2_gsc"` — a Gen 1, Gen 3 or legacy `gen2_crystal` claim is refused like any disagreeing claim. The Gen 2 hello sends `rom_type`, `foundation:"gen2_gsc"`, `artifact_kind:"clean"`, `party`, `ot_id`, `trainer_name`, `has_pokeballs`, `ball_count`, `badges` (Johto), `kanto_badges`, `area_id`, `loc_name`, `pc_boxes`, `writes_enabled`, `rom_sha1`, `in_battle`, `panel:false`, `panel_abi:0`, `sfx:false`; no `rom_content` (`lua/gen2/client.lua:55`, `:618-629`). The current Gen 3 client also declares both fields (`lua/gen3/client.lua` `hello_fields`); older hellos that omit them still use the server defaults. The permitted relation is data: `HELLO_DECLARES` plus the server's own `foundation_for_rom_type` (`tests/unit/protocol_schema.py`), never a `game_id` branch.

### 2.2 Admission and identity (in order)

| Step | Rule | Outcome | Cite |
|---|---|---|---|
| 0 | Any non-`hello` event from a player with a standing `identity_error` → `[noop]`, not processed | blocked | `server.py:2183-2184` |
| 0' | Any non-`hello` event from a player not admitted (including a randomized cartridge refused without a contract) → `[noop]` | blocked | `server.py:2185-2191`, `server.py:2185-2191` |
| 1 | Cartridge proof and ROM contract check (`_decide_admission`): declared randomized FR/LG and Emerald cartridges MUST provide a valid report even without a contract. Missing, malformed, incomplete, extra-byte, wrong-hash or forbidden-rule reports are refused by name and any previously adopted tables for that player are invalidated. The contract additionally rejects an unreadable contract, an unnamed player, missing content, decoder failure, mismatched fingerprint, or mismatched full-ROM SHA-1 when both values exist. The default hook preserves other foundations' existing policy; an unsupported legacy fingerprint (`None`) still has nothing to compare. | rejected ⇒ **`[noop]` returned, rule/identity state untouched** | `server.py` `_decide_admission`, `base.py` `refused_rom_content` |
| 1'' | **Companion required** (`GameRulesAdapter.companion_refusal`, enforced at the wire in `handle_client` and again in `_decide_admission`; owner 2026-10-02). A hello for a title that has a companion (Red/Blue, pureRGB, Crystal/Gold/Silver, FireRed/LeafGreen/Emerald, Radical Red) is refused unless it shows evidence of it: Red/Blue need `panel: true` (the WRAM mailbox), Crystal/Gold/Silver need `companion_abi` equal to the pack's `profile.json` `overlay.abi` while `artifact_kind` is `overlay`, pureRGB needs `artifact_kind` `overlay` or `rand_overlay`, Gen 3 needs `companion_abi` equal to the pack's pinned ABI. `artifact_kind` therefore **defaults to a refusal** for these titles, not to `clean`. Yellow, the Archipelago builds, the Emerald Expansion and Gen 4/5 are exempt. Reply: `hud_show{text:"[x] NEEDS COMPANION PATCH"}`, `identity_error[pid]` set, nothing else changes. | rejected | `server.py` `_companion_refusal`, `adapters/*.py` `companion_refusal` |
| 1' | Pairing (`_mixed_games_error`, in `handle_client` **before** the per-cartridge capability updates and the adapter reselection, so a refused hello changes nothing). The run is locked to one **foundation** — a pack, not a `game_id`: `firered`/`leafgreen` ⇒ `gen3_frlg`, `firered_rr` ⇒ `gen3_rr`, `emerald` ⇒ `gen3_emerald` (its own pack, so Emerald pairs only with Emerald: FR/LG/RR beside Emerald is refused), every Gen 2 spelling (`Crystal`/`crystal`, `Gold`/`gold`, `Silver`/`silver`) ⇒ `gen2_gsc`, so any two Gen 2 titles pair (`crystal_ap` is not one: it keeps its legacy foundation and pairs with none of them), and elsewhere the `game_id` itself (`gen1_rby` vs `gen1_purergb`). The foundation is **derived** from `rom_type` via `foundation_for_rom_type()`, and the check is **fail-closed on every input**: a `rom_type` that is absent, empty, non-string or unrecognized is refused (it used to be skipped, which let a hello with no `rom_type` past the lock entirely), and an invalid retry does not clear a standing rejection. The hello's own `foundation` is optional but **absent is not empty**: omit the key and it is derived; send it and it must be the derived string, so `null`, `""`, `false`, `0`, `[]`, `{}` and any other value are refused. A non-string or unknown `artifact_kind` is refused likewise. Before content-based kind normalization, the adapter class's `supports_randomized(rom_type)` gate checks BOTH the candidate and the committed run title. The base default is false; Gen 1 RBY, pureRGB, Gen 2, FR/LG and Emerald explicitly opt in. Gen 2 admits `rand_overlay` only (the randomized companion overlay; a plain `rand` is refused), bound by the contract's per-player `rom_sha1`. RR, expansion, Gen 4/5 and unknown adapters refuse `rand` and `rand_overlay` by name. It is also locked to one **pairing kind**: each foundation's adapter class normalizes the declared `artifact_kind` through `GameRulesAdapter.pairing_kind()` (default `named → clean`; `Gen3Adapter` adds `companion → clean`, since both patches are per cartridge). For FR/LG and Emerald, `_kind` calls the optional `pairing_kind_for_title(rom_type, kind, rom_content)` hook; other adapters retain `pairing_kind_for()`. This checks the report against the declared title family before content normalization: a declared `rand` whose verified trainers, wild tables and evolutions match the clean title commits and pairs as `clean` (owner ruling 32); any decode failure keeps `rand` and the admission proof still has to pass. Other kinds retain their existing mapping, so pureRGB `clean` vs `overlay` stays refused. Variants of one foundation (Red beside Blue, FireRed beside LeafGreen, companion RR beside clean RR) pair. Reply is only `hud_show{text:"[x] MIXED GAMES", color:[255,0,0], duration:600}`; `identity_error[pid]` is set and every later event gets `[noop]` until a conforming hello. | rejected | `server.py` `_mixed_games_error`, `adapters/__init__.py` `foundation_for_rom_type` |
| 2 | Identity: `incoming_ot = msg.ot_id` else `adapter.parse_ot_id(party[0].key)`; empty party and no `ot_id` ⇒ no identity check at all | — | `state.py:2052-2060` |
| 3 | First hello with an OT locks `player_identity[pid] = {ot_id, trainer_name or "A"/"B"}` and persists | locked | `state.py:2099-2109` |
| 4 | Later hello with a different OT: `identity_error[pid]` set, `msg["_rejected"]=True`, the reply is **only** `hud_show{text:"[x] WRONG SAVE: slot A", r:255,g:0,b:0,frames:600}`, and `_handle_hello` returns before touching party state | rejected | `state.py:2072-2088` |
| 5 | Matching OT clears `identity_error` and may refresh `trainer_name` | ok | `state.py:2089-2098` |

⚠ DISAGREEMENT, CLOSED by `ca0888ba`: step 1' says a non-string `artifact_kind` is refused, and it now is — both call sites pass the key's presence through, so only a MISSING key defaults to `clean` (`msg.get("artifact_kind", "clean")` at `server.py:1768-1770` and at the run-commit path `server.py:2329-2335`): a present `null`/`""`/`false`/`0`/`[]`/`{}` reaches the type check (`server.py:771-837`) and is refused instead of being admitted and committed as `clean`. REMAINING LIMIT: a non-empty but undocumented *string* (e.g. `"banana"`) still passes that type check and is committed as declared — the run is protected only in the pairing sense, because two different kinds cannot mix (`server.py:771-837`). The documented set is test-side (`tests/unit/protocol_schema.py` `ARTIFACT_KINDS`); a server-side set check is a carry. Gen 2 kind normalization today comes from the routed legacy adapter class (`adapter_class_for_rom_type` → `gen2_crystal`, `server/adapters/__init__.py:68-76`); `Gen2GSCAdapter.pairing_kind` keeps `named` distinct (`server/adapters/gen2_gsc.py:606-607`) once the G3 cutover routes it.

⚠ DISAGREEMENT, RESOLVED for the current Gen 3 client: the WRONG SAVE `hud_show` now uses `r,g,b,frames` (`state.py:2081-2085`); its former `color`/`duration` spelling differed from other `hud_show` commands. The old (reference) client's parser only read `r,g,b,frames` (archived tag `archive/gen3-old-client`, file `lua/clients/gen3_frlge_client.lua` around line 300), so that toast rendered white for 300 frames. The shared `hud_show`/`msgbox`/`gui_prompt` handler now accepts either spelling via one `hud_color()` helper (`lua/core/session.lua:59-62`, used at `:278-282`). A client not built on `lua/core/session.lua` SHOULD still accept both spellings.

**What a rejected client must do:** keep the connection, display the toast, and stop expecting any state effect until it sends a `hello` whose OT matches. Every other event will get `[noop]` (`server.py:2183-2184`). Under a ROM-contract rejection the client gets `[noop]` even for the hello and the dashboard shows `admission_reason` (`server.py:2278`).

### 2.3 What `hello` does on the server (accepted path)

| Effect | Cite |
|---|---|
| `party_size[pid] = len(party)`; blob cache refreshed (`_ingest_party_blobs`) | `state.py:4453-4512` |
| `_has_helld.add(pid)`; **partner removed from `_has_helld`** so partner-side `box_mon` becomes optimistic until the partner hellos | `state.py:2164-2169` |
| `party_keys[pid] = {key for entries with maxHP > 0}` minus DEAD/MEMORIAL keys | `state.py:2170-2179` |
| Re-quarantine: any pending (unlinked) capture found in the party gets `box_mon` re-queued, unless that would leave zero alive mons | `state.py:2181-2196` |
| Any party entry with `hp == 0` whose key is an ALIVE link, if `pokeballs_obtained[pid]`, is treated as a faint that happened offline → `_propagate_faint` (partner gets `force_faint`/`force_explode`) | `state.py:4285-4339` |
| Every key in `pending_memorials[pid]` gets `memorialize` re-queued (dedup against queue) | `state.py:2253-2259` |
| Any DEAD/MEMORIAL key present in the party gets `memorialize` + `hud_show "[x] Dead in party -> grave"` | `state.py:2267-2277` |
| Nickname/species back-fill into `LinkEntry.MonInfo` from the snapshot | `state.py:2279-2302` |
| **Always** `resolved_areas{areas:[...]}` (LINKED + DEAD_ZONE areas + areas where this player has a pending capture; may be `[]`) | `state.py:2304-2322` |
| **Always** `config{overworld_presence, native_messages, native_sounds, battle_calc, pc_trade_npc}` | `state.py:2330-2337` |
| Re-arm an interrupted whiteout rebuild (`party_mon` × outstanding + `rebuild_start`) | `state.py:2347-2374` |
| `game_over` if `run_over` | `state.py:2376-2378` |
| server.py (`_dispatch_event`): commit `rom_type` once, commit `trainer_name` once, ingest `rom_content`, seed `party_details`/`battle_state`, call `_cache_mon_info` | `server.py:2321-2400`, `server.py:2007-2053` |

Nothing else is "replayed": there is no event log replay. Pending commands queued for this player while offline are simply included in the hello reply (they were never removed from `queued_commands`).

### 2.4 `tick` / `safe` shared handling

`handle_event` treats `safe` and `tick` identically (`state.py:667-698`): if `has_pokeballs is True` the gate opens; if `party` is present, `party_size` is updated, blobs are re-ingested and `_reconcile_party_keys` runs. `_reconcile_party_keys` (`state.py:3608-3707`) repairs `party_keys` toward the snapshot and may queue `party_mon` to the partner for a ghost-boxed linked mon. It is suppressed during an active rebuild, during a trade in phase `applying`, and for `_trade_settle_ticks[pid]` (=12) ticks after a trade commits (`state.py:3608-3707`, `state.py:3643-3647`, `state.py:3643-3647`).

⚠ DISAGREEMENT: the Gen 3 `safe` event carries **no fields** (`lua/core/session.lua:402-404`) although `_ingest_party_blobs`' docstring says blobs ride "every hello / tick / safe" (`state.py:4453-4456`). `safe` is therefore, in practice, only a queue flush that marks "the client is in the overworld again". server.py logs it and nothing more (`server.py:2499-2500`). A new client MAY send `safe` without a party.

### 2.5 `stats_cache`

`{event:"stats_cache", key, stats}` — sent by the Gen 1 client **immediately before** executing a `box_mon` deposit (`lua/gen1/client.lua:996-998`); the shared Gen 3 core instead sends it **immediately after** a successful deposit, before the rescan (`lua/core/deferred.lua:159-168`). Server: `mon_stats[key] = stats`; if the key is in `party_keys`, `party_size -= 1` and the key is discarded (`state.py:594-607`). server.py drops the key from `party_details` (`server.py:2477-2481`). Both `key` and `stats` MUST be truthy or the event is a no-op. The cached `stats` is echoed verbatim in later `party_mon` commands for that key (`state.py:3470-3484`, `state.py:56` (SYNC_COMMANDS), `state.py:3782-3791`).

---

### 2.6 Keyed sync-command lifetime

**Shipped (`state.py:76`, `state.py:222`, `state.py:3496-3533`).** A keyed `party_mon`, `box_mon` or `memorialize` remains in flight after its reply leaves the server until a matching keyed acknowledgement resolves it or `SYNC_INFLIGHT_RECONCILES` (= 6) reconciler passes expire the window. `SoulLinkState.sync_inflight` tracks `(key, cmd) -> passes_remaining` per player (`state.py:222`); `_arm_inflight` seeds the window at `SYNC_INFLIGHT_RECONCILES` when `handle_event` drains and clears the player's outbound queue (`state.py:704-706` calls `state.py:3496-3502`); `_ack_inflight` clears every in-flight entry for a key the instant any event carrying that key arrives (`state.py:533`, `state.py:3504-3508`); `_expire_inflight` spends one pass only when `_reconcile_party_keys` actually reconciles, not on a pass it skips for a rebuild or trade (`state.py:3510-3521`, called at `state.py:3650`). Reply delivery is not execution. While the command is queued or in flight, `_has_pending_command` (`state.py:3523-3533`) makes reconciliation treat the target's transient party/box placement as still-pending rather than a player move — consulted at `state.py:3662-3663`, `state.py:3685-3686` and `state.py:3700-3701`. Expiry permits reconciliation again; it is not a success acknowledgement or a delivery guarantee.

The existing keyed responses are `sync_retrieve_done` / `sync_retrieve_failed` for retrieval, `box_mon_failed` for failed deposit, and `memorialize_done` / `memorialize_failed` for memorial handling (`handle_event`'s reply branches at `state.py:608-658` for the first two; `_handle_memorialize_done`/`_handle_memorialize_failed` at `state.py:4400-4451`). `stats_cache` currently updates cached stats and the party model (`state.py:594-607`); it is sent before the Gen 1 deposit (`lua/gen1/client.lua:533`), not proof of successful deposit, but because it carries the target `key` it still acks that key's in-flight window through the generic `_ack_inflight` path (`state.py:3504-3508`). Do not invent a `box_mon_done` event.

## 3. Client → server events

### 3.1 Mon key

| Property | Rule | Cite |
|---|---|---|
| Who computes it | **the client**; the server treats it as an opaque string except via `adapter.parse_ot_id`, `adapter.is_shiny`, `adapter.gender_from_key`, `adapter.is_valid_mon_key` | `base.py:166-204`, `state.py:2060`, `state.py:2449` |
| Gen 3 format | `"%08X:%08X"` = `personality:otId`, uppercase hex, 8+8 digits | `lua/gen3/reads.lua:402` |
| Gen 1/2 format (adapter contract) | three segments `DVs:OTID:species` (`DDDD:TTTT:SS`), OT = middle segment | `base.py:736-748`, `gen1_rby.py:439-509`, `state.py:4162-4164` |
| Uniqueness | scoped **per player** (KEY-SCOPE): every lookup goes through that player's own index (`entry_for`), so two players' mons, including the two halves of one pair, may share a key, as a fixed-DV/OT in-game trade or gift gives every cartridge (Gen 2 Kyle's Onix is always `9666:BF1E:5F`). Within one player a key is not guaranteed unique for Gen 1 either; `_check_link_violation` refuses (force-faints) a capture whose key already indexes one of the SAME player's live links | `state.py:4366-4368`, `state.py:4174-4184` |
| Stability | MUST be stable across party↔box moves and across reconnects; MUST change only via `key_change` (or a trade, §6) | `state.py:3840-3867` |

`species_id` on the wire is the **game-internal** species id (CFRU id for RR, internal index for Gen 1). The adapter converts with `to_national_dex`/`species_name` (`base.py:457-459`, `gen3_frlge.py:943-944`, `gen1_rby.py:522-524`). `level` is the displayed level (int). Slots are 0-based (`lua/gen3/reads.lua:461-482`).

### 3.2 Event table

`player_gender` is also optional on `tick`, with the same `0`/`1` encoding as
`hello`. Only accepted hello/tick messages update the presentation hint; an
omission (or null/invalid value) clears that player's previous hint, so reconnecting legacy clients
retain the two-rival fallback. This field does not select a run-wide rival.
Emerald's native opposite-character branches are pinned by
`pret/pokeemerald@c65e93f2:data/maps/Route103/scripts.inc:22-24` and
`data/maps/Route110/scripts.inc:386-388`. The adapter adds optional
`required_player_gender` metadata to the corresponding trainer briefs, using
their script constants (preserved by ROM ingestion) rather than display names.

Dispatch order and the full accepted set: `state.py:513-715`. Anything else is logged and answered with the queue flush (`server.py:2723-2733`).

| `event` | Required fields | Optional fields | Gen 3 trigger | Server effect | Notes / traps |
|---|---|---|---|---|---|
| `hello` | `rom_type:str`, `party:list` | see §2.1 | TCP connect edge `lua/core/session.lua:362-370` | §2.2–2.3 | Always answered with `resolved_areas` + `config`. |
| `tick` | — (all optional) | `has_pokeballs:bool`, `party`, `area_id`, `loc_name`, `in_battle:bool`, `is_trainer_battle:bool`, `trainer_id:int`, `opponent_name:str`, `opponent_class:str`, `enemy_party:list`, `is_doubles:bool`, `pc_boxes:list`, `pc_boxes_generation:int`, `ball_count:int`, `badges:int bitmask`, `kanto_badges`, `trainer_name:str` | every 30 frames (~0.5 s) `lua/core/session.lua:52`, `lua/gen3/client.lua:1158-1182,lua/core/session.lua:398-400` | state: gate, `party_size`, blobs, reconcile `state.py:667-698`; server.py: dashboard state, `battle_state`, dupes-clause check on wild-battle start, `party_details` **replaced** from `party` `server.py:2498-2616` | `party` omitted while a borrowed party is in RAM (`lua/gen3/client.lua:1171-1172`). `enemy_party` MUST be `[]` when not in battle to clear stale foes (`lua/gen3/client.lua:317-333`, `server.py:2498-2616`). The **first** tick with `in_battle=true` MUST carry `area_id` and `enemy_party[0].species_id` for the dupes-at-battle-start prompt (`server.py:2498-2616`). |
| `safe` | — | same as tick | first overworld frame after a battle (`pending_safe`) `lua/gen3/client.lua:606-609,lua/core/session.lua:402-404` | same as tick | Gen 3 sends `{event:"safe"}` only. Gen 1/2 send the battle-end box census (`pc_boxes`, `pc_boxes_generation`), since a battle may have boxed a catch. |
| `area_enter` | `area_id:str` | `loc_name:str` | `loc ~= prev_loc` (any map change) `lua/gen3/client.lua:560-571` | ignored if `area_id==""` or `is_gift_area`; else UNSEEN→PENDING_{partner}, PENDING_{self}→PENDING_BOTH; `_save` via `_handle_area_enter` `state.py:2387-2413`; server.py: `player_area`, event log `server.py`: `_dispatch_event`'s `area_enter` branch `server.py:2404-2416` | Gen 3 sends it for every location change even with `area_id=""` (town). `area_id` strings are adapter-namespace snake_case (`route_1`, `oaks_lab`, `intro`, `gift_<g>_<n>`). |
| `capture` | `key`, `area_id` | `species_id:int`, `level:int`, `hp:int`, `maxHP:int`, `nickname:str`, `held_item_id:int`, `is_egg:bool`, `gift:bool`, `in_box:bool`, `stats:dict` | `settle_acquisitions` (`lua/gen3/client.lua:441-486`) reports every unknown non-egg key found in the party first, then a single attributable unknown boxed key when the party had none; native hatch uses `settle_hatches` (`lua/gen3/client.lua:488-500`). Party catch and hatch include full `stats`; armed by the `on_signal` flag dispatch (`capture_wild`→wild party catch, `mon_given`/`pc_move`→out-of-battle or PC-move acquisition, `lua/gen3/client.lua:1312-1318`). The old (reference) client's separate frame-buffered sub-paths (battle-vs-box-vs-gift-vs-post-freeze) collapse into this one signal-armed settle now; "post-freeze recovery" is just `update_frozen`/`rebaseline` resuming normal settling once the borrowed party clears (`lua/gen3/client.lua:417-428`, `lua/gen3/client.lua:383-387`) | `_handle_capture` `state.py:2426-2923`: gift detection, shiny clause, bonus pairing, DZ/LINKED/second-capture rejection (`force_faint`+`memorialize`+SE 26+`hud_show`), species clause (`force_faint`+`gui_prompt "[x] Dup X"`+`unresolve_area`), pending add, **quarantine `box_mon`** if `!in_box && party_size>=1 && !gift`, stats cache, link formation (`msgbox "X and Y linked!"`, SE 25 both, `party_mon` both if both parties < 6) or first-capture (`hud_show ">> Got X"` to partner) | `key` and `area_id` MUST be non-empty or the event is dropped. `species_id` MUST be sent for the species clause. `stats` absent ⇒ server builds `{level,maxHP}` from top-level (`state.py:2789-2798`). `in_box=true` MUST be set when the catch landed in the PC (party full) so the server does not queue a redundant `box_mon`. No special-case sentinel: a capture before `has_pokeballs` just carries whatever `area_now()` resolves (often `""`), not a forced `area_id="intro"` — that was the old (reference) client only (`area_now`, `lua/gen3/client.lua:242-247`). |
| `faint` | `key` | `area_id` | signalled party HP at 0 after an earlier observed alive state `lua/gen3/client.lua:367-372`; in battle on a `faint` or `battle_end` signal after party readback `lua/gen3/client.lua:431-439,625-670,1291-1334` | `party_keys.discard`; ignored if `!pokeballs_obtained`; if key is an ALIVE link → `_propagate_faint`: partner gets `force_faint` (or `force_explode` if `explode_mode && adapter.supports_explode_mode()`) + `play_sound 26`, both get `memorialize`, entry DEAD, `_check_game_over` `state.py:4285-4339`, `_handle_faint` `state.py:2925-2955` | server.py enriches killer from cached `battle_state.enemy_party` (first foe with hp>0) and `_level` from `party_details` `server.py:2422-2438`. A client MUST NOT re-report the HP=0 it wrote itself for a `force_faint` (`lua/gen3/client.lua:399-401`). |
| `no_catch` | `area_id` | `species_id:int`, `level:int` | wild battle ended, nothing caught, area unresolved; the current signal-driven client settles on battle end (the archived reference client used a 90-frame grace) `lua/gen3/client.lua:606-623` | ignored for gift areas / resolved areas / own pending capture; suppressed with `unresolve_area` if a clause retry is pending for either player; **species-clause reroll** (`gui_prompt "Dupes clause: X -- reroll!"` + `unresolve_area`) if `species_lock` and `species_id` is in a family already held; else area → DEAD_ZONE, `LinkEntry(status=DEAD, cause="dead_zone")`, SE 26 + `msgbox "<Area> is a dead zone!"` to both, partner's pending capture gets `force_faint`+`memorialize`; `_handle_no_catch` (`state.py:3088-3239`) calls `_check_game_over` (`state.py:4698-4731`) | `species_id`/`level` MUST be sent: without `species_id` the reroll can never fire and a legitimate dupe encounter dead-zones the area. The client MUST mark the area resolved locally before sending (`lua/gen3/client.lua:619-622`) and un-mark on `unresolve_area`. |
| `whiteout` | — | — | a native `whiteout` signal settled `lua/gen3/client.lua:1308-1311` | plan rebuild from boxed alive pairs (`party_mon`s + `rebuild_start` to self, `party_mon`s + `hud_show` to partner), then for every ALIVE link whose half is in `party_keys[pid]`: partner `force_faint`, DEAD/`cause="whiteout"`, `memorialize` both; `party_keys[pid].clear()`; `hud_show "[x] PC empty"` + `game_over` both if nothing to rebuild `state.py:3241-3331` | server.py zeroes all `party_details` hp `server.py:2469-2476`. |
| `party_to_box` | `key` | `stats:dict` | a party key that left outside battle and is found in a complete PC census (`lua/gen3/client.lua:502-535`); the archived reference client waited five frames and required HP>0 | cache stats, `party_size -= 1`, discard key; if key is an ALIVE link and partner's half is in their party (or partner hasn't hello'd) → cancel pending `party_mon` for it, queue `box_mon` to partner, discard from partner's `party_keys` `state.py:3333-3379` | MUST NOT be sent for a client-commanded move. The current client suppresses native trade changes once its operation posts (`lua/gen3/client.lua:646-660`); the archived reference client used a separate `sync_written_keys` map.. |
| `box_to_party` | `key` | `area_id`, `nickname` | known key reappeared in party during a PC settle (`lua/gen3/client.lua:502-535`); the archived reference client used a five-frame buffer | rebuild path confirm; quarantine enforcement (`box_mon` + `hud_show "[!] X: unlinked"`); dead/memorial → `memorialize` + `hud_show`; partner party logically full (≥6 counting pending `box_mon`s) → `box_mon` back + `hud_show "[!] X: re-boxed"`; else add key, queue `party_mon{key,nickname?,stats?}` to partner (cancelling pending `box_mon`) `state.py:3381-3487` | Partner's key is **not** added to `party_keys` until their `sync_retrieve_done`. |
| `release` | `key` | — | a PC release of the player's own mon, from the box or the party (Gen 1 boxed RELEASE, Gen 2/3 `pc_release`) | owner ruling O-35: `party_keys.discard`; ignored unless nuzlocke active and `key` is the SENDER's half of an ALIVE link; else `_propagate_faint(cause="release")` (partner `force_faint` + memorialize), and the released key gets no memorialize (the mon no longer exists). Held like `faint` while a trade names the key. | Gen 3 resolves the unique removed key from complete native `pc_release_begin`/`pc_release` snapshots and queues it durably until its server response. |
| `key_change` | `old_key`, `new_key` | `reason:str`, `new_species:int`, `new_nickname:str` | `settle_trade` (`lua/gen3/client.lua:540-558`) reports an in-game NPC trade slot-swap, `reason="npc_trade"` only; grepping the current client turns up no distinct RR Nature Changer signature-matcher, so that trigger the old (reference) client had is not currently reachable | **validate → accept \| reject → mutate** (`_handle_key_change`). Accept: migrate `old→new` in `_key_index`/MonInfo (incl. `encounter_a/b`), pending captures, `party_keys`, `mon_stats`, `bonus_keys`, `pending_memorials`, queued commands (`key` and `old_key` fields) + in-flight ids, `pending_bonus`, `partner_blobs`, `rebuild_pending`, `pending_trade`, then the presentation caches (`party_details`, `_mon_cache`, `pc_boxes`); update `species`/`nickname` if given; reply `key_change_ack{migrated:true}`. **Owner ruling 35:** for `reason="npc_trade"`, validate the resulting alive pair against the enabled species/evolution-family, type and gender clauses, excluding its own keys/entry from duplicate checks. A violation keeps the accepted identity and retires only that changed pair with `cause="npc_trade_clause"`: force-faint the received mon and its partner, then memorialize both. No other pair is retired and no capture area is reopened; this is distinct from identity-loss rejection. If the old key's link is already DEAD/MEMORIAL the death is re-queued under the new key (`force_faint` + `memorialize`, deduped). Replay (already migrated) or an `old_key` referenced nowhere: `key_change_ack{migrated:false}`, no mutation. Reject when `new_key` is load-bearing — a different ALIVE link, or present in any live structure of either player or in a party / non-memorial box of the presentation caches — with `key_change_rejected{reason}` and **nothing migrated**; the old key's link is then retired `cause="identity_lost"` (partner `force_faint`, both memorialized). A DEAD/MEMORIAL index hit is accepted (buried keys are reusable; `KEY COLLISION` logged), and so is a key in any memorial box, primary or overflow. **KEY-SCOPE-5:** a same-mon tick that beat its `key_change` is recognised from the raw party key multiset of the latest snapshot (entries without a blob included): `new_key` exactly once, `old_key` absent, and `new_key` first seen only after `old_key` was last seen (a `new_key` present beside `old_key` is another mon). The box check uses the newest complete census of a client whose foundation implements `pc_boxes_generation` (Gen 1/2 and vanilla Gen 3/RR: the adapter's `reports_box_census`), or of one that sent a generation. A snapshot without a generation, any box-mutation event (`party_to_box`, `box_to_party`, `stats_cache`, `release`, `memorialize_done`), or a `safe` without a census makes it stale until a newer generation arrives. A missing or stale census refuses the change with `reason:"box census unavailable"`, which retires nothing (uncertainty is not a collision). A foundation that does not advertise this capability and never sends a generation keeps the presence-based check. Expansion explicitly returns false until its client is admitted. FR/LG, Emerald and RR require the matching stamped client: an older client without the stamp gets `box census unavailable` on key changes; the server and client must land together. A latch in `ambiguous_keys` on `old_key` **or** `new_key` refuses the change (`key_change_rejected{reason:"ambiguous key (trade clash)"}`, pair not retired). Accepted migrations go to a persisted per-player ledger (newest 256): a replay of any ledgered `{old_key,new_key}` acks `migrated:false` even after a reconnect hello re-reported `old_key`, unless a LIVE link is indexed under `old_key` again (then it is a new mon and the change is judged normally). The drift reconciler (never a hello, which is the cartridge's own word) counts a stale tick's retired alias as its terminal key, only when the terminal key is tracked in the party, absent from that tick, and sat in the alias's slot. | `reason` vocabulary: `nature_change` (default when absent; Gen 3 never sends it), `evolution`, `npc_trade`, `trade_undo`, `transform`, `apex_chip`; unknown values are still accepted. Only server.py's event-feed text branches on it (`new_species is not None`→"evolved", `reason=="trade_undo"`→"trade reverted", else "nature/ability changed"). **MUST NOT** be sent for a trade (§6.5). A client that keeps an old→new alias until acknowledged drops it on `key_change_ack` and reverts to the old key on `key_change_rejected`. |
| `trainer_battle_start` | `trainer_id:int`, `battle_id:int`, `session:hex` | — | in battle, non-wild, non-borrowed, once per battle after an identified trainer battle or pre-battle announcement `lua/gen3/client.lua:575-604` | if `trainer_id ∈ adapter.rival_trainer_ids()` and `rival_team_swap` and partner has blobs → `replace_rival_team` `state.py:4591-4646` | `trainer_id` MUST be a JSON int > 0 (`isinstance(int)` check). `session` + `battle_id` are the client's battle request identity (card C5-10): `session` is a nonce minted once per client PROCESS, `battle_id` a counter incremented on every battle-begin signal. The server stores the pair (with `trainer_id`) and echoes it on every command belonging to that battle. Both are OPTIONAL on the wire — Gen 1, Gen 2 and old Gen 3 clients never send them, and the server then stores nothing and echoes nothing. A malformed pair (a boolean, fractional or out-of-range counter; a non-hex or >16-char nonce) is a protocol violation, never coerced away. The NEW Gen 3 client REQUIRES both on `replace_rival_team`. The nonce is what makes a client restart safe: a restarted client's counter 1 is not the previous session's counter 1. |
| `rival_team_replaced` | `trainer_id:int`, `species_ids:list[int]` | `error:str` | after `replace_rival_team` settles `lua/gen3/native.lua:1025-1106`, `lua/gen3/client.lua:1500-1567` | `error` ⇒ `hud_show "Rival Swap failed: <error>"`; else log `state.py:4648-4674` | Gen 3 error values: `not_in_battle`, `writes_paused`, `patch_required`, `patch_failed`, `patch_timeout`, `stage_failed`, decode messages, and `stale_battle_id` (the command's `session`/`battle_id` is missing, malformed, from another client session, or not the battle the client is in — nothing was written). |
| `stats_cache` | `key`, `stats` | — | before deposit `lua/core/deferred.lua:159-168` | §2.5 | |
| `sync_retrieve_done` | `key` | — | after `party_mon` succeeded **or** the mon was already in the party `lua/core/deferred.lua:175-178`, `1740-1741`, `2490`, `2505` | `party_keys.add`; rebuild bookkeeping, `rebuild_done` when complete `state.py:608-616`, `state.py:3823-3838` | ACK for `party_mon`. |
| `sync_retrieve_failed` | `key` | — | party full after 3 retries / no stats / write failed `lua/core/deferred.lua:179-188`, `1726`, `1745`, `2508` | discard key; drop from rebuild; **re-box the partner's linked half** (`box_mon` + `hud_show "[!] X: re-boxed"`) `state.py:617-644` | NACK for `party_mon`. |
| `box_mon_failed` | `key` | `reason:str` | RESOLVED: the current client DOES send it, via the shared `box_mon` executor (`lua/core/deferred.lua:164-166`) | restore key to `party_keys`, `party_size += 1`, `_save` in `handle_event` `state.py:645-658` | ⚠ DISAGREEMENT, historical: the old (reference) client never sent this and only logged the failure, leaving the server's party model one mon short. A new client MUST send it on deposit failure (now true of the current Gen 3 client). |
| `memorialize_done` | `key` | `box:int` | after the mon is in the memorial box `lua/core/deferred.lua:196-200` | discard from `pending_memorials`+`party_keys`; when both halves are done → `LinkStatus.MEMORIAL` + `memorial.json` `state.py:4400-4423` | `box` is ignored by the server. |
| `memorialize_failed` | `key` | `reason:str` | Lua path failed `lua/core/deferred.lua:208-210` | treated as done for pair-status purposes `state.py:4425-4451` | |

| `trade_request` | — | — | talk to ghost / PC-NPC when no UI or trade in flight `lua/gen3/native.lua:1245-1265` | §6.1 | Silently ignored while a trade is pending. |
| `menu_result` | `token:str`, `choice:int` | `withdraw:bool` | poll of `show_menu`/`show_choices` `lua/gen3/native.lua:924-952`, immediate cancel `lua/gen3/native.lua:924-952` | §6.2/6.3 | `withdraw:true` from the initiator (GB native: its cartridge left the offer) cancels in `confirming`/`preparing` and is a certain `none` in `applying`; a bare `choice` from the initiator is ignored there. |
| `apply_ready` | `token:str`, `ok:bool` | — | answer to `apply_prepare` | §6.1 `preparing` | `ok` only when the side can still take its APPLY (GB: visit accepted, same slot, cartridge still waiting). |
| `mon_chosen` | `token:str`, `slot:int` | — | poll of `choose_mon` `lua/gen3/native.lua:953-959`, immediate cancel `lua/gen3/native.lua:953-959` | §6.2 | `slot` 0-5; anything else = cancel (Gen 3 uses 7). |
| `trade_done` | `new_key:str`, `new_species:int` (or `token:str`, `uncertain:true`: GB native commit entered, no DONE; the side's next party snapshot decides; with `after_reset:true` (a native result 2, held until a reset) only its next `hello` counts) | `token:str`, `slot:int` | after the journaled native trade result or its proved-unchanged refusal `lua/gen3/trade.lua:149-273` | §6.4 | Empty `token` accepted for compat; a wrong token is ignored. |
| `status` | `badges:int` **count 0-8** | — | **not currently sent by the Gen 3 client** — no `send("status", ...)` anywhere in `lua/gen3/*.lua` (grepped clean); `badges()` at `lua/gen3/client.lua:248-252` only feeds the `hello`/`tick` bitmask field | `player_badges[pid]` (SoulLinkState) clamp 0-8 `state.py:776-784` | ⚠ Different from `hello`/`tick.badges` (bitmask, SLinkServer). The wide `link_panel` "Badges" row reads this count (`server.py:3006`); the 20-column layout popcounts the bitmask instead (`server.py:3032-3051`). A11-style gap: with no `status` sender, the wide layout now reads 0/8 for every Gen 3 run (see §8 row 10, A12). |
| `ghost_pos` | `mg,mn,x,y,f,gfx,mv,run,an:int`, `imgs,anim:int`, `pcol:hex[≤64]` | — | **not currently sent by the Gen 3 client** (peer ghost is deferred post-RC for Gen 3); `C.ghost_pos` only handles the incoming *command* (relay from the partner), it is not a sender (`lua/gen3/client.lua:1799`) | relayed to partner as `ghost_pos` cmd, coalesced to one in queue; dropped when `overworld_presence` off `state.py:717-750` | Non-int field ⇒ sample dropped. The old client's `x,y`-units comment mismatch no longer applies since the current client sends nothing to compare. |
| `peer_interact` | — | — | legacy, not sent by Gen 3 | `msgbox "<name> says hey!"` to partner if presence on `state.py:752-759` | |

Fields the server reads from **enrichment-only** paths (server.py, not state.py): `capture.hp/level/maxHP/nickname/species_id/held_item_id|held_item/ability_id|ability` → `party_details` (`server.py:2440-2481`); `faint.key` → `party_details[key].hp=0` (`server.py:2440-2481`).

---

Gen 3 eggs follow O-15: GiveEgg and boot/PC observation do not publish a capture. The native `hatch` completion signal snapshots the completed non-egg party record and publishes exactly one `capture` with `gift=true`, `is_egg=false`, `area_id="gift_daycare"`, and full party `stats`, using the hatchling's key. The ordinary gift namespace hook handles linking; native egg receipt itself creates no pair. See `docs/gen3/research/gift_egg_rows_2026-09-27.md` for title pins and evidence boundaries.

### 3.3 PC RELEASE (owner ruling O-35)

Releasing a linked mon from the PC counts as losing it: on `release{key}` the server kills and memorializes the partner like a faint (section 3.2 row `release`), which closes the former S-6 gap where the pair stayed ALIVE with a phantom boxed half. Gen 1 sends `release{key}` for a boxed Bill's PC RELEASE, distinguished from WITHDRAW; Gen 2 and Gen 3 send it for a party or box `pc_release` without classifying release as `party_to_box`. Gen 3 takes a complete identity census at the native entry before purge, pairs it with the completion in the same invocation, and requires exactly one disappearance. An unreadable or ambiguous census emits no guessed key. The report uses the trade outbox's response-bound replay on reconnect. A moving-cursor release without a party/box preimage remains unqualified. The server ignores an unlinked key.

### 3.4 `whiteout` → rebuild sequence

The client can emit the final per-mon `faint` followed immediately by one `whiteout`, before `battle_end` and the engine's blackout/heal path; the blackout hook only supplies `whiteout` if it has not already been sent (`lua/gen1/client.lua:605-618`, `:681`). Do not require a `whiteout` death cause on links already retired by their preceding faint events.

1. `_handle_whiteout` plans against surviving ALIVE links whose two halves are boxed. It excludes pending/unlinked captures (`state.py:3711-3738`, `_alive_pc_mons`) and caps picks by the partner's available party room (`state.py:3740-3767`, `_plan_rebuild`).
2. For the whited-out player, enqueue `party_mon` for each chosen key, then `rebuild_start{text,keys}`; for the partner, enqueue corresponding `party_mon` commands then the informational `hud_show` (`state.py:3769-3821`, `_queue_rebuild_commands`). These are per-player queues, not a globally ordered cross-socket stream.
3. Rebuild retrievals are queued before any additional force-faint/memorial commands produced by this whiteout handler (`state.py:3263-3278` queues the rebuild before `state.py:3280-3302` force-faints and memorializes). Earlier per-mon faints may already have queued memorials. Gen 1 appends a last-mon-blocked memorial to the deferred tail so a later retrieval can unblock it (`lua/gen1/client.lua:552-557`).
4. At each cartridge's safe write checkpoint, `party_mon` yields keyed `sync_retrieve_done` or `sync_retrieve_failed` (`lua/gen1/client.lua:546-547`). The server adds confirmed keys, records rebuild completion, or drops failed keys and may re-box the partner (`server/state.py:608-644`).
5. Once every queued key for that player's rebuild has resolved, send `rebuild_done` and clear `rebuild_pending[player]` (`state.py:3823-3838`, `_maybe_finish_rebuild`). This banner completion is not itself a bilateral barrier: physical proof requires both sides' retrieval acknowledgements and saved-state readback (`prep/PLAN_v3.9.md:494-505`).

With no rebuildable pair, the game-over path applies; the whiteout handler handles retired party links with no picks and also invokes the shared game-over check (`state.py:3309-3331`). Rebuild does not resurrect DEAD/MEMORIAL links. A release-created phantom boxed half remains a separate limit, not a guaranteed rebuild candidate on the cartridge.

## 4. Snapshot shapes

### 4.1 Party entry (element of `party` in `hello`/`tick`/`safe`)

Built by `party_wire`/`party_entry` (`lua/gen3/client.lua:297-316`, fields at `:299-303`).

| Field | Type | Required | Server consumer | Cite |
|---|---|---|---|---|
| `key` | key | **MUST** | `_handle_hello` populates `party_keys` (`m["key"]` guarded by `m.get("key")` — entries with missing/falsy keys are skipped), `_reconcile_party_keys`, blobs, `party_details` (skipped if falsy) | `state.py:2170-2172`, `state.py:3608-3707`, `server.py:2371-2392` |
| `maxHP` | int | MUST | hello: only `maxHP > 0` entries count as party members; HP bars | `state.py:2171`, `server.py:2891-2911` |
| `hp` | int | MUST | hello offline-faint detection (`hp == 0`), alive set for re-quarantine; HP bars | `state.py:2184`, `state.py:2209-2233` |
| `level` | int | MUST | `partner_blobs.level`, display back-fill, `_resolve_level`, killfeed level | `state.py:4506`, `server.py:5348-5367` |
| `slot` | int 0-5 | SHOULD | `partner_blobs.slot` (trade `apply_trade.slot`), party ordering (`999` fallback) | `state.py:4504`, `server.py:5338-5346` |
| `species_id` | int (game-internal) | SHOULD | display back-fill into MonInfo, blobs, sprites, names, types | `state.py:2283-2299`, `server.py:2891-2911` |
| `nickname` | str | SHOULD | MonInfo back-fill, HUD labels, dashboard | `state.py:2279-2302` |
| `active` | bool | SHOULD (battle) | active-battler marker, `stat_stages` shown only when true, doubles inference on foes | `server.py:3438-3465`, `server.py:3438-3465`, `server.py:3438-3465` |
| `status_cond` | int (Gen 3 `status1` layout) | SHOULD | `status_pill` macro (dashboard) and `adapter.status_token` (`link_panel`) | `server.py:3438-3465`, `server.py:3438-3465`, `server/templates/_macros.html:43-53` |
| `stat_stages` | list[7] of int 0-12, 6 = neutral, order ATK,DEF,SPD,SATK,SDEF,ACC,EVA; `nil`/absent when not active | optional | `stat_stages_row(stages, labels)` macro — `(raw\|int)-6` | `server/templates/_macros.html:64-83`, `server.py:3438-3465`, `lua/gen3/reads.lua:675-687` |
| `moves` | list[4] int move ids | optional | `move_details` via `adapter.move_data` | `server.py:2856-2889` |
| `pp` | list[4] int | optional | `current_pp` | `server.py:2856-2889` |
| `pp_bonuses` | int (2 bits/move, Gen 3) **or** `pp_ups: list[4]` (Gen 4) | optional | max PP scaling `base + base*ups//5` | `server.py:2856-2889` |
| `held_item_id` (legacy alias `held_item`) | int | optional | `adapter.item_name` | `server.py:2891-2911`, `server.py:2891-2911` |
| `ability_id` (legacy alias `ability`) | int | optional | `adapter.ability_name` (hidden when `!supports_abilities()`) | `server.py:2891-2911`, `server.py:2891-2911` |
| `form` | int | optional (Gen 4+) | sprite form | `server.py:2891-2911` |
| `blob_hex` | hex, **exactly `adapter.party_blob_size()*2` chars** | MUST for trade / rival swap | `_ingest_party_blobs` — wrong length or non-hex ⇒ entry silently dropped from `partner_blobs` ⇒ that mon is never trade-eligible and rival swap says "no cached party blobs" | `state.py:4453-4512`, `base.py:235-249` |

Gen 3 does **not** send `ot`, `nature`, `gender` or `pp_ups` in the party entry; `gender` is derived server-side from `adapter.gender_from_key(key, species_id)` (`server.py:2891-2911`).

### 4.2 `tick` top-level fields (beyond `party`)

| Field | Type | Meaning | Consumer |
|---|---|---|---|
| `has_pokeballs` | bool | nuzlocke gate; only `True` has an effect | `state.py:674` |
| `ball_count` | int | dashboard | `server.py:2503-2505` |
| `area_id`, `loc_name` | str | current area / display location | `server.py:2498-2512` |
| `in_battle` | bool | battle edge detection; `false` clears `trainer_id/opponent_*/enemy_party/is_doubles` | `server.py:2519-2529` |
| `is_trainer_battle` | bool | wild vs trainer; suppresses dupes check | `server.py:2530-2531`, `server.py:2557-2573` |
| `trainer_id` | int | `adapter.trainer_info(tid)` → opponent name/class; if the adapter returns no class, `opponent_name`/`opponent_class` from the tick are accepted instead | `server.py:2532-2547` |
| `opponent_name`, `opponent_class` | str | non-RR fallback for trainer display and killfeed | `server.py:2532-2547` |
| `enemy_party` | list[FoeEntry] (§4.3); `[]` when not in battle | sanitised on EVERY inbound message before dispatch (non-list ⇒ `[]`, non-dict entries dropped); then battle panel, killer enrichment, dupes check (`[0].species_id`) | `server.py:2166-2169`, `server.py:2548-2556`, `server.py:2557-2573` |
| `is_doubles` | bool | doubles chip; if absent, inferred from >1 `active` foe | `server.py:2550-2556` |
| `pc_boxes` | list[BoxEntry] (§4.4), full cache every tick | box table, memorial contamination scan, `_mon_cache` | `server.py:2512-2518` |
| `badges` | int bitmask | 8 gym circles, badges overlay, compact panel popcount | `server.py:2506-2507`, `server.py:2970`, `server.py:1945-1970` |
| `kanto_badges` | int bitmask | second-region badges (Gen 4) | `server.py:2508-2509`, `server.py:2971` |
| `trainer_name` | str | dashboard | `server.py:2510-2511` |
| `trade_blocked` | bool | the cartridge refuses any trade right now (Gen 2: the Bug-Catching Contest party mask); while set, neither player has an eligible trade pair | `state.py` `_eligible_trade_pairs` |
| `awaiting_save` | bool | a GB client's boxed burial waits on an in-game SAVE before it acks `memorialize_done` (BOX-MEMORIAL-2); the pair board shows "awaiting an in-game SAVE" on that player | `state.py` `awaiting_save`, `_board.html` |

`money` and `frame` are **not** part of the protocol (nothing sends or reads them).

### 4.3 Foe entry (element of `enemy_party`)

Gen 3 builds it from `r.read_enemy_party()` (`lua/gen3/reads.lua:657-674`) overlaid with live `gBattleMons` data (`enemy_wire`, `lua/gen3/client.lua:317-333`).

| Field | Type | Consumer | Cite |
|---|---|---|---|
| `species_id` | int | name, sprite, killer species, `/api/calc/mons` entry | `server.py:2925-2944`, `server.py:3196-3270` |
| `level` | int | display, killer level, calc entry | `server.py:3196-3270` |
| `hp`, `maxHP` | int | HP bar; "active foe" for killer/calc preview = first with `hp > 0` | `server.py:3196-3270` |
| `active` | bool | active marker, doubles inference, calc preview | `server.py:3196-3270`, `server.py:2552-2556` |
| `ability_id`, `held_item_id`, `status_cond`, `stat_stages`, `moves`, `pp`, `form`, `key` | as §4.1 | battle panel enrichment, calc entry | `server.py:3196-3270` |
| `dvs_raw` | int, raw 16-bit DV word | Gen 1 only (`lua/gen1/client.lua` `enemy_party`); every battle, wild included. `Gen1Adapter.calc_stats` decodes DVs from it when no `blob_hex` is present (a wild mon has no party record) | `lua/gen1/client.lua` `enemy_party`, `server/adapters/gen1_rby.py:475-481` |
| `blob_hex` | hex, 44 bytes | Gen 1 only, **trainer battles only**: the active mon's party record (`wEnemyMons + PartyPos*44`), carrying stat exp `Gen1Adapter.calc_stats` needs that the live battle struct doesn't have | `lua/gen1/client.lua` `enemy_party`, `server/adapters/gen1_rby.py:460-474` |

### 4.4 Box entry (element of `pc_boxes`)

Built by `rescan_boxes` (`lua/gen3/client.lua:335-351`, entry at `:338-343`): the client rescans every box on trigger events (baseline, post-trade, post-battle, hello) rather than a few per tick, and always sends the **full accumulated cache** (`pc_boxes_wire`, `lua/gen3/client.lua:356-364`).

| Field | Type | Consumer | Cite |
|---|---|---|---|
| `box` | int, 0-based box index | memorial contamination (`box == adapter.memorial_box_index`), display `box+1` | `server.py:5237-5336` |
| `slot` | int, 0-based | display `slot+1`, logs | `server.py:5237-5336` |
| `key` | key | `_cache_mon_info`, dead-in-regular-box re-memorialize (`_check_memorial_box_contamination`), level fallbacks (`_resolve_level`) | `server.py:2007-2053`, `server.py:5237-5336`, `server.py:5348-5367` |
| `species_id`, `nickname` | int, str | display | `server.py:2913-2923` (`_enrich_box`, wire-through to the template) |
| `level` | int | optional; falls back through `mon_stats` → link entry → `party_details` → `_mon_cache` | `server.py:5348-5367` |
| `held_item_id`, `ability_id`, `moves` | | box table | `server.py:2913-2923` |

There is **no** "active box index" on the wire.

### 4.5 What the status builders read (adapter-relevant summary)

| Builder | Reads | Adapter calls |
|---|---|---|
| `_build_status_dict` `server.py:2837-3126` | `connected_players`, `player_area(_id)`, `ball_count`, `badges`, `kanto_badges`, `trainer_name`, `pc_boxes`, `party_details` (ordered by `slot`), `battle_state`, `identity_error`, `admission`, links/killfeed/pending/bonus | `species_name`, `sprite_html(sid, form)`, `ability_name(aid, sid)`, `move_data`, `area_display_name`, `gym_badge_slugs(rom_type)`, `encounter_table` + `sprite_src` via `adapter_for(pid)` |
| `_handle_dashboard_template` `server.py:3146-3163` | the dict above; per mon: `nickname, species_id, gender, sprite_html, active, level, held_item_id, ability_name/id, move_details, hp, maxHP, status_cond, stat_stages` | `supports_abilities`, `stat_stage_labels` (`server/ui_capabilities.py:26-30`), `gender_from_key`, `item_name`, `ability_description`, `species_types`/`type_name`, `memorial_box_index`, `trainer_info` |
| `_build_link_panel` `server.py:1886-1994` | links, `party_details` (`species_id, nickname, level, hp, maxHP, status_cond`), `_mon_cache`, `area_states`, `SoulLinkState.player_badges` (count) or `SLinkServer.player_badges` (bitmask) | `area_display_name`, `species_name`, `status_token`, `info_panel_width`, `supports_info_panel` |
| `_build_party_overlay_context` `server.py:3298-3346` | `party_keys` order, `party_details` `hp,maxHP,species_id,species_name,nickname,level,sprite_html,status_cond,stat_stages,active` | — |
| `_build_badges_overlay_context` `server.py:3730-3750` | `badges` bits 0-7, `kanto_badges` bits 0-7 for slugs 8+ | `gym_badge_slugs` |
| `_check_memorial_box_contamination` `server.py:5237-5336` | `pc_boxes[].box/key/nickname/species_id/slot` | `memorial_box_index`, `species_name` |
| `_memorial_box_indices` `server.py:5122-5141` | dead count | `memorial_box_index`, `mons_per_box` |

`status_pill` (`server/templates/_macros.html:43-53`) decodes `status_cond` with the Gen 3 bit layout directly (SLP bits 0-2, TOX 0x80, PSN 0x08, BRN 0x10, FRZ 0x20, PAR 0x40). A client for a generation with a different layout MUST translate to this layout on the wire (see §8).

---

## 5. Server → client commands

Every command is a JSON object with `cmd`. Fields are listed exhaustively. "Obligation": **immediate** = act on receipt; **deferred** = the client MUST hold it until its own safe-state predicate is true (Gen 3: overworld-safe and no PC trade in flight — `drv.checkpoint_ok`/`overworld_ok`, `lua/gen3/client.lua:1033-1044`) and execute in FIFO order, one per frame (`Deferred:run` pops one item per call — `lua/core/deferred.lua:116-130` — driven once per frame from `lua/core/session.lua:427-436`). Colour fields `r,g,b` are 0-255; `frames` is a display duration at 60 fps (client default when absent: 240 for `hud_show`, 300 for `msgbox`/`gui_prompt` — `lua/hud.lua:301`, `:327`).

| `cmd` | Fields | Obligation | ACK event | NACK event | May drop? | Gen 3 handling | Server follow-up / cite |
|---|---|---|---|---|---|---|---|
| `noop` | `refused:str` (optional) | ignore. `refused` = `identity`\|`admission`\|`no_hello`\|`duplicate`\|`load_failed`\|`error`\|`malformed`\|`party_snapshot`\|`superseded` (KEY-SCOPE-5: a newer connection has helloed as this player, so this connection's later lines are dropped) marks a reply to a line the server did NOT process; a client must not treat it as the acknowledgement of that line (an owed report goes out again). Absent on every processed line. | — | — | yes | `:1037` (silent) | `state.py:715`, `server.py:1877-1880` |
| `force_faint` | `key`, `nickname` | **immediate** if the mon is benched/out of battle (write HP=0, suppress the resulting local faint); **deferred until switch-out or battle end** if it is the active battler — this is the reference (old) client's behaviour (line 11). **On the new Gen 3 client, FR/LG singles instead apply this immediately, no held window, via mechanism P+H** (engine Perish KO + controller hand-off; doubles still hold) (owner rulings 15-16, `docs/gen3/G4_request_draft.md` §6, 2026-09-23/24; built `1b3943e3`/`39bcc4f8`/`66595498`/`9719b519`/`375cb963`/`cdc571f1`, review fixes `4a91daeb`/`9e227101`); RR gets parity at G5 | none (server already marked the pair DEAD). No wire change, but a lost one is repaired server-side for every generation (owner ruling O-24, `state.py _repair_lost_faints`): a tick `party` entry at `hp > 0` whose link is not ALIVE gets `force_faint` again, at most once per 120 out-of-battle reconciler passes (~60 s; Gen 1/2 hold a delivered faint out of battle until their overworld checkpoint, so a short window would fire on a player merely in the PC or a menu), 3 times per incident, then it stalls (error log + status `faint_repair_stalled`) for 600 passes (~5 min) and the budget refills. The client must treat a repeat as idempotent. The repair is always plain `force_faint`, even in Explode Mode (both leave the mon at zero HP). Limit: it sees only the PARTY snapshot, so a dead mon deposited in a box before its faint landed, or a key whose `key_change` was rejected, cannot be repaired this way | none | **no** | `lua/gen3/client.lua` `force_faint` | `state.py:3543-3606` (partner half), rejections `state.py:3543-3606`, DZ `state.py:3543-3606`, whiteout `state.py:3543-3606` |
| `force_explode` | `key`, `nickname` | as `force_faint`, but an active battler is coerced into Explosion (RR); bench = immediate HP=0. **On the new Gen 3 client, RR's Explode keeps its hold** (`battle.commit_hold`) **but its commit plan now ends in the same `battle.handoff` tail as P, so it fires immediately again without a press, as on the old RR client** (owner ruling 19, `docs/gen3/G4_request_draft.md` §6 item 19, 2026-09-24; G5) | none | none | no | `lua/gen3/client.lua` `force_explode` | only when `explode_mode && adapter.supports_explode_mode()` `state.py:4301-4316` |
| `box_mon` | `key` | deferred; idempotent (no-op if the key is already boxed or unknown); MUST refuse to deposit the last party mon | `stats_cache` before the write (informational) | **`box_mon_failed{key,reason}`** on failure | no | `lua/core/deferred.lua:159-171` | server pre-discards the key and decrements `party_size` at queue time (`state.py:3345-3352`, `state.py:3373-3375`); cancels an opposing pending `party_mon` `state.py:3368-3372` |
| `party_mon` | `key`, `nickname?`, `stats?` (`{level,maxHP,attack,defense,speed,spAtk,spDef,pp1..pp4?}`) | deferred; idempotent (already in party ⇒ still ACK) | **`sync_retrieve_done{key}`** | **`sync_retrieve_failed{key}`** (party full after retries, no stats, write failed) | no | `lua/core/deferred.lua:172-191` | server adds the key to `party_keys` only on ACK (`state.py:608-616`); on NACK re-boxes the partner's half (`state.py:617-644`) |
| `memorialize` | `key` | deferred; move the (dead) mon to the memorial box; MUST NOT empty the party — block until a `party_mon` lands, or drop if `game_over` was received | **`memorialize_done{key,box}`** | **`memorialize_failed{key,reason}`** | only when game over and it is the last party mon (`state.py:4381-4392`) | `lua/core/deferred.lua:192-211` | `state.py:4381-4392`; re-queued on every hello until acked (`state.py:2253-2259`) |
| `game_over` | — | immediate: persistent HUD, sound; also unblocks dropping the last-mon `memorialize` | none | none | no | `lua/core/session.lua:299-303` | `state.py:3320-3324`, `state.py:4727-4731` |
| `msgbox` | `text`, `fb?` (`"prompt"`), `r?,g?,b?,frames?` | immediate: native in-game box when safe, else centre prompt (`fb=="prompt"`) or HUD line | none | none | yes (display only) | `:897-900`, `try_native_box :705-719` | 17 sites in state.py (links, dead zones, shiny, trade texts) |
| `gui_prompt` | `text`, `r,g,b,frames` | immediate: momentous overworld prompt (dupes/clause reroll); native box when safe else centre prompt | none | none | display only | `lua/core/session.lua` `gui_prompt` | `state.py:2749-2759` |
| `hud_show` | `text`, `r?,g?,b?,frames?` (legacy alias: `color:[r,g,b]`, `duration`) | immediate HUD line | none | none | display only | `:971-972` | 14 sites in state.py + `:910-915` |
| `play_sound` | `sound:int` (Gen 3 SE id: 25 SE_SUCCESS, 26 SE_FAILURE, 22 SE_BOO, 95 SE_SHINY). Gen 1 maps 25/95→1, 26→2, 22→3 and writes the code to the companion mailbox `+7` when `config.native_sounds` and the cartridge's `sfx` capability both hold (`lua/gen1/panel.lua request_sfx`; the ROM picks the per-bank sound) | immediate | none | none | yes | `lua/core/session.lua` `play_sound`; Gen 1 `lua/gen1/client.lua` `play_sound` | `state.py:2461-2470` |
| `resolved_areas` | `areas:list[str]` | immediate: mark each area resolved locally; set the "seeded" flag (hello reply carries it even when empty) | none | none | no | `lua/core/session.lua:285-288` | `state.py:2304-2322` |
| `unresolve_area` | `area_id` | immediate: clear the local resolved mark so the encounter is available again | none | none | no | `lua/core/session.lua:289-290` | `state.py:2981-2987` |
| `dead_keys` | `keys:list[str]` (this player's DEAD/MEMORIAL link keys) | immediate: the GB clients REPLACE their dead-key re-zero set and resume the re-zero sweep (held after a reset, reload or identity change until this sync); sent in every accepted hello reply | none | none | no | Gen 1/Gen 2 `client.lua` `dead_keys`; Gen 3 ignores it | `state.py _handle_hello` |
| `config` | `overworld_presence, native_messages, native_sounds, battle_calc, pc_trade_npc : bool`; opted-in recovery clients also receive `run_id:str` | immediate; every hello reply; existing server run ID, empty if unmanaged/unbound (the client must require a nonempty identity before journal use); omitted for other adapters | none | none | no | `state.py` `_handle_hello`; `server.py` `_with_run_identity` | informational configuration; never evidence of native save/reload |
| `rebuild_start` | `text`, `keys:list[key]` | immediate: show persistent REBUILDING banner | (completes via the `party_mon` ACK/NACKs) | | no | `state.py:3807-3817` | `lua/core/session.lua` `rebuild_start`
| `rebuild_done` | — | immediate: clear banner | none | none | no | `lua/core/session.lua` `rebuild_done` | `state.py:3823-3838` |
| `replace_rival_team` | `trainer_id:int`, `n:int`, `blobs_hex:list[hex]`, `source:"auto"\|"manual"`, `session:hex?`, `battle_id:int?` | immediate, in battle only | **`rival_team_replaced{trainer_id,species_ids}`** | `rival_team_replaced{...,error}` | no (must NACK) | `lua/gen3/native.lua:1025-1106`, `lua/gen3/client.lua:1500-1567` | `state.py:4516-4589` |
| `show_choices` | `token`, `options:list[str]`, `text` | immediate: native multichoice; if impossible reply cancel at once | `menu_result{token, choice}` (0-based index) | `menu_result{token, choice:127}` | no (must reply) | `lua/gen3/native.lua:924-959` | `state.py:991` |
| `show_menu` | `token`, `text` | immediate: native YES/NO; if impossible reply `0` at once | `menu_result{token, choice}` (1 = yes) | `menu_result{token, choice:0}` | no | `lua/gen3/native.lua:924-959` | `state.py:1104-1106` |
| `choose_mon` | `token` | immediate: native party picker; if impossible reply cancel at once | `mon_chosen{token, slot}` (0-5) | `mon_chosen{token, slot:7}` | no | `lua/gen3/native.lua:924-959` | `state.py:1161`, `state.py:1161` |
| `withdraw_trade` | `token` | immediate: pull an APPLY the cartridge has not picked up | `trade_done{token, new_key:<old_key>}` (certain nothing changed), or `trade_done{token, uncertain:true}` past the commit boundary | (silence: the watchdog then awaits evidence) | no | only to clients that declared hello `trade_prepare`, from the `applying` watchdog | `state.py` `_tick_pending_trade` |
| `trade_final` | `token:nonempty str`, `verdict:committed` / `rolled_back` / `split` / `resolved`; optional `epoch:int` (1..4294967295) | immediate bookkeeping receipt for opted-in recovery clients; committed/rolled-back settlement, direct split, or explicit admin resolution; durable newest 256 final tokens per side | none | none | no | `state.py` `_remember_trade_final`, `_announce_trade_final`, `_queue_trade_final` | re-sent for known-final tokens in a recovery hello, echoing its normalized epoch after `config`; never clears hidden/hello-only barriers, supplies party evidence, or proves a native save; unknown/evicted tokens receive no receipt |
| `apply_prepare` | `token`, `slot:int`, `old_key:key` | immediate: can this side still take its APPLY? stage nothing | `apply_ready{token, ok:true}` | `apply_ready{token, ok:false}` | no | only to clients that declared hello `trade_prepare` | `state.py` `_prepare_trade` |
| `apply_trade` | `slot:int`, `blob_hex:hex`, `old_key:key`, `token` | deferred until the field is clear; locate the mon by `old_key` (slot is a snapshot), stage + run the trade scene, or fall back to a faithful slot write | **`trade_done{token, slot, new_key, new_species}`** | (none — `trade_done` with the pre-trade key is the "nothing changed" signal; the watchdog moves a silent side to `uncertain`, §6.6) | **no** | `lua/gen3/trade.lua:149-273` | `state.py:1336-1341` |
| `ghost_pos` | `mg,mn,x,y,f,gfx,mv,run,an,imgs,anim:int`, `pcol:hex` | immediate, render peer ghost | none | none | yes (ephemeral, coalesced) | `lua/gen3/client.lua:1799` | `state.py:717-750` |
| `link_panel` | `rows:list[str]` (`label\|name\|level\|hp\|barpx\|state\|status` or plain text; empty label = continues pair) | immediate: stage into native panel; sent only when content changed and `_player_has_panel(pid)` | none | none | yes | `lua/gen3/client.lua` `link_panel` | `server.py:1886-1994` |
| `pending_sync` | `message` | — | | | | `:855-856` | ⚠ **never emitted by the server** (dead client branch) |
| `key_change_ack` | `old_key`, `new_key`, `migrated:bool` | immediate: drop any old→new alias; `migrated:false` = the server had nothing under `old_key` (replay, or an unlinked mon) | none (one-way) | none | yes (Gen 3 ignores unknown commands, §1.2) | not handled | `_handle_key_change`, same reply as the event |
| `key_change_rejected` | `old_key`, `new_key`, `reason:str` | immediate: the server still knows the mon as `old_key`. `reason` `key collision: …`: the client MUST NOT re-send the change (the pair is being retired `identity_lost`; a `force_faint` for the partner and `memorialize` for both follow). `box census unavailable` or `ambiguous key (trade clash)`: nothing is retired, and the client keeps its alias and MAY re-send the change later. A Gen 3 client additionally treats `party hidden` (a withheld party, §2.1) as retryable on the same terms; it is a Gen 3-only reason and the Gen 1/2 clients never receive it. The Gen 1/2 clients re-send it after the next complete box census newer than the refusal, and a census refusal triggers that rescan at once | none (one-way) | none | yes | retryable reasons: the alias stays, no red HUD, and the SAME message is re-sent once a complete census newer than the refusal goes out — Gen 1/2 in their own `resend_refused_change`, Gen 3 through `lua/core/session.lua` (which also re-requests an incomplete scan once per tick, and only while such a refusal is armed, since Gen 3's census is otherwise only refreshed by PC activity). Gen 3's `party hidden` refusal arms the same credit, and only when the refusal names the exact `old_key`/`new_key` pair the session has pending (`lua/core/session.lua:304`); the retry goes out only on a tick that was actually published, after `hello`, with `party_hidden ~= true` (`lua/core/session.lua:374`), and only a published send spends the credit (`lua/core/session.lua:341`). A nil, hidden or disconnected tick therefore leaves the credit untouched: an INCOMPLETE scan is re-requested, while a complete-but-withheld census is carried to the next visible published tick. The re-send rides the tick that carries the newer census: a tick that went nowhere (`tick_fields()` nil) sends nothing. Every other reason retires the alias and shows the red HUD | `_handle_key_change`, same reply as the event |

There is no `hud` command; the name is `hud_show`.

Optional `phone:str` tag (O-29, `docs/gen2/reviews/P4_5_PHONE_CALLS_PLAN_2026-09-23.md` §3): the
partner's `force_faint`/`force_explode` from a battle death carries `"fallen"` (never an
`identity_lost` retirement), both dead-zone `msgbox`es carry `"dead_zone"`, and both "linked!"
`msgbox`es of the run's first two-sided link carry `"first_link"`. Gen 2 consumes it through
`lua/gen2/phone.lua` on a cartridge advertising `SLINK_CAP_PHONE`. The Emerald v2 consumer in
`lua/gen3/native.lua` uses `SLINK_CAP_MATCH_CALL` and a bound epoch; its production binding is
still held until the Emerald companion qualifies. FR/LG, RR, and unpatched clients ignore it.

Optional `phone_data:dict` (PHONE-NAMES), only beside a `phone` tag and relative to the receiver:
`{trainer_name:str, caller_mon?:{species_id:int, nickname?:str}, receiver_mon?:{species_id:int, nickname?:str}}`.
The caller is the other player. `fallen` carries the caller's fainted mon and the receiver's linked
mon, `first_link` the two newly linked mons, `dead_zone` the trainer name only. Each recipient gets
its own object. When the caller has no trainer name the server omits `phone_data`, and the
consuming Gen 2 or Emerald v2 client uses the fixed-text call.

### 5.0 Gen 1 LINK PANEL mailbox (Red/Blue companion patch)

`link_panel{rows}` is a server payload, not permission to write whenever it arrives. The client holds sanitized, pre-rendered pages; the cartridge owns the screen, whites it out, draws a fallback and requests staging (`lua/gen1/panel.lua:1-10`; the staging poll itself is shared, `lua/gb_panel.lua:198-236`). This is separate from the native SLINK TRADE overlay ABI.

The mailbox base is `$DEE2`: offsets `+0..3` are `SLNK`, `+4` ABI, `+8` capabilities (`CAP_PANEL = $02`), `+9` state (`CLOSED=0`, `AWAIT=1`, `STAGED=2`), `+10` requested zero-based page (patch → client), and `+11` page count (client → patch; zero means one to the patch). Presence requires the beacon and capability bit, not an ABI-number guess (`lua/gen1/panel.lua:13-35`; `self:present()`/`beacon()`, `lua/gb_panel.lua:97-106`).

Only an **observed non-AWAIT → AWAIT transition** arms a staging opportunity: CLOSED → AWAIT on open or STAGED → AWAIT on a page turn. First attachment to an already-AWAIT mailbox has unknown age and MUST NOT paint. A persistent AWAIT does not renew the deadline (`lua/gb_panel.lua:198-236`, the `tick` function).

Each page is 18 rows × 20 tiles (360 bytes), up to eight pages. The client accepts staging at elapsed frames ≤60 from its observed transition and refuses later staging; the patch's fallback timeout is 90 frames. The narrow `panel` write window permits only the title's `wTileMap` range plus state/page-count bytes (`lua/gb_panel.lua:73-79`, the `allow` predicate). Write all tiles, then page count, then publish STAGED **last** (`lua/gb_panel.lua:187-196`, `self:stage()`).

Rows arriving too late remain held for a future valid open/page transition, never paint over an already revealed fallback. The patch can retain AWAIT after timeout, so the client deadline is mandatory (`docs/gen1_requirements.md:160-162`). Yellow duo/trade/panel is outside this release's scope because the mailbox space is unavailable (`:157-158`); do not infer capability from generation alone.

### 5.1 Prompt token handshake

| Flow | Server sends | Client replies | Codes | Timeouts |
|---|---|---|---|---|
| Action menu | `show_choices{token, options:["Trade","Say hey"], text}` `state.py:991` | `menu_result{token, choice}` | `choice` = 0-based option index; **127 (0x7F) = cancel/B**; anything not 0 or 1 aborts silently `state.py:1112-1171` | client defaults to 1800 frames for a native operation, then cancels `lua/gen3/native.lua:176-182,924-959`; server watchdog §6.6 |
| Party pick | `choose_mon{token}` `state.py:1161`, re-prompt `state.py:1081` | `mon_chosen{token, slot}` | `slot` 0-5 valid; `<0` or `>5` = cancel (Gen 3 sends **7**) `state.py:1035-1056`; ineligible slot ⇒ `msgbox "Pick a linked POKeMON!"` + new `choose_mon` (same token), max 3 re-prompts then `msgbox "Trade canceled."` `state.py:1071-1082` | same |
| Yes/No confirm | `show_menu{token, text}` `state.py:1104-1106` | `menu_result{token, choice}` | `1` = yes/accept; **anything else = decline** (Gen 3 sends 0 on cancel/failure) `state.py:1172-1210` | same |

Tokens are `"t<N>"` (`state.py:974-975`). A reply with a non-matching token is ignored (`state.py:1040-1045`, `state.py:1113-1120`). Only the initiator's `menu_result`/`mon_chosen` count in phases `menu`/`choosing`; only the partner's `menu_result` counts in `confirming` (`state.py:1113-1120`, `state.py:1127-1201`). The client MUST reply exactly once per prompt, immediately with the cancel code when it cannot render the prompt (unpatched, in battle, another prompt in flight — `lua/gen3/native.lua:924-959`).

---

## 6. Trade sub-protocol

Radical Red trade uses the same durable contract (RR-DURABLE). The RR companion stays ABI1. It runs the shared FR/LG trade producer through a shadow v2 mailbox in its free EWRAM tail (`patch/src/rr_trade_relay.h`; the profile's `native.TRADE_BASE`), so opcodes 29/21/30/31 behave as on FR/LG: a native pre-save, then the native scene, then a native post-save before the witnessed DONE. RR marks `COMMIT_ENTERED` earlier than FR/LG: at scene launch, the no-return boundary, not at `TradeMons`. After that marker every failure is UNCERTAIN and WITHDRAW is too late. On this build the raw opcodes 16 and 18 are always refused. The journal's RR reload proof reads RR's own CFRU layout (chunk 0xFF0). The server opts `firered_rr` into trade recovery. Its named unavailability message now goes only to an RR client whose hello lacks `trade_prepare`, which is a build without the witness (the old UPS). No raw-swap success is allowed. See `patch/src/ADDRESSES.md` and `docs/gen3/research/clause_fix_2_2026-09-27.md` (the earlier refusal).

Durable outbox readiness is per report. `trade_done` and `menu_result` retain the run/journal gate; a non-recovery report such as `release` can drain after hello without a bound journal. An unsent gated report at the head still blocks later reports: there is no reordering. Duo servers now receive a unique `--run-id`, as managed runs do; unmanaged servers never invent one.

State lives in `SoulLinkState.pending_trade` (one slot for the whole run, `state.py:399`). Only **linked pairs** may be traded: the initiator's mon must be one half of an ALIVE link **and** both halves must have cached blobs, i.e. both mons are in their owners' parties with valid `blob_hex` (`_eligible_trade_pairs`, `state.py:786-811`).

### 6.1 Phases

| Phase | Entered by | Server → initiator (I) | Server → partner (P) | Exit |
|---|---|---|---|---|
| (none) | — | — | — | `trade_request` from either player while `pending_trade is None` `state.py:965-972` |
| `menu` | `trade_request` | `show_choices{token, ["Trade","Say hey"], text}`; `text` = `"OAK: Took you long enough.\n<P> is waiting. Make it quick."` when presence off, else `"<P> is right here!\nWhat will you do?"` `state.py:983-991` | — | `menu_result` from I: 0 → `choosing` (or abort with `msgbox "No linked pair in your party to trade."` if nothing eligible); 1 → abort + `msgbox "<I> says hey!"` to P; else abort `state.py:1127-1171` |
| `choosing` | choice 0 | `choose_mon{token}` | — | `mon_chosen` from I: cancel → `msgbox "Trade canceled."`; ineligible → re-prompt ×3; eligible → `confirming`. For a recovery-capable pair with both native prepare capabilities, a withheld or recovery-pending party holds the selected key and slot under this token until a fresh visible census revalidates the pair; no offer is sent while held (`state.py:1035-1070`, `state.py:1250-1267`). |
| `confirming` | eligible pick (`_handle_mon_chosen`) | `hud_show "Trade offer sent - waiting for partner..."` (600 frames) `state.py:1097-1103` | `show_menu{token, "Trade your <P's mon> for <I's mon>?"}` `state.py:1104-1106` | `menu_result` from P: 1 → `preparing` when both players declared hello `trade_prepare`, else `_execute_trade` (`state.py:1324-1355`); else `msgbox "Your partner declined the trade."` to I and `"Trade declined."` to P. `menu_result{withdraw:true}` from I → `msgbox "Trade did not go through."` to both |
| `preparing` | P accepted, both declared `trade_prepare` | `apply_prepare{token, slot:a_slot, old_key:a_key}` | `apply_prepare{token, slot:b_slot, old_key:b_key}` | both `apply_ready{ok:true}` → `_execute_trade`, except a trade-recovery adapter holds both ready bits while either party is hidden and waits for a new visible party census; any `ok:false` or I's `withdraw` → `msgbox "Trade did not go through."` to both, nothing applied |
| `applying` | `_execute_trade` `state.py:1324-1355` | `apply_trade{slot:a_slot, blob_hex:b_blob, old_key:a_key, token}` to **a** | `apply_trade{slot:b_slot, blob_hex:a_blob, old_key:b_key, token}` to **b** `state.py:1336-1341` | both `trade_done` → `_settle_trade` (commit / roll back / conflict); a side that cannot vouch (`uncertain`, or the watchdog) is settled by its next party snapshot |
| committed | `_commit_trade` `state.py:1756-1810` | `play_sound 25` + `msgbox "Traded <gives> for <gets>!"` to both `state.py:1796-1804` | same | `pending_trade=None`, `_trade_settle_ticks = {a:12, b:12}` `state.py:1805-1810` |

`_execute_trade` **re-validates** before dispatch: the link must still be ALIVE, both keys must remain in their parties, and the full pair/key availability checks must pass; otherwise it cancels the slot with a named message (`state.py:1324-1355`).
For a trade-recovery adapter, if either party is hidden when both `apply_ready` replies arrive, the server persists the existing ready bits in `preparing` and waits for a later accepted, visible `hello`/`tick`/`safe` with `party`. It then reruns those same checks before dispatch (`state.py:700-702`). The existing 4,000-event preparing watchdog still applies (`state.py:844-890`). Gen 1/2 keep their previous immediate ready-to-dispatch path; this adds no wire field or persisted schema.

For a recovery-capable pair with both native prepare capabilities, partner acceptance during a withheld or recovery-pending snapshot retains the original offer in `confirming`. A later accepted visible party census may start preparation only after rechecking both offered keys and slots and both prepare capabilities. Duplicate acceptance cannot restart the watchdog or duplicate preparation; withdrawal, decline, changed identity, capability loss and the existing watchdog still end the wait. This wait sends no native prepare or apply command while either party remains withheld.

The same capability-gated wait covers the initiator's initial Trade action. It retains that action and token in `menu`, without selecting from an untrusted party. A fresh visible census must supply current eligible pairs before one `choose_mon` is issued. Replayed actions do not restart the watchdog or duplicate the picker; cancellation, SAY HEY, lost eligibility or capability, and the watchdog still end the request.

If the initiator picks a slot while that party becomes withheld, `choosing` retains only the selected key, slot and partner identity under the original token. It neither trusts the cached blob as a fresh census nor sends `show_menu`. A later visible party snapshot must revalidate the same linked pair, both slots and both prepare capabilities before the existing `mon_chosen` path issues one offer. Duplicate picks cannot renew the watchdog; cancel, withdraw, changed identity, lost capability or watchdog expiry aborts the held selection (`state.py:1035-1070`, `state.py:1250-1267`).
These pre-application waits are memory-only; restarting drops them before any native application.

### 6.2 `apply_trade` client obligations

1. `apply_prepare` verifies the current key, native visit, eligibility and a durable journal epoch. After native pre-save, the current Gen 3 client sends a fresh, non-empty visible party `tick` on the same TCP stream **before** `apply_ready{ok:true}`; an unreadable or hidden census holds the prepared lease. A journal lock busy result holds the same prepare until its deadline and retries; it does not turn into `apply_ready{ok:false}` merely because another process briefly owns the guard (`lua/gen3/trade.lua:275-369,451-461`, `lua/gen3/client.lua:1595-1603`, `lua/gen3/run.lua:277-325`).
2. `apply_trade` re-locates `old_key` in the current party, checks the incoming blob and native trade eligibility, then waits for a clear field. A temporary busy journal holds the command only against its original prepared object, epoch, run/trainer binding and deadline; the retry never allocates another preparation or extends the deadline. It never silently substitutes a raw party-slot write for a native scene (`lua/gen3/trade.lua:409-490`, `lua/gen3/client.lua:1659-1677,1914-1932`).
3. A native scene may post only after the write-ahead journal arm succeeds. A busy arm retains its allocated epoch and retries before publication; a busy saved-result guard retries the same already-witnessed result. Neither retries the scene nor fabricates a certain outcome (`lua/gen3/trade.lua:149-210,442-490`).
4. `trade_done{token,slot,new_key,new_species}` follows the native commit/scene/save witnesses, independent party readback and durable journal result. If proof cannot be established after the no-return boundary, the client reports uncertainty and withholds an unsupported success (`lua/gen3/trade.lua:149-190`, `lua/gen3/client.lua:1611-1652`).
5. Hide the party while a write-ahead trade is unresolved and suppress acquisition/PC/key-change settling after the actual native post and during its settle window. A merely queued or refused post does not suppress ordinary party reduction (`lua/gen3/client.lua:646-669,1105-1113,1158-1182`). An UNREADABLE trade journal is not a trade-owned party change: any other process holding `ROOT/slink_gen3_trade.guard` (a sync client, an indexer, a backup, a second emulator; `run.lua` counts a CreateNew that sees an existing guard as busy) makes `journal:hidden()` true although the journal holds no record. During that window only the acquisition is special; every other flag is cleared as always. A wild catch (`capture_wild`, the battle engine's catch, which no trade can produce) is reported at once, because its key needs only a party read. A gift or PC move (`mon_given`/`pc_move`) is held as a bounded acquisition-only record that carries the area and the trade-ownership verdict of its signal frame; it is released when the journal is readable again, and dropped by name if the journal then shows entries or a failure, a native trade was posted or settling at the signal or started during the hold, or no baseline exists. A hold lasts at most 1800 frames: on expiry the client logs `ACQ expired ... DROPPED` and shows one player-visible HUD line ("Capture not recorded - check your party / tell the host"); the server never receives that capture.
6. After a proved swap, discard queued `box_mon`/`party_mon`/`memorialize` commands for the old and new keys and publish a visible recovery `hello` before the certain `trade_done` (`lua/gen3/client.lua:1626-1652,1720-1756`).

The archived reference client used a silent slot replacement and frame-buffered diffs; those behaviors are historical and do not describe this durable native trade client (`archive/gen3-old-client:lua/clients/gen3_frlge_client.lua`).

### 6.3 `_handle_trade_done` (`state.py:1357-1379`)

Accepts only in phase `applying`; ignores a mismatching non-empty `token`; buffers `(new_key, new_species)` per side; commits when **both** sides have reported. Nothing about the link is mutated before that, so a half-completed trade is never observable.

### 6.4 `_commit_trade` (`state.py:1756-1810`)

| Step | Cite |
|---|---|
| A side that never reported gets `new_key` = the pre-trade key of the mon it received (`a` ← `b_key`, `b` ← `a_key`), species unchanged | `state.py:1764-1773` |
| `entry.a, entry.b = entry.b, entry.a` (the MonInfo objects move with the data) | `state.py:1764` |
| `_commit_trade` unindexes the old keys per player; each half's `key` (and `species` if non-zero) is patched from the readback | `state.py:1765-1773` |
| `party_keys`: a drops `a_key`, adds `entry.a.key`; b likewise | `state.py:1774-1778` |
| `_commit_trade` reindexes the halves, calls `_save`, and queues jingle + msgbox for both | `state.py:1779-1804` |
| Settle window armed | `state.py:1808-1810` |

### 6.5 What the client MUST NOT do

- **MUST NOT send `key_change` for either traded key.** The server has already re-keyed the link from `trade_done`; a `key_change{old:a_key,new:new_key}` would pop the entry from `_key_index` under the new key and re-index it wrongly. The Gen 3 client handles the swap as a local key migration only (`lua/gen3/client.lua:646-660,1626-1641`).
- MUST NOT emit `capture` for the received mon or `party_to_box` for the traded-away mon (`:3412-3413`, `:3435`).
- MUST NOT execute sync commands for the swapped keys queued during the trade (`:3423-3429`).

### 6.6 Watchdog (`state.py:844-890`)

`pending_trade["age"]` increments on `handle_event` calls from either player (ticks, ghost_pos included); an opted-in Gen 3 hello must pass identity/recovery validation before it advances the watchdog. The age resets to 0 on each trade handler that makes progress. When `age > TRADE_WATCHDOG_EVENTS (4000)`: phase `applying` ⇒ first `withdraw_trade{token}` to each silent side that declared `trade_prepare` (once; the age restarts), then `uncertain` (each silent side awaits its next party snapshot; nothing is guessed); any phase before `applying` ⇒ the slot is freed with `msgbox "Trade canceled - no response."`. An applied trade (`applying`/`uncertain`/`conflict`) is persisted in `links.json` and restored as `uncertain` after a restart; `POST /api/debug/resolve_trade {token, action: commit|rollback|adopt, sides?: {a|b: traded|none}}` settles a conflict by hand: refused while a side has not answered its apply at all; a side whose outcome is known (verdict `traded`/`none`) keeps it, the action decides only the `await` sides (`commit` = traded, `rollback` = none, `adopt` = refused while a side awaits); a side with contradictory evidence (`holds NEITHER`/`BOTH`/...) must be named in `sides`, which overrides any verdict; any undelivered `apply_trade`/`apply_prepare` for the token is dropped; a clash inside the trade window (the taker already holds a live link on the key it received) retires the traded pair `identity_lost` and latches the key as ambiguous for that player: `faint`/`party_to_box`/`box_to_party`/`release`/`key_change`/sync answers naming it are refused (persisted `ambiguous_keys` with a refusal count) until `POST /api/debug/resolve_ambiguous_key {player, key}`; one traded side and one not is a `split` (journaled `trade_split`): the traded side's half names the copy it holds. Held events (a trade key's faint, deposit, withdraw, a hello hp-0 faint) wait on the trade, are listed on the board banner (status `trade_held`) and replay after it settles. At ~4 events/s (two clients ticking) that is roughly 15–20 minutes without ghost traffic; far less with it.

---

## 7. Adapter contract (`server/adapters/base.py`)

`GameRulesAdapter` (state machine) + `GamePresentationAdapter` (display) = `GameAdapter`. Abstract methods MUST be implemented; the rest have defaults.

### 7.1 Rules (`GameRulesAdapter`)

| Member | Signature | Default | Purpose | Used at | Cite |
|---|---|---|---|---|---|
| `game_id` | property → str | abstract | registry key, persisted in `links.json` | `state.py:1885-1914`, `state.py:4740`; `server.py:968-1001`, `server.py:2230-2240` | `base.py:88-90` |
| `is_gift_area` | `(area_id) -> bool` | abstract | gift/static areas: no `area_enter` state, no `no_catch`, no quarantine; MUST recognise the `gift_` prefix | `state.py:2399`, `state.py:3101-3103`; `gift_link_area` | `base.py:93-99` |
| `is_fixed_species_gift` | `(area_id) -> bool` | `False` | bypass species/gender/type clauses for forced-species gifts | `state.py:2709`, `state.py:2812` | `base.py:101-113` |
| `is_daycare_area` | `(area_id) -> bool` | `False` | daycare eggs are not gifts | `state.py:2424`; `gift_link_area` | `base.py:115-124` |
| `gift_link_area` | `(area_id) -> str` | `area_id` if gift/daycare else `f"gift_{area_id}"` | namespace under which a gift caught in a wild area links | `state.py:2635` | `base.py:126-140` |
| `is_egg_pickup_area` | `(area_id) -> bool` | `startswith("egg_")` | (declared; still not called by state.py at this commit) | — | `base.py:142-155` |
| `evo_family` | `(species_id) -> int` | abstract | species clause family key | `state.py:2710-2737`, `state.py:3012-3075`, `state.py:4188-4219` | `base.py:158-163` |
| `gender_from_key` | `(key, species_id) -> "male"\|"female"\|"genderless"` | abstract | gender clause (`genderless` never violates); dashboard gender | `state.py:4222-4223`; `server.py:2038`, `server.py:2891-2911` | `base.py:166-172` |
| `species_types` | `(species_id) -> (t1,t2)\|None` | abstract | type clause (the "type badges" display consumer is gone with `html_render.py`) | `state.py:819`, `state.py:4229-4230` | `base.py:175-180` |
| `is_shiny` | `(key) -> bool` | abstract | shiny clause (bonus mons) | `state.py:2449` (`self.adapter.is_shiny` in `_handle_capture`; the standalone helper at `state.py:166` is legacy, per its own docstring) | `base.py:183-190` |
| `parse_ot_id` | `(key) -> str` | `GameAdapter`: second `:` segment of a 2-part key | identity lock fallback when hello lacks `ot_id` | `state.py:2060` | `base.py:193-199`, `base.py:736-748` |
| `is_valid_mon_key` | `(key) -> bool` | `GameAdapter`: two hex segments ≤ 8 digits | validation (dashboard APIs) | — | `base.py:202-204`, `base.py:750-764` |
| `species_name` | `(species_id) -> str` | abstract | every HUD/msgbox label, logs | pervasive | `base.py:207-212` |
| `type_name` | `(type_id) -> str` | abstract | type clause message, badges | `state.py:4237` | `base.py:215-217` |
| `rival_trainer_ids` | `() -> set[int]` | `set()` | rival-team-swap trigger set | `state.py:4626` | `base.py:219-233` |
| `party_blob_size` | `() -> int` | `0` (= do not cache blobs) | byte length every `blob_hex` MUST decode to; gates trade eligibility and rival swap | `state.py:4486` | `base.py:235-249` |
| `supports_abilities` | `() -> bool` | `True` | hide Ability columns | `server.py:119-179` | `base.py:251-261` |
| `status_token` | `(status_cond) -> "SLP"\|"PSN"\|"BRN"\|"FRZ"\|"PAR"\|"TOX"\|""` | `""` | native `link_panel` status field (helper `gb_status_token` for GB layouts) | `server.py:1907-1926` | `base.py:263-269`, `base.py:702-722` |
| `info_panel_width` | `() -> int` | `0` | 0 = no native panel width concept (Gen 3 fallback: panel on adapter's say-so); ≤20 ⇒ compact rows | `server.py:948-966`, `server.py:1963-1964` | `base.py:271-279` |
| `supports_info_panel` | `() -> bool` | `False` | veto for `link_panel` | `server.py:948-966` | `base.py:287-301` |
| `supports_explode_mode` | `() -> bool` | `False` | `force_explode` instead of `force_faint` | `state.py:4301-4316` | `base.py:303-316` |

### 7.2 Presentation (`GamePresentationAdapter`)

| Member | Signature | Default | Used at | Cite |
|---|---|---|---|---|
| `sprite_html` | `(species_id, form=0) -> str` | abstract | `server.py:646-653` → everywhere sprites render | `base.py:414-421` |
| `ability_name` | `(ability_id, species_id=0) -> str` | abstract | `server.py:119-179`, `server.py:2891-2911` | `base.py:424-430` |
| `ability_description` | `(ability_id) -> str` | abstract | not currently called from server.py (`html_render.py`, its former caller, is gone) | `base.py:433-435` |
| `trainer_info` | `(trainer_id) -> (name, class)`; `("","")` if unknown | abstract | `server.py:2532-2547` (tick `trainer_id`) | `base.py:438-444` |
| `item_name` | `(item_id) -> str` | abstract | `server.py:119-179`, `server.py:2891-2923` | `base.py:447-449` |
| `area_display_name` | `(area_id) -> str` | abstract | dead-zone text `state.py:2914`, `state.py:3192`; panel, dashboard `server.py:1886-1994`, `server.py:2837-3126` | `base.py:452-454` |
| `to_national_dex` | `(species_id) -> int` | abstract | `sprite_src` default | `base.py:457-459` |
| `gender_symbol` | `(gender) -> str` | abstract | dashboard | `base.py:462-464` |
| `form_sprite_id` | `(species_id) -> int\|None` | abstract | forms | `base.py:467-469` |
| `form_sprite_url` | `(species_id, form=0) -> str\|None` | `None` | Gen 4+ forms | `base.py:471-482` |
| `rom_content_fingerprint` | `(payload) -> str\|None`; MUST raise on malformed | `None` | admission `server.py:839-926` | `base.py:484-496` |
| `ingest_rom_content` | `(payload) -> tables\|None`; MUST raise on malformed | `None` | `server.py:665-712`; adapter also needs `use_rom_encounters(tables)` for per-player adoption `server.py:665-712` | `base.py:498-514` |
| `encounter_table` | `(area_id) -> {method: [ {name, species_id, rate, min_level, max_level} ]}\|None` | `None` | encounter panel `server.py:1445-1452` | `base.py:527-538` |
| `trainers_for_area` / `trainer_party` / `trainer_brief` | see file | `[]` / `[]` / synthesised | Upcoming Trainers panel: `trainers_for_area` `server.py:1021-1372`, `trainer_brief` `server.py:1021-1372`, `server.py:1021-1372`; `trainer_party` not currently called | `base.py:540-546` |
| `sprite_src` | `(species_id) -> url` | PokeAPI by national dex | encounter panel | `base.py:622-633` |
| `move_name` / `move_data` | `(move_id) -> str` / `-> {name,type_id,type_name,power,accuracy,pp,split}\|None` | `""` / `None` | move tables `server.py:2856-2889`, `server.py:2856-2889` | `base.py:635-640` |
| `stat_stage_labels` | `() -> list[str]` (7 slots; `""` blanks a slot) | `["ATK","DEF","SPD","SATK","SDEF","ACC","EVA"]` | `server/ui_capabilities.py:30` | `base.py:650-658` |
| `mons_per_box` | property → int | `30` | memorial overflow box count `server.py:5122-5141` | `base.py:661-669` |
| `memorial_box_index` | property → int (0-based; `-1` = none) | `-1` | contamination scan, memorial contents | `base.py:672-679` |
| `gym_badge_slugs` | `(rom_type) -> [(pokeapi_id, name)]` | Kanto 1-8 | badges overlay | `base.py:681-699` |
| `calc_name` | `(kind, name) -> str`; kind is species/ability/item/move | identity | calc payload names (`_build_mon_entry`) `server.py:119-179` | `base.py:571-577` |
| `calc_species` | `(species_id) -> str` | `calc_name("species", species_name(id))` | calc species key; lets calc naming diverge from HUD naming (pureRGB's alternate forms share a base `species_name`) `server.py:119-179` | `base.py:579-592` |
| `calc_profile` | `() -> {"gen": int, "dex": str}\|None` | `None` | `None` hides the Calc tab and dashboard calc preview for that game `server.py:1015-1019`; `manager.py:265` | `base.py:594-601` |
| `calc_nature` | `(key) -> str\|None` | `None` (no natures, Gen 1/2) | calc nature `server.py:119-179` | `base.py:603-608` |
| `calc_stats` | `(detail) -> {ivs, evs, stats}\|{dvs, stat_exp, stats}\|None` | `None` | decoded IV/EV/stats (Gen 3+) or DV/stat-exp (Gen 1/2) for the calc: `_foe_detail` (`server.py:182-209`) and `handle_calc_mons` (`server.py:3196-3270`) | `base.py:610-620` |

### 7.3 Routing (`server/adapters/__init__.py`)

| Step | Rule | Cite |
|---|---|---|
| `hello.rom_type` → `game_id` via `_ROM_TYPE_TO_GAME_ID` (unknown ⇒ `None` ⇒ adapter unchanged, silently) | `server/adapters/__init__.py:42-86`, `server/adapters/__init__.py:131-138` |
| Adapter switched only while `state.rom_type` is unset; later hellos with a different `rom_type` are logged and ignored | `server.py:2230-2240` |
| `is_rr = rom_type.endswith("_rr")`; `get_adapter(game_id, is_rr=..., rom_type=...)` — adapters MUST accept `**kwargs` | `server.py:2233-2237`, `server/adapters/__init__.py:24-32` |
| `rom_type` persisted; on reload the saved `game_id` re-resolves the adapter with `rom_type` | `state.py:1885-1914` |
| Human label: `variant_label(rom_type)` (`server/adapters/__init__.py:265-267`); RESOLVED — the second, incomplete `ROM_LABEL` map this row used to warn about is gone from server.py; the dashboard header now calls `variant_label` directly | |

---

## 8. Gen 3-isms baked into shared code

Things a non-Gen-3 client/adapter must neutralise on the wire, or that should become adapter hooks.

| # | Where | What | Impact on another generation | Mitigation today |
|---|---|---|---|---|
| 1 | `state.py:1796,2461,2470,2543,2618-2619,2650,2671,2695,2749,2825-2826,2870-2871,3194,3201,4316` | `play_sound` ids are Gen 3 SE numbers (25 success, 26 failure, 22 boo, 95 shiny) | meaningless on GB | Gen 1 binds them in the client (`panel.lua SFX_CODE_FOR_GEN3_ID` → mailbox codes 1-3; the ROM owns the per-bank sound ids); Gen 2: §8.1 row 1 |
| 2 | `state.py:989` | `"OAK: Took you long enough..."` in the trade action-menu text when `overworld_presence` is off (assumes the RR PC trade NPC is Prof. Oak) | wrong speaker on other games | none; client may re-render |
| 3 | `state.py:1080`, `state.py:1319` | `POKeMON` (FR charmap spelling) in msgbox text | cosmetic | route through the client's text sanitiser |
| 4 | `state.py:2457,2613-2614` | `"Pokémon"` (non-ASCII é) fallback when `species_id` is 0 | HUD mangling on BizHawk | always send `species_id` |
| 5 | `server/templates/_macros.html:43-53` | `status_pill` decodes `status_cond` with the Gen 3 `status1` layout (dashboard); only `link_panel` uses `adapter.status_token` | a client whose RAM layout differs MUST send `status_cond` re-encoded to: SLP = bits 0-2 counter, PSN 0x08, BRN 0x10, FRZ 0x20, PAR 0x40, TOX 0x80 (GB layout already matches for SLP/PSN/BRN/FRZ/PAR, `base.py:702-722`) | encode on the wire |
| 6 | `server/templates/_macros.html:64-83` | `stat_stages` are 7 slots, raw 0-12 with **6 = neutral** | Gen 1 stat mods are 1-13 with 7 neutral; client MUST subtract 1 and blank/omit slots per `adapter.stat_stage_labels()` | encode on the wire; adapter blanks labels |
| 7 | `server.py:2856-2889` | PP-Up encoding accepts `pp_bonuses` (packed u8) or `pp_ups` (list) | Gen 1 stores PP-Ups in the PP byte's top 2 bits — client must split into `pp` and `pp_ups` | send `pp_ups` |
| 8 | `state.py:2052-2060`, `base.py:736-748` | identity fallback parses OT from `party[0].key` with the 2-part default | a 3-part key MUST override `parse_ot_id`; better: send `ot_id` in hello | Gen 1 adapter overrides `gen1_rby.py:345-346` |
| 9 | `state.py:4484-4512` | blob validation by `party_blob_size()` | default 0 disables trade + rival swap entirely | adapter override (Gen 1: 66) |
| 10 | `server.py:1886-1994` vs `server.py:1886-1994` | wide `link_panel` "Badges" row reads the `status` event count; compact rows popcount the hello/tick bitmask | a client without `status` shows 0/8 in the wide layout | send `status{badges:count}` or use width ≤ 20 |
| 11 | `server.py:948-966` | absent `panel` capability ⇒ panel sent iff `info_panel_width()==0` | a Gen with width > 0 gets **no** panel unless hello carries `panel:true` | send `panel`/`panel_abi` |
| 12 | RESOLVED | dashboard `ROM_LABEL` (formerly `server.py:4101-4115`) is gone | — | header now calls `variant_label` directly (`server/adapters/__init__.py:216-218`) |
| 13 | `state.py:3452-3468`, `state.py:3740-3767` | party capacity hardcoded 6 | correct for every supported generation | — |
| 14 | `state.py` `MonInfo.species` vs wire `species_id`; `_build_status_dict` links expose `a_species` (`server.py:3017-3021`, `server.py:3076-3080`), while `_build_link_panel` reads party/cache `species_id` then `MonInfo.species` directly (`server.py:1907-1913`) | RETIRED: the old `_lp_mon_cell` fallback looked up `a_species_id`; that helper is no longer used | none needed on the wire: **always** `species_id` |
| 15 | `state.py:4221-4226` | gender clause via `gender_from_key`; a `genderless` result never violates | Gen 1 adapter returns `genderless` — clause inert, no wire impact | — |
| 16 | `state.py:2449-2506` | shiny clause via `adapter.is_shiny(key)` | Gen 1 adapter returns `False` — inert | — |
| 17 | `lua/gen3/reads.lua:19` | client hardcodes 100-byte blobs (`R.PARTY_MON_SIZE = 100`, `sizeof(struct Pokemon)`) | client-side only; a new client checks its own adapter size | — |
| 18 | `server.py:2233-2237` | `is_rr` inferred from `rom_type` suffix `_rr` | none for other gens | — |
| 19 | `state.py:3978-3983` | `key_change.new_species`/`new_nickname` exist **for** non-Gen-3 evolution (key embeds species) | Gen 1 MUST send `key_change{old_key,new_key,new_species,new_nickname?,reason:"evolution"}` on evolution because its key changes; Gen 3 never does (key is PID:OT) | documented hook |
| 20 | `server.py:2532-2547` | `opponent_name`/`opponent_class` on tick accepted only when `adapter.trainer_info` returns no class | documented hook for generations without a trainer table | send both on trainer battles |

### 8.1 Gen 2 answers

One per row above, plus the held item. Client = `lua/gen2/client.lua`, wire = `lua/gen2/wire.lua`, reads = `lua/gen2/reads.lua`, adapter = `server/adapters/gen2_gsc.py`. Adapter answers are `Gen2GSCAdapter`'s: Crystal/Gold/Silver route straight to `gen2_gsc` (`server/adapters/__init__.py:79-81`); the legacy `gen2_crystal` adapter was removed at the P3b.8 cutover (`_RETIRED_GAME_IDS`, `server/adapters/__init__.py:235-237`).

| # | Gen 2 answer | Cite |
|---|---|---|
| 1 | **Native sound (P4.2b/c, `ed8a0929`, `032b32ee`).** `play_sound` and the client's own cues go through `request_sfx_local`: `panel:sfx_code_for` maps 25→NOTIFY (SUCCESS on a cartridge without `SFX_NOTIFY`), 26→FAILURE, 22→BOO and 95→SUCCESS, one cue per frame through `lua/sfx_arbiter.lua` to the cartridge's sound service; any other id is dropped. The hello's `sfx` is `panel:sfx_present()`. PHYSICAL on the C/G/S SLink overlays; a clean ROM is unchanged (no beacon: nothing sent, the hello says `sfx:false`) | client `request_sfx_local`, hello `sfx`; `lua/gen2/panel.lua` `sfx_code_for`/`request_sfx` |
| 2 | Inert: the trade prompts are answered with the protocol cancel and never rendered, so the `OAK:` text never shows (native trade UI is P4.3) | client `:303-306`, `:412-413`; adapter `native_trade_ui` `:452-453` |
| 3 | Every `msgbox`/`gui_prompt`/`hud_show` text goes through `hud.show`/`hud.prompt`, which sanitise | client `:375-380`; `lua/hud.lua:69-86`, `:259-260`, `:285-286` |
| 4 | `species_id` is always sent: capture, no_catch, key_change, every party/box/foe entry | client `:482`, `:525`, `:570`; wire `:154`, `:188`, `:219` |
| 5 | Raw status byte, no re-encoding: the GB layout already matches (bit 7 unused, no persistent TOX); adapter `status_token` is the GB decoder (`gb_status_token`) | wire `:155`, `:189`; adapter `:472-473`; `server/adapters/base.py:702-722` |
| 6 | Seven independent stages, converted to 0-12 / 6-neutral in ATK..EVA order from the source's neutral 7; sent only for the active mon; default labels kept | reads `:529-560`; wire `:24-31`, `:113-122`, `:161-165`; `server/adapters/base.py:650-658` |
| 7 | PP byte split into `pp` (low 6 bits) and `pp_ups` (top 2) | reads `:133`, `:520`; wire `:144-147`, `:156`, `:190` |
| 8 | Hello sends `ot_id`; the adapter also overrides `parse_ot_id` (middle key segment) | client `:620`; adapter `:179-180` |
| 9 | `party_blob_size()` = 70 (48-byte struct + 11 OT + 11 nickname) | adapter `:253-254`; wire `:81-97` |
| 10 | **OPEN (P4).** No `status` event is sent; the wide Badges row is unused while there is no panel (row 11) | client (no `status` send); `:698-705` |
| 11 | Hello sends `panel:false, panel_abi:0`; `link_panel` is ignored; adapter width 0, no info panel. Native panel is P4 | client `:629`, `:419-421`; adapter `:449-450`, `:458-459` |
| 12 | `ROM_LABEL` is gone; the dashboard uses `variant_label`, which has every Gen 2 spelling | `server/server.py:1259-1260`, `:1071-1072`; `server/adapters/__init__.py:95-97` |
| 13 | Correct: party slots 0-5 | wire `:136` |
| 14 | Wire is `species_id` throughout (row 4) | — |
| 15 | **Live, not inert:** `gender_from_key` derives gender from the Attack/Speed DVs against the species ratio | adapter `:182-196` |
| 16 | **Live:** `is_shiny` is the Gen 2 DV rule | adapter `:198-203` |
| 17 | Client builds 70-byte blobs from the record's own raw bytes | wire `:81-97` |
| 18 | None: no Gen 2 `rom_type` ends `_rr` | `server/adapters/__init__.py:73-76` |
| 19 | `key_change{old_key,new_key,new_species,new_nickname,reason}` for `evolution` and `npc_trade` | client `:567-572`; `lua/gen2/signals.lua:422`, `:461` |
| 20 | **OPEN.** Neither `trainer_id` nor `opponent_name`/`opponent_class` is sent and `trainer_info` answers `("","")`: the Gen 2 (class, id) pair has no agreed single-int packing, so trainer display stays blank and there is no `trainer_battle_start` | client `:510-513`, `:701`; adapter `:492-495` |
| held item | `held_item_id` rides every party, foe and box entry and `capture`, read from struct offset 1; named by adapter `item_name` | reads `:121`; wire `:148`, `:157`, `:186`, `:189`, `:214`, `:220`; client `:483`; adapter `:235` |

### 8.2 `play_sound` ids (Gen 3 titles)

`play_sound.sound` is always a **FireRed/LeafGreen** SE number, on every Gen 3 title: 16 SE_FAINT, 17 SE_FLEE, 22 SE_BOO, 25 SE_SUCCESS, 26 SE_FAILURE, 95 SE_SHINY (pret pokefirered `c75f3523` `include/constants/songs.h:20,21,26,29,30,99`). This includes the client's own `play_sound(26)` (`lua/core/session.lua:301`). The server and `lua/core` never renumber.

- **The client translates.** `lua/gen3/client.lua` maps the wire id to the title's own song id through `sound.se_ids` in the title's `write_checkpoint.json` (generated by `tools/gen_gen3_write_checkpoint.py`, `SE_WIRE_IDS`). The m4a fallback then looks that id up in `profile.rom.SE_SONG_HEADERS`.
- **FR/LG/RR** carry the identity map.
- **Emerald** maps 25→31, 26→32 and 95→102, and keeps 16, 17 and 22 (pret pokeemerald `c65e93f2` `include/constants/songs.h:22,23,28,37,38,108`).
- **Refusals.** A wire id the map lacks is refused and logged (`sound refused: pack maps no title SE for wire id N`), and so is a pack with no `se_ids`. A title's own id is not a wire id: on Emerald, 31 is refused.
- **Native path.** The RR companion's `native:play_sound` receives the wire id unchanged, because the companion is built on FR numbering.

---

## 9. Conformance checklist

Assertions for `tests/unit/test_protocol_conformance.py`: a lupa-driven fake server (mock socket, as in `tests/unit/test_connector_fragmentation.py`) drives the client and inspects the lines it writes and the RAM writes it performs. Each item names the cite that makes it normative.

**Transport**

1. Every outbound line is a single JSON object terminated by exactly one `\n`, with `event:str`, `player ∈ {"a","b"}`, `seq:int` (`lua/core/session.lua:83-87`, `connector.lua:196`).
2. `seq` starts at 1 on script load and increases by exactly 1 per event, across TCP reconnects (`lua/core/session.lua:71,83`; `server.py:1814-1821`).
3. The client sends **nothing** while `C.connected()` is false and does not buffer events for later (`lua/core/session.lua:78-81`).
4. On connect (and every reconnect) the first line is `hello` (`lua/core/session.lua:362-370`).
5. The client tolerates a reply of `{"commands":[{"cmd":"noop"}]}` for any event and a reply carrying commands it does not know (logs, does not crash) (`lua/core/session.lua:265-269,309`).
6. A server line > 4 MiB is discarded and the next line still parses (`connector.lua:242-247`).
7. Field order in a reply is irrelevant to the client.

**hello**

8. `hello` carries `rom_type` ∈ `_ROM_TYPE_TO_GAME_ID` keys, `party:list`, `has_pokeballs:bool`, `trainer_name:str`, `badges:int` bitmask (`lua/gen3/client.lua:1101-1156`; `server/adapters/__init__.py:42-86`). A client whose foundation is in `HELLO_DECLARES` (Gen 1, Gen 2) also sends `foundation` equal to `foundation_for_rom_type(rom_type)` and an `artifact_kind` from §2.1's list; any client that sends either sends it so (`tests/unit/protocol_schema.py`; §2.1).
9. Every `party` entry has `key`, `hp`, `maxHP`, `level`, `slot`, `species_id`, `nickname`, `blob_hex` with `len == 2*party_blob_size()` (`state.py:4484-4508`).
10. A new client SHOULD send `ot_id`; if it does not, `adapter.parse_ot_id(party[0].key)` must yield the save's trainer id (`state.py:2052-2060`).
11. `hello` omits `party` contents (sends `[]`) when the save is not loaded or the party is borrowed (`lua/gen3/client.lua:1105-1113`).
12. After a `hud_show` whose text starts with `[x] WRONG SAVE`, the client renders it (accepting `color`/`duration` **or** `r,g,b,frames`) and does not crash (`state.py:2081-2085`).
13. The client applies `resolved_areas` (including an empty list) and sets its seeded flag; `config` booleans are applied (`lua/core/session.lua:285-298`).

**tick / snapshots**

14. `tick` is periodic (Gen 3: every 30 frames) and carries `has_pokeballs`, `area_id`, `loc_name`, `in_battle`, `badges`, `trainer_name`; `party` when the save is valid and not borrowed; `enemy_party` (`[]` outside battle); `pc_boxes` when the party diff is trustworthy (`lua/gen3/client.lua:1158-1182,lua/core/session.lua:398-400`).
15. The first tick after a wild battle starts has `in_battle=true`, `is_trainer_battle=false`, non-empty `area_id`, and `enemy_party[0].species_id > 0` (`server.py`: the wild-battle-start dupes block inside `_dispatch_event`, gated on `_enc_species` — `:2129-2148` at `99c70d9c`; the block moved from `:3074-3093`, which is why this cites the function).
16. In a trainer battle the tick has `is_trainer_battle=true` and `trainer_id>0`, or `opponent_name`/`opponent_class` (`server.py`: `_dispatch_event`'s tick branch — `is_trainer_battle` at `:2102-2103`, the `trainer_id` → `adapter.trainer_info` resolution that fills `opponent_name`/`opponent_class` at `:2104-2121`; the range moved from `:3049-3064`, which is why this cites the function).
17. `status_cond` uses the §8-5 bit layout; `stat_stages` is a 7-list with 6 = neutral present only for `active` mons; `pp_ups` or `pp_bonuses` present when `moves` are (`server/templates/_macros.html:43-83`, `server.py:2856-2889`).
18. `pc_boxes` entries have 0-based `box`/`slot`, `key`, `species_id`, `nickname`; the memorial box index used by the client equals `adapter.memorial_box_index` (`server.py`: `_memorial_box_indices`, `server.py:5122-5141`).
19. `safe` is sent on the first overworld frame after a battle (`lua/core/session.lua:402-404`).

**Encounter events**

20. `area_enter{area_id, loc_name}` fires on map change; `area_id` is a known adapter id or `""` (`lua/gen3/client.lua:560-571`).
21. `capture` has non-empty `key` and `area_id`, `species_id>0`, `level>0`; `in_box=true` iff the mon landed in the PC; `gift=true` for out-of-battle acquisitions; `is_egg` present (`lua/gen3/client.lua:441-500`).
22. RETIRED for the current client: an out-of-battle capture before `has_pokeballs` no longer forces `area_id="intro"`, it just carries `area_now()`'s real result (`lua/gen3/client.lua:242-247`); this was the old (reference) client's behaviour.
23. `no_catch{area_id, species_id, level}` fires once per unresolved wild battle with no catch, never for gift areas, never twice for the same area, and never after a `capture` in that battle (`lua/gen3/client.lua:606-623`).
24. `unresolve_area{area_id}` re-arms `no_catch`/encounter HUD for that area (`lua/core/session.lua:289-290`).
25. `faint{key}` fires once per HP >0→0 transition of a party mon and **not** for HP the client zeroed itself on `force_faint`/`force_explode` (`lua/gen3/client.lua:367-372,399-401,431-439`).
26. `whiteout` fires exactly once when every previously-alive party mon is at 0 HP after a real faint (`lua/gen3/client.lua:663-666`).

**Party/box sync**

27. `party_to_box{key, stats}` fires for a key that left the party outside battle with HP>0 and was **not** moved by the client on server command; `box_to_party{key}` for a known key that re-entered (`lua/gen3/client.lua:502-535`).
28. On `box_mon{key}` the client deposits when safe, sends `stats_cache{key,stats}` first, refuses when the key is the last party mon, and sends `box_mon_failed{key,reason}` when the deposit did not happen (`lua/core/deferred.lua:159-171,lua/gen3/boxes.lua:364-400`; `state.py:645-658`).
29. On `party_mon{key,stats?}` the client withdraws when safe and replies with exactly one of `sync_retrieve_done{key}` / `sync_retrieve_failed{key}`; an already-present mon is acked as done (`lua/gen3/boxes.lua:401-429,lua/core/deferred.lua:172-191`).
30. On `memorialize{key}` the client moves the mon to the memorial box when safe and replies with exactly one of `memorialize_done{key,box}` / `memorialize_failed{key,reason}`; it never empties the party unless `game_over` was received (`lua/gen3/boxes.lua:430-493,lua/core/deferred.lua:192-211`, `2645-2650`).
31. Deferred commands execute FIFO, at most one per frame, only in the client's safe state; opposing `box_mon`/`party_mon` for one key cancel each other; duplicate `memorialize` is deduped (`lua/core/deferred.lua:74-99,116-130`, `2634-2716`).
32. Commands referencing an unknown key are no-ops that do not crash the client (`lua/core/deferred.lua:123-130`).

**Deaths**

33. `force_faint{key}` on a benched/out-of-battle mon writes HP=0 within the same frame; on the active battler it is applied at switch-out or battle end; neither path emits `faint` for that key (`archive/gen3-old-client:lua/clients/gen3_frlge_client.lua`) — this is the reference (old) client. **On the new Gen 3 client, FR/LG singles apply the active-battler write immediately via mechanism P+H instead of holding for switch-out/battle end** (owner rulings 15-16, `docs/gen3/G4_request_draft.md` §6, 2026-09-23/24; doubles keep the hold; RR gets parity at G5, ruling 19 for Explode).
34. `force_explode` is handled at least as `force_faint` (a client for a generation whose adapter returns `supports_explode_mode()==False` never receives it) (`state.py:4301-4316`).
35. `game_over` sets a persistent HUD state and does not stop ticks (`lua/core/session.lua:299-303`).

**Keys**

36. Keys are stable across box↔party moves, reconnects, and nickname/held-item changes (`state.py:3840-3867`).
37. A same-mon key change (nature change or evolution on generations whose key embeds species) MUST be reported as `key_change{old_key,new_key,new_species?,new_nickname?,reason}` without `capture`/`party_to_box`/`box_to_party` for either key. The current Gen 3 client implements the in-game NPC trade slot-replacement case with `reason="npc_trade"` (`lua/gen3/client.lua:540-558`); its PID:OT key does not change merely for nature, nickname, held item, or species evolution.
38. Keys are scoped per player: a client derives a key from the mon's own record only, so equal keys arise only from equal identity data (an in-game trade or fixed-identity gift on both cartridges), and it MUST NOT alter a key to keep the two halves of a pair apart. The server accepts such a pair and routes each half through its own player's index (`entry_for`, `state.py:4366-4368`); only a collision with the same player's live links is refused (`_check_link_violation`, `state.py:4174-4184`).
38a. Every `key_change` is answered in the same reply by exactly one `key_change_ack{old_key,new_key,migrated}` or `key_change_rejected{old_key,new_key,reason}`; a client that aliases old→new until acknowledged resolves the alias on either (`_handle_key_change`).
38b. A `key_change` re-sent after a reconnect is idempotent (`migrated:false`, nothing mutated); a rejected one mutates nothing on the server, including the presentation caches (`server.py` migrates them only after acceptance).

**Prompts and trade**

39. `show_choices` → one `menu_result{token,choice}` where `choice` is the 0-based index or 127; `show_menu` → one `menu_result{token,choice}` with 1 = yes, 0 = no; `choose_mon` → one `mon_chosen{token,slot}` 0-5 or 7; the token is echoed verbatim; the cancel code is sent immediately when the prompt cannot be shown (`lua/gen3/native.lua:924-959`).
40. Two prompts are never in flight at once; a second `trade_request` is not sent while a prompt or trade is pending (`lua/gen3/native.lua:1245-1265`).
41. `apply_trade{slot,blob_hex,old_key,token}` MUST resolve exactly once through the owed/replay protocol. A witnessed result reports `trade_done{token,slot,new_key,new_species}` with the received key read back from the saved party; when the no-return boundary was crossed but proof is unavailable, it declares `trade_done{token,uncertain:true}` instead of inventing a proved key (`lua/gen3/trade.lua:149-190`).
42. After a trade the client emits **no** `key_change`, `capture`, or `party_to_box` for the two traded keys, and discards queued sync commands for them (`lua/gen3/client.lua:646-660`).
43. During `apply_trade` the client does not execute `box_mon`/`party_mon`/`memorialize` (`lua/gen3/client.lua:1033-1044`, the `checkpoint_ok` PC-trade gate and posted-trade settle guard).

**Misc**

44. `trainer_battle_start{trainer_id:int>0}` fires once per trainer battle, never for wild or borrowed battles (`lua/gen3/client.lua:575-604`).
45. `replace_rival_team` MUST produce exactly one `rival_team_replaced{trainer_id, species_ids, error?}`: a named refusal on any failed guard, including `writes_paused` before staging, or one result when the native job settles (`lua/gen3/native.lua:1025-1106,lua/gen3/client.lua:1500-1567`).
45a. `replace_rival_team` echoes the `session` nonce and `battle_id` counter of the battle it belongs to (when the client announced an identity), and the new Gen 3 client refuses a missing, malformed, other-session or mismatched pair with `rival_team_replaced{error:"stale_battle_id"}`, writing nothing (`state.py:4516-4589`, `lua/gen3/client.lua:1500-1567`). A manual inject with no stored identity is refused server-side **for a client that declared `battle_identity: true` in its hello**; a client that never declared it (Gen 1, Gen 2, the old Gen 3 RR client) keeps the pre-card behaviour and gets a command with no identity fields. Old clients that never send an identity keep today's behaviour throughout.
46. `status{badges:int 0-8}` is a count, not a bitmask (`state.py:776-784`).
47. All on-screen text from `msgbox`/`gui_prompt`/`hud_show` passes through the client's text sanitiser (non-ASCII in server strings, §8-3/4).

---

## Appendix A — Disagreements and ambiguities

| # | Topic | Server | Gen 3 client | Resolution for new clients |
|---|---|---|---|---|
| A1 | WRONG SAVE toast fields | `hud_show{r,g,b,frames}` `state.py:2081-2085` | parser accepts both spellings `lua/core/session.lua:59-62,278-282` | resolved |
| A2 | `box_mon_failed` | handled, restores party model `state.py:645-658` | sent on deposit failure `lua/core/deferred.lua:164-166` | MUST send on failure |
| A3 | `safe` payload | `safe` treated like `tick` (may carry `party`) `state.py:667-698`; blobs doc claims "hello / tick / safe" `state.py:4453-4456` | `{event:"safe"}` only `lua/core/session.lua:402-404` | fields optional |
| A4 | `ot_id` in hello | preferred `state.py:2052` | never sent → OT parsed from `party[0].key` | send `ot_id` |
| A5 | `panel` capability | read at hello `server.py:2218-2220`; absent ⇒ `info_panel_width()==0` | never sent (works because Gen 3 width is 0) | any gen with width > 0 MUST send `panel:true` to get `link_panel` |
| A6 | `pending_sync` command | never emitted | the old (reference) client handled it; the current client has no handler either — an unhandled command falls to the generic log branch (`lua/core/session.lua:309`) | dead; ignore |
| A7 | `party_mon.stats.pp1..pp4` | echoed verbatim from `stats_cache` | RESOLVED differently than expected: the client does send them (`stats_of`, `lua/gen3/client.lua:377-381`) and its real JSON decoder no longer drops them (§1.2 is retired), but `boxes.lua`'s withdraw deliberately ignores them and lets the engine refill PP to max on expansion — RR's `CreateBoxMonFromCompressedMon` recomputes PP for every move slot, so a cached depleted value would wrongly override a full refill (`lua/gen3/boxes.lua:278-310`) | optional; a client may still restore PP from them where its own engine does not auto-refill |
| A8 | `peer_interact` | handled `state.py:752-759` | not sent (replaced by `trade_request`) | legacy; not required |
| A9 | `ghost_pos.x/y` units | comment says tile coords `state.py:717-726` | MOOT for the current client: it does not send `ghost_pos` at all (peer ghost deferred post-RC, `C.ghost_pos` at `lua/gen3/client.lua:1799` only handles the incoming relay command), so there is no client-side units comment to disagree with | opaque ints relayed unchanged; patch-defined |
| A10 | ~~`seq` restart heuristic~~ **RETIRED 2026-09-17 (`0629736`)** | the heuristic (`restart recognised only if seq<=1 and last>10`) is deleted; the counter is a per-connection local and a connection is ignored until it says hello — `server.py:1686-1697`, `server.py:1810-1821`, `server.py:1625-1866` | client restarts at 1 regardless — which is now simply correct | **no divergence left.** The old resolution ("a harness must not reuse a server across restarts with ≤10 prior events, or must send ≥ `last` events first") described a constraint that no longer exists; a harness may reuse a server across restarts freely. Row kept, not deleted, so the history reads straight |
| A11 | Trade text | `"OAK: ..."`, `POKeMON` `state.py:989,1080` | rendered verbatim | cosmetic; not generation-neutral |
| A12 | `link_panel` Badges row | wide layout: `status` count `server.py:1886-1994`; compact: bitmask popcount `server.py:1886-1994` | STALE: the current Gen 3 client does not send `status` at all (confirmed above, §3.2); the wide layout's badge count reads 0/8 for it now unless `info_panel_width()` keeps it under the ≤20 compact threshold | generation-dependent |
| A13 | Badge field semantics | `hello/tick.badges` bitmask (SLinkServer) vs `status.badges` count (SoulLinkState) | both sent correctly | do not confuse them |
| A14 | Tick interval | RESOLVED: the old (reference) client's stray "every 60 frames" comment is gone with that file | `TICK_INTERVAL = 30` (`lua/core/session.lua:52`) | 30 frames is the reference behaviour |
| A15 | Oversize inbound line | server drops it **without** a reply `server.py:1643-1658` | client's `pending_labels` FIFO would then be off by one (cosmetic logging only) | never send > 4 MiB |
| A16 | `_lp_mon_cell` link fallback | reads `lnk["a_species_id"]` `server.py:1886-1994` while links dict has `a_species` `server.py:1886-1994` | — | server-internal; harmless |
| A18 | `key_change` acknowledgement | `key_change_ack` / `key_change_rejected` appended to the sender's reply (§3.2, §5) | not handled (unknown commands are ignored, §1.2) — Gen 3's local migration is unconditional, so a rejection leaves the client and server disagreeing on the key until the pair is retired | a new client keeps an old→new alias until acknowledged; Gen 3's key never collides in practice (PID:OT), so the rejection path is a Gen 1 / pureRGB concern |
| A19 | Empty lists on the wire | reads `enemy_party` / `pc_boxes` with `or []` and iterates, so an empty object is folded silently | RESOLVED for the current Gen 3 client: the old (reference) client's hand-rolled encoder emitted `{}` for an empty Lua table (characterized 2026-09-21 on the six RR duo transcripts, `tests/fixtures/gen3/wire/`); the current client binds the shared `lua/json_codec.lua` and tags every list with `json.array()` (`lua/json_codec.lua:192-197`, used pervasively e.g. `lua/gen3/client.lua:307-317`), so an empty list is always encoded `[]` | a new client binds `lua/json_codec.lua` and always sends `[]` for an empty list |
| A17 | Capture quarantine count | uses `party_size` at event time (`state.py:2775-2788`), which may already include the new mon if a tick preceded the capture | client sends capture before the next tick in practice | send `capture` promptly, before the next `tick` |
