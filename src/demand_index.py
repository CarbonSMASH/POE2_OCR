"""
LAMA - Demand Index

Builds a per-(item_class, mod_group) demand score from poe.ninja popular items.
Measures what fraction of top builds want a given mod on a given equipment slot.

Used as a feature in k-NN distance and GBM training to capture meta demand:
items with mods that top builds want are worth more.

Cache: ~/.poe2-price-overlay/demand_index.json (1-hour TTL)
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cache settings
_CACHE_TTL = 3600  # 1 hour
_CACHE_DIR = Path("~").expanduser() / ".poe2-price-overlay"
_CACHE_FILE = _CACHE_DIR / "demand_index.json"

# Slot → LAMA item_class mapping
_SLOT_TO_CLASS = {
    "Ring": "Rings",
    "Ring2": "Rings",
    "Amulet": "Amulets",
    "Belt": "Belts",
    "Helm": "Helmets",
    "BodyArmour": "Body Armours",
    "Gloves": "Gloves",
    "Boots": "Boots",
    "Shield": "Shields",
    "Offhand": "Shields",  # also Quivers, Foci — approximation
    "Weapon": "Weapons",   # grouped for simplicity
    "Weapon2": "Weapons",
}

# Mod text patterns → mod_group name (simplified extraction from item mods)
_MOD_PATTERNS = {
    r"increased maximum life": "IncreasedLife",
    r"maximum life": "IncreasedLife",
    r"to maximum energy shield": "EnergyShield",
    r"to armour": "Armour",
    r"to evasion": "Evasion",
    r"fire resistance": "FireResist",
    r"cold resistance": "ColdResist",
    r"lightning resistance": "LightningResist",
    r"chaos resistance": "ChaosResist",
    r"all elemental resistances": "AllResist",
    r"to all attributes": "AllAttributes",
    r"to strength": "Strength",
    r"to dexterity": "Dexterity",
    r"to intelligence": "Intelligence",
    r"movement speed": "MovementVelocity",
    r"attack speed": "AttackSpeed",
    r"cast speed": "CastSpeed",
    r"critical strike chance": "CriticalStrikeChance",
    r"critical strike multiplier": "CriticalStrikeMultiplier",
    r"spell damage": "SpellDamage",
    r"physical damage": "PhysicalDamage",
    r"fire damage": "FireDamage",
    r"cold damage": "ColdDamage",
    r"lightning damage": "LightningDamage",
    r"increased spirit": "Spirit",
    r"to spirit": "Spirit",
    r"mana regeneration": "ManaRegeneration",
    r"life regeneration": "LifeRegeneration",
    r"to maximum mana": "MaximumMana",
    r"gem level": "SocketedGemLevel",
    r"skill level": "AddedSkillLevels",
    r"increased area damage": "AreaDamage",
    r"projectile speed": "ProjectileSpeed",
    r"life on hit": "LifeOnHit",
    r"life leech": "LifeLeech",
    r"life recoup": "LifeRecoup",
    r"mana reservation": "ManaReservation",
}

# Pre-compiled patterns
_COMPILED_PATTERNS = [(re.compile(pat, re.IGNORECASE), group)
                      for pat, group in _MOD_PATTERNS.items()]


def _extract_mod_groups(mod_lines: list) -> list:
    """Extract mod_group names from item mod text lines."""
    groups = []
    for line in mod_lines:
        if not isinstance(line, str):
            continue
        for pattern, group in _COMPILED_PATTERNS:
            if pattern.search(line):
                if group not in groups:
                    groups.append(group)
    return groups


class DemandIndex:
    """Per-(item_class, mod_group) demand scores from poe.ninja builds."""

    def __init__(self):
        # {item_class: {mod_group: demand_score}}
        self._index: Dict[str, Dict[str, float]] = {}
        self._loaded = False
        self._load_ts = 0.0

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load_cached(self) -> bool:
        """Load demand index from cache file. Returns True if fresh cache found."""
        if not _CACHE_FILE.exists():
            return False
        try:
            age = time.time() - _CACHE_FILE.stat().st_mtime
            if age > _CACHE_TTL:
                return False
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._index = data.get("index", {})
            self._loaded = bool(self._index)
            self._load_ts = time.time()
            logger.info(f"Demand index loaded from cache ({len(self._index)} classes)")
            return self._loaded
        except Exception as e:
            logger.debug(f"Demand cache load failed: {e}")
            return False

    def _save_cache(self):
        """Save demand index to cache file."""
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "generated_at": time.time(),
                    "index": self._index,
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"Demand cache save failed: {e}")

    def build_from_builds_client(self, builds_client) -> bool:
        """Build demand index by querying poe.ninja popular items.

        Queries the top skills for each class, extracts equipped rare items,
        and counts mod group frequencies per equipment slot.

        Returns True if index was built successfully.
        """
        if not builds_client:
            return False

        # Top class/skill combos to query (covers major archetypes)
        queries = [
            ("Warrior", "Shield Charge"),
            ("Warrior", "Slam"),
            ("Ranger", "Lightning Arrow"),
            ("Ranger", "Rain of Arrows"),
            ("Witch", "Spark"),
            ("Witch", "Raise Skeleton"),
            ("Monk", "Tempest Flurry"),
            ("Monk", "Ice Strike"),
            ("Mercenary", "Rapid Shot"),
            ("Mercenary", "Snipe"),
            ("Sorceress", "Fireball"),
            ("Sorceress", "Arc"),
        ]

        # Slots to analyze
        slots = ["Ring", "Amulet", "Belt", "Helm", "BodyArmour",
                 "Gloves", "Boots", "Shield", "Weapon"]

        # Count mod_group appearances per item_class
        # {item_class: {mod_group: count}}
        counts: Dict[str, Dict[str, int]] = {}
        totals: Dict[str, int] = {}

        for char_class, skill in queries:
            for slot in slots:
                item_class = _SLOT_TO_CLASS.get(slot, "")
                if not item_class:
                    continue

                try:
                    items = builds_client.fetch_popular_items(
                        char_class, skill, slot)
                except Exception as e:
                    logger.debug(f"Demand: fetch failed for {char_class}/{skill}/{slot}: {e}")
                    continue

                if not items:
                    continue

                if item_class not in counts:
                    counts[item_class] = {}
                    totals[item_class] = 0

                for item in items:
                    if item.rarity != "rare":
                        continue
                    # Count this item
                    totals[item_class] += item.count

                    # If the item has mod data, extract mod groups
                    # (popular items don't always have mods, but we count
                    # appearances to weight by popularity)
                    if hasattr(item, "mods") and item.mods:
                        groups = _extract_mod_groups(item.mods)
                        for g in groups:
                            counts[item_class][g] = (
                                counts[item_class].get(g, 0) + item.count)

        # Normalize: demand_score = count / total for each (class, mod_group)
        self._index.clear()
        for item_class, mod_counts in counts.items():
            total = totals.get(item_class, 1) or 1
            self._index[item_class] = {}
            for mod_group, count in mod_counts.items():
                self._index[item_class][mod_group] = round(count / total, 4)

        self._loaded = bool(self._index)
        if self._loaded:
            self._save_cache()
            logger.info(f"Demand index built: {len(self._index)} classes")
        return self._loaded

    def get_demand_score(self, item_class: str, mod_groups: list) -> float:
        """Get aggregate demand score for an item's mods.

        Returns average demand score across the item's mod groups (0.0-1.0).
        Returns 0.0 if no demand data available.
        """
        if not self._loaded or not mod_groups:
            return 0.0
        class_demand = self._index.get(item_class, {})
        if not class_demand:
            return 0.0
        scores = [class_demand.get(g, 0.0) for g in mod_groups]
        return sum(scores) / len(scores) if scores else 0.0

    def get_mod_demand(self, item_class: str, mod_group: str) -> float:
        """Get demand score for a specific mod on a specific class."""
        if not self._loaded:
            return 0.0
        return self._index.get(item_class, {}).get(mod_group, 0.0)

    def to_dict(self) -> Dict[str, Dict[str, float]]:
        """Return the raw index for serialization."""
        return dict(self._index)

    @classmethod
    def from_dict(cls, data: dict) -> "DemandIndex":
        """Create a DemandIndex from serialized data."""
        idx = cls()
        idx._index = data
        idx._loaded = bool(data)
        return idx
