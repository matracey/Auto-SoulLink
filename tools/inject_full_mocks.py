"""Comprehensive mock data injector — populates every widget with realistic
state for visual testing. Run while the SLink server is listening on
TCP 54321 and HTTP 8080 (the defaults).
Set SLINK_ADMIN_KEY for protected servers. A failed reset aborts injection.

What you get after running:
* Both players (Alice / Bob) connected, FireRed / LeafGreen
* 6 linked party pairs (12 mons total) across routes 1-6
* 1 dead-zone link at route7 (Alice missed) → Memorial + Killfeed populate
* 1 boxed-link pair (route8) → Boxed Links populates
* 1 active battle on player B vs a wild Caterpie → Enemy widget
* 1 shiny captured → Shiny Counter shows ≥ 1
* Attempt counter set to 7
* Lock rules: species clause enabled

After running, refresh http://localhost:8099/ in your browser. If the
widgets still show "no data", call /api/reset first.

`--game gen1` swaps in a Red/Blue cast. `--game gen2 --title crystal|gold|silver` swaps
in a Johto cast on that title (both players), and adds one MEMORIAL pair (fainted, then
confirmed into the memorial box the way the client confirms a memorialize) beside the
DEAD one.

`--game gen3 --title firered_rr|frlg|emerald` picks the Gen 3 cartridge pair: firered_rr
(default, unchanged) is Radical Red on both sides; frlg is vanilla FireRed/LeafGreen
(the existing Kanto cast, just non-RR rom_types); emerald is vanilla Emerald on both
sides with its own Hoenn cast (Kanto area ids don't exist on that foundation).
"""
import asyncio
import contextlib
import json
import os
import sys
import urllib.request

# Defaults match `python -m server.server`. A run spawned by the Run Manager gets its own
# pair of ports (TCP from 54321, HTTP from 8081), so both are overridable rather than
# requiring a second copy of this script to talk to a managed run.
TCP_HOST = os.environ.get("SLINK_MOCK_TCP_HOST", "127.0.0.1")
TCP_PORT = int(os.environ.get("SLINK_MOCK_TCP_PORT", "54321"))
HTTP = os.environ.get("SLINK_MOCK_HTTP", "http://127.0.0.1:8080")


# Seconds to keep the socket open after the last event. The server marks a socket's
# owning player disconnected when it closes, so a capture from /api/status after this
# script exits shows player A gone. A hold lets the capture happen while the run looks
# the way a live one does.
HOLD = float(os.environ.get("SLINK_MOCK_HOLD", "0"))


async def send_tcp(events: list[dict]) -> None:
    r, w = await asyncio.open_connection(TCP_HOST, TCP_PORT)
    for m in events:
        w.write((json.dumps(m) + "\n").encode())
        await w.drain()
        # The server replies with newline-JSON; consume the response to
        # keep the connection clean.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(r.readline(), timeout=2.0)
    # Replay each player's last tick while holding: the server calls a player silent for
    # STALE_AFTER_SECS (10 s) and the board would show "No data" in the capture.
    last_tick = {m["player"]: m for m in events if m.get("event") == "tick"}
    deadline = asyncio.get_running_loop().time() + HOLD
    while HOLD and asyncio.get_running_loop().time() < deadline:
        for m in last_tick.values():
            w.write((json.dumps(m) + "\n").encode())
            await w.drain()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(r.readline(), timeout=2.0)
        await asyncio.sleep(3)
    w.close()
    await w.wait_closed()


def http_post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        HTTP + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if key := os.environ.get("SLINK_ADMIN_KEY"):
        req.add_unredirected_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.loads(r.read().decode("utf-8"))


# Six linked pairs — each row is (area, alice_capture, bob_capture).
# species_id, key, nickname, level, held_item_id
# Held items use real Gen 3 FRLG IDs so the held-item rendering path is
# exercised with name lookups (verify against `adapter.item_name`):
#   183=Quick Claw, 186=Choice Band, 196=Focus Band, 197=Lucky Egg,
#   200=Leftovers, 202=Light Ball, 217=Silk Scarf.
PAIRS = [
    ("route1",  (25, "PIKA001", "Sparky",  6, 202),  (4,  "CHAR001", "Embo",   6, 196)),
    ("route2",  (16, "PIDG002", "Pidge",   8, 0),    (19, "RATT002", "Rattie", 8, 183)),
    ("route3",  (21, "SPEA003", "Sparrow", 9, 210),  (74, "GEOD003", "Rocky",  9, 0)),
    ("route4",  (29, "NIDO004", "Nidi",   11, 0),    (41, "ZUBA004", "Vampy", 11, 200)),
    ("route5",  (27, "SAND005", "Shrewd", 12, 186),  (37, "VULP005", "Flick", 12, 0)),
    ("route6",  (43, "ODDI006", "Smelly", 13, 0),    (69, "BELL006", "Twig",  13, 197)),
]

# Common moves for each species (Gen 3 FRLG IDs). The party tick passes
# these in `moves:[...]` so move dropdowns render under each row.
MOVES = {
    25:  [84, 98, 86, 39],   # Pikachu: ThunderShock, QuickAttack, ThunderWave, Tail Whip
    4:   [52, 10, 43, 108],  # Charmander: Ember, Scratch, Leer, Smokescreen
    16:  [16, 33, 45, 98],   # Pidgey: Gust, Tackle, Sand-Attack, Quick Attack
    19:  [33, 39, 98, 44],   # Rattata: Tackle, Tail Whip, Quick Attack, Bite
    21:  [64, 43, 98, 31],   # Spearow: Peck, Leer, Quick Attack, Fury Attack
    74:  [88, 111, 33, 106], # Geodude: Rock Throw, Defense Curl, Tackle, Harden
    29:  [40, 33, 44, 24],   # Nidoran♀: Poison Sting, Tackle, Bite, Double Kick
    41:  [141, 48, 109, 44], # Zubat: Leech Life, Supersonic, Confuse Ray, Bite
    27:  [10, 28, 111, 154], # Sandshrew: Scratch, Sand-Attack, Defense Curl, Fury Cutter
    37:  [52, 39, 46, 44],   # Vulpix: Ember, Tail Whip, Roar, Bite
    43:  [71, 78, 230, 51],  # Oddish: Absorb, Sweet Scent, Sweet Kiss, Acid
    69:  [22, 71, 78, 51],   # Bellsprout: Vine Whip, Absorb, Sweet Scent, Acid
    # Hoenn cast (--title emerald). Gen 3 vanilla move ids.
    288: [33, 45, 29],       # Zigzagoon: Tackle, Growl, Headbutt
    286: [33, 43],           # Poochyena: Tackle, Leer
    295: [45, 71],           # Lotad: Growl, Absorb
    298: [117, 106],         # Seedot: Bide, Harden
    309: [45, 55],           # Wingull: Growl, Water Gun
    304: [64, 45],           # Taillow: Peck, Growl
    306: [71, 78],           # Shroomish: Absorb, Stun Spore
    311: [145],              # Surskit: Bubble
    370: [1],                # Whismur: Pound
    332: [28, 117],          # Trapinch: Sand-Attack, Bide
    339: [45, 52],           # Numel: Growl, Ember
    290: [33, 81],           # Wurmple: Tackle, String Shot
}

# One ability per species (Gen 3 FRLG ids, resolved by the adapter). Every mon used to
# be sent ability_id 1, so the whole column read "Stench" twelve times and looked like
# the lookup was broken. Radical Red renames some entries (51 is "Bad Company" there, 38
# is "Dragon's Maw"), so these are chosen to read right under RR rather than vanilla.
ABILITIES = {
    25: 9,    # Pikachu: Static
    4:  66,   # Charmander: Blaze
    16: 50,   # Pidgey: Run Away
    19: 62,   # Rattata: Guts
    21: 22,   # Spearow: Intimidate
    74: 69,   # Geodude: Rock Head
    29: 39,   # Nidoran: Inner Focus
    41: 39,   # Zubat: Inner Focus
    27: 8,    # Sandshrew: Sand Veil
    37: 18,   # Vulpix: Flash Fire
    43: 34,   # Oddish: Chlorophyll
    69: 34,   # Bellsprout: Chlorophyll
    60: 11,   # Poliwag: Water Absorb
    54: 6,    # Psyduck: Damp
    10: 19,   # Caterpie (the wild foe): Shield Dust
    # Hoenn cast (--title emerald, see GEN3_EMERALD_PAIRS below). Gen 3 INTERNAL species ids
    # (the save/wire index, not National Dex: Zigzagoon is 288 here, 263 nationally).
    288: 53,  # Zigzagoon: Pickup
    286: 50,  # Poochyena: Run Away
    295: 33,  # Lotad: Swift Swim
    298: 34,  # Seedot: Chlorophyll
    309: 51,  # Wingull: Keen Eye
    304: 62,  # Taillow: Guts
    306: 27,  # Shroomish: Effect Spore
    311: 33,  # Surskit: Swift Swim
    370: 43,  # Whismur: Soundproof
    332: 52,  # Trapinch: Hyper Cutter
    339: 12,  # Numel: Oblivious
    290: 19,  # Wurmple: Shield Dust
}

# A capture waiting on the other player (see GEN1_PENDING_*).
PENDING_AREA = "route9"
PENDING_A = (58, "GROW009", "Flame", 14, 0)

# Dead-zone pair (Alice missed, Bob would have caught Caterpie)
DEAD_ZONE_AREA = "route7"
DEAD_ZONE_BOB = (10, "CATE007", "Cat",     8, 0)

# Boxed pair (both caught but neither in party — would show in Boxed Links)
BOXED_AREA = "route8"
BOXED_A = (60, "POLI008", "Bubbles", 10, 217)
BOXED_B = (54, "PSYD008", "Quack",   10, 183)


# ── Gen 3 Emerald (--title emerald) variant ──────────────────────────────
# The FR/LG cast above is Kanto-only: route1..route9 etc. don't exist in a Hoenn
# game_id, and clean Emerald pairs against clean Emerald only (server/adapters/__init__.py
# _ROM_TYPE_TO_FOUNDATION: gen3_emerald != gen3_frlg), so both players run "emerald".
# Areas are real ids from data/games/gen3_emerald/area_map.json; final areas additionally
# need entries in data/games/gen3_emerald/emerald_trainers.json's trainers_by_area so the
# Upcoming Key Trainers widget renders (verified against that file, not invented).
#
# The wild-encounters panel reads each title's shipped pret-derived table (card WILD-VANILLA):
# data/games/gen3_frlge/{firered,leafgreen}_encounters.json, data/games/gen3_emerald/emerald_encounters.json.
GEN3_EMERALD_PAIRS = [
    ("route_101", (288, "ZIGZ101", "Ziggy",  6, 0),   (286, "POOC101", "Snarl",  6, 0)),
    ("route_102", (295, "LOTA102", "Lily",   8, 183), (298, "SEED102", "Nutty",  8, 0)),
    ("route_104", (309, "WING104", "Gully",  9, 0),   (304, "TAIL104", "Swifty", 9, 210)),
    ("route_105", (306, "SHRO105", "Puff",  11, 0),   (311, "SURS105", "Skater", 11, 200)),
    ("route_106", (370, "WHIS106", "Echo",  12, 186), (332, "TRAP106", "Digger", 12, 0)),
    ("route_107", (339, "NUME107", "Numie", 13, 0),   (290, "WURM107", "Wormy",  13, 197)),
]

GEN3_EMERALD_PENDING_AREA = "route_108"
GEN3_EMERALD_PENDING_A = (315, "SKIT108", "Kitty", 14, 0)   # Skitty
ABILITIES.setdefault(315, 56)  # Skitty: Cute Charm

GEN3_EMERALD_DEAD_ZONE_AREA = "route_109"
GEN3_EMERALD_DEAD_ZONE_BOB = (318, "BALT109", "Spinny", 8, 0)  # Baltoy

GEN3_EMERALD_BOXED_AREA = "route_112"
GEN3_EMERALD_BOXED_A = (364, "SLAK112", "Lazy",    10, 217)   # Slakoth
GEN3_EMERALD_BOXED_B = (335, "MAKU112", "Bruiser", 10, 183)   # Makuhita

# Final standing areas: BOTH have data/games/gen3_emerald/emerald_trainers.json
# trainers_by_area entries (route_103: [520,523,526,529,532,535], route_110: [267,521,
# 524,527,530,533,536,656,778,779,780,781]) so Upcoming Key Trainers renders on both
# sides, and they differ so the split view shows distinct trainer cards per player.
GEN3_EMERALD_FINAL_A = "route_103"
GEN3_EMERALD_FINAL_B = "route_110"


# ── Gen 1 (Red/Blue) variant ─────────────────────────────────────────────
# Gen 3 is the default because that is the game this script was written for.
# `--game gen1` swaps the whole cast: Gen 1 keys are `DVs:OTID:species`, area ids are
# underscored, and there are no abilities and no held items to send at all — passing
# Gen 3's values through would paint columns the generation does not have, which is the
# exact class of bug these mocks exist to expose.
GAME = "gen3"


def _is_gen1() -> bool:
    return GAME == "gen1"


# EVERY species_id ON THE GEN 1 WIRE IS THE GAME'S INTERNAL INDEX, NOT THE DEX NUMBER
# (internal 1 = Rhydon, 153 = Bulbasaur, 84 = Pikachu) -- that is what a cartridge sends
# and what the adapter resolves. The cast below is written in dex numbers for
# readability and converted here, key byte included: dex 25 sent as-is renders Gastly.
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "games", "gen1_rby", "species_index.json"), encoding="utf-8") as _f:
    _G1_INDEX = {int(k): v for k, v in json.load(_f)["national_to_index"].items()}


def _g1(dex: int) -> int:
    return _G1_INDEX[dex]


def _sid(dex: int) -> int:
    """A species for the active generation: the internal index on Gen 1, the dex elsewhere."""
    return _g1(dex) if _is_gen1() else dex


def _g1cap(dex: int, dvs_ot: str, nick: str, lv: int) -> tuple:
    """capture = (species, key, nickname, level, held_item); the key is DVs:OTID:species
    with the species byte the INTERNAL index. held_item is always 0 on Gen 1."""
    return (_g1(dex), f"{dvs_ot}:{_g1(dex):02X}", nick, lv, 0)


# (area, alice_capture, bob_capture)
GEN1_PAIRS = [
    ("route_1",         _g1cap(25, "4A5B:30B8", "Sparky",  6), _g1cap(4,  "3C2D:7B0B", "Embo",    6)),
    ("route_2",         _g1cap(16, "5B6C:30B8", "Pidge",   8), _g1cap(19, "2D3E:7B0B", "Rattie",  8)),
    ("viridian_forest", _g1cap(13, "6C7D:30B8", "Sting",   9), _g1cap(10, "1E2F:7B0B", "Wiggle",  9)),
    ("route_3",         _g1cap(21, "7D8E:30B8", "Sparrow", 11), _g1cap(74, "0F1A:7B0B", "Rocky",  11)),
    ("mt_moon_1f",      _g1cap(41, "8E9F:30B8", "Vampy",  12), _g1cap(46, "9A0B:7B0B", "Shroom", 12)),
    ("route_4",         _g1cap(27, "9F0A:30B8", "Shrewd", 13), _g1cap(23, "8B1C:7B0B", "Slither", 13)),
]

# Gen 1 move ids (pokered constants/move_constants.asm), keyed by INTERNAL index.
GEN1_MOVES = {_g1(dex): moves for dex, moves in {
    25: [84, 98, 86, 39],    # Pikachu: ThunderShock, Quick Attack, Thunder Wave, Tail Whip
    4:  [52, 10, 43, 108],   # Charmander: Ember, Scratch, Leer, Smokescreen
    16: [16, 33, 45, 98],    # Pidgey: Gust, Tackle, Sand-Attack, Quick Attack
    19: [33, 39, 98, 44],    # Rattata: Tackle, Tail Whip, Quick Attack, Bite
    13: [40, 81],            # Weedle: Poison Sting, String Shot
    10: [33, 81],            # Caterpie: Tackle, String Shot
    21: [64, 43, 98, 31],    # Spearow: Peck, Leer, Quick Attack, Fury Attack
    74: [88, 111, 33, 106],  # Geodude: Rock Throw, Defense Curl, Tackle, Harden
    41: [141, 48, 44],       # Zubat: Leech Life, Supersonic, Bite
    46: [10, 78, 147],       # Paras: Scratch, Stun Spore, Spore
    27: [10, 28, 111],       # Sandshrew: Scratch, Sand-Attack, Defense Curl
    23: [35, 40, 44],        # Ekans: Wrap, Poison Sting, Bite
}.items()}

# A capture waiting on the other player. Alice has caught here; Bob has not been here.
GEN1_PENDING_AREA = "route_9"
GEN1_PENDING_A = _g1cap(58, "AB12:30B8", "Flame", 14)
GEN1_DEAD_ZONE_AREA = "route_22"
GEN1_DEAD_ZONE_BOB = _g1cap(56, "7A8B:7B0B", "Mankey", 8)
GEN1_BOXED_AREA = "route_5"
GEN1_BOXED_A = _g1cap(60, "6D7E:30B8", "Bubbles", 10)
GEN1_BOXED_B = _g1cap(54, "5E6F:7B0B", "Quack", 10)


# ── Gen 2 (Crystal/Gold/Silver) variant ──────────────────────────────────────────────────
# server/adapters/gen2_gsc.py: keys are DVs:OTID:species like Gen 1, but the species byte
# IS the National Dex number (1..251), gender comes from the key's DVs (gender_from_key,
# so none is sent) and none of these DVs is shiny (is_shiny). Held items are real
# items.json ids that is_valid_held_item accepts; there are no abilities. Every area is
# a real area_map.json id with wild encounters in all three titles.
TITLE = "crystal"  # --title: the rom_type both players report


def _g2cap(dex: int, dvs_ot: str, nick: str, lv: int, item: int) -> tuple:
    return (dex, f"{dvs_ot}:{dex:02X}", nick, lv, item)


GEN2_PAIRS = [
    ("route_29",   _g2cap(161, "4A5B:30B8", "Scout",  4, 173), _g2cap(16,  "3C2D:7B0B", "Pidge",  4, 0)),
    ("route_30",   _g2cap(163, "5B6C:30B8", "Hooty",  6, 0),   _g2cap(60,  "2D3E:7B0B", "Swirl",  6, 73)),
    ("route_31",   _g2cap(69,  "6C7D:30B8", "Twig",   7, 117), _g2cap(129, "1E2F:7B0B", "Flop",   7, 95)),
    ("dark_cave",  _g2cap(41,  "7D8E:30B8", "Vampy",  8, 146), _g2cap(74,  "0F1A:7B0B", "Rocky",  8, 125)),
    ("route_32",   _g2cap(194, "8E9F:30B8", "Squish", 9, 76),  _g2cap(187, "9A0B:7B0B", "Puff",   9, 83)),
    ("union_cave", _g2cap(95,  "9F0A:30B8", "Pillar", 10, 112), _g2cap(98, "8B1C:7B0B", "Pinch", 10, 119)),
]

# Gen 2 move ids (pokecrystal constants/move_constants.asm); four slots, 0 = empty, as
# the client's wire.party_entry always sends.
GEN2_MOVES = {
    161: [33, 111, 0, 0],      # Sentret: Tackle, Defense Curl
    16:  [33, 28, 16, 0],      # Pidgey: Tackle, Sand-Attack, Gust
    163: [33, 45, 193, 64],    # Hoothoot: Tackle, Growl, Foresight, Peck
    60:  [145, 95, 55, 0],     # Poliwag: Bubble, Hypnosis, Water Gun
    69:  [22, 74, 35, 0],      # Bellsprout: Vine Whip, Growth, Wrap
    129: [150, 33, 0, 0],      # Magikarp: Splash, Tackle
    41:  [141, 48, 44, 0],     # Zubat: Leech Life, Supersonic, Bite
    74:  [33, 111, 88, 222],   # Geodude: Tackle, Defense Curl, Rock Throw, Magnitude
    194: [55, 39, 21, 0],      # Wooper: Water Gun, Tail Whip, Slam
    187: [150, 235, 39, 33],   # Hoppip: Splash, Synthesis, Tail Whip, Tackle
    95:  [33, 103, 20, 88],    # Onix: Tackle, Screech, Bind, Rock Throw
    98:  [145, 43, 11, 106],   # Krabby: Bubble, Leer, Vicegrip, Harden
    92:  [95, 122, 180, 212],  # Gastly: Hypnosis, Lick, Spite, Mean Look
    19:  [33, 39, 98, 0],      # Rattata: Tackle, Tail Whip, Quick Attack
    96:  [1, 95, 50, 93],      # Drowzee: Pound, Hypnosis, Disable, Confusion
    204: [33, 182, 120, 0],    # Pineco: Tackle, Protect, Selfdestruct
    102: [140, 95, 115, 0],    # Exeggcute: Barrage, Hypnosis, Reflect
}

GEN2_PENDING_AREA = "route_34"
GEN2_PENDING_A = _g2cap(96, "AB12:30B8", "Snooze", 12, 0)
GEN2_DEAD_ZONE_AREA = "route_33"
GEN2_DEAD_ZONE_BOB = _g2cap(190, "7A8B:7B0B", "Aipom", 8, 0)
GEN2_BOXED_AREA = "route_36"
GEN2_BOXED_A = _g2cap(204, "6D7E:30B8", "Cone", 12, 143)
GEN2_BOXED_B = _g2cap(102, "5E6F:7B0B", "Eggs", 12, 126)
# Fainted, then confirmed into the memorial box by both clients -> status "memorial".
GEN2_MEMORIAL_AREA = "sprout_tower"
GEN2_MEMORIAL_A = _g2cap(92, "4C5D:30B8", "Boo", 8, 113)
GEN2_MEMORIAL_B = _g2cap(19, "3E4F:7B0B", "Nibbles", 8, 0)
# gen2_gsc.memorial_box_index: NUM_BOXES - 1 (the UI's "Box 14").
GEN2_MEMORIAL_BOX = 13
# Party slot of the DEAD pair (the faint below): both halves are sent at 0 HP.
DEAD_SLOT = 2


def _gen2_pack(name: str) -> dict:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "games", f"gen2_{TITLE}", f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def _gen2_rom_sha1() -> str:
    """The pinned clean sha1 lua/gen2/entry.lua reports for this title."""
    return _gen2_pack("profile")["source"]["rom_sha1"]


def _gen2_pp(moves: list[int]) -> list[int]:
    """Full PP per slot, so no move reads over its maximum (a flat 25 showed "25/15")."""
    pp = {row["id"]: row["pp"] for row in _gen2_pack("moves")["moves"]}
    return [pp.get(m, 0) for m in moves]


def _is_gen3_emerald() -> bool:
    """--game gen3 --title emerald: the only Gen 3 title with its own cast (see
    GEN3_EMERALD_PAIRS above) -- clean Emerald pairs only with clean Emerald
    (server/adapters/__init__.py _ROM_TYPE_TO_FOUNDATION), and Kanto area ids don't
    exist in it."""
    return GAME == "gen3" and TITLE == "emerald"


def _rom_type(player: str) -> str:
    """What each player's client reports in its hello.

    Must be a string _ROM_TYPE_TO_GAME_ID knows: an unrecognised rom_type leaves the
    server on whichever adapter it already had, silently, and the run keeps going under
    the wrong generation. Gen 1 was reporting "gen1_rby" -- an adapter game_id, which no
    client ever sends -- and produced Gen 3 abilities and genders on Red/Blue mons.

    Gen 1 uses Red and Blue, which is a real pair and exercises two different versions in
    one run. Gen 3 defaults to firered_rr on BOTH sides (unchanged from before --title
    existed), because Radical Red is a FireRed hack and there is no LeafGreen build of it:
    "leafgreen_rr", which this script used to send, is not a cartridge that exists. It is
    routed nowhere, though server.py:3397 does carry a display label for it.

    --title frlg sends the real vanilla pair (firered / leafgreen, both foundation
    gen3_frlg -- server/adapters/__init__.py). --title emerald sends "emerald" on both
    sides: Emerald has no LeafGreen-equivalent second cartridge either, and its own
    foundation (gen3_emerald) only pairs with itself.
    """
    if _is_gen1():
        return "red" if player == "a" else "blue"
    if GAME == "gen2":
        return TITLE.capitalize()  # lua/gen2/entry.lua sends "Crystal" / "Gold" / "Silver"
    if GAME == "gen3":
        if TITLE == "frlg":
            return "firered" if player == "a" else "leafgreen"
        if TITLE == "emerald":
            return "emerald"
    return "firered_rr"


def _pairs():
    if _is_gen3_emerald():
        return GEN3_EMERALD_PAIRS
    return {"gen1": GEN1_PAIRS, "gen2": GEN2_PAIRS}.get(GAME, PAIRS)


def _moves():
    return {"gen1": GEN1_MOVES, "gen2": GEN2_MOVES}.get(GAME, MOVES)


def _final_areas() -> tuple[str, str]:
    """Where each player is standing when the mocks finish.

    BOTH must be areas with a wild encounter table, and they must DIFFER. Encounter data
    is per player -- two randomized cartridges do not share one -- so a player parked
    somewhere with no encounters leaves that panel empty, and an empty panel reads as
    missing data rather than as the point being made.

    Gen 3 also wants priority trainers so the Upcoming Trainers widget renders. This used
    to say pewter_museum "so the widget renders (Falkner @ Pewter Museum)", which has one
    priority trainer and no encounters at all; route_22 has seven and six.

    Every Gen 3 title ships a wild table now (RR: rr_encounters.json; clean FR/LG and
    Emerald: the pret-derived files, card WILD-VANILLA), so each title's picks need both.
    """
    if _is_gen3_emerald():
        return (GEN3_EMERALD_FINAL_A, GEN3_EMERALD_FINAL_B)
    return {"gen1": ("route_3", "route_24"),
            "gen2": ("ilex_forest", "national_park")}.get(GAME, ("route_22", "cerulean_city"))


def _pending():
    if _is_gen3_emerald():
        return (GEN3_EMERALD_PENDING_AREA, GEN3_EMERALD_PENDING_A)
    return {"gen1": (GEN1_PENDING_AREA, GEN1_PENDING_A),
            "gen2": (GEN2_PENDING_AREA, GEN2_PENDING_A)}.get(GAME, (PENDING_AREA, PENDING_A))


def _dead_zone():
    if _is_gen3_emerald():
        return (GEN3_EMERALD_DEAD_ZONE_AREA, GEN3_EMERALD_DEAD_ZONE_BOB)
    return {"gen1": (GEN1_DEAD_ZONE_AREA, GEN1_DEAD_ZONE_BOB),
            "gen2": (GEN2_DEAD_ZONE_AREA, GEN2_DEAD_ZONE_BOB)}.get(GAME, (DEAD_ZONE_AREA, DEAD_ZONE_BOB))


def _boxed():
    if _is_gen3_emerald():
        return (GEN3_EMERALD_BOXED_AREA, GEN3_EMERALD_BOXED_A, GEN3_EMERALD_BOXED_B)
    return {"gen1": (GEN1_BOXED_AREA, GEN1_BOXED_A, GEN1_BOXED_B),
            "gen2": (GEN2_BOXED_AREA, GEN2_BOXED_A, GEN2_BOXED_B)}.get(GAME, (BOXED_AREA, BOXED_A, BOXED_B))


def _memorial():
    """A pair driven all the way to "memorial". Gen 2 only: the other casts predate it
    and their event lists are kept as they were."""
    return (GEN2_MEMORIAL_AREA, GEN2_MEMORIAL_A, GEN2_MEMORIAL_B) if GAME == "gen2" else None


def _companion_fields(rom_type: str) -> dict:
    """The mock cartridges are PATCHED ones: a hello for a title that requires the SLink companion is
    refused by the server without this evidence (patch-first, owner 2026-10-02; the same fields
    GameRulesAdapter.companion_refusal asks for). Yellow and Gen 4/5 need none."""
    name = rom_type.lower()
    if name in ("red", "blue"):
        return {"artifact_kind": "named", "panel": True}
    if name in ("purered", "pureblue", "puregreen"):
        return {"artifact_kind": "overlay"}
    if name in ("firered", "leafgreen", "emerald", "firered_rr"):
        # Gen 3 asks for the live mailbox's ABI, equal to the one the title's pack pins
        # (server/adapters/gen3_frlge.py _COMPANION_PACKS / _companion_abi)
        pack = {"emerald": "gen3_emerald", "firered_rr": "gen3_rr"}.get(name, "gen3_frlg")
        path = os.path.join(os.path.dirname(__file__), "..", "data", "games", pack, "profile.json")
        with open(path, encoding="utf-8") as fh:
            abi = json.load(fh)["native"]["ABI"]
        return {"artifact_kind": "companion", "companion_abi": abi}
    return {}


def _hello_extra(player: str) -> dict:
    """Identity fields each generation's real client puts in its hello."""
    ot = "30B8" if player == "a" else "7B0B"
    if _is_gen1():
        return {"ot_id": ot, **_companion_fields(_rom_type(player))}
    if GAME == "gen2":
        # lua/gen2/client.lua send_hello; the Gen 2 wire's ot_id is the integer.
        # the companion evidence the server's Gen 2 gate asks for: the pinned mailbox ABI of the title's pack
        path = os.path.join(os.path.dirname(__file__), "..", "data", "games", f"gen2_{TITLE}", "profile.json")
        with open(path, encoding="utf-8") as fh:
            abi = json.load(fh)["titles"][TITLE]["overlay"]["abi"]
        return {"foundation": "gen2_gsc", "artifact_kind": "overlay", "ot_id": int(ot, 16),
                "rom_sha1": _gen2_rom_sha1(), "party": [], "companion_abi": abi}
    return _companion_fields(_rom_type(player))


def _capture_extra(gender: str, species: int, item: int) -> dict:
    """What a capture carries beyond the shared fields. Gen 2 sends its held item only:
    the server derives gender from the key, and there are no abilities."""
    if _is_gen1():
        return {}
    if GAME == "gen2":
        return {"held_item_id": item}
    return {"gender": gender, "ability_id": ABILITIES.get(species, 1), "held_item_id": item}


def _boot_key(player: str) -> str:
    ot, dex = ("30B8", 25) if player == "a" else ("7B0B", 4)
    if _is_gen1():
        return f"{'0001' if player == 'a' else '0002'}:{ot}:{_g1(dex):02X}"
    if GAME == "gen2":
        return f"{'0001' if player == 'a' else '0002'}:{ot}:{dex:02X}"
    return "BOOT0001" if player == "a" else "BOOT0002"


def _wild_foe() -> dict:
    """The mon the player is mid-battle against. Gen 1 has no abilities, so sending an
    ability_id would be a lie the enemy panel would happily render."""
    if GAME == "gen2":
        # wire.foe_entry: no key, four move/pp/pp_ups slots.
        return {"species_id": 191, "level": 11, "hp": 28, "maxHP": 32, "status_cond": 0,
                "held_item_id": 0, "active": True, "moves": [71, 74, 0, 0], "pp": [20, 40, 0, 0],
                "pp_ups": [0, 0, 0, 0]}  # Sunkern: Absorb, Growth
    if _is_gen1():
        return {"species_id": _g1(10), "level": 11, "hp": 28, "maxHP": 32, "active": True,
                "key": f"1A2B:0000:{_g1(10):02X}", "status_cond": 0, "stat_stages": {},
                "moves": [33, 81], "pp": [35, 40]}
    return {"species_id": 10, "level": 11, "hp": 28, "maxHP": 32, "active": True,
            "ability_id": ABILITIES[10], "key": "WILD_CATE", "status_cond": 0, "stat_stages": {},
            "moves": [33, 81], "pp": [35, 40], "pp_bonuses": 0}


def _stored(tag: str, ot: str, species: int) -> str:
    """A box-slot key valid for the active generation.

    Gen 1 keys are structured (``DVs:OTID:species``) and the adapter rejects anything
    else, so a readable literal like "STORED_A1" would silently drop the row on Gen 1
    and leave the box table looking merely short rather than broken.
    """
    if GAME in ("gen1", "gen2"):
        return f"{ord(tag[0]):02X}{int(tag[1]):02X}:{ot}:{species:02X}"
    return "STORED_" + tag


# Party slot (0-based) whose mon is sent low on HP, and the HP to send. Slot 3 is Nidi /
# Vampy on Gen 3 and Sparrow / Rocky on Gen 1 -- a linked pair either way, so the board
# has one pair to tint as at risk.
LOW_HP_SLOT = 3
LOW_HP = 7


def _mon(species, key, nick, lv, item, *, gender, active):
    """One party entry, with the fields this generation actually has.

    Gen 1 gets no ability_id, no held_item_id and no gender: the adapter reports
    genderless for every key, and a card that prints "male" beside a Gen 1 mon is
    showing the player something their cartridge cannot know.
    """
    d = {"key": key, "level": lv, "hp": 20 + lv, "maxHP": 20 + lv,
         "species_id": species, "nickname": nick, "active": active,
         "_slot": None,
         "moves": _moves().get(species, []), "pp": [25, 25, 25, 25]}
    if GAME == "gen2":
        d.update(held_item_id=item, pp=_gen2_pp(d["moves"]), pp_ups=[0, 0, 0, 0], status_cond=0)
    elif not _is_gen1():
        d.update(ability_id=ABILITIES.get(species, 1), held_item_id=item, gender=gender, pp_bonuses=0)
    return d


def _memorial_box_rows(side: int) -> list[dict]:
    """The memorial pair where a completed memorialize leaves it: in the memorial box,
    which is the only place the contamination check expects a dead mon."""
    memorial = _memorial()
    if not memorial:
        return []
    cap = memorial[1 + side]
    return [{"box": GEN2_MEMORIAL_BOX, "slot": 0, "key": cap[1], "nickname": cap[2],
             "species_id": cap[0], "held_item_id": cap[4], "moves": _moves().get(cap[0], [])}]


async def main() -> None:
    print("Resetting server state...")
    try:
        result = http_post("/api/reset", {})
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError("server did not acknowledge a successful reset")
    except (OSError, ValueError) as e:
        print(f"  reset failed: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    print("Sending hellos + initial tick...")
    # ONE socket for the whole session. The server binds a socket to the first player who
    # speaks on it and marks that player disconnected when it closes -- and `connected`
    # only goes back to True on a hello. This used to be two sends on two sockets, so the
    # first socket's close took player A offline before a single link had formed, and no
    # later message could bring her back. Every fixture captured that way had A gone.
    # Use Radical Red ROM types so the Upcoming Trainers widget activates
    # (the trainers_for_area / encounter_table adapter methods are RR-gated).
    events: list[dict] = [
        {"event": "hello", "player": "a", "rom_type": _rom_type("a"), "trainer_name": "Alice",
         "has_pokeballs": True, **_hello_extra("a")},
        {"event": "hello", "player": "b", "rom_type": _rom_type("b"), "trainer_name": "Bob",
         "has_pokeballs": True, **_hello_extra("b")},
        # Tick events with party of 1 dummy so size > 0 and quarantine logic kicks in.
        # We'll set proper parties after all captures.
        {"event": "tick", "player": "a", "has_pokeballs": True, "party": [{"key": _boot_key("a")}], "current_area_id": "starter"},
        {"event": "tick", "player": "b", "has_pokeballs": True, "party": [{"key": _boot_key("b")}], "current_area_id": "starter"},
    ]

    print("Sending 6 paired captures + faint + shiny...")
    # 6 linked pairs
    for area, (a_sid, a_key, a_nick, a_lv, a_item), (b_sid, b_key, b_nick, b_lv, b_item) in _pairs():
        events.append({"event": "area_enter", "player": "a", "area_id": area})
        events.append({"event": "area_enter", "player": "b", "area_id": area})
        events.append({
            "event": "capture", "player": "a", "area_id": area,
            "species_id": a_sid, "key": a_key, "nickname": a_nick,
            "level": a_lv, "hp": 20 + a_lv, "maxHP": 20 + a_lv,
            "in_box": False,
            **_capture_extra("male" if a_lv % 2 else "female", a_sid, a_item),
        })
        events.append({
            "event": "capture", "player": "b", "area_id": area,
            "species_id": b_sid, "key": b_key, "nickname": b_nick,
            "level": b_lv, "hp": 20 + b_lv, "maxHP": 20 + b_lv,
            "in_box": False,
            **_capture_extra("female" if b_lv % 2 else "male", b_sid, b_item),
        })

    # Dead zone: Alice misses, Bob catches (but link won't form → dead_zone)
    dz_area, _dz_bob = _dead_zone()
    events.append({"event": "area_enter", "player": "a", "area_id": dz_area})
    events.append({"event": "no_catch", "player": "a", "area_id": dz_area})
    events.append({"event": "area_enter", "player": "b", "area_id": dz_area})

    # Boxed-link area: both catch, link forms; later we move them to box
    boxed_area, boxed_a, boxed_b = _boxed()
    events.append({"event": "area_enter", "player": "a", "area_id": boxed_area})
    events.append({"event": "area_enter", "player": "b", "area_id": boxed_area})
    a_sid, a_key, a_nick, a_lv, a_item = boxed_a
    b_sid, b_key, b_nick, b_lv, b_item = boxed_b
    events.append({
        "event": "capture", "player": "a", "area_id": boxed_area,
        "species_id": a_sid, "key": a_key, "nickname": a_nick,
        "level": a_lv, "hp": 20 + a_lv, "maxHP": 20 + a_lv, "in_box": False,
        **_capture_extra("male", a_sid, a_item),
    })
    events.append({
        "event": "capture", "player": "b", "area_id": boxed_area,
        "species_id": b_sid, "key": b_key, "nickname": b_nick,
        "level": b_lv, "hp": 20 + b_lv, "maxHP": 20 + b_lv, "in_box": False,
        **_capture_extra("female", b_sid, b_item),
    })

    # Alice catches somewhere Bob has not been. Stays pending -- and quarantined to her
    # box -- until Bob catches there too.
    pend_area, (p_sid, p_key, p_nick, p_lv, p_item) = _pending()
    events.append({"event": "area_enter", "player": "a", "area_id": pend_area})
    events.append({
        "event": "capture", "player": "a", "area_id": pend_area,
        "species_id": p_sid, "key": p_key, "nickname": p_nick,
        "level": p_lv, "hp": 20 + p_lv, "maxHP": 20 + p_lv, "in_box": True,
        **_capture_extra("male", p_sid, p_item),
    })

    # A pair taken all the way to "memorial": both caught, A's half faints (the server
    # force-faints B's and queues a memorialize for each), then both clients confirm the
    # move into the memorial box -- lua/gen2/client.lua run_box sends memorialize_done --
    # and later report the mons in that box (the pc_boxes ticks below).
    memorial = _memorial()
    if memorial:
        mem_area, mem_a, mem_b = memorial
        for player, cap in (("a", mem_a), ("b", mem_b)):
            events.append({"event": "area_enter", "player": player, "area_id": mem_area})
            events.append({
                "event": "capture", "player": player, "area_id": mem_area,
                "species_id": cap[0], "key": cap[1], "nickname": cap[2],
                "level": cap[3], "hp": 20 + cap[3], "maxHP": 20 + cap[3], "in_box": False,
                **_capture_extra("", cap[0], cap[4]),
            })
        events.append({"event": "faint", "player": "a", "key": mem_a[1], "area_id": mem_area})
        for player, cap in (("a", mem_a), ("b", mem_b)):
            events.append({"event": "memorialize_done", "player": player, "key": cap[1],
                           "box": GEN2_MEMORIAL_BOX})

    # Faint one of the linked party mons — the route3 pair becomes a Memorial
    # (on Gen 2 it stays DEAD: its memorialize is never confirmed, see DEAD_SLOT)
    events.append({
        "event": "faint", "player": "a", "key": _pairs()[2][1][1],
        "area_id": _pairs()[2][0],
    })

    # Final area positions — drive each player into a real RR area that has
    # entries in rr_priority_trainers.json so the Upcoming Trainers widget
    # renders. A → Pewter Museum (Falkner), B → Route 25 (Bugsy). Different
    # areas exercise the split-view trainer column per player AND keep both
    # populated in the combined view's Area Briefing card.
    final_a, final_b = _final_areas()
    events.append({"event": "area_enter", "player": "a", "area_id": final_a})
    events.append({"event": "area_enter", "player": "b", "area_id": final_b})

    # Send party tick with all 6 alive Alice mons (so widget shows party of 6).
    # Held items + moves propagate so the held-item, move-dropdown, and LP
    # widget rendering paths all paint.
    alice_party = [
        _mon(*a_cap, gender="male", active=(i == 0))
        for i, (_, a_cap, _) in enumerate(_pairs())
    ]
    alice_party[LOW_HP_SLOT]["hp"] = LOW_HP
    bob_party = [
        _mon(*b_cap, gender="female", active=(i == 0))
        for i, (_, _, b_cap) in enumerate(_pairs())
    ]
    if memorial:
        # The DEAD pair is still in both parties, at 0 HP: its memorialize is queued but no
        # client has reached a safe state to run it yet. That is what keeps it "dead".
        alice_party[DEAD_SLOT]["hp"] = bob_party[DEAD_SLOT]["hp"] = 0
    # Tick fields are FLAT (not nested under "battle_state") — the server
    # only reads in_battle / enemy_party / is_trainer_battle at the top
    # level of the tick message. See server.py handle_event tick branch.
    events.append({
        "event": "tick", "player": "a", "has_pokeballs": True,
        # Real RR area_id so the Upcoming Trainers widget renders (Falkner @ Pewter Museum).
        "party": alice_party, "current_area_id": final_a,
        "ball_count": 12, "badges": 0b00000011,  # 2 badges
        "in_battle": False, "enemy_party": [],
    })
    # Bob in active battle vs wild Caterpie — populates Enemy widget,
    # Calc preview, and the "NEW ENCOUNTER" badge when nuzlocke-active.
    # Different area (route_25 = Bugsy) so split view shows distinct trainer
    # cards per side, and combined view's Area Briefing shows two sides.
    events.append({
        "event": "tick", "player": "b", "has_pokeballs": True,
        "party": bob_party, "current_area_id": final_b,
        "ball_count": 7, "badges": 0b00000001,  # 1 badge
        "in_battle": True, "is_trainer_battle": False,
        "opponent_name": "", "opponent_class": "",
        "is_doubles": False,
        "enemy_party": [_wild_foe()],
    })

    # PC boxes. Without these every box-facing surface (the dashboard's box table,
    # the Boxed Links overlay, any "where is it stored" lookup) renders empty, and an
    # empty panel hides its own layout bugs. The boxed-link pair from BOXED_AREA is
    # deposited for real; two filler mons give the table more than one row to lay out.
    # Deliberately NOT writing into the memorial box -- that trips the contamination
    # check, which is a different behaviour from the one these mocks exist to show.
    box_a, box_b = boxed_a, boxed_b
    events.append({
        "event": "tick", "player": "a", "has_pokeballs": True,
        "party": alice_party, "current_area_id": final_a,
        "pc_boxes": [
            {"box": 0, "slot": 0, "key": box_a[1], "nickname": box_a[2],
             "species_id": box_a[0], "held_item_id": box_a[4], "moves": _moves().get(box_a[0], [])},
            {"box": 0, "slot": 2, "key": p_key, "nickname": p_nick,
             "species_id": p_sid, "held_item_id": p_item, "moves": _moves().get(p_sid, [])},
            {"box": 0, "slot": 1, "key": _stored("A1", "30B8", _sid(133)), "nickname": "Spare",
             "species_id": _sid(133), "held_item_id": 0, "moves": []},
            {"box": 1, "slot": 4, "key": _stored("A2", "30B8", _sid(63)), "nickname": "Bench",
             "species_id": _sid(63), "held_item_id": 0, "moves": []},
        ] + _memorial_box_rows(0),
    })
    events.append({
        "event": "tick", "player": "b", "has_pokeballs": True,
        "party": bob_party, "current_area_id": final_b,
        "pc_boxes": [
            {"box": 0, "slot": 0, "key": box_b[1], "nickname": box_b[2],
             "species_id": box_b[0], "held_item_id": box_b[4], "moves": _moves().get(box_b[0], [])},
            {"box": 0, "slot": 1, "key": _stored("B1", "7B0B", _sid(129)), "nickname": "Reserve",
             "species_id": _sid(129), "held_item_id": 0, "moves": []},
        ] + _memorial_box_rows(1),
    })

    # The attempt counter goes over HTTP and depends on nothing in the event stream, so
    # it is set BEFORE the stream. That way the run is complete while the socket is still
    # open (see HOLD), rather than only after it has closed and player A reads as gone.
    print("Bumping attempt counter via /api/attempts...")
    try:
        http_post("/api/attempts", {"count": 7})
    except Exception as e:
        print(f"  /api/attempts failed: {e}")

    await send_tcp(events)

    print("Done. Refresh http://localhost:8080/ in the browser.")


if __name__ == "__main__":
    import argparse

    _ap = argparse.ArgumentParser(description=__doc__)
    _ap.add_argument("--game", choices=("gen3", "gen1", "gen2"), default="gen3",
                     help="which generation's cast to inject (default: gen3)")
    _ap.add_argument("--title",
                     choices=("crystal", "gold", "silver", "firered_rr", "frlg", "emerald"),
                     default="crystal",
                     help="gen2: the title both players run (default: crystal). "
                          "gen3: firered_rr (default, RR both sides), frlg (vanilla "
                          "firered/leafgreen), emerald (vanilla emerald both sides, Hoenn cast)")
    _args = _ap.parse_args()
    GAME, TITLE = _args.game, _args.title
    asyncio.run(main())
