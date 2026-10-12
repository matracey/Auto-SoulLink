"""
server/adapters/base.py — Abstract base classes for game adapters.

GameRulesAdapter: Methods used by the state machine (FSM) for Soul Link logic.
GamePresentationAdapter: Methods used by the HTTP status page for display.
GameAdapter: Combined interface — most games implement both.

Each supported game (Gen 1 RBY, Gen 3 FRLG, Gen 4 HGSS/Pt, etc.) provides a
concrete adapter implementing these interfaces.

ISOLATION CONTRACT:
- Adapters MUST NOT import from server.server (circular dependency).
- Adapters load their own data files independently (from data/games/<gen>/).
- All game-specific display logic (sprites, items, areas, types) goes through
  adapter methods — never through standalone functions in server.py.
- Use gen4_hgsspt.py as the model adapter (zero external dependencies).
- Item/species data should be hardcoded or loaded from game data files,
  never imported from server.py.
"""

import json
import logging
import os
import re
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


def humanize_area_id(area_id: str) -> str:
    """The fallback display name for an area id nothing else names: words at every `_`,
    at a lower->Upper or letter->digit seam ("Route21_North" -> "Route 21 North",
    "route8" -> "Route 8"), each capitalised, floor tokens kept ("1F", "B2F")."""
    words = []
    for tok in area_id.replace("-", "_").split("_"):
        if not tok:
            continue
        # a digit run only splits off at the END of a token: "route8" -> "Route 8", but
        # a floor code stays whole: "b1f" -> "B1F"
        tok = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[a-z])(?=[0-9]+$)|(?<=[0-9])(?=[A-Z][a-z])", " ", tok)
        words.extend(w if w.isupper() else w.title() for w in tok.split(" "))
    return " ".join(words)


def load_area_names_from_obj_map(json_path: str) -> "dict[str, str]":
    """Load area display names from a JSON file with {key: {area_id, name}} schema.

    Used by Gen 1 and Gen 2 adapters whose area_map.json files share this format.
    Returns a dict mapping area_id → display name.
    """
    result: dict[str, str] = {}
    if not os.path.exists(json_path):
        log.warning("Area map not found: %s — area names will use fallback", json_path)
        return result
    with open(json_path) as f:
        raw = json.load(f)
    entries = raw.values() if isinstance(raw, dict) else raw
    for entry in entries:
        if isinstance(entry, dict) and "area_id" in entry:
            result[entry["area_id"]] = entry.get("name", entry["area_id"])
    return result


def companion_required_reason(title: str) -> str:
    """The player-facing refusal for a clean cartridge of a title that requires the companion."""
    return (f"this {title} cartridge needs the SLink companion patch; "
            "prepare it through the Manager or /patcher")


class GameRulesAdapter(ABC):
    """Interface for game-specific Soul Link rule logic.

    Used by SoulLinkState (the FSM) to enforce link rules, determine
    shininess, gender, evolution families, and gift area detection.
    """

    @property
    def rom_contract_by_sha1(self) -> bool:
        """Bind randomized ROM content to the server's contract SHA1 instead of a client fingerprint.

        Default False preserves fingerprint-based admission. Adapters opting in must ingest only
        the server's SHA1-checked cartridge bytes; client-supplied decoded tables are not authority.
        """
        return False

    @property
    @abstractmethod
    def game_id(self) -> str:
        """Unique identifier for this game family (e.g., 'frlg', 'gen1_rby')."""
        ...

    @abstractmethod
    def is_gift_area(self, area_id: str) -> bool:
        """Return True if the area is a gift/static encounter location.

        Gift areas do not activate the pokeball gate and do not generate
        no_catch events.
        """
        ...

    def is_fixed_species_gift(self, area_id: str) -> bool:
        """Return True if this gift area always produces the same predetermined species.

        Used to bypass clause checks (species/gender/type) when both players are
        guaranteed to receive an identical species with no player choice involved.
        Examples: Magikarp salesman, Eevee, Lapras.

        Areas where the player chooses (starters, fossils, Hitmonlee/Hitmonchan)
        return False — clauses still apply there.

        Default: False. Adapters override this for areas with forced species.
        """
        return False

    def is_daycare_area(self, area_id: str) -> bool:
        """Return True if the area is a daycare where eggs originate from breeding.

        Used to distinguish egg captures that should be treated as gifts (NPC
        egg-givers in encounter areas) from daycare-bred eggs (player has
        already deposited mons; the egg is not a gift).

        Default: False. Adapters override this for games with known daycare areas.
        """
        return False

    def gift_link_area(self, area_id: str) -> str:
        """Area_id under which a gift/egg capture should be linked.

        A gift received in a real (non-gift) encounter area must NOT consume or
        lock that area's single wild-encounter slot. Map it into the gift
        namespace so it forms a standalone gift pair instead. Gifts already in a
        gift area (or daycare-bred eggs) keep their area_id unchanged.

        The "gift_" prefix is recognized by every adapter's is_gift_area, so the
        remapped id is treated as a gift area downstream (no quarantine, no
        no_catch dead-zone, rendered as a gift).
        """
        if self.is_gift_area(area_id) or self.is_daycare_area(area_id):
            return area_id
        return f"gift_{area_id}"

    def is_egg_pickup_area(self, area_id: str) -> bool:
        """Return True if the area is an NPC egg-pickup location.

        Egg pickups (Togepi from Mr. Pokémon, Riolu from Riley, Spiky-eared Pichu
        from Ilex Forest, etc.) are gift-like: the player receives an unhatched
        egg whose species is fixed by the NPC, not determined at hatch time. This
        is used by SoulLinkState to bypass species/gender/type clauses for eggs
        that would otherwise fail because the hatched species could differ
        between players' linked saves (it doesn't — it's NPC-fixed).

        Clients emit a "egg_<area>" prefix on the area_id; the default impl
        recognizes that prefix. Adapters override with explicit area sets.
        """
        return (area_id or "").startswith("egg_")

    @abstractmethod
    def evo_family(self, species_id: int) -> int:
        """Return the base-form species ID for species lock checks.

        Single-stage mons return themselves.
        """
        ...

    @abstractmethod
    def gender_from_key(self, key: str, species_id: int) -> str:
        """Derive gender from the mon's key and species ID.

        Returns 'male', 'female', or 'genderless'.
        Key format is game-specific (adapter knows how to parse it).
        """
        ...

    @abstractmethod
    def species_types(self, species_id: int) -> tuple[int, int] | None:
        """Return (type1, type2) for a species, or None if unknown.

        Used for type lock checks.
        """
        ...

    @abstractmethod
    def is_shiny(self, key: str) -> bool:
        """Determine if a mon is shiny from its key.

        Key format is game-specific; the adapter knows how to extract
        the necessary values (e.g., personality/otId for Gen 3+,
        DVs for Gen 1-2).
        """
        ...

    @abstractmethod
    def parse_ot_id(self, key: str) -> str:
        """Extract the OT (Original Trainer) ID portion from a mon key.

        Used for player identity lock validation.
        Returns an opaque string representing the trainer identity.
        """
        ...

    @abstractmethod
    def is_valid_mon_key(self, key: str) -> bool:
        """Validate that a mon key string is well-formed for this game."""
        ...

    @abstractmethod
    def species_name(self, species_id: int) -> str:
        """Return display name for a species ID.

        Used in HUD messages and log output from the state machine.
        """
        ...

    @abstractmethod
    def type_name(self, type_id: int) -> str:
        """Return display name for a type ID (e.g., 0 -> 'Normal')."""
        ...

    def rival_trainer_ids(self) -> set[int]:
        """Return the set of trainer IDs that represent the player's RIVAL.

        Used by the Rival Team Swap feature: when a player walks into a
        trainer battle whose ID is in this set, the server may forward the
        partner's cached party blobs as a `replace_rival_team` command for
        the player's Lua to inject into gEnemyParty.

        Default returns an empty set so non-supporting adapters (Gen 1/2/4/5,
        plus Gen 3 variants other than Radical Red) become no-ops without
        any extra branching elsewhere — the server's gate naturally falls
        through when the set is empty.  Per CLAUDE.md, only the gen-specific
        adapter (Gen 3) overrides this; no `is_rr` flag in shared code.
        """
        return set()

    def party_blob_size(self) -> int:
        """Bytes in one cached party-mon blob, or 0 if this game doesn't cache them.

        `_ingest_party_blobs` validates every incoming blob against this before storing it
        for the Rival Team Swap. It used to test a hardcoded 100 — Gen 3's boxmon size —
        which silently discarded any other generation's blobs, leaving `partner_blobs`
        empty so the swap could only ever answer "partner has no cached party blobs".

        The size is per-game and not simply the party-struct size: Gen 1 keeps OT names and
        nicknames in parallel arrays outside the struct, so a faithful copy is 44 + 11 + 11.

        Default 0 so a game that has not opted in stores nothing, rather than storing blobs
        of an unvalidated length.
        """
        return 0

    def supports_abilities(self) -> bool:
        """Whether this game has Pokémon abilities at all.

        Gen 1 and Gen 2 do not, so their party tables must not render an Ability column. This
        used to be a `game_id in ("gen1_rby", "gen2_crystal")` test inside server.py — the last
        game_id branch in shared code, and exactly what the isolation contract forbids.

        Default True because abilities exist from Gen 3 onward; the two adapters that predate
        them override it.
        """
        return True

    def status_token(self, status_cond: int) -> str:
        """A 3-letter status token (SLP/PSN/BRN/FRZ/PAR/TOX) for a raw status value, or "".

        The encoding is per-generation, so shared code must not decode it. Default "" means a game
        that has not implemented this simply shows no status rather than a wrong one.
        """
        return ""

    def info_panel_width(self) -> int:
        """Columns the native panel can show, or 0 when there is no native panel.

        The panel is drawn on the console's own screen, so its width is a property of the
        hardware rather than a style choice: Gen 3 has 30 columns to lay out in and Gen 1
        has 20, which is not enough for the same layout. Callers format to this rather
        than branching on which game it is.
        """
        return 0

    def reports_box_census(self) -> bool:
        """KEY-SCOPE-5: whether this foundation's Lua client stamps each complete PC-box scan
        with `pc_boxes_generation`. When True, a snapshot without one is NO census (a key_change
        is refused, retiring nothing); when False the server keeps the legacy presence check."""
        return False

    def supports_info_panel(self) -> bool:
        """Whether this game's Lua client can render the native in-game info panel.

        The panel is drawn by the companion ROM patch's SOULLINK start-menu screen,
        which exists only for Radical Red.  Other clients would fall into their
        unknown-command branch and log a warning every time the panel changed, so
        the server simply doesn't send `link_panel` to them.

        Default False for the same reason as `supports_explode_mode`: the
        game-specific adapter opts in and no `game_id` branch appears in shared
        code.  A client that supports it still decides for itself whether to turn
        the menu row on, since only the client can see whether the patch is
        actually present in the ROM it is attached to.
        """
        return False

    def supports_explode_mode(self) -> bool:
        """Whether this game's Lua client understands the `force_explode` command.

        Explode Mode swaps the deferred `force_faint` for `force_explode`, which
        coerces the surviving partner into Explosion.  Only the Gen 3 Radical Red
        client implements it (`archive/gen3-old-client:lua/clients/gen3_frlge_client.lua`); the Gen 1/2/4/5 clients
        fall into their unknown-command branch and merely log it, so the linked
        mon would never faint — a silent Soul Link rule violation.

        Default False so an unsupported game falls back to the normal deferred
        faint, mirroring the `rival_trainer_ids()` gate: the game-specific adapter
        opts in, and no `game_id` branch appears in shared code.
        """
        return False

    def set_artifact_kind(self, kind: str) -> None:
        """Bind the run's committed artifact kind (hello `artifact_kind`; server/state.py).

        A per-run capability such as `native_trade_ui()` may follow it: the server calls
        this once the kind commits and on every adapter it builds for the run. No-op by
        default; a game whose capabilities depend on the artifact opts in (Gen 1 pureRGB:
        the SLink companion overlay carries the panel and the receptionist, a clean build
        does not). A pair of mixed kinds never reaches here: the hello check refuses it.
        """
        return None

    @staticmethod
    def pairing_kind(kind: str) -> str:
        """Normalize a declared `artifact_kind` for the PAIRING comparison only.

        `_mixed_games_error` asks each foundation's adapter CLASS whether two declared
        kinds describe the same artifact layout, so this is a pure lookup: static, no
        instance, no candidate adapter installed to answer a hello that may be refused.
        The COMMITTED kind is untouched -- `set_artifact_kind` still receives what the
        client declared.

        Default is today's rule: a companion-patched vanilla cartridge ("named") is a
        clean-layout artifact, so it pairs with a clean one. A game whose patch is
        likewise per cartridge overrides this; pureRGB does not (its overlay is a
        run-level capability, so clean and overlay must never mix).
        """
        return {"named": "clean"}.get(kind, kind)

    @staticmethod
    def companion_refusal(hello: dict) -> str | None:
        """Patch-first (owner 2026-10-02): why this hello's cartridge is refused for lacking the
        SLink companion patch, or None when it may connect.

        A pure CLASS lookup on the hello, like `pairing_kind` (the server asks it before any
        adapter is installed for a hello that may be refused). Inert by default: a game with no
        companion, or whose companion is optional (Yellow, Archipelago builds, the Emerald
        Expansion, Gen 4/5), is never refused. A companion title overrides this and returns
        `companion_required_reason(title)` for a hello with no evidence of the patch.
        """
        return None

    @classmethod
    def supports_randomized(cls, rom_type: str) -> bool:
        """Opt in only when this title has a supported randomized-cartridge binding."""
        return False

    @classmethod
    def pairing_kind_for(cls, kind: str, rom_content: object) -> str:
        """`pairing_kind`, given the hello's own ROM report (None when it sent none). Still a
        class lookup, for the same reason. A foundation may pair a declared kind by what the
        cartridge holds (Gen 3: a `rand` ROM whose tables equal pret's pairs as clean); the
        server then commits that effective kind. Default: the declared kind alone."""
        return cls.pairing_kind(kind)

    def native_trade_ui(self) -> bool:
        """Whether the cartridge itself drives the trade menus.

        The Gen 1 companion patch puts a receptionist in the ROM: it shows the
        action menu and the party picker on the console, sends the server a
        physical slot, and runs the cartridge's own trade scene.  The shared
        trade FSM therefore skips its own `show_choices`/`choose_mon` step, hands
        out an eligible-slot mask, and puts the slot/blob/partner name the ROM
        needs on the confirm prompt and the apply commands.

        Default False so a game without that ROM support keeps the ordinary
        server-driven menu flow, like `supports_explode_mode`: the game-specific
        adapter opts in and no `game_id` branch appears in shared code.
        """
        return False

    def supports_trade_recovery(self) -> bool:
        """Accept withheld snapshots and outstanding trade leases on hello.

        This opts into a wire protocol, not native trade capability. Other clients
        retain their existing snapshot and reconciliation behavior.
        """
        return False

    def trade_unavailable_reason(self) -> str:
        """Named refusal for a trade_request while either player's hello lacks trade_prepare
        (a cartridge build without the durable-trade witness); empty preserves existing behavior."""
        return ""

    def refused_trade_recovery(self) -> str:
        """Named refusal when a foundation cannot interpret these optional fields."""
        return ""


class GamePresentationAdapter(ABC):
    """Interface for game-specific display/UI logic.

    Used by the HTTP status page and stream overlays for sprites,
    formatted names, and other visual elements.
    """

    @abstractmethod
    def sprite_html(self, species_id: int, form: int = 0) -> str:
        """Return an HTML <img> tag for the species sprite.

        Optional `form` byte (default 0) selects alternate-form sprites for games
        that support them (Gen 4+). Adapters that don't support forms can ignore
        the parameter — the default keeps the existing single-arg call sites valid.
        """
        ...

    @abstractmethod
    def ability_name(self, ability_id: int, species_id: int = 0) -> str:
        """Return display name for an ability ID.

        species_id: internal species ID (optional). Passed through to enable
        species-specific name overrides for aliased abilities in RR/CFRU.
        """
        ...

    @abstractmethod
    def ability_description(self, ability_id: int) -> str:
        """Return tooltip description for an ability ID."""
        ...

    @abstractmethod
    def trainer_info(self, trainer_id: int) -> tuple[str, str]:
        """Return (trainer_name, trainer_class) for a trainer ID.

        Returns ("", "") if the game doesn't support trainer lookup.
        trainer_id is 1-based as received from Lua.
        """
        ...

    @abstractmethod
    def item_name(self, item_id: int) -> str:
        """Return display name for an item ID."""
        ...

    @abstractmethod
    def area_display_name(self, area_id: str) -> str:
        """Return a human-friendly display name for an area_id."""
        ...

    @abstractmethod
    def to_national_dex(self, species_id: int) -> int:
        """Convert game-internal species ID to National Dex number."""
        ...

    @abstractmethod
    def gender_symbol(self, gender: str) -> str:
        """Return the display symbol for a gender string."""
        ...

    @abstractmethod
    def form_sprite_id(self, species_id: int) -> int | None:
        """Return alternative sprite ID for forms, or None for base form."""
        ...

    def form_sprite_url(self, species_id: int, form: int = 0) -> str | None:
        """Return a PokeAPI sprite URL for an alternate form, or None for base form.

        Gen 4+ Pokémon (Rotom appliances, Giratina Origin, Shaymin Sky, Wormadam
        cloaks, Burmy, Cherrim, Castform, Shellos/Gastrodon, Deoxys, Unown letters,
        Arceus plates) have a `form` byte from Block B that distinguishes visuals.
        Adapters that support form-aware sprites override this; default is None
        (no alternate form), which makes callers fall back to the base sprite URL.

        Returned URL is the full PNG URL on github.com/PokeAPI/sprites.
        """
        return None

    def rom_content_fingerprint(self, payload: dict) -> str | None:
        """A digest of what a client reports about its own ROM, or None.

        Used for admission: a run built from randomized ROMs records the fingerprint the
        Manager computed when it made each one, and the server compares what the client
        reports against it. Returning None means this generation cannot answer, and such a
        run simply has nothing to check.

        MUST RAISE on a malformed payload rather than returning a fingerprint of partial
        data -- a value that happens to differ would read as "wrong ROM" when the truth is
        "unreadable report", and those need different messages.
        """
        return None

    def ingest_rom_content(self, payload: dict) -> dict[str, dict[str, list[dict]]] | None:
        """Turn a client's report of its own ROM into encounter tables, or None.

        A run may be played on ROMs randomized per player -- same settings, different
        seeds -- so the shipped tables, which describe the retail cartridge, are simply
        wrong for that player. A client that can read its cartridge sends what it found and
        this converts it into the same shape ``encounter_table`` returns.

        Generic on purpose: server.py must not know which generation can do this. Adapters
        that cannot return None and the caller keeps using the shipped tables.

        MUST RAISE on a malformed payload rather than returning partial data. The caller's
        correct response to a bad payload is to mark the encounter data unavailable, never
        to fall back to the shipped tables -- retail species shown beside a randomized
        cartridge is precisely the misinformation this exists to remove.
        """
        return None

    def refused_rom_content(self, payload: object, *, artifact_kind: str | None = None) -> str:
        """Why a player's cartridge must be refused even when no contract binds the run, or "".

        For a READABLE cartridge the server cannot rule on (its rule tables were randomized):
        admitting it would apply the shipped rules to a game that no longer has them.
        `artifact_kind` is the candidate hello's declared kind, before the run commits it.
        Foundations may require a complete report for selected kinds. The base hook is
        unchanged for foundations that do not impose a ROM-content admission requirement.
        """
        return ""

    def encounter_table(self, area_id: str) -> dict[str, list[dict]] | None:
        """Return wild encounter data for an area, or None if unavailable.

        Returns a dict mapping method label → list of encounter entries, e.g.:
            {
              "Day":  [{"name": "Bidoof", "species_id": 452, "rate": 20,
                        "min_level": 2, "max_level": 4}, ...],
              "Night": [...],
            }
        Only available for RR runs in Gen3Adapter; other adapters return None.
        """
        return None

    def trainers_for_area(self, area_id: str) -> list[int]:
        """Return runtime trainer IDs that appear in the given area, or [] if none.

        Used by the dashboard's Upcoming Trainers panel. Only populated for RR
        runs in Gen3Adapter; other adapters return [].
        """
        return []

    def trainer_party(self, trainer_id: int) -> list[dict]:
        """Return the curated party list for a trainer ID, or [] if unknown.

        Each entry has at minimum: species_id (int), level (int). Optional keys:
        species (str), ability (str), item (str), nature (str), moves (list[str]).
        Only populated for RR runs in Gen3Adapter.
        """
        return []

    def trainer_brief(self, trainer_id: int) -> dict | None:
        """Return {name, class, party, area?} for a curated trainer, or None.

        Convenience for callers that want display info + roster in one call.
        Default fallback synthesizes name/class via `trainer_info` and party
        via `trainer_party`; adapters with richer per-trainer metadata
        (e.g. RR's priority roster) may override.
        """
        party = self.trainer_party(trainer_id)
        if not party:
            return None
        name, cls = self.trainer_info(trainer_id)
        return {"name": name, "class": cls, "party": party, "area": ""}

    def calc_name(self, kind: str, name: str) -> str:
        """Return the damage calc's name for a display name.

        kind is "species", "ability", "item" or "move". Default is identity;
        adapters whose ROM text differs from the calc's names override it.
        """
        return name

    def calc_species(self, species_id: int) -> str:
        """Return the damage calc's species name for an internal species id.

        Default is `calc_name("species", species_name(species_id))` -- the same expression
        every caller used inline before this existed, so behaviour is unchanged for every
        adapter that doesn't override it. An adapter overrides this instead of `species_name`
        when the two need to diverge: pureRGB's 7 alternate forms (Hardened Onix, Volcanic
        Magmar, ...) all display in-game with their BASE species' name (species_name(172) ==
        species_name(34) == "Onix" -- that's the whole trick behind an alternate form), which
        `species_name` must keep returning for HUD/log text, but the calc needs a name unique
        per species (both a JS object key and a toID() lookup key in species.ts), so it can't
        use that colliding text -- see docs/calc_multigen/PURERGB_MECHANICS.md §2.
        """
        return self.calc_name("species", self.species_name(species_id))

    def calc_profile(self) -> dict | None:
        """Return {"gen": int, "dex": str} for the bundled Smogon calc, or None.

        gen is the calc generation to run (1/2/3/9). dex is which data set to use:
        "rr" | "vanilla" | "purergb". None means this game's calc numbers are not
        verified yet, so the Calc tab and dashboard calc preview are hidden.
        """
        return None

    def calc_nature(self, key: str) -> str | None:
        """Nature name for the calc, or None when the game has no natures (Gen 1/2).

        Default None.
        """
        return None

    def calc_stats(self, detail: dict) -> dict | None:
        """Calc-ready stat inputs decoded from the party snapshot `detail` (keys include
        "key", "level", "blob_hex" when the client sent one). Default None = unknown
        (the calc keeps its defaults). Shape, stat ids as the calc uses them:
          Gen 3+: {"ivs": {hp,atk,def,spa,spd,spe}, "evs": {hp,atk,def,spa,spd,spe},
                   "stats": {hp,atk,def,spa,spd,spe}}
          Gen 1/2: {"dvs": {atk,def,spe,spc}, "stat_exp": {hp,atk,def,spe,spc} (raw 0-65535),
                    "stats": {hp,atk,def,spa,spd,spe}}  (Gen 1 spa == spd == Special)
        "stats" are the in-game computed stats; the calc warns when its own result differs.
        """
        return None

    def sprite_src(self, species_id: int) -> str:
        """Return just the sprite image URL for a species (no HTML wrapping).

        Default returns a PokeAPI URL. Override in adapters that need
        game-specific sprite repositories (e.g. funnotbun for RR/CFRU).
        Used by _enc_table_for_status() to keep the JSON payload small.
        """
        if not species_id or species_id < 1:
            return ""
        nat = self.to_national_dex(species_id)
        sid = nat if (nat and 1 <= nat <= 1025) else species_id
        return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{sid}.png"

    def move_name(self, move_id: int) -> str:
        """Return display name for a move ID.

        Default returns empty string. Override in adapters with move data.
        """
        return ""

    def move_data(self, move_id: int) -> dict | None:
        """Return move details for a move ID, or None if unknown.

        Returns dict with keys: name, type_id, type_name, power, accuracy,
        pp, split (0=Physical, 1=Special, 2=Status).
        """
        return None

    def stat_stage_labels(self) -> list[str]:
        """Labels for the seven stat-stage slots, in order.

        A generation with fewer stats than slots blanks the ones it does not have; a
        blank label suppresses that badge entirely. Gen 1 needs this because it has a
        single Special, and the shared renderer would otherwise have to be told about
        generations, which is exactly what adapters exist to prevent.
        """
        return ["ATK", "DEF", "SPD", "SATK", "SDEF", "ACC", "EVA"]

    @property
    def mons_per_box(self) -> int:
        """How many mons fit in one PC box on this generation.

        Used to work out how many overflow boxes the memorial needs. It was a bare 30
        in shared code -- the Gen 3 figure -- which on Gen 1 (MONS_PER_BOX = 20,
        pokered/constants/pokemon_data_constants.asm) meant the first overflow box was
        allocated ten corpses too late.
        """
        return 30

    @property
    def memorial_box_index(self) -> int:
        """Return the 0-based PC box index reserved for memorialized (dead) mons.

        Returns -1 if the game has no dedicated memorial box (Gen 1/2).
        Gen 3: box 13 (last of 14 boxes).
        Gen 4: box 17 (last of 18 boxes).
        """
        return -1

    def gym_badge_slugs(self, rom_type: str) -> list[tuple[int, str]]:
        """Return (pokeapi_badge_id, display_name) for each gym badge.

        IDs come from the PokeAPI sprites repo: sprites/badges/{id}.png
        at https://raw.githubusercontent.com/PokeAPI/sprites/master/
        Kanto 1-8, Johto 9-16, Hoenn 17-24, Sinnoh 25-32.
        Ordered by bit position (bit 0 = index 0). Override in adapters
        that use a region other than Kanto.
        """
        return [
            (1, "Boulder Badge"),
            (2, "Cascade Badge"),
            (3, "Thunder Badge"),
            (4, "Rainbow Badge"),
            (5, "Soul Badge"),
            (6, "Marsh Badge"),
            (7, "Volcano Badge"),
            (8, "Earth Badge"),
        ]


def gb_status_token(status_cond: int) -> str:
    """SLP/PSN/BRN/FRZ/PAR from a Game Boy status byte, or "".

    Gen 1 and Gen 2 share this layout exactly: sleep is a COUNTER in bits 0-2, so it
    must be MASKED rather than compared, then PSN 3, BRN 4, FRZ 5, PAR 6.
    pokered/constants/status_constants.asm and pokecrystal/constants/battle_constants.asm:162
    (`DEF SLP_MASK EQU %111`, then `const_def 3` / PSN / BRN / FRZ / PAR) agree bit
    for bit. Bit 7 is unused in both -- neither has a persistent Toxic, it is a
    volatile that lasts only for the battle -- so there is deliberately no TOX branch.

    Checked in the same order as the Gen 3 adapter, so a mon carrying two bits reports
    the same one whichever generation it is on.
    """
    if not status_cond:
        return ""
    if status_cond & 0x07:
        return "SLP"
    for bit, token in ((0x08, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR")):
        if status_cond & bit:
            return token
    return ""


class GameAdapter(GameRulesAdapter, GamePresentationAdapter):
    """Combined adapter interface — most games implement both layers.

    Subclass this for a complete game adapter that provides both
    rule logic and presentation methods.

    Default implementations are provided for the PID:OTID key format
    (Gen 3/4/5). Gen 1 and Gen 2 override these with their 3-part
    key format (DDDD:TTTT:SS).
    """

    def parse_ot_id(self, key: str) -> str:
        """Extract OT ID from PID:OTID key (personality:otId).

        Default for Gen 3/4/5 two-part key format.
        Gen 1 and Gen 2 override this (three-part DDDD:TTTT:SS format).
        """
        return key.split(":")[1] if self.is_valid_mon_key(key) else ""

    def is_valid_mon_key(self, key: str) -> bool:
        """Validate PID:OTID format: two ASCII hex segments of 1-8 digits.

        Default for Gen 3/4/5 two-part key format.
        Gen 1 and Gen 2 override this (three-part DDDD:TTTT:SS format).
        """
        return isinstance(key, str) and re.fullmatch(r"[0-9A-Fa-f]{1,8}:[0-9A-Fa-f]{1,8}", key) is not None
