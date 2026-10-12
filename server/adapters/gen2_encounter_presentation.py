"""Shared encounter presentation policy for Gen 2 packs; codecs supply mon fields.

Probabilities, method labels and map/swarm disambiguation belong here. Title-specific
species/form decoding and native fishing/tree admission facts stay with the adapter.
"""


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _weights(thresholds, maximum, *, inclusive=False):
    _require(thresholds and thresholds == sorted(set(thresholds)) and thresholds[-1] == maximum,
             "fish thresholds invalid" if inclusive else "wild slot probabilities invalid")
    lows = [-1 if inclusive else 0, *thresholds[:-1]]
    return [(high - low) * 100 / 256 if inclusive else high - low
            for low, high in zip(lows, thresholds, strict=True)]


def present_encounters(areas, encounters, mon_fields, *, tree_set_limit=None,
                       fishing_time_groups=None, fishing_water_required=False):
    """Build wild/tree/fishing display rows without erasing native table identities.

    mon_fields(entry) returns the codec's display fields, or None for an unresolved
    native slot. A title may supply its tree bound, resolved fishing time rows and
    a source-derived fishing-water gate. Contest rows stay with its own codec.
    """
    grouped = {}

    def emit(key, label, slots, weights, **metadata):
        area = areas.get(str(key))
        _require(area is not None, "encounter refers to missing map")
        target = grouped.setdefault((area["area_id"], label, key, metadata.get("source_table")), [])
        for slot, (entry, weight) in enumerate(zip(slots, weights, strict=True)):
            fields = mon_fields(entry)
            if fields is None:
                continue
            level = entry.get("level")
            target.append({**fields, "rate": weight, "min_level": entry.get("min_level", level),
                           "max_level": entry.get("max_level", level), "slot": slot,
                           "map_group": area["map_group"], "map_number": area["map_number"],
                           "map_name": area["map_name"], **metadata})

    wild = encounters["wild"]
    for kind in ("grass", "water"):
        weights = _weights([row["threshold"] for row in wild[f"{kind}_probabilities"]], 100)
        for row in wild[kind]:
            label = "Surf" if kind == "water" else {"morning": "Morn", "day": "Day", "night": "Nite"}[row["time"]]
            emit(row["map_group"] * 256 + row["map_number"], label, row["slots"], weights,
                 encounter_rate_byte=row["rate"], time=row["time"], source_table=row["table"])
    tree = encounters["tree"]
    sets = {row["set_id"]: row for row in tree["sets"]}
    if tree_set_limit is not None:
        _require(type(tree_set_limit) is int and 2 <= tree_set_limit <= 255
                 and sorted(sets) == list(range(1, tree_set_limit)), "tree set gate invalid")
    for kind, maps in (("Headbutt", tree["headbutt_maps"]), ("Rock Smash", tree["rock_smash_maps"])):
        for row in maps:
            if tree_set_limit is not None and not 0 < row["set_id"] < tree_set_limit:
                continue
            _require(row["set_id"] in sets, "unknown encounter tree set")
            source = sets[row["set_id"]]
            for rarity in ("common", "rare"):
                slots = source.get(rarity)
                if slots:
                    emit(row["map_group"] * 256 + row["map_number"],
                         kind + (f" {rarity.title()}" if kind == "Headbutt" else ""),
                         slots, [entry["weight"] for entry in slots], set_id=row["set_id"])
    groups = {row["group_id"]: row for row in encounters["fishing"]["groups"]}
    for key, area in areas.items():
        group = groups.get(area["fishing_group"])
        if group is None:
            if fishing_water_required:
                _require(area["fishing_group"] == 0, "unknown map fishing group")
            continue
        if fishing_water_required:
            _require(isinstance(area.get("fishing_water"), bool), "missing fishing_water flag")
            if not area["fishing_water"]:
                continue
        for rod in ("old", "good", "super"):
            # Native Random spans 0..255 and accepts <= threshold, conditional on a bite.
            weights = _weights([entry["threshold"] for entry in group[rod]], 255, inclusive=True)
            metadata = {"fish_group": group["group_id"], "bite_threshold": group["bite_threshold"]}
            if fishing_time_groups is None:
                emit(int(key), f"{rod.title()} Rod", group[rod], weights,
                     **metadata, map_association="UNQUALIFIED")
            else:
                for daytime, label in (("day", "Morn/Day"), ("night", "Nite")):
                    slots = [entry if entry["species"] else fishing_time_groups[entry["level"]][daytime]
                             for entry in group[rod]]
                    emit(int(key), f"{rod.title()} Rod ({label})", slots, weights, **metadata,
                         selection_scope="base_group; runtime swarm selection OPEN", map_association="UNQUALIFIED")
    tables, maps_by_method, tables_by_map = {}, {}, {}
    for area, label, key, source_table in grouped:
        maps_by_method.setdefault((area, label), set()).add(key)
        tables_by_map.setdefault((area, label, key), set()).add(source_table)
    for (area, label, key, source_table), slots in grouped.items():
        method = label
        if source_table is not None and len(tables_by_map[area, method, key]) > 1:
            label += " (Swarm)" if source_table.startswith("Swarm") else " (Normal)"
        if len(maps_by_method[area, method]) > 1:
            label += f" ({areas[str(key)]['map_name']})"
        target = tables.setdefault(area, {})
        _require(label not in target, "encounter presentation variants collide")
        target[label] = slots
    return tables
