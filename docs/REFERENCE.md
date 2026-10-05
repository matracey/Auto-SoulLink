# SLink technical reference

SLink automates a Soul Link Nuzlocke across two Pokémon games running in two [BizHawk](https://github.com/TASEmulators/BizHawk) instances. A Lua client in each emulator reads game RAM every frame and sends JSON events (area entered, capture, faint, and so on) to a Python server over TCP. The server enforces the Soul Link rules and answers each event with the commands the client must carry out. A web UI shows the run, and stream overlays, an OBS scene switcher and a Twitch bot sit on top of it.

This page is for hosts and contributors. Players should start with the [README](../README.md) and the [companion patch guide](../patch/README.md). Field-level wire details live in [docs/protocol.md](protocol.md).

## Supported games

Both players must play the same family. The Manager's family keys come from `GAMES` in `server/manager.py`; the server refuses a second player whose cartridge belongs to another family.

| Manager family | Titles | Lua client | Server adapter | Companion patch | Randomizer |
|---|---|---|---|---|---|
| `gen1` | Red, Blue, Yellow (US) | `lua/gen1/` | `gen1_rby` | Required on Red and Blue; Yellow runs clean | Yes |
| `gen1_purergb` | PureRed, PureBlue, PureGreen ([pureRGB](https://github.com/Vortyne/pureRGB) v2.7.6) | `lua/gen1/` | `gen1_purergb` | Required | Yes |
| `gen2` | Gold, Silver (US), Crystal (US 1.0) | `lua/gen2/` | `gen2_gsc` | Required | No |
| `gen3` | FireRed, LeafGreen (US 1.0) | `lua/gen3/` | `gen3_frlge` | Required | Yes |
| `gen3_e` | Emerald (US) | `lua/gen3/` | `gen3_frlge` | Required | Yes |
| `gen3_rr` | Radical Red 4.1 | `lua/gen3/` | `gen3_frlge` (RR mode) | Required | No |
| `gen3_exp` | Emerald Expansion (a pinned reference build of pokeemerald-expansion 1.17.0, ROM sha1 `28877d73...`) | `lua/gen3/` | `gen3_exp` | None; runs clean | No |

Gen 4 (HeartGold, SoulSilver, Platinum; `lua/clients/gen4_hgsspt_client.lua`, adapter `gen4_hgsspt`) and Gen 5 (Black, White, Black 2, White 2; `lua/clients/gen5_bw_client.lua`, adapter `gen5_bw`) have clients and adapters with unit tests but have never been run against a real cartridge. They are experimental and the Manager does not offer them; run them with `python -m server.server` and the `lua/slink_gen4.lua` or `lua/slink_gen5.lua` launcher.

Cartridges are admitted by ROM hash. The Gen 1 and Gen 2 clients detect the title and admit or refuse it in `lua/gen1/entry.lua` and `lua/gen2/entry.lua`; the Gen 3 client admits by hash, then by engine-site anchors, and refuses header-only matches (`lua/gen3/entry.lua`, `Entry.admit_routed`). A randomized cartridge is admitted only when the run prepared it (see [Cartridges and the randomizer](#cartridges-and-the-randomizer)).

Per-generation runtime coverage is tracked in [docs/gen1_gen2_runtime_checks.md](gen1_gen2_runtime_checks.md).

## Requirements

| Requirement | Detail |
|---|---|
| Python | 3.11 or newer. `pip install -r requirements.txt` installs aiohttp, Jinja2 and aiohttp-jinja2. |
| Optional Python packages | `pip install "twitchio>=3.0" "simpleobsws>=1.4" psutil` for the Twitch bot and the OBS scene switcher. Both are imported lazily. |
| BizHawk | 2.11 or newer for Gen 1 and Gen 3 (`lua/slink.lua` refuses older versions). Gen 2 also runs on 2.9. |
| LuaSocket | Committed at `lua/x64/socket-windows-5-4.dll`; nothing to install. |
| ROMs | Your own dumps. None are distributed. |
| Java | Only for the randomizer, which runs the SLink fork of Universal Pokémon Randomizer ZX. |
| Node.js | Only to build the damage calculator (`cd calc && npm install && npm run build`). |
| Network | Both emulators must reach the server's TCP port; viewers must reach the HTTP port. |

## Running SLink

### With the Run Manager

```bash
pip install -r requirements.txt
python -m server.manager --host 0.0.0.0
```

Open `http://localhost:8090/`. Creating a run on the New-run form starts a dedicated `server.server` process for it with its own data directory (`data/runs/<run_id>/`), a game TCP port from 54321 up and an HTTP port from 8081 up. Ports are the first pair unused by the registry and bindable on the machine. The run page offers each player's setup ZIP (the release package with a launcher pre-set to the run's host, port and slot) and, where the Manager prepared them, each player's cartridge.

Manager options (`python -m server.manager --help`):

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address. |
| `--port` | `8090` | Manager HTTP port. |
| `--data-dir` | `data/runs` | Where runs live. A fresh directory is a fresh Manager. |
| `--allow-host NAME` | | Extra Host name the web UI answers to, such as a tunnel name or `*.<tailnet>.ts.net`. Repeatable; also `SLINK_ALLOWED_HOSTS` (comma-separated). Runs inherit it. |
| `--public-host ADDRESS` | this machine's LAN address | The address launchers and setup ZIPs connect to. Also settable on a run page (stored in `data/runs/settings.json`). |

The Manager registry is `data/runs/registry.json`. A run directory under `data/runs/` that is missing from the registry is adopted as a stopped run.

### A single server by hand

```bash
python -m server.server                      # TCP :54321, HTTP :8080, state in data/
python -m server.server --reset              # wipe state first
python -m server.server --species-clause --gender-clause --type-clause
```

Server options (`python -m server.server --help`):

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address. |
| `--port` | `54321` | Game TCP port. |
| `--http-port` | `8080` | HTTP port. |
| `--data-dir` | `data/` | Where `links.json`, `memorial.json`, `events.json` and `backups/` live. |
| `--reset` | | Clear saved state and start a fresh run. |
| `--run-id`, `--run-name` | | Run label for logs and display name for page titles. |
| `--manager-port` | | Set by the Manager. A managed run redirects its HTML pages to the Manager. |
| `--species-clause`, `--gender-clause`, `--type-clause` | off | Link clauses (see [Soul Link rules](#soul-link-rules)). |
| `--ignore-wild-faints` | off | Do not retire linked pairs for faints or whiteouts during wild battles. |
| `--explode-mode`, `--rival-team-swap`, `--overworld-presence`, `--native-messages`, `--native-sounds` | off | Run options (see [Run options](#run-options)). |
| `--no-battle-calc`, `--no-pc-trade-npc`, `--no-phone-calls` | | Turn off run options that default on. |
| `--verbose` | | DEBUG level for the console and `<data-dir>/slink.log` (the log file is always written, at INFO by default). |
| `--wire-log DIR` | | Write every TCP line to `DIR/wire_<player>.jsonl`. |
| `--allow-host NAME` | | As for the Manager. |

Some `--help` strings still say "RR only" for `--explode-mode` and `--rival-team-swap`; the table under [Run options](#run-options) is what the code supports.

### Loading the Lua client

Load the cartridge and its save in BizHawk first, then open **Tools > Lua Console** and load the launcher.

| Launcher | Use |
|---|---|
| The run's launcher (`slink_<run>_<player>.lua`, in the setup ZIP or from `/api/runs/{id}/launcher/{player}`) | Normal use. Host, port and player are set; on first load it asks for the SLink folder and caches it in `slink_path.cfg` next to the launcher. |
| `lua/slink.lua` | Universal entry point. Detects the system and title and loads the right client. Defaults: `127.0.0.1:54321`, player `a`. |
| `lua/slink_gen1.lua`, `lua/slink_gen3.lua`, `lua/slink_gen4.lua`, `lua/slink_gen5.lua` | Hand-edited launchers: set `SLINK_HOST`, `SLINK_PORT` and `SLINK_PLAYER` at the top, then load. Each one calls `lua/slink.lua`. There is no Gen 2 launcher; use a run launcher or `lua/slink.lua`. |
| `GET /launcher/{player}` on a standalone server | A launcher whose host comes from the request's `Host` header. |

`lua/slink.lua` mirrors every `console.log` line to `slink_lua.log` in the project root, truncated on each load. A client that cannot reach the server logs "Cannot reach the server" once and retries with exponential backoff (about 0.5 s doubling to a 30 s cap, `lua/connector.lua`).

## Architecture

```
[BizHawk A] lua/slink.lua ─┐                 (routes to lua/gen1/, lua/gen2/, lua/gen3/,
[BizHawk B] lua/slink.lua ─┤                  or lua/clients/gen4|gen5 for NDS)
                           │ newline-delimited JSON over one TCP connection per player
                           ▼
              server/server.py      aiohttp app: TCP listener + HTTP pages and API
              server/state.py       SoulLinkState: every Soul Link rule
              server/adapters/      per-game rules and presentation
              <data-dir>/           links.json, memorial.json, events.json, backups/
                           │
                           ▼
              HTTP: board, timeline, debug, calc, /stream/* overlays, Twitch, OBS

              server/manager.py     Run Manager on :8090, one server.server process per run
```

The Gen 1, Gen 2 and Gen 3 clients are composition roots (`entry.lua`) over modules for reads, writes, signals, boxes and the native panel and trade, with shared pieces in `lua/core/` and `lua/*.lua` (connector, HUD, admission, write permits, hook registry). Game facts (addresses, engine sites, area maps, write checkpoints) are generated JSON under `data/games/<pack>/`, which the clients and adapters read directly. Gen 4 and Gen 5 still use the older single-file shape: `lua/clients/<gen>_client.lua` plus `lua/games/<gen>.lua` and `lua/memory_nds.lua`.

A client diffs RAM each frame and sends an event only when something changes, plus a `tick` heartbeat every 30 frames carrying the party snapshot. The server replies on the same round trip. Commands that change party or box contents (`box_mon`, `party_mon`, `memorialize`, `apply_trade`) are deferred by the client until its own safe-state check passes; the rest act at once.

State persists to `links.json` on every mutation. A background task copies `links.json` and `events.json` into `backups/` every 5 minutes, keeping 6 slots. `memorial.json` is the log of retired pairs.

### Hosted access

When `SLINK_ADMIN_KEY` is unset, the Manager and run server retain their trusted-local behavior. When configured, public GET access is limited to boards, timelines, status/SSE, calculators, health checks and stream overlays. Run lifecycle actions, launchers and player packages, ROM tools, reset/debug/rollback, OBS and Twitch controls require either the encrypted administrator session cookie or `Authorization: Bearer <administrator key>`.

Each managed run stores separate Player A and Player B instrumentation keys. Generated launchers send the relevant key only in `hello`; the run server binds that TCP socket to the key-derived player slot until disconnect. A new socket may reconnect with the same key. These keys cross the existing game TCP connection in plaintext and identify telemetry sources; they do not provide transport confidentiality.

Container deployments may set `SLINK_DATA_DIR`, `SLINK_ROM_DIR`, `SLINK_PUBLIC_HOST` and `SLINK_TCP_PORT_POOL`. A configured TCP pool leases ports only to active runs. Desired run state survives Manager/container restarts and previously active children are respawned on startup.

### Adapters

Game-specific behaviour lives behind two abstract classes in `server/adapters/base.py`:

- `GameRulesAdapter`: mon keys, gift and egg areas, species, evolution, gender and types, admission and companion refusals, rival trainer IDs, memorial box, and the opt-in capabilities listed below.
- `GamePresentationAdapter`: sprites, species, move, item, ability and area names, trainer info, the Upcoming Key Trainers panel and the damage-calculator hooks.

The adapter is chosen from the client's `hello.rom_type` through `_ROM_TYPE_TO_GAME_ID` in `server/adapters/__init__.py` and committed to `links.json` once. Shared code (`server.py`, `state.py`, `base.py`, `pokemon_data.py`) never branches on a game ID: a game-specific behaviour gets an inert default on the base class and an override in the adapters that support it.

| Method | Default | Overridden by |
|---|---|---|
| `supports_explode_mode()` | `False` | Gen 1, pureRGB, Gen 2, Gen 3 (FireRed, LeafGreen, Emerald, Radical Red) |
| `rival_trainer_ids()` | empty | Gen 1, pureRGB, Gen 2, Radical Red |
| `party_blob_size()` | `0` (no party blobs) | Gen 1 (66 bytes: struct plus OT name and nickname), Gen 2, Gen 3 (100) |
| `supports_info_panel()` | `False` | Gen 1 Red and Blue, pureRGB and Gen 2 companion overlays, Gen 3 (FireRed, LeafGreen, Emerald, Radical Red) |
| `native_trade_ui()` | `False` | Gen 1 Red and Blue, pureRGB and Gen 2 companion overlays (the cartridge's receptionist drives the trade menus) |
| `supports_abilities()` | `True` | `False` on Gen 1 and Gen 2 |
| `status_token()` | `""` | Each generation's status bitfield |
| `gift_link_area()` | `gift_<area>` | Gen 2 |
| `trainers_for_area()`, `trainer_party()`, `trainer_brief()` | empty | Gen 3 (all titles, including the Expansion) |
| `calc_profile()` and the other `calc_*` methods | `None` (calc hidden) | Gen 1, pureRGB, Gen 2, Gen 3, Emerald Expansion |
| `memorial_box_index()` | `-1` | Gen 1 and Gen 2: last box. Gen 3: Box 14, or Box 25 on Radical Red. Gen 4: Box 18. Gen 5: Box 24. |

`server/adapters/gen4_hgsspt.py` is the simplest complete adapter and the best template for a new one. [docs/protocol.md](protocol.md) section 7 lists the full adapter contract.

## Soul Link rules

| Rule | Behaviour |
|---|---|
| Nuzlocke gate | The rules below stay inactive for a player until the client reports Poké Balls in the bag. |
| Encounter linking | Each player's first capture in an area is paired with the other player's first capture there. |
| Encounter retry | With `retry_same_species` enabled (the default), the first eligible species seen by each player is persisted for that area. A failed attempt sends `unresolve_area`; later encounters remain open, but catching another species force-faints and memorializes that illegal catch. Gifts, eggs, shiny bonuses and clause rerolls keep their existing exemptions. |
| Dead zone | With `retry_same_species` disabled (`--dead-zone-on-miss`), a failed catch closes the area for both. The missed species and level are recorded. |
| Unlinked capture | A capture waiting for its partner's catch is sent to the PC until the pair forms. |
| Illegal capture | A capture in an area that is already linked or dead, or a second capture in the same area, is fainted and sent to the memorial box. |
| Faint propagation | When a linked Pokémon faints, its partner is sent `force_faint` (or `force_explode` under Explode Mode) on the same round trip. |
| Whiteout | When a player's whole party faints, the partners of their linked party Pokémon are fainted too, and the party is rebuilt from boxed survivors. |
| Run over | Once both gates are open and at least one pair has formed, the run ends (`game_over`) when no live pair and no pending capture remain. |
| PC release | Releasing a linked Pokémon kills its partner. |
| Party sync | Both halves of a pair are in the party, or both in the PC. A withdrawal happens only when both players have party room. |
| Memorial box | Dead pairs move to the adapter's memorial box once each game reports a safe overworld state. |
| Gifts and eggs | Link in their own `gift_<area>` namespace (adapter `gift_link_area()`): they pair with each other, do not close or consume the real encounter area, skip the unlinked-capture quarantine and do not open the Nuzlocke gate. Day-care eggs are normal captures. |
| Shiny bonus | Always on. A player who catches a shiny gives their partner a bonus encounter: the partner's next capture pairs with the shiny under the normal rules, and the triggering area is not consumed. Bonuses queue in order. Gen 1 has no shinies. |
| Species clause (opt-in) | Refuses a link when both Pokémon share an evolution family, and refuses a capture when either player already has a live linked Pokémon of that family. A duplicate seen at the start of a wild battle prompts the player to reroll. |
| Gender clause (opt-in) | Refuses a link when both Pokémon have the same gender. Genderless Pokémon are exempt. Not offered on Gen 1 or pureRGB. |
| Type clause (opt-in) | Refuses a link when the two Pokémon share any type, using the adapter's type data. |
| NPC trades | After an in-game NPC trade the enabled clauses are rechecked against the new Pokémon. A violation retires only that pair. |

A capture that breaks a clause is fainted and the area stays open for another try.

### Player identity lock

The first `hello` that carries a trainer ID (the hello's `ot_id`, or failing that the OT ID in the first party Pokémon's key) locks that slot to the save. A later hello from a different save is refused: the client shows "WRONG SAVE" on the HUD for about 10 seconds, the board shows an identity banner, and further events from that connection are ignored until the right save connects. Hellos with an empty party do not lock. Each slot locks independently, and the lock is stored in `links.json` under `player_identity`.

## Run options

The New-run form greys out what a family cannot do, with the reason, from `OPTION_SUPPORT` in `server/manager.py`. `RUN_FLAGS` maps each option to the server flag the Manager passes when the value differs from the default.

| Option | Server flag | Default | Offered for |
|---|---|---|---|
| Species Clause | `--species-clause` | off | All families |
| Gender Clause | `--gender-clause` | off | All except Gen 1 and pureRGB (no genders) |
| Type Clause | `--type-clause` | off | All families |
| Ignore Wild Faints | `--ignore-wild-faints` | off | All families |
| Explode Mode | `--explode-mode` | off | All except Emerald Expansion |
| Rival Swap | `--rival-team-swap` | off | All except Emerald Expansion |
| Overworld Presence | `--overworld-presence` | off | None yet |
| Native Messages | `--native-messages` | off | None yet |
| Native Sounds | `--native-sounds` | off | All except Emerald Expansion; does nothing on an unpatched cartridge (Yellow) |
| Phone Calls | `--no-phone-calls` | on | Gold, Silver, Crystal |
| Battle Calc | `--no-battle-calc` | on | Radical Red, Emerald Expansion |
| PC Trade NPC | `--no-pc-trade-npc` | on | FireRed, LeafGreen, Emerald, Radical Red |

Explode Mode: when a linked Pokémon dies in battle, its partner's active battler is forced to use Explosion (`force_explode`); a benched partner is fainted normally. Gen 1 and Gen 2 need no patch for it, since the move choice is a plain RAM write.

Ignore Wild Faints: when the server's live battle state identifies a wild battle, individual faint and whiteout events do not retire any linked pair. Trainer-battle faints and out-of-battle deaths still use the normal Soul Link death rule.

Rival Swap: on a `trainer_battle_start` against one of the adapter's rival IDs, the server sends `replace_rival_team` with the partner's live party blobs, and the client writes them over the enemy party. Gen 1 and Gen 2 write the plaintext enemy party directly; Gen 3 needs the companion patch because the enemy party is encrypted. On FireRed, LeafGreen and Emerald the Manager offers the option, but the Gen 3 adapter lists rival trainers only for Radical Red, so it has no effect there.

Phone Calls: on Gen 2 the companion rings the Pokégear when a pair links, an area dies or a Pokémon falls. The HUD message shows either way.

Battle Calc: the in-game damage and type-effectiveness display. It is separate from the board's damage preview and the web calculator.

PC Trade NPC: on Gen 3 the in-game trade is offered by an NPC in the Pokémon Center. On Gen 1, pureRGB and Gen 2 the trade is at the Cable Club receptionist and is always available on a patched cartridge.

Overworld Presence and Native Messages are accepted by the server and stored, but the Manager greys them out because the current clients do not implement them.

## Companion patch

Every title except Yellow and the Emerald Expansion needs the SLink companion patch, and the launcher or server refuses a clean cartridge of those titles. The list is `COMPANION_TITLES` in `server/cartridges.py`. The patch adds the SoulLink title screen, the SLINK panel in the START menu (`link_panel`), native sounds, the in-game trade and, on Gen 3, the native operations the Rival Swap needs.

The Manager patches each cartridge it prepares. To patch your own, use `/patcher` on the Manager or a run server; it applies the UPS in the browser. The patches are in `patch/dist/` and are served at `/companion/{name}` (for example `/companion/SLink-RR.ups`). Build and design details are in [patch/README.md](../patch/README.md) and [patch/gen1/README.md](../patch/gen1/README.md).

### In-game trade

A player may trade a Pokémon only for its own linked partner: the two halves of a live pair swap games, and both must be in their owners' parties. On Gen 1, pureRGB and Gen 2 the Cable Club receptionist runs the trade; on Gen 3 the Pokémon Center NPC opens a Trade / Say hey menu. The server runs the trade as a small state machine (`trade_request`, `choose_mon`, `apply_prepare`, `apply_trade`, `trade_done`) with a watchdog. A trade the server cannot settle on its own shows a banner on the board, and `POST /api/debug/resolve_trade` settles it. The sub-protocol is in [docs/protocol.md](protocol.md) section 6.

## Cartridges and the randomizer

On the New-run form, the Cartridges step picks each player's ROM from the SLink folder, `roms/`, `patch/build/` and local `.cache` build folders (`.gb`, `.gbc`, `.gba`), or uploads one. The Manager then makes each player's cartridge (`server/cartridges.py`): a copy, the companion patch applied, and randomized if asked. Outputs land in the run directory as `roms/a.<ext>` and `roms/b.<ext>` and download from `/api/runs/{id}/rom/{player}`.

The randomizer supports Red, Blue and Yellow, pureRGB, Gold, Silver and Crystal, FireRed and LeafGreen, and Emerald (`FAMILIES` in `server/upr_settings.py`). On Gen 2 the companion patch is applied first and the patched cartridge is then randomized (`docs/gen2/RANDOMIZER.md`); move tutors exist only on Crystal. Radical Red and the Emerald Expansion cannot be randomized, and the Manager refuses the request by name (`NON_RANDOMIZABLE_GAMES` in `server/manager.py`). Both cartridges get the same settings and different seeds. The run records the settings, both seeds and both ROM hashes in `rom_contract.json`, and the server then admits only those two cartridges.

The randomizer is the SLink fork of Universal Pokémon Randomizer ZX (`tools/build_upr_fork.py`, patches in `patch/upr/`). Only a jar whose SHA-256 is listed in `data/upr_jars.json` is run. `find_upr_jar()` in `server/upr_pipeline.py` looks at `$SLINK_UPR_JAR`, then `PokeRandoZX.jar` in the repo root and `tools/`, then `.cache/slink-upr/` and `.cache/upr/`. A jar uploaded through the form is saved as the repo-root `PokeRandoZX.jar`. Settings can be saved as presets and exported or imported as UPR `.rnqs` files. Settings that change data the rules depend on (types, evolutions, moves, base stats) are refused (`forbidden_enabled` in `server/upr_settings.py`).

## Web UI

The run server and the Manager render the same templates (`server/templates/`), refreshed by HTMX every 2 seconds with idiomorph swaps so open `<details>`, scroll position and focus survive. The theme picker stores its choice in `localStorage` and a `slink-theme` cookie.

### Board

`GET /` on a run server, `/runs/{id}` on the Manager. `server/board.py` turns the status payload into rows; `server/templates/_board.html` draws them.

Each player has a Now card: trainer, cartridge, badges, area, ball count and last event, then the current battle (the player's active Pokémon and each foe with status, HP, stat stages and moves with PP) or, out of battle, the party lead. In battle the card shows a damage preview for each move (`server/static/calc-preview.js`), for every game whose adapter has a `calc_profile()`. It needs the calculator to be built. On Gen 3 the card also lists Upcoming Key Trainers for the current area, with an Open in Calc button.

Below, one row per linked pair (each player's half either side of the bond, with HP, ability and held item where the game has them), sorted into In party, Pending link, Split (one half boxed), Boxed, Linked (a stopped run, where each half sits is unknown) and Fallen. The event log runs beside the board on wide screens. Banners report a save failure, game over, a wrong save, a refused cartridge and unsettled trades.

### Timeline

`GET /timeline`, or `/runs/{id}/timeline` on the Manager: the run in order, from `server.board.timeline`. It lists pairs formed, deaths, dead zones and burials with their causes, then the areas still open.

### Debug console

`GET /debug`, or `/runs/{id}/debug` on the Manager: manual linking (by key or party slot), event injection, command queueing, Nuzlocke gate and area-state overrides, unlink and revive, trade and ambiguous-key resolution, a live state panel and backup rollback. Every action is an `/api/debug/*` call listed below.

### Stream overlays

Every overlay is an OBS browser source at `/stream/{slug}`, defined in `server/overlay_catalog.py` and drawn from `server/templates/stream/`. The overlay gallery (`/stream`, or Broadcast on the Manager) lists each URL with its recommended size. On the Manager, `/stream/{name}` proxies to the pinned run (else the most recent running one), so OBS URLs stay fixed across runs.

| Slug | Shows |
|---|---|
| `party-a`, `party-b` | A player's party: sprites, HP bars, levels. |
| `links` | Live pairs as cards, dead pairs dimmed below. |
| `linked-party` | Pairs with both halves in the party, with HP, levels and area. |
| `boxed-links` | Live pairs with a half in the PC. Scrolls when it overflows. |
| `focus-a`, `focus-b` | The player's active battler: sprite, HP, status, stat stages, moves with PP. |
| `enemy-focus-a`, `enemy-focus-b` | The active foe or foes, wild or trainer, with moves and live PP. Both foes in a double battle. |
| `enemy-trainer-a`, `enemy-trainer-b` | The opposing trainer's whole team, scrolling. Hidden in wild battles. |
| `deaths` | Alive and dead pair counts. |
| `attempts` | The run attempt counter (`POST /api/attempts`). |
| `stream-memorial` | Memorial Scroll: every dead pair, scrolling. |
| `badges-a`, `badges-b` | A player's badges, unearned ones dimmed. |
| `areas` | Linked, dead and pending area counts. |
| `events` | Live event feed. |
| `encounters` | Total encounters, shiny count and the last linked pair. |
| `ticker` | Horizontally scrolling event marquee. |
| `enc-table-a`, `enc-table-b` | Wild encounter rates for the player's current area (Radical Red and Gen 1 Red/Blue/Yellow). |
| `area-encounter` | Soul Link status of the most recently active area. |

Each overlay polls its own `/stream/{slug}/fragment` every 2 seconds. Query parameters:

| Parameter | Applies to | Values |
|---|---|---|
| `theme` | All | `default`, `light`, `transparent`, `funtastic-grape`, `funtastic-jungle`, `funtastic-fire`, `funtastic-ice`, `funtastic-watermelon`, `funtastic-smoke`. Falls back to the `slink-theme` cookie. |
| `layout` | `party-a`, `party-b` (`h`, `thin-h`, `thin-v`); `boxed-links` (`thin-v`) | |
| `speed` | Scrolling overlays | Multiplier from 0.25 to 3, default 1. |
| `pause` | `boxed-links`, `enemy-trainer-*` | Seconds to pause after each loop, 0 to 10, default 2. |

## Damage calculator

`calc/` is a fork of the [RadicalRedShowdown damage calculator](https://github.com/RadicalRedShowdown/damage-calc), itself a fork of the Smogon calculator. The run server serves it at `/calc/` (which redirects to `/calc/normal.html`; `hardcore.html` is the other page) and the Manager at `/runs/{id}/calc`. Files resolve from `calc/src/` first, then `calc/dist/` (`server/calc_files.py`); the HTML entry points exist only in `dist/`, so the calculator, and the board's damage preview, need a build:

```bash
cd calc && npm ci --ignore-scripts && npm ci --prefix calc --ignore-scripts && npm run build
```

The production `Dockerfile` runs that locked build in its `calc-builder` stage and copies `calc/dist/` into the runtime image, so hosted deployments do not depend on generated files being present in the checkout.

The adapter's `calc_profile()` picks the mechanics, dex and trainer sets: Radical Red and the Emerald Expansion at generation 9 with their own sets; FireRed, LeafGreen and Emerald at generation 3; Gold, Silver and Crystal at generation 2 (only Crystal has trainer sets); Red, Blue and Yellow at generation 1; pureRGB at generation 1 with its own dex and sets. A game whose profile is `None` (Gen 4, Gen 5) has no calculator.

The SLink panel inside the calculator (`calc/src/js/slink_bridge.js`) reads `GET /api/calc/mons` and refreshes on the `/api/events` server-sent events. It shows each player's party, linked Pokémon and current foes; clicking a row loads that Pokémon into the attacker or defender slot. Its Prep tab lists trainers for pre-battle planning, and the board's Open in Calc button opens a trainer there. The Radical Red key-trainer roster is generated:

```bash
python tools/gen_rr_priority_trainers.py   # data/games/gen3_frlge/rr_priority_trainers.json + calc/src/js/data/sets/slink_priority.js
```

More detail, including set matching and the generation and dex options, is in [calc/README.md](../calc/README.md).

## Twitch bot

The bot uses twitchio 3.x over EventSub. Configure it on `/twitch` (or Broadcast > Twitch on the Manager).

1. Register an app at [dev.twitch.tv/console](https://dev.twitch.tv/console) (category Chat Bot, client type Confidential, OAuth redirect URL `https://twitchtokengenerator.com/`). Note the Client ID and create a Client Secret.
2. At [twitchtokengenerator.com](https://twitchtokengenerator.com), make a Custom Scope Token with your Client ID and the scopes `user:read:chat`, `user:write:chat`, `user:bot` and `channel:bot`, authorizing as the account the bot should post as (your own, or a separate bot account).
3. Set `TWITCH_ACCESS_TOKEN`, `TWITCH_REFRESH_TOKEN` and `TWITCH_CLIENT_SECRET` in the environment before starting the server or Manager. Tokens are never written to disk by SLink; twitchio may create `.tio.tokens.json` (gitignored).
4. Set the channel, bot name, Client ID, prefix and cooldown on `/twitch`. They are saved to `twitch_bot.json` in the run's data directory (template: `data/twitch_bot.example.json`).

| Command | Reply |
|---|---|
| `!soullink` | The Soul Link rules in plain English. |
| `!clauses` | Which clauses are on. |
| `!rip` | The most recent death and what caused it. |
| `!runstats` | Attempt number, alive and dead counts, shinies, oldest pair. |
| `!alltime` | Totals across runs. |
| `!lastrun` | How the previous run ended. |
| `!attempts` | Current attempt number. |
| `!partner <name>` | A Pokémon's linked partner, by nickname. |
| `!area <name>` | An area's link status. |

## OBS scene triggers

SLink can switch scenes in each player's OBS through obs-websocket v5 (`simpleobsws`).

1. In OBS, enable **Tools > WebSocket Server Settings** (default port 4455).
2. On `/obs` (or Broadcast > OBS on the Manager), enter host, port and password for each player's OBS, save and connect.

The config is global, at `data/obs_config.json`; passwords are never returned by the API. Each rule maps an event to a scene, with a player filter (`any`, `a`, `b`), a target OBS (`own`, `a`, `b`, `both`) and, for area events, an area filter: an `area_id` or a group (`group:route`, `group:city`, `group:cave`, `group:forest`, `group:tower`, `group:building`, `group:water`, `group:gift`, `group:other`, from `classify_area` in `server/obs_controller.py`).

Events: `battle_start`, `wild_battle_start`, `trainer_battle_start`, `battle_end`, `battle_start_new` (in an area with an open encounter), `area_enter`, `area_enter_new`, `faint`, `link_death`, `whiteout`, `capture`, `shiny`, `linked`, `dead_zone`, `party_to_box`, `box_to_party`, `memorialize_done`, `run_over`.

Several events can fire at once. Rules are evaluated top to bottom and the first match per OBS instance wins; drag rules to reorder (saved through `POST /api/obs/triggers`). Each OBS connection has a one-slot queue, so a slow OBS only receives the latest scene, and reconnects with backoff from 5 s to 60 s. OBS failures never affect the game server.

## TCP protocol

One persistent TCP connection per player, carrying newline-delimited JSON. Every message from the client is an event with an `event` field; the reply is `{"commands": [...]}`, each command an object with a `cmd` field. [docs/protocol.md](protocol.md) has every field, ordering rule and acknowledgement.

### Events (client to server)

| Event | Meaning |
|---|---|
| `hello` | Handshake: `rom_type`, artifact kind, trainer name and ID, party, capabilities. Admission and the identity lock run here; the reply carries `config`, `resolved_areas` and `dead_keys`. |
| `tick` | Heartbeat every 30 frames with the party snapshot, PC boxes and battle state. |
| `safe` | The client is back in a safe overworld state. |
| `area_enter` | Entered an area (`area_id`). |
| `capture` | Caught a Pokémon: `key`, `area_id`, `level`, `gift` for gifts and eggs. |
| `no_catch` | Left an encounter without catching. The server either persists/reuses the player's first-species lock and reopens the area, or applies the classic dead-zone rule when that run option is disabled. |
| `faint`, `whiteout` | A party Pokémon fainted; the whole party fainted. |
| `release` | A Pokémon was released from the PC. |
| `party_to_box`, `box_to_party` | A known Pokémon moved between party and PC. |
| `stats_cache` | Party-only stats of a Pokémon about to be boxed, so `party_mon` can restore them. |
| `key_change` | A Pokémon's key changed (evolution, NPC trade, nature change, pureRGB transformation). |
| `sync_retrieve_done`, `sync_retrieve_failed`, `box_mon_failed`, `memorialize_done`, `memorialize_failed` | Results of deferred commands. |
| `trainer_battle_start` | A trainer battle began (`trainer_id`). Drives Rival Swap. |
| `rival_team_replaced` | Result of `replace_rival_team`, with a species readback. |
| `status` | Companion patch presence and features. |
| `trade_request`, `trade_query`, `trade_offer`, `mon_chosen`, `menu_result`, `apply_ready`, `trade_done` | The in-game trade and native menus. |
| `peer_interact`, `ghost_pos` | Overworld Presence. The server relays `ghost_pos` only when the option is on. |

### Commands (server to client)

| Command | Meaning |
|---|---|
| `force_faint` | Set a Pokémon's HP to 0 (linked death, illegal capture, whiteout). |
| `force_explode` | Explode Mode: force the active battler to use Explosion. |
| `box_mon`, `party_mon` | Deposit or withdraw a Pokémon. Deferred. |
| `memorialize` | Move a dead Pokémon to the memorial box. Deferred. |
| `hud_show`, `msgbox`, `gui_prompt` | HUD line (`text`, `r`, `g`, `b`, `frames`), native message box (falls back to the HUD), and a prominent prompt (clause rerolls). |
| `play_sound` | A sound effect, through the cartridge when Native Sounds is on and the patch supports it. |
| `config` | Per-run options, in every hello reply. |
| `resolved_areas`, `unresolve_area` | Areas already decided (hello reply); reopen an area (shiny bonus, clause reroll). |
| `dead_keys` | The player's dead Pokémon, so the client keeps their HP at 0. |
| `key_change_ack`, `key_change_rejected` | Answer to `key_change`. |
| `link_panel` | Rows for the in-game SLINK panel, built per recipient and sent only when they change. |
| `replace_rival_team` | Rival Swap: the partner's party blobs to write over the enemy party. |
| `rebuild_start`, `rebuild_done` | Bracket a party rebuild after a whiteout. |
| `game_over` | The run has ended. Re-sent on reconnect. |
| `show_menu`, `show_choices`, `choose_mon` | Native yes/no menu, multiple-choice list and party picker. |
| `trade_mask`, `trade_offer_ack`, `apply_prepare`, `apply_trade`, `withdraw_trade`, `trade_final` | In-game trade steps. `apply_trade` is deferred. |
| `ghost_pos` | Overworld Presence: the partner's position. |
| `noop` | Nothing to do, or a refused message (`refused` says why). |

The OBS event names above are a separate set derived on the server, even where a name matches a protocol event.

## HTTP routes

`tests/unit/test_routes_documented.py` fails if a route registered by either app is missing from this page.

### Run server (default port 8080)

| Path | Method | Description |
|---|---|---|
| `/` | GET | The board. |
| `/healthz` | GET | Process liveness for container probes. |
| `/readyz` | GET | Readiness: mounted data directory is writable and state persistence has no standing error. |
| `/admin/login` | GET, POST | Shared administrator-key login; sets an encrypted session cookie. |
| `/admin/logout` | POST | Clear the administrator session. |
| `/timeline` | GET | The run's timeline. |
| `/debug` | GET | Debug console. |
| `/twitch` | GET | Twitch bot settings and activity. |
| `/obs` | GET | OBS scene triggers. |
| `/stream`, `/stream/` | GET | Overlay gallery. |
| `/stream/{slug}` | GET | One overlay per slug in the table above, for example `/stream/party-a`, `/stream/party-b`, `/stream/links`, `/stream/linked-party`, `/stream/boxed-links`, `/stream/focus-a`, `/stream/focus-b`, `/stream/enemy-focus-a`, `/stream/enemy-focus-b`, `/stream/enemy-trainer-a`, `/stream/enemy-trainer-b`, `/stream/deaths`, `/stream/attempts`, `/stream/stream-memorial`, `/stream/badges-a`, `/stream/badges-b`, `/stream/areas`, `/stream/events`, `/stream/encounters`, `/stream/ticker`, `/stream/enc-table-a`, `/stream/enc-table-b`, `/stream/area-encounter`. |
| `/stream/{slug}/fragment` | GET | The fragment each overlay polls. Same query parameters. |
| `/launcher/{player}` | GET | A launcher for player `a` or `b`, host taken from the request. |
| `/calc`, `/calc/` | GET | Redirect to `/calc/normal.html`. |
| `/calc/{path}` | GET | Calculator pages and files. |
| `/patcher` | GET | In-browser companion patcher. |
| `/companion/{name}` | GET | A companion UPS from `patch/dist/`. |
| `/static/...` | GET | CSS, JS, fonts, themes (`server/static/`). |
| `/api/status` | GET | The full status payload. |
| `/api/events` | GET | Server-sent events: a `ping` on each state change (clients then fetch `/api/status`), plus comment heartbeats. Used by the calculator panel. |
| `/api/calc/mons` | GET | Calculator profile plus each player's party, linked Pokémon and foes with Showdown pastes. |
| `/api/attempts` | POST | `{"count": N}` sets the attempt counter (persisted in `links.json`). |
| `/api/reset` | POST | Delete `links.json` and start a fresh run with the same rules. |
| `/api/inject_link` | POST | `{a_key, b_key, area_id, force?}`: link two Pokémon by key. A Pokémon pending in another area needs `force`. |
| `/api/inject_link_by_slot` | POST | `{a_slot, b_slot, area_id, force?}`: link by party slot (0-based). |
| `/api/bot/status` | GET | Bot status, config and activity log. |
| `/api/bot/config` | POST | Save bot settings (no tokens). |
| `/api/bot/reload`, `/api/bot/enable`, `/api/bot/disable` | POST | Restart, enable or disable the bot. |
| `/api/bot/preview` | POST | What a command would reply, without posting. |
| `/api/obs/status` | GET | Connection state and rules, passwords redacted. |
| `/api/obs/config` | POST | Save OBS settings and reconnect. |
| `/api/obs/connect`, `/api/obs/disconnect` | POST | Connect or disconnect one or both players' OBS. |
| `/api/obs/scenes/{player}` | GET | Scene names from a connected OBS. |
| `/api/obs/triggers` | POST | Save the rule list. |
| `/api/obs/areas` | GET | Area groups for the area filter picker. |
| `/api/obs/test` | POST | Test a scene switch. |
| `/api/debug/raw_state` | GET | `links.json` plus live-only fields. |
| `/api/debug/manual_link_data` | GET | Known keys and area IDs for the linking form. |
| `/api/debug/backups` | GET | Backup slots with timestamps and counts. |
| `/api/debug/rollback` | POST | `{"slot": N}`: restore `links.json` and `events.json` from a backup slot. The current files are kept as `*.pre_rollback.json`. |
| `/api/debug/inject_event` | POST | `{player, event, ...}`: run a synthetic event through the state machine; returns the commands it produced. |
| `/api/debug/queue_command` | POST | `{player, cmd, ...}`: queue any command for a player's next reply. |
| `/api/debug/set_pokeballs` | POST | `{player, value}`: open or close a player's Nuzlocke gate. |
| `/api/debug/set_area_state` | POST | `{area_id, state}` with `unseen`, `pending_a`, `pending_b`, `pending_both`, `linked` or `dead_zone`. |
| `/api/debug/clear_pending` | POST | `{area_id?}`: clear pending captures, all or one area. |
| `/api/debug/unlink` | POST | `{area_id, index?}`: remove a link and reset the area. |
| `/api/debug/revive` | POST | `{area_id, index?}`: mark a dead pair alive. The Pokémon must be restored in-game by hand. |
| `/api/debug/resolve_trade` | POST | `{token, action, sides?}` with `action` `commit`, `rollback` or `adopt`: settle an in-game trade the server could not. Check both parties first. |
| `/api/debug/resolve_ambiguous_key` | POST | `{player, key}`: clear an ambiguous-key hold after checking the cartridge. |

`/api/status` returns `players` (per player: connection, `rom_type`, trainer, area, `ball_count`, `nuzlocke_active`, `party_keys`, `party_details`, `pc_boxes`, `battle_state`, `encounter_table`, `capabilities`, admission and identity errors), `links`, `area_states`, `pending_captures`, `killfeed`, `recent_events`, `rules`, `attempts_count`, `run_over` and the trade and save-failure flags. The empty shape is `server/status_payload.py`.

Example calls:

```bash
curl http://localhost:8080/api/status
curl -X POST http://localhost:8080/api/debug/inject_event -H "Content-Type: application/json" \
  -d '{"player": "a", "event": "capture", "key": "12345678:87654321", "area_id": "route_1", "level": 12}'
curl -X POST http://localhost:8080/api/debug/queue_command -H "Content-Type: application/json" \
  -d '{"player": "a", "cmd": "hud_show", "text": "Route 3 is closed", "r": 255, "g": 80, "b": 80, "frames": 180}'
```

### Run Manager (port 8090)

| Path | Method | Description |
|---|---|---|
| `/` | GET | The first running run's board, or the New-run form when there are no runs. |
| `/healthz` | GET | Manager process liveness for container probes. |
| `/readyz` | GET | Readiness: registry is readable and the mounted Manager data directory is writable. |
| `/admin/login` | GET, POST | Shared administrator-key login; sets an encrypted session cookie. |
| `/admin/logout` | POST | Clear the administrator session. |
| `/new` | GET | New-run form: family, options with reasons, the Cartridges step (pick, patch, randomize, presets, `.rnqs` import and export). |
| `/runs/{run_id}` | GET | A run's page: start, stop, pin, launchers, cartridges and its board (live, or as persisted once stopped). |
| `/runs/{run_id}/board` | GET | The board fragment the run page polls. |
| `/runs/{run_id}/cartridges` | GET | What each player plays, the downloads and the `.rnqs` used. Also at `/runs/{run_id}/randomizer`. |
| `/runs/{run_id}/timeline` | GET | The run's timeline. |
| `/runs/{run_id}/debug` | GET | The run's debug console. |
| `/runs/{run_id}/calc`, `/runs/{run_id}/calc/{path:.*}` | GET | The calculator for that run. |
| `/calc/{path:.*}` | GET | Calculator assets requested by absolute path. |
| `/runs/{run_id}/api/{tail:.*}` | GET, POST | Relayed to that run's `/api/{tail}`, including `/api/events`. |
| `/broadcast` | GET | Overlay gallery (same page as `/stream`). |
| `/tools` | GET | The patcher and cartridge tools. |
| `/stream`, `/stream/` | GET | Overlay gallery. |
| `/stream/{name}`, `/stream/{name}/{suffix:fragment}` | GET | Proxied to the pinned run, else the most recent running one. |
| `/patcher`, `/companion/{name}` | GET | The patcher and companion UPS files. |
| `/api/runs` | GET | The run registry. |
| `/api/runs/new` | POST | `{name, game, ...options}`: create and start a run. `game` is a family key from `GAMES`. A server that fails to start is reported in `start_error`. |
| `/api/runs/{id}/start` | POST | Start a run. |
| `/api/runs/{id}/stop` | POST | Stop a run. |
| `/api/runs/{id}/archive` | POST | Archive a run. |
| `/api/runs/{id}/delete` | POST | Delete a run. |
| `/api/runs/{id}/live` | GET | The run's `/api/status`. |
| `/api/runs/{id}/launcher/{player}` | GET | The player's launcher. |
| `/api/runs/{id}/player-pack/{player}` | GET | The player's setup ZIP (`tools/make_release.py` package with the run's launcher). |
| `/api/runs/{id}/cartridges` | POST | `{rom_a, rom_b, companion?, randomize?, jar?, spec? or categories? or settings?}`: make each player's cartridge. |
| `/api/runs/{id}/randomize` | POST | Older form of `cartridges` with randomizing implied. |
| `/api/runs/{id}/rom/{player}` | GET | Download a player's cartridge. |
| `/api/runs/{id}/settings.rnqs` | GET | The `.rnqs` the pair was built with. |
| `/api/randomizer/status` | GET | `?jar=&rom_a=&rom_b=`: jar trusted, Java present, each ROM a clean dump. |
| `/api/randomizer/settings/export` | POST | `{spec, name?}` to a `.rnqs` file. |
| `/api/randomizer/settings/import` | POST | Multipart `.rnqs` to `{spec, summary}`, or a refusal naming what it changes. |
| `/api/presets` | GET, POST | Saved randomizer presets (`data/runs/presets.json`); POST `{name, spec}`. |
| `/api/presets/delete` | POST | `{name}`. |
| `/api/roms` | GET | Every ROM the Manager can see, with the scanner's verdict and family. |
| `/api/roms` | POST | Multipart upload into `roms/` (a trusted jar becomes `PokeRandoZX.jar`); 64 MiB cap. |
| `/api/settings/public-host` | POST | `{host}`: the address launchers connect to; `""` means automatic. |
| `/api/stream/pin` | GET, POST | Which run the overlays show. |
| `/api/status`, `/api/attempts` | GET, POST | Proxied to the pinned run. |
| `/api/bot/{tail:.*}`, `/api/obs/{tail:.*}` | GET, POST | Relayed to the pinned run for the Broadcast panels. |

`/broadcast/{tab:twitch|obs}` (GET) shows the pinned run's Twitch or OBS panel in the Manager.

A run started by the Manager redirects its own HTML pages (`/`, `/timeline`, `/debug`, `/twitch`, `/obs`, calculator pages, `/patcher`, `/stream`) to the Manager's equivalents. Its overlays, API and calculator files are served as usual.

## Development

### Tests

```bash
pip install -r requirements-dev.txt
pytest tests/unit/ -q                     # no emulator needed
pytest tests/integration/ -q              # starts a real server on a free port
ruff check .
python tools/lua_syntax_check.py          # Lua 5.5 syntax via lupa; the system luac 5.1 rejects valid files
```

Live gates and two-emulator runs need EmuHawk and your ROMs, and skip cleanly when either is absent:

```bash
SLINK_LIVE=1 pytest tests/live/test_lua_gates.py -q         # Gen 3
SLINK_LIVE=1 pytest tests/live/test_gen1_new_gates.py -q    # Gen 1
SLINK_LIVE=1 pytest tests/live/test_gen2_new_gates.py -q    # Gen 2 (more gates in tests/live/test_gen2_*.py)
SLINK_E2E=1 pytest tests/e2e/test_duo_gen3.py -q            # two emulators and a server; also test_duo_gen1_new.py,
                                                            # test_duo_gen1_pure.py, test_duo_gen2_new.py
python tools/e2e_duo.py --game gen3_rr --scenario all       # the duo runner directly
python tools/verify_gen1_release.py --list                  # release checks (also verify_gen2_release.py, verify_gen3_release.py)
```

Gen 1 and Gen 2 fixtures are committed battery saves (`tests/fixtures/gen1/`, `tests/fixtures/gen2/`), not version-locked savestates. [tests/TESTING.md](../tests/TESTING.md) has the manual walkthrough, the per-suite table and the rule that absent input skips while present-but-wrong input fails.

`python tools/inject_full_mocks.py` fills a running server with mock pairs, a dead zone, a boxed pair, a memorial and a battle for UI work.

### Generated data

Game facts under `data/games/<pack>/` (profiles, engine signals, write checkpoints, area maps, species, trainers, encounters) are generated from pinned decompilation sources by `tools/gen_*.py`. Change the generator, not the output. The Gen 1 symbol tables come from `python tools/build_pret_syms.py` (add `--rom-syms` for ROM labels).

### Key files

| Path | Purpose |
|---|---|
| `lua/slink.lua` | Universal entry point: routes Game Boy titles to `lua/gen1/` or `lua/gen2/`, GBA to `lua/gen3/` after admission, NDS through `lua/game_detect.lua` to the Gen 4/5 clients. |
| `lua/gen1/`, `lua/gen2/`, `lua/gen3/` | Clients: `run.lua` (BizHawk bootstrap), `entry.lua` (composition root), `client.lua` and modules. |
| `lua/core/`, `lua/*.lua` | Shared Lua: session, deferred queue, identity, connector, HUD (`hud.lua`), admission, write permits, hook registry, JSON codec. |
| `lua/clients/`, `lua/games/`, `lua/memory_nds.lua` | Gen 4 and Gen 5 clients. |
| `server/server.py` | aiohttp app, TCP handling, status payload, HTTP routes. |
| `server/state.py` | `SoulLinkState`: rules, persistence, trade state machine. |
| `server/adapters/` | Per-game adapters and codecs. |
| `server/manager.py` | Run Manager: `GAMES`, `OPTIONS`, `OPTION_SUPPORT`, `RUN_FLAGS`, run lifecycle, routes. |
| `server/cartridges.py`, `server/upr_pipeline.py`, `server/upr_settings.py` | Cartridge preparation and the randomizer. |
| `server/board.py`, `server/templates/`, `server/static/` | Board and timeline logic, templates (`_rail.html` is the shared sidebar), CSS, JS and themes. |
| `server/overlay_catalog.py` | Overlay slugs, sizes, layouts and controls. |
| `server/patcher.py` | `/patcher` and `/companion/{name}`. |
| `server/obs_controller.py`, `server/twitch_bot.py` | OBS scene switching and the Twitch bot. |
| `server/calc_files.py`, `calc/` | Damage calculator serving and source. |
| `patch/` | Companion patches: sources, build tools, built UPS files in `patch/dist/`. |
| `data/games/` | Per-game data packs ([data/games/README.md](../data/games/README.md)). |
| `tools/` | Generators, build tools, the duo runner, release checks. |

### Adding a game

1. A Lua client under `lua/<gen>/` as a composition root, following `lua/gen1/` or `lua/gen3/`.
2. A data pack under `data/games/<gen>_<game>/`, generated by a `tools/gen_*.py` script.
3. An adapter in `server/adapters/<gen>_<game>.py`, registered with its `rom_type` values in `server/adapters/__init__.py`. Override only the base-class methods the game supports.
4. A family in `GAMES` and its rows in `OPTION_SUPPORT` (`server/manager.py`) once the Manager should offer it.
5. Unit tests, then a committed battery-save fixture, headless live gates and a duo run. Passing unit tests and the Lua syntax check is necessary but not sufficient: defects in deferred commands, debounces and struct reads have passed both.

After changing shared code, test Gen 3 first, then the other generations.

## External references

- [pret](https://github.com/pret) decompilations ([pokered](https://github.com/pret/pokered), [pokeyellow](https://github.com/pret/pokeyellow), [pokecrystal](https://github.com/pret/pokecrystal), [pokegold](https://github.com/pret/pokegold), [pokefirered](https://github.com/pret/pokefirered), [pokeemerald](https://github.com/pret/pokeemerald)): addresses and struct layouts.
- [pokeemerald-expansion](https://github.com/rh-hideout/pokeemerald-expansion): the Emerald Expansion source.
- [Complete-Fire-Red-Upgrade](https://github.com/Skeli789/Complete-Fire-Red-Upgrade): the engine Radical Red is built on.
- [pureRGB](https://github.com/Vortyne/pureRGB).
- [Universal Pokémon Randomizer ZX](https://github.com/Ajarmar/universal-pokemon-randomizer-zx).
- [BizHawk Lua functions](https://tasvideos.org/BizHawk/LuaFunctions).
- [Bulbapedia: Pokémon data structure (Generation III)](https://bulbapedia.bulbagarden.net/wiki/Pok%C3%A9mon_data_structure_(Generation_III)).
