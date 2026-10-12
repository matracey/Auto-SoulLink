"""Strict, title-neutral NDS companion pins. Validators never write artifacts.

Container hashes describe OUTPUT containers. The complete parent hashes and
site preimages describe byte-patched inputs. Output verification requires the
actual parent bytes whenever it claims a declared diff; a digest is not a diff.

VERIFIED by this module (given the image objects): identity/hashes of parent,
output and optional reference build; container raw/decoded hashes, RAM size and
overlay geometry; site before/after bytes in the DECODED container; no-touch
spans; declared-diff completeness; DSi payload preservation; distribution
artifact application via an injected callable.
Also checked at construction: a changed or new CODE site's `after` must decode, under
its declared ISA, as a recognised first instruction (not 'unknown'/'split'; `data=True`
opts a pointer/table/literal site out) and its `continuation` must be an in-extent RAM
address outside the site's own bytes.
RECORDED ONLY (schema-checked, never proven here): title, evidence_class labels,
receipt/evidence refs, the ISA label beyond the first-instruction decode, generator/source
hashes, SourceBuild fields (repo, commit, toolchain, patch_set, vanilla
reproduction, tracked inputs), native_abi, capabilities, native_arena,
roundtrip_verified, dsi_preserved_reason.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import MISSING, asdict, dataclass, fields
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from tools.nds_image import (
    AutoloadBlock,
    ChangedSpan,
    ImageError,
    NdsImage,
    digest,
    verify_only_declared_changes,
)
from tools.nds_isa import ARM, NdsIsaError, decode_arm, decode_thumb

SCHEMA_VERSION = 1
EVIDENCE = {"SOURCE", "FILE", "PHYSICAL", "REPORTED", "UNKNOWN"}
DISTRIBUTION_FORMATS = ("ups", "bps", "xdelta", "custom")
# Documented vocabulary (mirrors patch/src/nds/common/abi.h; a test compares).
NATIVE_ABI_VERSION = 3
CAPABILITY_BITS = {"DURABLE_TRADE": 0, "INFO_PANEL": 1, "NATIVE_SOUND": 2, "EXPLODE": 3,
                   "RIVAL_SWAP": 4, "BATTLE_CALC": 5, "MATCH_CALL": 6}
AUTOLOAD = ("itcm", "dtcm")
_NO_FILE_ID = ("arm9",) + AUTOLOAD
_CONTAINER = re.compile(r"arm9|itcm|dtcm|overlay[79]:(?:0|[1-9][0-9]*)")


class PinError(ValueError):
    """A schema, identity, site, or declared-change claim failed."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise PinError(f"{name}: nonempty string required")


def _opt_text(value, name):
    if value is not None:
        _text(value, name)


def _uint(value, name, limit=0xFFFFFFFF):
    if type(value) is not int or not 0 <= value <= limit:
        raise PinError(f"{name}: unsigned integer required")


def _hash(value, n, name):
    if not isinstance(value, str) or re.fullmatch(f"[0-9a-f]{{{n}}}", value) is None:
        raise PinError(f"{name}: canonical lowercase {n}-digit hash required")


def _commit(value, name):
    if not isinstance(value, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise PinError(f"{name}: full pinned commit required")


def _hex(value, name):
    if not isinstance(value, str) or not value or len(value) % 2 or re.fullmatch("[0-9a-fA-F]+", value) is None:
        raise PinError(f"{name}: nonempty even-length hex required")


def _bool(value, name):
    if type(value) is not bool:
        raise PinError(f"{name}: bool required")


@dataclass(frozen=True)
class Hashes:
    sha1: str
    md5: str
    sha256: str

    def __post_init__(self):
        _hash(self.sha1, 40, "sha1")
        _hash(self.md5, 32, "md5")
        _hash(self.sha256, 64, "sha256")

    @classmethod
    def of(cls, image):
        data = image.data if isinstance(image, NdsImage) else image
        return cls(*(digest(data, alg) for alg in ("sha1", "md5", "sha256")))


@dataclass(frozen=True)
class ParentPin:
    size: int
    gamecode: str
    version: int
    unit_code: int
    hashes: Hashes

    def __post_init__(self):
        _uint(self.size, "parent size")
        if self.size < 0x400 or not isinstance(self.gamecode, str) or re.fullmatch(r"[A-Z0-9]{4}", self.gamecode) is None:
            raise PinError("invalid parent size/gamecode")
        _uint(self.version, "version", 255)
        _uint(self.unit_code, "unit_code", 255)
        if not isinstance(self.hashes, Hashes):
            raise PinError("parent hashes required")

    @classmethod
    def of(cls, image):
        return cls(len(image.data), image.gamecode, image.version, image.unit_code, Hashes.of(image))


@dataclass(frozen=True)
class NamedHash:
    name: str
    sha256: str

    def __post_init__(self):
        _text(self.name, "input name")
        _hash(self.sha256, 64, "input sha256")


def _named_unique(seq, label, *, nonempty):
    if (nonempty and not seq) or len({v.name for v in seq}) != len(seq):
        raise PinError(f"{label}: {'nonempty ' if nonempty else ''}unique input hashes required")


@dataclass(frozen=True)
class VanillaReproduction:
    """Same commit + toolchain, NO SLink patch, rebuilds the base ROM byte-identically."""
    base_sha1: str
    rebuilt_sha1: str
    toolchain_id: str
    toolchain_sha256: str
    notes_ref: str

    def __post_init__(self):
        _hash(self.base_sha1, 40, "vanilla base sha1")
        _hash(self.rebuilt_sha1, 40, "vanilla rebuilt sha1")
        _text(self.toolchain_id, "vanilla toolchain id")
        _hash(self.toolchain_sha256, 64, "vanilla toolchain sha256")
        _text(self.notes_ref, "vanilla build notes ref")
        if self.base_sha1 != self.rebuilt_sha1:
            raise PinError("vanilla reproduction is not byte-identical (rebuilt_sha1 != base_sha1)")


@dataclass(frozen=True)
class PatchSet:
    """Identity of the SLink-side change set, distinct from the upstream commit."""
    slink_repo_commit: str
    patch_tree_sha256: str

    def __post_init__(self):
        _commit(self.slink_repo_commit, "patch_set slink_repo_commit")
        _hash(self.patch_tree_sha256, 64, "patch_set tree sha256")


@dataclass(frozen=True)
class SourceBuild:
    repo: str
    commit: str
    toolchain: str
    toolchain_binary_sha256: str
    base_rom_sha1: str
    patch_set: PatchSet
    base_arm9_compressed: bool | None = None
    vanilla_reproduction: VanillaReproduction | None = None
    vanilla_reproduction_waived: str | None = None
    dirty_tree: bool = False
    tracked_inputs: tuple[NamedHash, ...] = ()

    def __post_init__(self):
        _text(self.repo, "source repo")
        _commit(self.commit, "source commit")
        _text(self.toolchain, "toolchain")
        _hash(self.toolchain_binary_sha256, 64, "toolchain binary sha256")
        _hash(self.base_rom_sha1, 40, "source build base ROM sha1")
        if not isinstance(self.patch_set, PatchSet):
            raise PinError("patch_set required")
        if self.base_arm9_compressed is not None and type(self.base_arm9_compressed) is not bool:
            raise PinError("base_arm9_compressed must be bool or null")
        _bool(self.dirty_tree, "dirty_tree")
        _opt_text(self.vanilla_reproduction_waived, "vanilla_reproduction_waived")
        if (self.vanilla_reproduction is None) == (self.vanilla_reproduction_waived is None):
            raise PinError("source build needs exactly one of vanilla_reproduction or a waiver reason")
        if self.vanilla_reproduction is not None:
            if not isinstance(self.vanilla_reproduction, VanillaReproduction):
                raise PinError("invalid vanilla_reproduction")
            if self.vanilla_reproduction.base_sha1 != self.base_rom_sha1:
                raise PinError("vanilla reproduction base disagrees with source base ROM")
        if not isinstance(self.tracked_inputs, tuple) or any(not isinstance(v, NamedHash) for v in self.tracked_inputs):
            raise PinError("tracked_inputs: tuple of NamedHash required")
        _named_unique(self.tracked_inputs, "tracked_inputs", nonempty=self.dirty_tree)


@dataclass(frozen=True)
class ContainerPin:
    name: str
    raw_sha256: str
    decompressed_sha256: str
    file_id: int | None
    ram_base: int
    ram_size: int
    compressed: bool
    source_provided: bool = False

    def __post_init__(self):
        if not isinstance(self.name, str) or _CONTAINER.fullmatch(self.name) is None:
            raise PinError("container must be arm9, itcm, dtcm or overlay7/9:<id>")
        for value in (self.raw_sha256, self.decompressed_sha256):
            _hash(value, 64, "container sha256")
        if self.file_id is not None:
            _uint(self.file_id, "file_id")
        if (self.name in _NO_FILE_ID) != (self.file_id is None):
            raise PinError("only ARM9/ITCM/DTCM omit file_id (overlays require it)")
        _uint(self.ram_base, "RAM base")
        _uint(self.ram_size, "RAM size")
        if not self.ram_size or self.ram_base + self.ram_size > 0x100000000:
            raise PinError("invalid RAM extent")
        _bool(self.compressed, "container compressed")
        _bool(self.source_provided, "container source_provided")
        if self.name in AUTOLOAD and (self.compressed or self.raw_sha256 != self.decompressed_sha256):
            raise PinError("autoload blocks are raw: compressed false and raw == decompressed hash")


@dataclass(frozen=True)
class SitePin:
    """A site is addressed by CONTAINER NAME (overlay id) plus offset or RAM address."""
    id: str
    container: str
    isa: str
    expected_before: str | None
    after: str
    continuation: int
    evidence_class: str
    address: int | None = None
    offset: int | None = None
    receipt_ref: str | None = None
    evidence_ref: str | None = None
    data: bool = False

    def __post_init__(self):
        _text(self.id, "site id")
        if not isinstance(self.container, str) or _CONTAINER.fullmatch(self.container) is None:
            raise PinError("site container must be a container name (arm9/itcm/dtcm/overlay7|9:<id>), never a bare RAM range")
        if self.isa not in ("arm", "thumb") or self.evidence_class not in EVIDENCE:
            raise PinError("unknown ISA/evidence class")
        if (self.address is None) == (self.offset is None):
            raise PinError("site requires address OR offset")
        for label, value in (("address", self.address), ("offset", self.offset)):
            if value is not None:
                _uint(value, label)
        _uint(self.continuation, "continuation")
        if self.expected_before is not None:
            _hex(self.expected_before, "expected_before")
        _hex(self.after, "after")
        if self.expected_before is not None and len(self.expected_before) != len(self.after):
            raise PinError("site replacement must retain instruction extent")
        width = 4 if self.isa == "arm" else 2
        coordinate = self.address if self.address is not None else self.offset
        if coordinate % width or self.continuation % width or len(bytes.fromhex(self.after)) % width:
            raise PinError("unaligned site/continuation/instruction extent")
        _opt_text(self.receipt_ref, "receipt_ref")
        _opt_text(self.evidence_ref, "evidence_ref")
        _bool(self.data, "site data")
        after = bytes.fromhex(self.after)
        if not self.data and (self.expected_before is None or bytes.fromhex(self.expected_before) != after):
            # A changed/new CODE site: its first instruction must exist under the declared ISA.
            # Only the entry instruction is checked (veneers carry a literal word after it).
            try:
                kind = (decode_arm if self.isa == ARM else decode_thumb)(after, 0, coordinate).kind
            except NdsIsaError as exc:
                raise PinError(f"site {self.id}: after does not decode under {self.isa}: {exc}") from exc
            if kind in ("unknown", "split"):
                raise PinError(f"site {self.id}: after bytes are not a {self.isa} instruction ({kind}); "
                               "set data=True for a pointer/table/literal site")
        if self.evidence_class == "PHYSICAL" and self.receipt_ref is None:
            raise PinError("PHYSICAL evidence requires a receipt_ref")


@dataclass(frozen=True)
class NoTouchSpan:
    """Absolute image span that THIS transaction must neither change nor overlap.

    Not Gen 3's `randomizer_exclusion_spans` (see docs/shared-nds-companion.md).
    """
    offset: int
    length: int
    sha256: str

    def __post_init__(self):
        _uint(self.offset, "no-touch offset")
        _uint(self.length, "no-touch length")
        if not self.length:
            raise PinError("empty no-touch span")
        _hash(self.sha256, 64, "no-touch sha256")


@dataclass(frozen=True)
class Distribution:
    """Record of the distributed patch artifact; no patch-format code lives here."""
    format: str
    artifact_sha256: str
    input_sha1: str
    output_sha1: str
    roundtrip_verified: bool

    def __post_init__(self):
        if self.format not in DISTRIBUTION_FORMATS:
            raise PinError(f"distribution format must be one of {DISTRIBUTION_FORMATS}")
        _hash(self.artifact_sha256, 64, "artifact sha256")
        _hash(self.input_sha1, 40, "distribution input sha1")
        _hash(self.output_sha1, 40, "distribution output sha1")
        _bool(self.roundtrip_verified, "roundtrip_verified")


@dataclass(frozen=True)
class NativeArena:
    """Informational: where the companion's arena/mailbox lives. Never verified."""
    image: str
    address: int
    size: int

    def __post_init__(self):
        _text(self.image, "arena image")
        _uint(self.address, "arena address")
        _uint(self.size, "arena size")
        if not self.size or self.address + self.size > 0x100000000:
            raise PinError("invalid arena extent")


@dataclass(frozen=True)
class CompanionPin:
    schema_version: int
    title: str
    source_kind: str
    parent: ParentPin | None
    source_build: SourceBuild | None
    output_arm9_compressed: bool
    arm9_ram_base: int
    containers: tuple[ContainerPin, ...]
    sites: tuple[SitePin, ...]
    no_touch_spans: tuple[NoTouchSpan, ...]
    dsi_preserved: bool
    output: Hashes
    distribution: Distribution | None
    generator_hashes: tuple[NamedHash, ...]
    source_hashes: tuple[NamedHash, ...]
    native_abi: int
    capabilities: int
    dsi_preserved_reason: str | None = None
    sites_reason: str | None = None
    distribution_base: ParentPin | None = None
    reference_build: ParentPin | None = None
    native_arena: NativeArena | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise PinError("unsupported schema_version")
        _text(self.title, "title")
        if self.source_kind not in ("byte_patched", "source_built"):
            raise PinError("unknown source_kind")
        for name, typ in (("parent", ParentPin), ("source_build", SourceBuild), ("distribution", Distribution),
                          ("distribution_base", ParentPin), ("reference_build", ParentPin),
                          ("native_arena", NativeArena)):
            value = getattr(self, name)
            if value is not None and not isinstance(value, typ):
                raise PinError(f"invalid {name}")
        _bool(self.output_arm9_compressed, "output_arm9_compressed")
        _bool(self.dsi_preserved, "dsi_preserved")
        _opt_text(self.dsi_preserved_reason, "dsi_preserved_reason")
        _opt_text(self.sites_reason, "sites_reason")
        if not isinstance(self.output, Hashes):
            raise PinError("output hashes required")
        if self.source_kind == "byte_patched":
            if (self.parent is None or self.source_build is not None or self.distribution is None
                    or self.distribution_base is not None or self.reference_build is not None):
                raise PinError("byte_patched requires parent and distribution, not source_build/extra parents")
            if self.distribution.input_sha1 != self.parent.hashes.sha1:
                raise PinError("distribution input is not the pinned parent")
            if not self.dsi_preserved and self.parent.unit_code & 2 and self.dsi_preserved_reason is None:
                raise PinError("dropping DSi preservation on a DSi parent needs dsi_preserved_reason")
        else:
            if self.source_build is None:
                raise PinError("source_built requires pinned source/toolchain/base ROM")
            base = self.source_build.base_rom_sha1
            for p in (self.parent, self.distribution_base):
                if p is not None and p.hashes.sha1 != base:
                    raise PinError("source base ROM and parent/distribution_base disagree")
            if self.distribution is not None and self.distribution.input_sha1 != base:
                raise PinError("distribution input is not the source base ROM")
        if self.distribution is not None and self.distribution.output_sha1 != self.output.sha1:
            raise PinError("distribution output disagrees with pinned output")
        _uint(self.arm9_ram_base, "ARM9 RAM base")
        for name, typ in (("containers", ContainerPin), ("sites", SitePin),
                          ("no_touch_spans", NoTouchSpan), ("generator_hashes", NamedHash),
                          ("source_hashes", NamedHash)):
            seq = getattr(self, name)
            if not isinstance(seq, tuple) or any(not isinstance(v, typ) for v in seq):
                raise PinError(f"{name}: tuple of {typ.__name__} required")
        names = [c.name for c in self.containers]
        if len(names) != len(set(names)) or "arm9" not in names:
            raise PinError("unique containers including ARM9 required")
        by_name = {c.name: c for c in self.containers}
        arm9 = by_name["arm9"]
        if arm9.compressed != self.output_arm9_compressed or arm9.ram_base != self.arm9_ram_base:
            raise PinError("ARM9 container disagrees with title configuration")
        for c in self.containers:
            if c.source_provided and (self.source_kind == "byte_patched" and c.name in _NO_FILE_ID):
                raise PinError("ARM9/autoload containers cannot be source-provided in a byte_patched parent")
        if not self.sites:
            if self.source_kind != "source_built" or self.sites_reason is None:
                raise PinError("a table with zero sites needs source_built and an explicit sites_reason")
        elif self.sites_reason is not None:
            raise PinError("sites_reason is only for tables without sites")
        if len({s.id for s in self.sites}) != len(self.sites):
            raise PinError("duplicate site id")
        intervals = {}
        for site in self.sites:
            if site.container not in by_name:
                raise PinError("site container lacks pin")
            c = by_name[site.container]
            off = site.offset if site.offset is not None else site.address - c.ram_base
            end = off + len(bytes.fromhex(site.after))
            if off < 0 or end > c.ram_size:
                raise PinError("site outside pinned decoded extent")
            if site.expected_before is None and not c.source_provided:
                raise PinError("only sites in source-provided containers may omit preimage")
            if site.expected_before is not None and c.source_provided and self.source_kind == "byte_patched":
                raise PinError("byte_patched sites inside a source-provided (parent-absent) container have no preimage")
            if (self.source_kind == "source_built" and site.container in _NO_FILE_ID
                    and site.expected_before is not None and self.source_build.base_arm9_compressed is None):
                raise PinError("source-built ARM9/autoload preimages require explicit base compression")
            start, stop = c.ram_base + off, c.ram_base + end
            if start <= site.continuation < stop or not any(
                    k.ram_base <= site.continuation < k.ram_base + k.ram_size for k in self.containers):
                raise PinError(f"site {site.id}: continuation must be a RAM address inside a pinned container "
                               "and outside the site's own bytes")
            intervals.setdefault(site.container, []).append((off, end))
        for spans in intervals.values():
            _nonoverlap(spans, "site")
        _nonoverlap([(p.offset, p.offset + p.length) for p in self.no_touch_spans], "no-touch")
        for name in ("generator_hashes", "source_hashes"):
            _named_unique(getattr(self, name), name, nonempty=True)
        _uint(self.native_abi, "native ABI")
        _uint(self.capabilities, "capability bits")

    def to_json(self):
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text):
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise PinError(f"duplicate JSON key: {key}")
                out[key] = value
            return out
        try:
            data = json.loads(text, object_pairs_hook=pairs)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PinError(f"invalid JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data):
        return _decode(cls, data)


def _nonoverlap(spans, label):
    end = -1
    for start, stop in sorted(spans):
        if start < end:
            raise PinError(f"overlapping {label} spans")
        end = stop


@functools.cache
def _hints(typ):
    return get_type_hints(typ)


def _decode(typ, value):
    origin, args = get_origin(typ), get_args(typ)
    if origin is UnionType:
        if value is None and type(None) in args:
            return None
        return _decode(next(t for t in args if t is not type(None)), value)
    if origin is tuple:
        if not isinstance(value, list):
            raise PinError("JSON array required")
        return tuple(_decode(args[0], v) for v in value)
    if hasattr(typ, "__dataclass_fields__"):
        if not isinstance(value, dict):
            raise PinError(f"{typ.__name__}: JSON object required")
        known = {f.name for f in fields(typ)}
        if set(value) - known:
            raise PinError(f"{typ.__name__}: unknown keys {sorted(set(value) - known)}")
        required = {f.name for f in fields(typ) if f.default is MISSING and f.default_factory is MISSING}
        if required - set(value):
            raise PinError(f"{typ.__name__}: missing keys {sorted(required - set(value))}")
        hints = _hints(typ)
        return typ(**{k: _decode(hints[k], v) for k, v in value.items()})
    if type(value) is not typ:
        raise PinError(f"expected {typ.__name__}")
    return value


def _guarded(fn):
    """Every validator failure is a PinError, including image-layer ones."""
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ImageError as exc:
            raise PinError(str(exc)) from exc
    return inner


def _rows(manifest):
    """Normalise manifest rows through ChangedSpan; reject anything else."""
    rows = []
    for row in manifest:
        if isinstance(row, dict):
            try:
                row = ChangedSpan(**row)
            except TypeError as exc:
                raise PinError(f"manifest row keys differ from schema: {exc}") from exc
        if not isinstance(row, ChangedSpan):
            raise PinError("manifest rows must be ChangedSpan or its dict form")
        if type(row.offset) is not int or type(row.length) is not int or row.offset < 0 or row.length < 1:
            raise PinError("manifest row offset/length must be integers (length >= 1)")
        rows.append(row)
    return tuple(rows)


def _autoload(image, name, arm9_compressed):
    """Autoload block bytes via the image's `autoload_block(kind, *, arm9_compressed)`.

    The accessor must return an `nds_image.AutoloadBlock`: it carries the RAM base that
    ContainerPin/SitePin assert, so plain bytes (no base to bind) are refused.
    """
    accessor = getattr(image, "autoload_block", None)
    if accessor is None:
        raise PinError("autoload blocks unsupported by this image object")
    block = accessor(name, arm9_compressed=arm9_compressed)
    if block is None:
        raise PinError(f"image has no {name} autoload block")
    if not isinstance(block, AutoloadBlock):
        raise PinError("autoload accessor must return an AutoloadBlock carrying ram_address")
    return block


def _decoded(cache, image, name, arm9_compressed):
    """Memoised decoded container: one decode per (image, container, compression)."""
    key = (id(image), name, arm9_compressed if name == "arm9" or name in AUTOLOAD else None)
    if key not in cache:
        if name in AUTOLOAD:
            cache[key] = _autoload(image, name, arm9_compressed)
        else:
            if name != "arm9" and name not in image.overlays:
                raise PinError(f"image has no container {name}")
            cache[key] = image.decoded(name, arm9_compressed=arm9_compressed)
    return cache[key]


@_guarded
def verify_parent(image: NdsImage, table: CompanionPin):
    if table.parent is None:
        raise PinError("source-built artifact has no byte parent to verify")
    if ParentPin.of(image) != table.parent:
        raise PinError("parent identity mismatch")
    if image.arm9_ram_base != table.arm9_ram_base:
        raise PinError("parent ARM9 base mismatch")
    return True


def _site_bytes(cache, image, table, site, length, *, before=False):
    compressed = table.output_arm9_compressed
    if before and table.source_build is not None and table.source_build.base_arm9_compressed is not None:
        compressed = table.source_build.base_arm9_compressed
    pin = next(c for c in table.containers if c.name == site.container)
    data = _decoded(cache, image, site.container, compressed)
    if site.offset is not None:
        offset = site.offset
    else:
        # Address-form sites resolve against the PINNED base; the image must agree,
        # so a parent and an output cannot resolve one address to different offsets.
        if site.container == "arm9":
            actual = image.arm9_ram_base
        elif site.container in AUTOLOAD:
            actual = data.ram_address  # _autoload guarantees an AutoloadBlock
        else:
            actual = image.overlays[site.container].ram_base
        if actual != pin.ram_base:
            raise PinError(f"address-form site {site.id}: image RAM base differs from the pinned base")
        offset = site.address - pin.ram_base
    if offset < 0 or offset + length > len(data):
        raise PinError("site outside actual decoded container")
    return data[offset:offset + length]


@_guarded
def verify_sites_before(image: NdsImage, table: CompanionPin, *, _cache=None):
    """Return (verified_ids, omitted_ids).

    Sites in source-provided containers have no preimage and are listed as
    omitted, never counted as a before-byte pass. `verified == ()` therefore
    means "nothing was checked", distinguishable from a clean pass.
    """
    cache = {} if _cache is None else _cache
    verified, omitted = [], []
    for site in table.sites:
        if site.expected_before is None:
            omitted.append(site.id)
            continue
        want = bytes.fromhex(site.expected_before)
        if _site_bytes(cache, image, table, site, len(want), before=True) != want:
            raise PinError(f"site expected-before mismatch: {site.id}")
        verified.append(site.id)
    return tuple(verified), tuple(omitted)


def _verify_identity(image, pin, label):
    if pin is None:
        raise PinError(f"table declares no {label}")
    if ParentPin.of(image) != pin:
        raise PinError(f"{label} identity mismatch")


@_guarded
def verify_output(image: NdsImage, table: CompanionPin, manifest, *, parent: NdsImage | None = None,
                  reference: NdsImage | None = None):
    """Verify output; byte_patched MUST supply its parent, source_built need not.

    `parent` is the byte parent (declared same-size diff, preimages, no-touch
    comparison). `reference` is a source_built `reference_build` used only for
    no-touch comparison when no parent is given; a table that declares
    `reference_build` REQUIRES the reference bytes (a declared-but-unsupplied
    reference is a PinError, never a silent skip). `distribution_base` is byte-checked
    only when passed as `parent=`; otherwise only its sha1 is bound, in
    `verify_distribution`. Cost: three hashing passes per identity (Hashes.of), one
    chunked unchanged-byte pass for a parent diff, plus one decode per pinned
    container (memoised); no whole-image copy for mmapped inputs.
    """
    rows = _rows(manifest)
    cache = {}
    if Hashes.of(image) != table.output:
        raise PinError("output hash mismatch")
    if image.arm9_ram_base != table.arm9_ram_base:
        raise PinError("output ARM9 base mismatch")
    if table.source_kind == "byte_patched" and parent is None:
        raise PinError("byte_patched verification requires actual parent bytes")
    if rows and parent is None:
        raise PinError("declared diff cannot be verified without parent bytes")
    if parent is None and any(s.expected_before is not None for s in table.sites):
        raise PinError("site preimages require actual parent bytes")
    if parent is not None:
        if table.parent is not None:
            verify_parent(parent, table)
        else:
            _verify_identity(parent, table.distribution_base, "distribution_base")
        if parent.arm9_ram_base != table.arm9_ram_base:
            raise PinError("parent ARM9 base mismatch")
        verify_sites_before(parent, table, _cache=cache)
        verify_only_declared_changes(parent, image, rows)
    if table.reference_build is not None and reference is None:
        raise PinError("declared reference_build needs actual reference bytes")
    if reference is not None:
        _verify_identity(reference, table.reference_build, "reference_build")
    for c in table.containers:
        if table.source_kind == "byte_patched" and c.name not in AUTOLOAD:
            in_parent = c.name in parent.containers
            if c.source_provided and in_parent:
                raise PinError(f"source-provided container exists in the parent: {c.name}")
            if not c.source_provided and not in_parent:
                raise PinError(f"container absent from parent must be source_provided: {c.name}")
        if c.name in AUTOLOAD:
            raw = decoded = _decoded(cache, image, c.name, table.output_arm9_compressed)
            if decoded.ram_address != c.ram_base:
                raise PinError(f"autoload RAM base mismatch: {c.name}")
        else:
            if c.name != "arm9" and c.name not in image.overlays:
                raise PinError(f"output has no container {c.name}")
            raw = image.read(c.name)
            decoded = _decoded(cache, image, c.name, table.output_arm9_compressed)
        if digest(raw) != c.raw_sha256 or digest(decoded) != c.decompressed_sha256:
            raise PinError(f"container hash mismatch: {c.name}")
        if len(decoded) != c.ram_size:
            raise PinError("container RAM size mismatch")
        if c.name not in _NO_FILE_ID:
            ov = image.overlays[c.name]
            if (ov.file_id, ov.ram_base, ov.ram_size, ov.compressed) != (c.file_id, c.ram_base, c.ram_size, c.compressed):
                raise PinError("overlay geometry/compression mismatch")
    for site in table.sites:
        after = bytes.fromhex(site.after)
        if _site_bytes(cache, image, table, site, len(after)) != after:
            raise PinError(f"site after mismatch: {site.id}")
    base_ref = parent if parent is not None else reference
    if table.no_touch_spans and base_ref is None:
        raise PinError("no-touch spans need a comparison base (parent or reference bytes); "
                       "the output cannot vouch for itself")
    for p in table.no_touch_spans:
        if p.offset + p.length > len(image.data) or digest(image.data, start=p.offset, length=p.length) != p.sha256:
            raise PinError("no-touch span output differs")
        if base_ref is not None and (p.offset + p.length > len(base_ref.data)
                                     or digest(base_ref.data, start=p.offset, length=p.length) != p.sha256):
            raise PinError("no-touch span reference differs")
        for span in rows:
            if span.offset < p.offset + p.length and span.offset + span.length > p.offset:
                raise PinError("declared edit overlaps no-touch span")
    if table.dsi_preserved and image.unit_code & 2 and parent is None:
        raise PinError("DSi preservation needs actual parent bytes")
    if table.dsi_preserved and parent is not None:
        if parent.unit_code != image.unit_code or parent.data[0x180:0x400] != image.data[0x180:0x400]:
            raise PinError("DSi/header preservation failed")
        for name, ext in parent.containers.items():
            if name.startswith("dsi_") and (image.extent(name) != ext or digest(parent.read(name)) != digest(image.read(name))):
                raise PinError(f"DSi payload not preserved: {name}")
    return True


@_guarded
def verify_distribution(base, artifact_bytes, apply_callable, table: CompanionPin):
    """Apply the distributed artifact through an injected callable; no format code here.

    `apply_callable(base_data, artifact_bytes) -> output_bytes`. Checks the base
    sha1, artifact sha256 and the produced output sha1 against the Distribution
    record and the pinned output. `roundtrip_verified` stays a recorded claim.
    """
    d = table.distribution
    if d is None:
        raise PinError("table declares no distribution artifact")
    data = base.data if isinstance(base, NdsImage) else base
    if digest(data, "sha1") != d.input_sha1:
        raise PinError("distribution base is not the declared input")
    if digest(artifact_bytes) != d.artifact_sha256:
        raise PinError("distribution artifact hash mismatch")
    out = apply_callable(data, artifact_bytes)
    if not isinstance(out, (bytes, bytearray, memoryview)):
        raise PinError("apply callable must return bytes")
    out_sha1 = digest(out, "sha1")
    if out_sha1 != d.output_sha1 or out_sha1 != table.output.sha1:
        raise PinError("distribution output hash mismatch")
    return True
