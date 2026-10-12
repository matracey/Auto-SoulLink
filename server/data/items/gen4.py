"""Item names from each admitted Gen 4 cartridge's generated catalog."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)
_DATA = Path(__file__).resolve().parents[3] / "data/games"


def _load_item_names(foundation: str) -> dict[int, str]:
    path = _DATA / foundation / "names.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.error("Gen 4: cannot read item catalog %s (%s)", path, error)
        return {}
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, dict) or not items or not all(
        len(key) <= 5 and key.isascii() and key.isdecimal()
        and str(int(key)) == key and int(key) <= 0xFFFF
        and isinstance(name, str)
        for key, name in items.items()
    ):
        log.error("Gen 4: invalid items mapping in %s; item names unavailable", path)
        return {}
    return {int(key): name for key, name in items.items() if name.strip()}


ITEM_CATALOGS = {
    foundation: _load_item_names(foundation)
    for foundation in ("gen4_hgss", "gen4_hge")
}
ITEM_NAMES = ITEM_CATALOGS["gen4_hgss"]
