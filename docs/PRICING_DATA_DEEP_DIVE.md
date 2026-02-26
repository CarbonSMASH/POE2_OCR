# Pricing Data Deep Dive — What We Have, What We Use, What We're Missing

**Date**: 2026-02-25
**Context**: Accuracy dropped to ~30% within-2x on 16k samples (was 75.7% on 3.8k).
All three models (k-NN, Ridge, GBM) perform identically at ~30%, meaning the
**feature set** is the bottleneck — not the model.

---

## 1. The Full Data Pipeline

```
Trade API Listing (richest data)
    │
    ├─ item.extended.mods     ◄── TIER + ROLL RANGE per mod (NOT USED)
    ├─ item.extended.hashes   ◄── stat hash fingerprint (NOT USED)
    ├─ item.properties[]      ◄── quality, block%, etc. (NOT USED)
    ├─ item.corrupted/sanctified/fractured flags
    ├─ item.explicitMods[]    ◄── flat text strings
    ├─ item.extended.dps/pdps/edps/ar/ev/es
    ├─ listing.indexed        ◄── age of listing (UNDERUSED)
    └─ listing.price
            │
            ▼
    listing_to_parsed_item()      ◄── extracts ~60% of available data
            │
            ▼
    mod_parser.parse_mods()       ◄── regex matching → stat_id + value
            │
            ▼
    mod_database.score_item()     ◄── RePoE tiers → grade/score/factors
            │
            ▼
    write_calibration_record()    ◄── saves ~40% of scored data
            │
            ▼
    calibration k-NN / GBM        ◄── uses ~25% of what was available
```

**The core problem**: We start with rich structured data from the trade API and
throw away most of it at each stage of the pipeline.

---

## 2. Trade API Response — Full Schema

Every listing from `/api/trade2/fetch/{ids}` returns three sections:

### 2a. `listing` object (seller/price metadata)

| Field | Type | Currently Used | Pricing Value |
|-------|------|----------------|---------------|
| `price.amount` | float | Yes — normalized to divine | Core |
| `price.currency` | string | Yes — for normalization | Core |
| `price.type` | string | No | `~price` (exact) vs `~b/o` (buyout) — buyouts may be aspirational |
| `indexed` | ISO 8601 | Partial — disappearance tracking | **High** — old listings are stale/mispriced |
| `account.name` | string | No | Could track repeat price-fixers |
| `account.online.status` | string | No | AFK sellers = less likely to sell at listed price |
| `stash.name` | string | No | Often contains pricing intent (e.g. "~price 200 chaos") |
| `whisper` | string | Yes — for trade actions | Not pricing-relevant |
| `whisper_token` | string | Yes — for auth trade | Not pricing-relevant |

### 2b. `item` object (core item data)

| Field | Type | Currently Used | Pricing Value |
|-------|------|----------------|---------------|
| `name` | string | Yes | Identity for uniques |
| `typeLine` / `baseType` | string | Yes — as `base_type` | **High** — base type affects mod pool and implicit |
| `ilvl` | int | Yes — tier availability | **High** — determines max rollable tiers |
| `identified` | bool | Clipboard only | Unidentified = cannot be priced by mods |
| `corrupted` | bool | Clipboard only (uniques) | **High** — corrupted items can't be crafted further |
| `sanctified` | bool | **Not extracted** | **High** — POE2-specific, same impact as corrupted |
| `fractured` | bool | Partial (mod array) | **High** — locked mods = guaranteed value floor |
| `mirrored` | bool | No | Mirrored items have special value |
| `frameType` | int | Implicit via rarity | 0=normal, 1=magic, 2=rare, 3=unique |
| `w`, `h` | int | No | Inventory size (minor) |
| `verified` | bool | No | Stale listing detection |

### 2c. Mod arrays (flat text)

| Array | Currently Used | Notes |
|-------|----------------|-------|
| `explicitMods[]` | Yes — parsed → scored | Primary value driver |
| `implicitMods[]` | Yes — parsed → scored | Lower weight in scoring |
| `fracturedMods[]` | Yes — parsed → scored | Gold text, cannot be changed |
| `enchantMods[]` | Yes — parsed, low weight | Can be changed |
| `craftedMods[]` | Yes — parsed, included in score | Can be rerolled cheaply |
| `runeMods[]` | No — skipped | Can be swapped freely |
| `sanctifiedMods[]` | **Not handled** | POE2-specific permanent mods |
| `desecrationMods[]` | Partial | POE2-specific |

### 2d. `properties[]` array (structured stats)

```json
{"name": "Quality", "values": [["+20%", 1]], "type": 6}
{"name": "Physical Damage", "values": [["50-120", 1]], "type": 9}
{"name": "Attacks per Second", "values": [["1.50", 1]], "type": 13}
{"name": "Critical Strike Chance", "values": [["6.50%", 1]], "type": 12}
{"name": "Block Chance", "values": [["25%", 0]], "type": 15}
```

| Property (type code) | Currently Used | Pricing Value |
|----------------------|----------------|---------------|
| Quality (6) | **No** — clipboard only | Affects DPS/defense calculations |
| Physical Damage (9) | Via `extended.pdps` | Covered |
| Elemental Damage (10) | Via `extended.edps` | Covered |
| Critical Strike Chance (12) | **Not extracted** | **High for weapons** — crit base matters enormously |
| Attacks per Second (13) | Via `extended.dps` | Covered implicitly |
| Block Chance (15) | **Not extracted** | Moderate for shields |
| Armour (16) | Via `extended.ar` | Covered |
| Evasion (17) | Via `extended.ev` | Covered |
| Energy Shield (18) | Via `extended.es` | Covered |

### 2e. `extended` object — THE GOLD MINE

#### Computed stats (already used):
| Field | Used | Notes |
|-------|------|-------|
| `dps` | Yes | Total DPS |
| `pdps` | Yes | Physical DPS |
| `edps` | Yes | Elemental DPS |
| `ar` | Yes | Total armour |
| `ev` | Yes | Total evasion |
| `es` | Yes | Total energy shield |

#### `extended.mods` — Per-mod tier + roll range (NOT USED):

```json
{
  "explicit": [
    {
      "name": "Glyphic",           // affix name
      "tier": "P1",                // P1 = prefix tier 1 (best)
      "level": 80,                 // required ilvl for this tier
      "magnitudes": [
        {
          "hash": "explicit.stat_4015621042",
          "min": 100,              // tier floor
          "max": 150               // tier ceiling
        }
      ]
    }
  ]
}
```

**This is the single most valuable untapped data source.** It gives us:
1. **Exact tier** — `P1`/`S1` through `P7`/`S7` etc. (no need for RePoE lookup)
2. **Roll range** — `min`/`max` for the tier, so we can compute exact roll quality
3. **Prefix vs suffix** — `P` prefix, `S` suffix (we currently don't distinguish)
4. **Affix name** — maps to internal mod identity
5. **Stat hash** — exact stat ID match (no regex guessing)

**Why this matters for accuracy:** Currently our mod scoring:
- Uses regex to match mod text → stat_id (lossy, mismatches possible)
- Looks up tiers from RePoE (offline data, may be stale or missing POE2 mods)
- Computes roll quality from extracted value vs tier range (requires both)

The trade API gives us all of this **pre-computed and authoritative**.

#### `extended.hashes` — Stat fingerprint (NOT USED):

```json
{
  "explicit": [
    ["explicit.stat_4015621042", [0]],
    ["explicit.stat_1782086450", [2]]
  ]
}
```

Maps each stat hash to its position in the mods array. This is a **mod fingerprint**
that uniquely identifies the mod combination. Two items with identical hash sets
have identical mod types (though different rolls/tiers).

#### `extended.text` — Base64-encoded clipboard text (NOT USED):

Full item tooltip text. Could be decoded as a verification/fallback for clipboard
parsing. Useful for the harvester which doesn't have clipboard access.

---

## 3. What We Currently Save to Calibration Records

From `write_calibration_record()`:

| Field | Source | k-NN Feature? | GBM Feature? |
|-------|--------|---------------|-------------|
| `grade` | mod_database | Yes (grade penalty) | Yes |
| `score` | mod_database | Yes (score_weight) | Yes |
| `item_class` | harvester | Yes (class filter) | Yes |
| `min_divine` / `max_divine` | trade API price | Target variable | Target variable |
| `total_dps` | extended.dps | Yes (dps_factor) | Yes |
| `total_defense` | computed | Yes (defense_factor) | Yes |
| `pdps` | extended.pdps | Yes (dps_type) | Yes |
| `edps` | extended.edps | Yes (dps_type) | Yes |
| `armour` | extended.ar | No | No |
| `evasion` | extended.ev | No | No |
| `energy_shield` | extended.es | No | No |
| `item_level` | item.ilvl | No | No |
| `dps_factor` | mod_database | Yes | Yes |
| `defense_factor` | mod_database | Yes | Yes |
| `somv_factor` | mod_database | Yes | Yes |
| `top_tier_count` | mod_database | Yes | Yes |
| `mod_count` | mod_database | Yes | Yes |
| `mod_groups` | mod_database | Yes (Jaccard) | Yes (one-hot) |
| `mod_tiers` | mod_database | Yes (tier mismatch) | Partial |
| `mod_rolls` | mod_database | Yes (roll quality) | No |
| `mod_values` | mod_parser | No | No |
| `base_type` | item.typeLine | Yes (binary penalty) | Yes (one-hot) |
| `listing_id` | listing.id | No (disappearance only) | No |
| `listing_ts` | listing.indexed | No | No |

### NOT saved but available:
| Field | Source | Could help? |
|-------|--------|-------------|
| Quality % | item.properties | Yes — high quality weapons are worth more |
| Socket count | item (clipboard) | Yes — 3+ sockets adds value |
| Prefix/suffix counts | mod_database | Yes — open affixes = craft potential |
| Corrupted flag | item.corrupted | Yes — corrupted items price differently |
| Sanctified flag | item.sanctified | Yes — same as corrupted for POE2 |
| Crit base % | item.properties | Yes — high base crit weapons are premium |
| Block % | item.properties | Yes — shield pricing factor |
| Listing age | listing.indexed | Yes — temporal confidence weighting |
| Price type | listing.price.type | Yes — `~b/o` vs `~price` reliability |
| `extended.mods` tiers | extended.mods | **Yes — authoritative tier data** |
| `extended.mods` roll ranges | extended.mods | **Yes — exact roll quality** |
| Prefix vs suffix | extended.mods | Yes — craft potential signal |

---

## 4. Feature Gap Analysis — Why We're at 30%

### 4a. What the models see (current features)

For a typical rare body armour:

```
score=0.62, grade=B, mod_count=5, top_tier_count=2
dps_factor=1.0, defense_factor=0.85, somv_factor=1.02
mod_groups=[Life, FireRes, ColdRes, Armour%, MoveSpeed]
mod_tiers={Life: T2, FireRes: T3, ColdRes: T4, Armour%: T1, MoveSpeed: T5}
base_type="Full Plate"
```

### 4b. What the models DON'T see (missing features)

For that same item, a human pricer also considers:

1. **Exact roll values within tier** — T2 Life at 98/110 vs 75/110 is a big difference
2. **Quality** — 20% quality on armour = 20% more defense = higher price
3. **Open affixes** — 2 open suffixes = benchcraft potential = premium
4. **Base type economics** — Full Plate vs Glorious Plate have different implicit values
5. **Corruption status** — corrupted = no further crafting = discount
6. **Listing age** — listed 3 weeks ago at 5 div? Probably worth 3 div now
7. **Crit base** (weapons) — 7% base crit wand >> 5% base crit wand
8. **Defense type split** — pure ES vs hybrid AR/ES have different buyer pools
9. **Meta demand** — MoveSpeed boots for mapping builds are in high demand right now
10. **Socket count** — 3S item with good mods >> 0S same item

### 4c. Estimated impact of missing features

| Missing Feature | Estimated Items Affected | Estimated Accuracy Impact |
|----------------|--------------------------|--------------------------|
| `extended.mods` tier/roll data | 100% of rares | **+15-25%** — eliminates tier lookup errors, gives exact roll quality |
| Quality % | ~40% of items (non-zero quality) | **+3-5%** — weapons/armour with quality worth more |
| Open prefix/suffix count | ~60% of items | **+5-8%** — craft potential is a major price driver |
| Listing age weighting | 100% of calibration data | **+3-5%** — removes stale prices from training |
| Crit/block base stats | ~30% (weapons + shields) | **+2-4%** — base crit is a weapon multiplier |
| Corruption/sanctified flag | ~10% of items | **+1-2%** — corrupted items price lower |
| Socket count | ~50% of items | **+1-3%** — sockets add value |
| Defense type split (AR/EV/ES) | ~40% (armour pieces) | **+2-3%** — different buyer pools |

**Conservative total if all implemented: +30-50% accuracy** (bringing us to 60-80%).

The single biggest win is `extended.mods` — it replaces our entire brittle
regex-match → RePoE-lookup → tier-identification pipeline with authoritative
data straight from the game server.

---

## 5. The `extended.mods` Opportunity — Detailed Breakdown

### What we currently do (fragile):

```
Mod text: "+112 to maximum Life"
    → regex match → stat_id: "explicit.stat_3299347043"
    → RePoE lookup → tier ladder: [T1: 100-120, T2: 80-99, T3: 60-79, ...]
    → value 112 → falls in T1 range → tier_label: "T1"
    → roll_quality: (112 - 100) / (120 - 100) = 0.60
```

Failure modes:
- Regex doesn't match (new wording, markup, edge cases) → mod dropped
- RePoE data stale or missing for POE2 mods → no tier info
- Value extraction wrong (multi-stat mods, percentage vs flat) → wrong tier
- POE2 mods not in POE1-derived RePoE fork → unknown

### What the trade API gives us (authoritative):

```json
{
  "name": "Fecund",
  "tier": "P1",
  "level": 82,
  "magnitudes": [{
    "hash": "explicit.stat_3299347043",
    "min": 100,
    "max": 120
  }]
}
```

From this we get:
- **Tier**: `P1` = prefix tier 1 (no lookup needed)
- **Prefix vs suffix**: `P` vs `S` (free)
- **Roll range**: min=100, max=120 (authoritative, no RePoE needed)
- **Stat hash**: exact match to trade API stat ID (no regex needed)
- **Required ilvl**: 82 (confirms tier is rollable at item's ilvl)

### What this enables:

1. **Perfect roll quality**: `(actual_value - min) / (max - min)` with no guesswork
2. **Prefix/suffix counting**: count `P*` vs `S*` tiers → open affix detection
3. **Tier fingerprinting**: `[P1, P2, S1, S3, P4, S2]` as a feature vector
4. **Affix name as feature**: "Fecund" (T1 life prefix) vs "Rotund" (T2 life prefix)
5. **Cross-mod roll correlation**: items with ALL high rolls vs mixed → price premium

### Implementation path:

**In the harvester** (`listing_to_parsed_item` / `write_calibration_record`):
```python
# Extract extended.mods from each listing
ext_mods = listing.get("item", {}).get("extended", {}).get("mods", {})

# For each mod category, extract tier data
tier_data = []
for category in ("explicit", "implicit", "fractured", "crafted"):
    for mod in ext_mods.get(category, []):
        for mag in mod.get("magnitudes", []):
            tier_data.append({
                "hash": mag["hash"],
                "tier": mod["tier"],       # "P1", "S3", etc.
                "min": mag["min"],
                "max": mag["max"],
                "level": mod["level"],
                "category": category,
            })

# Save to calibration record
record["ext_tier_data"] = tier_data
record["prefix_count"] = sum(1 for t in tier_data if t["tier"].startswith("P"))
record["suffix_count"] = sum(1 for t in tier_data if t["tier"].startswith("S"))
```

---

## 6. Listing Age — Temporal Confidence

Currently we treat all listings equally regardless of age. But:

| Listing Age | Price Reliability | Action |
|------------|-------------------|--------|
| < 1 hour | Very high — just listed | Full weight |
| 1-24 hours | High — recent market | Full weight |
| 1-3 days | Medium — may be slightly stale | 0.8x weight |
| 3-7 days | Low — market may have moved | 0.5x weight |
| 7-14 days | Very low — probably delisted or sold | 0.3x weight |
| > 14 days | Unreliable — stale, abandoned listing | 0.1x weight or exclude |

The `listing.indexed` timestamp is already saved as `listing_ts` in calibration
records. We just need to use it.

### Implementation path:

```python
# In calibration engine, weight samples by freshness
age_hours = (now - listing_ts).total_seconds() / 3600
if age_hours < 24:
    freshness_weight = 1.0
elif age_hours < 72:
    freshness_weight = 0.8
elif age_hours < 168:
    freshness_weight = 0.5
else:
    freshness_weight = 0.2

# Apply to k-NN distance weighting
weight = (1.0 / distance) * freshness_weight
```

---

## 7. Open Affix Detection — Craft Potential

A rare item can have up to 3 prefixes and 3 suffixes. Items with open slots
are worth more because buyers can benchcraft or slam additional mods.

**Example**: A ring with T1 Life, T1 Crit Multi, T2 Cold Res (2P/1S) has
1 open prefix and 2 open suffixes — a buyer can craft resistances on it.
That's worth 30-100% more than an item with 3P/3S (no room to improve).

The trade API's `extended.mods` tier data gives us prefix (`P*`) vs suffix (`S*`)
counts for free.

### Implementation path:

```python
prefix_count = sum(1 for t in ext_tier_data if t["tier"].startswith("P"))
suffix_count = sum(1 for t in ext_tier_data if t["tier"].startswith("S"))
open_prefixes = max(0, 3 - prefix_count)
open_suffixes = max(0, 3 - suffix_count)

# Feature: craft_potential = open_prefixes + open_suffixes (0-6 scale)
# Items with 2+ open affixes get a premium multiplier
```

---

## 8. Quality as a Pricing Feature

Quality directly multiplies base defense (armour/evasion/ES) and weapon damage.
A 20% quality item has ~20% better combat stats than 0% quality.

| Item Type | Quality Effect | Price Impact |
|-----------|---------------|-------------|
| Weapons | +% physical damage | High — directly multiplies pDPS |
| Armour | +% base defenses | Moderate — more AR/EV/ES |
| Shields | +% block chance | Low-moderate |
| Flasks | +% duration | Low |

We parse quality from clipboard but **never save it to calibration records**.
The trade API's `properties` array also contains it (type code 6).

---

## 9. Crit Base and Block — Weapon/Shield Economics

### Base critical strike chance (weapons)

This is a **property of the base type**, not a mod. A Vaal Rapier with 6.5% base
crit is worth fundamentally more than a Broadsword with 5% base crit, even with
identical mods. Crit builds need high base crit, and they'll pay a premium.

Available from: `item.properties` (type 12) or `extended` computed stats.

### Block chance (shields)

Similar — high base block shields are preferred by block-capped builds.

Available from: `item.properties` (type 15).

---

## 10. Defense Type Split

Currently `defense_factor` combines AR + EV + ES into one number. But:

- Pure ES items serve CI/Low-Life builds (different buyer pool)
- Pure AR items serve armour stackers
- Hybrid AR/EV items serve different builds than pure
- The RATIO matters: 1500 AR / 0 EV / 0 ES vs 500 AR / 500 EV / 500 ES
  are very different items for different buyers

### Proposed features:

```python
defense_type = "pure_ar" | "pure_ev" | "pure_es" | "ar_ev" | "ar_es" | "ev_es" | "hybrid"
primary_defense_ratio = max(ar, ev, es) / total_defense  # 0.33 to 1.0
```

---

## 11. Priority-Ordered Implementation Roadmap

Based on estimated accuracy impact and implementation effort:

### Tier 1 — Highest Impact (target: +20-30% accuracy)

1. **Extract `extended.mods` in harvester** — authoritative tier data, roll ranges,
   prefix/suffix classification. Replaces the entire fragile regex→RePoE pipeline.
   Estimated: +15-25% accuracy.

2. **Open affix counting** — free once `extended.mods` is extracted.
   Estimated: +5-8% accuracy.

### Tier 2 — Medium Impact (target: +5-10% accuracy)

3. **Listing age weighting** — temporal confidence in k-NN and training data.
   Estimated: +3-5% accuracy.

4. **Quality %** — save to calibration, use as k-NN feature.
   Estimated: +3-5% accuracy.

5. **Crit base / block %** — extract from properties, use for weapons/shields.
   Estimated: +2-4% accuracy.

### Tier 3 — Lower Impact (target: +3-5% accuracy)

6. **Defense type split** — pure vs hybrid as feature.
   Estimated: +2-3% accuracy.

7. **Corruption/sanctified flags** — separate pricing.
   Estimated: +1-2% accuracy.

8. **Socket count** — add as feature.
   Estimated: +1-3% accuracy.

9. **Price type** (`~b/o` vs `~price`) — reliability signal.
   Estimated: +1% accuracy.

---

## 12. Data Collection Plan

To populate these new features, the harvester needs changes:

### Harvester changes needed:

1. **Extract `extended.mods`** from each listing (it's already in the fetch response)
2. **Extract `item.properties`** for quality, crit, block
3. **Extract `item.corrupted`** and `item.sanctified`** flags
4. **Save `listing.indexed`** (already saved as `listing_ts`)
5. **Save `listing.price.type`** (`~b/o` vs `~price`)

### New calibration record fields:

```python
record["ext_tiers"] = [{"hash": h, "tier": t, "min": mn, "max": mx} for ...]
record["prefix_count"] = int
record["suffix_count"] = int
record["open_prefixes"] = int
record["open_suffixes"] = int
record["quality"] = int
record["crit_chance"] = float  # base crit % (weapons)
record["block_chance"] = float  # block % (shields)
record["corrupted"] = bool
record["sanctified"] = bool
record["listing_age_hours"] = float
record["price_type"] = str  # "~price" or "~b/o"
```

### Shard regeneration:

After harvester changes, re-run 10 passes to collect ~20k samples with enriched
data. Then regenerate shard with new features in GBM/k-NN/Ridge.

---

## 13. Validation Plan

After implementing Tier 1 features:

1. Run harvester with enriched extraction (10 passes, ~20k samples)
2. Generate shard with new features
3. Run holdout validation
4. Compare: old features (30%) vs new features (target: 50%+)
5. Per-class breakdown to identify remaining weak spots
6. Iterate on Tier 2 features if needed

**Success criteria**: >=50% within 2x on holdout validation.
**Stretch goal**: >=70% within 2x (original target).
