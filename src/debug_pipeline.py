"""
LAMA - Pipeline Diagnostic Script

Investigates why full pipeline accuracy is only 30.8% and why modset gets 0 hits.
Loads JSONL data the same way accuracy_lab.py does, builds a CalibrationEngine
with training data, and prints detailed diagnostics.
"""

import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shard_generator import (
    load_raw_records, quality_filter, remove_outliers, dedup_records,
    _enrich_record, _compute_tier_aggregates, _GRADE_NUM, _GRADE_FROM_NUM,
)
from weight_learner import compute_archetype_scores
from accuracy_lab import load_and_prepare, split_data, prepare_gbm_records, compute_metrics

DEFAULT_JSONL_GLOB = os.path.expanduser(
    "~/.poe2-price-overlay/cache/calibration_shard_*.jsonl"
)
SEED = 42


def main():
    print("=" * 70)
    print("  LAMA Pipeline Diagnostic")
    print("=" * 70)

    # ── 1. Load data exactly as accuracy_lab does ──
    print("\n[1] Loading data...")
    records = load_and_prepare([DEFAULT_JSONL_GLOB])
    print(f"Total prepared records: {len(records)}")

    train, test = split_data(records)
    print(f"Train: {len(train)}, Test: {len(test)}")

    # ── 2. Build CalibrationEngine with training data ──
    print("\n[2] Building CalibrationEngine from training data...")
    from calibration import CalibrationEngine

    engine = CalibrationEngine()
    inserted = 0
    skipped_no_price = 0
    for rec in train:
        price = rec.get("min_divine", 0)
        if price <= 0:
            skipped_no_price += 1
            continue
        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        ts = rec.get("ts", 0)
        engine._insert(
            score=float(rec.get("score", 0)),
            divine=float(price),
            item_class=rec.get("item_class", ""),
            grade_num=_GRADE_NUM.get(rec.get("grade", "C"), 1),
            dps_factor=rec.get("dps_factor", 1.0),
            defense_factor=rec.get("defense_factor", 1.0),
            top_tier_count=rec.get("top_tier_count", 0),
            mod_count=rec.get("mod_count", 4),
            ts=ts,
            is_user=True,
            mod_groups=mod_groups,
            base_type=rec.get("base_type", ""),
            mod_tiers=rec.get("mod_tiers", {}),
            somv_factor=rec.get("somv_factor", 1.0),
            mod_rolls=rec.get("mod_rolls", {}),
            pdps=rec.get("pdps", 0.0),
            edps=rec.get("edps", 0.0),
            sale_confidence=rec.get("sale_confidence", 1.0),
            mod_stats=rec.get("mod_stats", {}),
            quality=rec.get("quality", 0),
            sockets=rec.get("sockets", 0),
            corrupted=1 if rec.get("corrupted", False) else 0,
            open_prefixes=rec.get("open_prefixes", 0),
            open_suffixes=rec.get("open_suffixes", 0),
        )
        inserted += 1

    print(f"  Inserted: {inserted}, Skipped (no price): {skipped_no_price}")
    print(f"  Global samples: {len(engine._global)}")
    print(f"  Classes: {len(engine._by_class)}")
    for cls, samples in sorted(engine._by_class.items(), key=lambda x: -len(x[1])):
        print(f"    {cls:25s}: {len(samples):5d} samples")

    # Train GBM models
    try:
        from gbm_trainer import train_gbm_models
        gbm_recs = prepare_gbm_records(train)
        models = train_gbm_models(gbm_recs)
        engine._gbm_models = models
        print(f"\n  GBM models loaded: {len(models)}")
        for cls, model in sorted(models.items()):
            print(f"    {cls:25s}: {model.get('n_train', '?')} train, R2={model.get('r2_cv', '?')}")
    except Exception as e:
        print(f"  GBM training failed: {e}")
        import traceback
        traceback.print_exc()

    engine._auto_populate_mod_weights()

    # ── 3. Modset lookup diagnostics ──
    print("\n" + "=" * 70)
    print("  [3] MODSET LOOKUP DIAGNOSTICS")
    print("=" * 70)

    # Force build
    engine._build_modset_lookup()

    total_keys = len(engine._modset_lookup)
    keys_ge3 = sum(1 for v in engine._modset_lookup.values() if len(v) >= 3)
    keys_ge5 = sum(1 for v in engine._modset_lookup.values() if len(v) >= 5)
    keys_ge10 = sum(1 for v in engine._modset_lookup.values() if len(v) >= 10)

    print(f"\n  Total modset keys: {total_keys}")
    print(f"  Keys with >= 3 samples: {keys_ge3}")
    print(f"  Keys with >= 5 samples: {keys_ge5}")
    print(f"  Keys with >= 10 samples: {keys_ge10}")

    # Size distribution
    sizes = [len(v) for v in engine._modset_lookup.values()]
    if sizes:
        sizes.sort()
        print(f"\n  Entry count distribution:")
        print(f"    min={sizes[0]}, p25={sizes[len(sizes)//4]}, "
              f"median={sizes[len(sizes)//2]}, p75={sizes[3*len(sizes)//4]}, "
              f"max={sizes[-1]}")

    # Sample a few keys
    print(f"\n  Sample modset keys (up to 10 with >= 3 entries):")
    sample_keys = [(k, v) for k, v in engine._modset_lookup.items() if len(v) >= 3]
    rng = random.Random(42)
    if sample_keys:
        rng.shuffle(sample_keys)
        for k, v in sample_keys[:10]:
            item_class, mod_set = k
            lps = sorted(e[0] for e in v)
            spread = lps[-1] - lps[0]
            prices = [math.exp(lp) for lp in lps]
            print(f"    {item_class} | {set(mod_set)} ({len(v)} entries)")
            print(f"      Price range: {min(prices):.1f} - {max(prices):.1f} divine "
                  f"(log spread: {spread:.2f}, ratio: {math.exp(spread):.1f}x)")
    else:
        print("    ** NO keys with >= 3 entries! **")

    # ── What % of test items have a matching modset key? ──
    print(f"\n  Test item modset coverage:")
    test_has_modgroups = 0
    test_key_exists = 0
    test_key_ge3 = 0
    test_modset_would_return = 0
    test_modset_blocked_spread = 0
    test_modset_blocked_samples = 0
    test_modset_blocked_tier_filter = 0
    test_modset_blocked_somv_filter = 0
    test_fuzzy_match = 0

    for rec in test:
        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        if not mod_groups:
            continue
        test_has_modgroups += 1
        item_class = rec.get("item_class", "")
        mg_frozen = frozenset(mod_groups)
        key = (item_class, mg_frozen)

        if key in engine._modset_lookup:
            test_key_exists += 1
            entries = engine._modset_lookup[key]
            if len(entries) >= 3:
                test_key_ge3 += 1
                # Check if it would pass the spread filter
                lps = sorted(e[0] for e in entries)
                spread = lps[-1] - lps[0]
                if spread > 2.2:
                    test_modset_blocked_spread += 1
                else:
                    # Check tier filtering
                    mt = rec.get("mod_tiers", {})
                    tiers = [t for t in mt.values() if t > 0]
                    ts = round(sum(1.0 / t for t in tiers), 3) if tiers else None

                    if ts is not None:
                        nearby = [e for e in entries if abs(e[1] - ts) <= 1.0]
                        if len(nearby) < 3:
                            test_modset_blocked_tier_filter += 1
                        else:
                            # Check somv filtering
                            somv = rec.get("somv_factor", 1.0)
                            somv_nearby = [e for e in nearby
                                           if len(e) > 2 and abs(e[2] - somv) <= 0.3]
                            if len(somv_nearby) < 3:
                                test_modset_blocked_somv_filter += 1
                            else:
                                test_modset_would_return += 1
                    else:
                        test_modset_would_return += 1
            else:
                test_modset_blocked_samples += 1
        else:
            # Check fuzzy match
            if len(mg_frozen) >= 4:
                for mod in mg_frozen:
                    subset = mg_frozen - {mod}
                    sub_key = (item_class, subset)
                    if sub_key in engine._modset_lookup:
                        sub_entries = engine._modset_lookup[sub_key]
                        if len(sub_entries) >= 3:
                            test_fuzzy_match += 1
                            break

    n_test = len(test)
    print(f"    Test items with mod_groups: {test_has_modgroups}/{n_test} "
          f"({test_has_modgroups/n_test*100:.1f}%)")
    print(f"    Exact key exists in lookup: {test_key_exists}/{test_has_modgroups} "
          f"({test_key_exists/max(1,test_has_modgroups)*100:.1f}%)")
    print(f"    Exact key with >= 3 samples: {test_key_ge3}/{test_has_modgroups} "
          f"({test_key_ge3/max(1,test_has_modgroups)*100:.1f}%)")
    print(f"    Would pass all filters: {test_modset_would_return}/{test_has_modgroups} "
          f"({test_modset_would_return/max(1,test_has_modgroups)*100:.1f}%)")
    print(f"    Blocked by < 3 samples: {test_modset_blocked_samples}")
    print(f"    Blocked by spread > 2.2: {test_modset_blocked_spread}")
    print(f"    Blocked by tier filter: {test_modset_blocked_tier_filter}")
    print(f"    Blocked by somv filter: {test_modset_blocked_somv_filter}")
    print(f"    Fuzzy match (n-1) available: {test_fuzzy_match}")

    # ── Investigate mod_groups uniqueness ──
    print(f"\n  Mod set uniqueness:")
    train_modsets = defaultdict(int)
    for rec in train:
        mg = tuple(sorted(g for g in rec.get("mod_groups", []) if g))
        ic = rec.get("item_class", "")
        if mg:
            train_modsets[(ic, mg)] += 1

    total_unique = len(train_modsets)
    count_1 = sum(1 for c in train_modsets.values() if c == 1)
    count_2 = sum(1 for c in train_modsets.values() if c == 2)
    count_3plus = sum(1 for c in train_modsets.values() if c >= 3)
    print(f"    Unique (class, modset) combos in train: {total_unique}")
    print(f"    With exactly 1 sample: {count_1} ({count_1/max(1,total_unique)*100:.1f}%)")
    print(f"    With exactly 2 samples: {count_2} ({count_2/max(1,total_unique)*100:.1f}%)")
    print(f"    With >= 3 samples: {count_3plus} ({count_3plus/max(1,total_unique)*100:.1f}%)")

    # Show how many mods per item
    print(f"\n  Mods per item distribution:")
    mod_counts = defaultdict(int)
    for rec in train:
        mg = [g for g in rec.get("mod_groups", []) if g]
        mod_counts[len(mg)] += 1
    for n_mods in sorted(mod_counts):
        print(f"    {n_mods} mods: {mod_counts[n_mods]} items "
              f"({mod_counts[n_mods]/len(train)*100:.1f}%)")

    # ── 4. Run estimates and categorize by estimator ──
    print("\n" + "=" * 70)
    print("  [4] PER-ESTIMATOR ACCURACY")
    print("=" * 70)

    # Manually run each estimator separately on test data
    modset_preds = []  # (est, actual)
    gbm_preds = []
    knn_preds = []
    knn_global_preds = []
    median_preds = []

    # Track combined pipeline
    pipeline_preds = []
    estimator_used = defaultdict(int)

    # Track disagreements
    disagreements = []  # (modset_est, gbm_est, knn_est, actual)

    for rec in test:
        price = rec.get("min_divine", 0)
        if price <= 0:
            continue

        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        item_class = rec.get("item_class", "")
        grade = rec.get("grade", "C")
        grade_num = _GRADE_NUM.get(grade, 1)

        mt = rec.get("mod_tiers", {})
        tiers = [t for t in mt.values() if t > 0]
        tier_score = round(sum(1.0 / t for t in tiers), 3) if tiers else 0.0
        best_tier = min(tiers) if tiers else 0
        avg_tier = round(sum(tiers) / len(tiers), 2) if tiers else 0.0

        arch = compute_archetype_scores(mod_groups)
        coc_score = arch.get("coc_spell", 0.0)
        es_score = arch.get("ci_es", 0.0)
        mana_score = arch.get("mom_mana", 0.0)

        mg_frozen = frozenset(mod_groups)

        # -- Modset estimate --
        ms_est = None
        if mod_groups:
            ms_est = engine._modset_estimate(
                item_class, mg_frozen, grade_num, tier_score,
                somv_factor=rec.get("somv_factor", 1.0))
        if ms_est is not None:
            modset_preds.append((ms_est, price))

        # -- GBM estimate --
        gbm_est = None
        if mod_groups and engine._gbm_models:
            gbm_est = engine._gbm_estimate(
                item_class, grade_num, rec.get("score", 0),
                rec.get("top_tier_count", 0), rec.get("mod_count", 4),
                rec.get("dps_factor", 1.0), rec.get("defense_factor", 1.0),
                rec.get("somv_factor", 1.0), tier_score, best_tier, avg_tier,
                coc_score, es_score, mana_score,
                mod_groups=mod_groups, base_type=rec.get("base_type", ""),
                mod_tiers=mt, mod_rolls=rec.get("mod_rolls", {}),
                pdps=rec.get("pdps", 0.0), edps=rec.get("edps", 0.0),
                mod_stats=rec.get("mod_stats", {}),
                quality=rec.get("quality", 0), sockets=rec.get("sockets", 0),
                corrupted=1 if rec.get("corrupted", False) else 0,
                open_prefixes=rec.get("open_prefixes", 0),
                open_suffixes=rec.get("open_suffixes", 0))
        if gbm_est is not None:
            gbm_preds.append((gbm_est, price))

        # -- k-NN class estimate --
        knn_est = None
        knn_conf = 0.0
        ms_tuple = tuple(sorted(rec.get("mod_stats", {}).items())) if rec.get("mod_stats") else ()
        class_samples = engine._by_class.get(item_class)
        if class_samples and len(class_samples) >= engine.MIN_CLASS_SAMPLES:
            knn_est, knn_conf = engine._interpolate(
                rec.get("score", 0), class_samples, grade_num,
                rec.get("dps_factor", 1.0), rec.get("defense_factor", 1.0),
                rec.get("top_tier_count", 0), rec.get("mod_count", 4),
                item_class, mod_groups=mg_frozen,
                base_type=rec.get("base_type", ""),
                tier_score=tier_score,
                coc_score=coc_score, es_score=es_score, mana_score=mana_score,
                mod_tiers=mt, somv_factor=rec.get("somv_factor", 1.0),
                mod_rolls=rec.get("mod_rolls", {}),
                pdps=rec.get("pdps", 0.0), edps=rec.get("edps", 0.0),
                mod_stats_tuple=ms_tuple,
                quality=rec.get("quality", 0), sockets=rec.get("sockets", 0),
                corrupted=1 if rec.get("corrupted", False) else 0,
                open_prefixes=rec.get("open_prefixes", 0),
                open_suffixes=rec.get("open_suffixes", 0))
        if knn_est is not None:
            knn_preds.append((knn_est, price))

        # -- k-NN global estimate (fallback) --
        knn_global_est = None
        if knn_est is None and len(engine._global) >= engine.MIN_GLOBAL_SAMPLES:
            knn_global_est, _ = engine._interpolate(
                rec.get("score", 0), engine._global, grade_num,
                rec.get("dps_factor", 1.0), rec.get("defense_factor", 1.0),
                rec.get("top_tier_count", 0), rec.get("mod_count", 4),
                item_class, mod_groups=mg_frozen,
                base_type=rec.get("base_type", ""),
                tier_score=tier_score,
                coc_score=coc_score, es_score=es_score, mana_score=mana_score,
                mod_tiers=mt, somv_factor=rec.get("somv_factor", 1.0),
                mod_rolls=rec.get("mod_rolls", {}),
                pdps=rec.get("pdps", 0.0), edps=rec.get("edps", 0.0),
                mod_stats_tuple=ms_tuple,
                quality=rec.get("quality", 0), sockets=rec.get("sockets", 0),
                corrupted=1 if rec.get("corrupted", False) else 0,
                open_prefixes=rec.get("open_prefixes", 0),
                open_suffixes=rec.get("open_suffixes", 0))
            if knn_global_est is not None:
                knn_global_preds.append((knn_global_est, price))

        # -- Grade median estimate --
        med_est = engine._grade_median_estimate(item_class, grade_num)
        if med_est is not None:
            median_preds.append((med_est, price))

        # -- Run through actual pipeline (blended) --
        full_est = engine.estimate(
            score=rec.get("score", 0),
            item_class=item_class,
            grade=grade,
            dps_factor=rec.get("dps_factor", 1.0),
            defense_factor=rec.get("defense_factor", 1.0),
            top_tier_count=rec.get("top_tier_count", 0),
            mod_count=rec.get("mod_count", 4),
            mod_groups=mod_groups,
            base_type=rec.get("base_type", ""),
            somv_factor=rec.get("somv_factor", 1.0),
            mod_tiers=mt,
            mod_rolls=rec.get("mod_rolls", {}),
            pdps=rec.get("pdps", 0.0),
            edps=rec.get("edps", 0.0),
            mod_stats=rec.get("mod_stats", {}),
            quality=rec.get("quality", 0),
            sockets=rec.get("sockets", 0),
            corrupted=1 if rec.get("corrupted", False) else 0,
            open_prefixes=rec.get("open_prefixes", 0),
            open_suffixes=rec.get("open_suffixes", 0),
        )
        conf = engine.last_confidence
        pipeline_preds.append((full_est, price))

        # Track which estimator the pipeline actually used
        if full_est is None:
            estimator_used["none"] += 1
        elif conf >= 0.75:
            estimator_used["modset/gbm_high"] += 1
        elif conf >= 0.5:
            estimator_used["gbm_mid"] += 1
        elif conf >= 0.3:
            estimator_used["knn_class"] += 1
        elif conf >= 0.1:
            estimator_used["knn_global/median"] += 1
        else:
            estimator_used["grade_median"] += 1

        # Track disagreements
        est_vals = []
        if ms_est is not None:
            est_vals.append(("modset", ms_est))
        if gbm_est is not None:
            est_vals.append(("gbm", gbm_est))
        if knn_est is not None:
            est_vals.append(("knn", knn_est))
        if len(est_vals) >= 2:
            vals = [v for _, v in est_vals]
            ratio = max(vals) / max(min(vals), 0.01)
            if ratio > 3.0:
                disagreements.append({
                    "estimates": est_vals,
                    "actual": price,
                    "ratio": ratio,
                    "item_class": item_class,
                    "grade": grade,
                })

    # Print per-estimator accuracy
    def print_acc(name, preds):
        if not preds:
            print(f"  {name:20s}: 0 predictions (no coverage)")
            return
        valid = [(e, a) for e, a in preds if e is not None and a > 0]
        if not valid:
            print(f"  {name:20s}: 0 valid predictions")
            return
        n = len(valid)
        w2x = sum(1 for e, a in valid if max(e/a, a/e) <= 2.0)
        w3x = sum(1 for e, a in valid if max(e/a, a/e) <= 3.0)
        ratios = sorted(max(e/a, a/e) for e, a in valid)
        med = ratios[len(ratios)//2]
        print(f"  {name:20s}: {n:5d} preds, "
              f"{w2x/n*100:5.1f}% within 2x, "
              f"{w3x/n*100:5.1f}% within 3x, "
              f"median ratio {med:.2f}x")

    print(f"\n  Per-estimator accuracy (standalone):")
    print_acc("Modset", modset_preds)
    print_acc("GBM", gbm_preds)
    print_acc("k-NN (class)", knn_preds)
    print_acc("k-NN (global)", knn_global_preds)
    print_acc("Grade median", median_preds)
    print_acc("Full pipeline", pipeline_preds)

    # ── Cascade (first-match-wins) vs blending ──
    print(f"\n  Cascade (first-match-wins) accuracy:")
    cascade_preds = []
    cascade_source = defaultdict(int)
    for rec in test:
        price = rec.get("min_divine", 0)
        if price <= 0:
            continue
        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        item_class = rec.get("item_class", "")
        grade = rec.get("grade", "C")
        grade_num = _GRADE_NUM.get(grade, 1)
        mg_frozen = frozenset(mod_groups)
        mt = rec.get("mod_tiers", {})
        tiers_list = [t for t in mt.values() if t > 0]
        ts = round(sum(1.0 / t for t in tiers_list), 3) if tiers_list else 0.0
        best_t = min(tiers_list) if tiers_list else 0
        avg_t = round(sum(tiers_list) / len(tiers_list), 2) if tiers_list else 0.0
        arch = compute_archetype_scores(mod_groups)
        ms_tuple = tuple(sorted(rec.get("mod_stats", {}).items())) if rec.get("mod_stats") else ()

        est = None
        source = "none"

        # 1. Modset
        if mod_groups:
            est = engine._modset_estimate(
                item_class, mg_frozen, grade_num, ts,
                somv_factor=rec.get("somv_factor", 1.0))
            if est is not None:
                source = "modset"

        # 2. GBM
        if est is None and mod_groups and engine._gbm_models:
            est = engine._gbm_estimate(
                item_class, grade_num, rec.get("score", 0),
                rec.get("top_tier_count", 0), rec.get("mod_count", 4),
                rec.get("dps_factor", 1.0), rec.get("defense_factor", 1.0),
                rec.get("somv_factor", 1.0), ts, best_t, avg_t,
                arch.get("coc_spell", 0.0), arch.get("ci_es", 0.0),
                arch.get("mom_mana", 0.0),
                mod_groups=mod_groups, base_type=rec.get("base_type", ""),
                mod_tiers=mt, mod_rolls=rec.get("mod_rolls", {}),
                pdps=rec.get("pdps", 0.0), edps=rec.get("edps", 0.0),
                mod_stats=rec.get("mod_stats", {}),
                quality=rec.get("quality", 0), sockets=rec.get("sockets", 0),
                corrupted=1 if rec.get("corrupted", False) else 0,
                open_prefixes=rec.get("open_prefixes", 0),
                open_suffixes=rec.get("open_suffixes", 0))
            if est is not None:
                source = "gbm"

        # 3. k-NN class
        if est is None:
            class_samples = engine._by_class.get(item_class)
            if class_samples and len(class_samples) >= engine.MIN_CLASS_SAMPLES:
                knn_r, _ = engine._interpolate(
                    rec.get("score", 0), class_samples, grade_num,
                    rec.get("dps_factor", 1.0), rec.get("defense_factor", 1.0),
                    rec.get("top_tier_count", 0), rec.get("mod_count", 4),
                    item_class, mod_groups=mg_frozen,
                    base_type=rec.get("base_type", ""),
                    tier_score=ts,
                    coc_score=arch.get("coc_spell", 0.0),
                    es_score=arch.get("ci_es", 0.0),
                    mana_score=arch.get("mom_mana", 0.0),
                    mod_tiers=mt, somv_factor=rec.get("somv_factor", 1.0),
                    mod_rolls=rec.get("mod_rolls", {}),
                    pdps=rec.get("pdps", 0.0), edps=rec.get("edps", 0.0),
                    mod_stats_tuple=ms_tuple,
                    quality=rec.get("quality", 0), sockets=rec.get("sockets", 0),
                    corrupted=1 if rec.get("corrupted", False) else 0,
                    open_prefixes=rec.get("open_prefixes", 0),
                    open_suffixes=rec.get("open_suffixes", 0))
                if knn_r is not None:
                    est = knn_r
                    source = "knn_class"

        # 4. Grade median
        if est is None:
            est = engine._grade_median_estimate(item_class, grade_num)
            if est is not None:
                source = "grade_median"

        cascade_preds.append((est, price))
        cascade_source[source] += 1

    print_acc("Cascade", cascade_preds)
    total_cascade = sum(cascade_source.values())
    print(f"\n  Cascade source breakdown:")
    for src in ["modset", "gbm", "knn_class", "grade_median", "none"]:
        cnt = cascade_source.get(src, 0)
        print(f"    {src:15s}: {cnt:5d} ({cnt/max(1,total_cascade)*100:.1f}%)")

    # ── Pipeline estimator hit rates ──
    print(f"\n  Pipeline (blended) estimator hit rates:")
    total_pipe = sum(estimator_used.values())
    for name, count in sorted(estimator_used.items(), key=lambda x: -x[1]):
        pct = count / max(1, total_pipe) * 100
        print(f"    {name:20s}: {count:5d} ({pct:5.1f}%)")

    # ── 5. Disagreement analysis ──
    print("\n" + "=" * 70)
    print("  [5] ESTIMATOR DISAGREEMENT ANALYSIS")
    print("=" * 70)

    n_multi_est = sum(1 for rec in test
                      if rec.get("min_divine", 0) > 0)  # approx
    print(f"\n  Items with >= 2 estimators disagreeing > 3x: "
          f"{len(disagreements)}")
    if disagreements:
        pct_disagree = len(disagreements) / max(1, len([r for r in test if r.get("min_divine", 0) > 0])) * 100
        print(f"  As % of testable items: {pct_disagree:.1f}%")

        # Show some examples
        print(f"\n  Sample disagreements (up to 15):")
        rng.shuffle(disagreements)
        for d in disagreements[:15]:
            ests = ", ".join(f"{n}={v:.1f}" for n, v in d["estimates"])
            print(f"    {d['item_class']:20s} {d['grade']:5s} actual={d['actual']:.1f}d | "
                  f"{ests} (ratio={d['ratio']:.1f}x)")

    # ── 6. Deep dive: WHY is k-NN accuracy poor? ──
    print("\n" + "=" * 70)
    print("  [6] k-NN DISTANCE ANALYSIS")
    print("=" * 70)

    # Compute k-NN distances for a sample of test items to understand
    # how far away neighbors typically are
    knn_distances = []
    knn_price_ratios = []
    for rec in test[:500]:  # sample for speed
        price = rec.get("min_divine", 0)
        if price <= 0:
            continue
        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        item_class = rec.get("item_class", "")
        grade_num = _GRADE_NUM.get(rec.get("grade", "C"), 1)
        mg_frozen = frozenset(mod_groups)
        mt = rec.get("mod_tiers", {})
        tiers_list = [t for t in mt.values() if t > 0]
        ts = round(sum(1.0 / t for t in tiers_list), 3) if tiers_list else 0.0
        arch = compute_archetype_scores(mod_groups)
        ms_tuple = tuple(sorted(rec.get("mod_stats", {}).items())) if rec.get("mod_stats") else ()

        class_samples = engine._by_class.get(item_class, [])
        if len(class_samples) < 10:
            continue

        # Manually compute distances to find the nearest neighbor
        query_rolls = rec.get("mod_rolls", {})
        query_tiers = mt

        def _dist(s):
            score_d = abs(s[0] - rec.get("score", 0)) * engine.SCORE_WEIGHT
            grade_d = abs(s[2] - grade_num) * engine.GRADE_PENALTY
            dps_d = abs(s[3] - rec.get("dps_factor", 1.0)) * engine.DPS_WEIGHT
            def_d = abs(s[4] - rec.get("defense_factor", 1.0)) * engine.DEFENSE_WEIGHT
            ttc_d = abs(s[5] - rec.get("top_tier_count", 0)) * engine.TOP_TIER_WEIGHT
            mc_d = abs(s[6] - rec.get("mod_count", 4)) * engine.MOD_COUNT_WEIGHT
            s_mods = frozenset(s[9]) if s[9] else frozenset()
            s_stats = s[22] if len(s) > 22 else ()
            if ms_tuple and s_stats:
                mod_d = engine._stat_distance(ms_tuple, s_stats)
            else:
                mod_d = engine._weighted_jaccard_distance(mg_frozen, s_mods)
            bt_d = 0.0
            base_type = rec.get("base_type", "")
            if base_type and s[10] and base_type != s[10]:
                bt_d = engine.BASE_TYPE_WEIGHT
            ts_d = abs(s[11] - ts) * engine.TIER_SCORE_WEIGHT
            return score_d + grade_d + dps_d + def_d + ttc_d + mc_d + mod_d + bt_d + ts_d

        dists = sorted((_dist(s), s[1]) for s in class_samples)
        if dists:
            # Nearest neighbor
            nn_dist, nn_price = dists[0]
            knn_distances.append(nn_dist)
            knn_price_ratios.append(max(price / nn_price, nn_price / price)
                                     if nn_price > 0 else 999)

    if knn_distances:
        knn_distances.sort()
        knn_price_ratios.sort()
        n = len(knn_distances)
        print(f"\n  Nearest-neighbor distance distribution (sample of {n}):")
        print(f"    min={knn_distances[0]:.3f}, p25={knn_distances[n//4]:.3f}, "
              f"median={knn_distances[n//2]:.3f}, p75={knn_distances[3*n//4]:.3f}, "
              f"max={knn_distances[-1]:.3f}")
        print(f"\n  Nearest-neighbor price ratio (vs actual):")
        print(f"    min={knn_price_ratios[0]:.2f}x, p25={knn_price_ratios[n//4]:.2f}x, "
              f"median={knn_price_ratios[n//2]:.2f}x, p75={knn_price_ratios[3*n//4]:.2f}x, "
              f"max={knn_price_ratios[-1]:.2f}x")

        # What fraction of nearest neighbors are within 2x price?
        nn_w2x = sum(1 for r in knn_price_ratios if r <= 2.0)
        print(f"    Nearest neighbor within 2x of actual: {nn_w2x}/{n} ({nn_w2x/n*100:.1f}%)")
        print(f"    (This is the theoretical ceiling for 1-NN)")

    # ── 7. Confidence vs accuracy ──
    print("\n" + "=" * 70)
    print("  [7] CONFIDENCE vs ACCURACY")
    print("=" * 70)

    conf_buckets = defaultdict(list)
    for rec in test:
        price = rec.get("min_divine", 0)
        if price <= 0:
            continue
        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        est = engine.estimate(
            score=rec.get("score", 0),
            item_class=rec.get("item_class", ""),
            grade=rec.get("grade", "C"),
            dps_factor=rec.get("dps_factor", 1.0),
            defense_factor=rec.get("defense_factor", 1.0),
            top_tier_count=rec.get("top_tier_count", 0),
            mod_count=rec.get("mod_count", 4),
            mod_groups=mod_groups,
            base_type=rec.get("base_type", ""),
            somv_factor=rec.get("somv_factor", 1.0),
            mod_tiers=rec.get("mod_tiers", {}),
            mod_rolls=rec.get("mod_rolls", {}),
            pdps=rec.get("pdps", 0.0),
            edps=rec.get("edps", 0.0),
            mod_stats=rec.get("mod_stats", {}),
            quality=rec.get("quality", 0),
            sockets=rec.get("sockets", 0),
            corrupted=1 if rec.get("corrupted", False) else 0,
            open_prefixes=rec.get("open_prefixes", 0),
            open_suffixes=rec.get("open_suffixes", 0),
        )
        conf = engine.last_confidence
        if est is not None:
            ratio = max(est / price, price / est)
            bucket = round(conf * 10) / 10  # bucket by 0.1
            conf_buckets[bucket].append(ratio)

    print(f"\n  Confidence bucket -> accuracy:")
    print(f"  {'Conf':>6s} | {'Count':>6s} | {'%<=2x':>6s} | {'Median':>8s}")
    print(f"  {'-'*6} | {'-'*6} | {'-'*6} | {'-'*8}")
    for bucket in sorted(conf_buckets):
        ratios = conf_buckets[bucket]
        n = len(ratios)
        w2x = sum(1 for r in ratios if r <= 2.0)
        ratios.sort()
        med = ratios[n//2]
        print(f"  {bucket:6.1f} | {n:6d} | {w2x/n*100:5.1f}% | {med:7.2f}x")

    # ── 8. Price range analysis ──
    print("\n" + "=" * 70)
    print("  [8] PRICE RANGE vs ACCURACY")
    print("=" * 70)

    price_buckets = defaultdict(list)
    for rec in test:
        price = rec.get("min_divine", 0)
        if price <= 0:
            continue
        mod_groups = [g for g in rec.get("mod_groups", []) if g]
        est = engine.estimate(
            score=rec.get("score", 0),
            item_class=rec.get("item_class", ""),
            grade=rec.get("grade", "C"),
            dps_factor=rec.get("dps_factor", 1.0),
            defense_factor=rec.get("defense_factor", 1.0),
            top_tier_count=rec.get("top_tier_count", 0),
            mod_count=rec.get("mod_count", 4),
            mod_groups=mod_groups,
            base_type=rec.get("base_type", ""),
            somv_factor=rec.get("somv_factor", 1.0),
            mod_tiers=rec.get("mod_tiers", {}),
            mod_rolls=rec.get("mod_rolls", {}),
            pdps=rec.get("pdps", 0.0),
            edps=rec.get("edps", 0.0),
            mod_stats=rec.get("mod_stats", {}),
            quality=rec.get("quality", 0),
            sockets=rec.get("sockets", 0),
            corrupted=1 if rec.get("corrupted", False) else 0,
            open_prefixes=rec.get("open_prefixes", 0),
            open_suffixes=rec.get("open_suffixes", 0),
        )
        if est is not None:
            ratio = max(est / price, price / est)
            if price < 1:
                bucket = "<1d"
            elif price < 5:
                bucket = "1-5d"
            elif price < 20:
                bucket = "5-20d"
            elif price < 50:
                bucket = "20-50d"
            elif price < 100:
                bucket = "50-100d"
            else:
                bucket = "100d+"
            price_buckets[bucket].append(ratio)

    print(f"\n  Price range -> accuracy:")
    print(f"  {'Range':>10s} | {'Count':>6s} | {'%<=2x':>6s} | {'Median':>8s}")
    print(f"  {'-'*10} | {'-'*6} | {'-'*6} | {'-'*8}")
    for bucket in ["<1d", "1-5d", "5-20d", "20-50d", "50-100d", "100d+"]:
        ratios = price_buckets.get(bucket, [])
        if not ratios:
            continue
        n = len(ratios)
        w2x = sum(1 for r in ratios if r <= 2.0)
        ratios.sort()
        med = ratios[n//2]
        print(f"  {bucket:>10s} | {n:6d} | {w2x/n*100:5.1f}% | {med:7.2f}x")

    print("\n" + "=" * 70)
    print("  DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
