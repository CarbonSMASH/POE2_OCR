"""
LAMA - Elite Calibration Harvester

Standalone CLI script that harvests calibration data for high-value rare items
across the full price spectrum — from exalted-tier through multi-mirror items.

Complements the standard calibration_harvester.py by covering the 10ex-5mirror
range that the standard harvester's 300d cap previously excluded.

Usage:
    python elite_harvester.py
    python elite_harvester.py --dry-run
    python elite_harvester.py --categories gloves,rings --max-queries 5
    python elite_harvester.py --league "Fate of the Vaal"
    python elite_harvester.py --resume
"""

import argparse
import json
import logging
import random
import sys
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import (
    CACHE_DIR,
    DEFAULT_LEAGUE,
    CALIBRATION_MAX_PRICE_DIVINE,
    TRADE_API_BASE,
)
from calibration_harvester import (
    CATEGORIES,
    FETCH_BATCH_SIZE,
    BURST_SIZE,
    BURST_PAUSE,
    LONG_PENALTY_THRESHOLD,
    build_harvester_query,
    listing_to_parsed_item,
    extract_price_divine,
    write_calibration_record,
    is_fake_listing,
    make_query_key,
)
from item_parser import ParsedItem
from mod_database import ModDatabase
from mod_parser import ModParser
from trade_client import TradeClient

logger = logging.getLogger(__name__)

# ─── Elite Price Brackets ────────────────────────────
# Wide coverage from exalted through mirror-tier

# Exalted brackets (lower-inflation / next-season coverage)
_EXALTED_BRACKETS = [
    ("ex_10_50",     10,   50, "exalted"),
    ("ex_50_100",    50,  100, "exalted"),
    ("ex_100_200",  100,  200, "exalted"),
    ("ex_200_500",  200,  500, "exalted"),
]

# Divine brackets (full spectrum)
_DIVINE_BRACKETS = [
    ("div_1_10",       1,   10, "divine"),
    ("div_10_50",     10,   50, "divine"),
    ("div_50_100",    50,  100, "divine"),
    ("div_100_200",  100,  200, "divine"),
    ("div_200_500",  200,  500, "divine"),
    ("div_500_1000", 500, 1000, "divine"),
    ("div_1000_1500", 1000, 1500, "divine"),
]

# Mirror brackets (mirror ≈ 9000d internally)
_MIRROR_BRACKETS = [
    ("mirror_1_2", 1, 2, "mirror"),
    ("mirror_2_3", 2, 3, "mirror"),
    ("mirror_3_5", 3, 5, "mirror"),
]

ELITE_BRACKETS = _EXALTED_BRACKETS + _DIVINE_BRACKETS + _MIRROR_BRACKETS

RESULTS_PER_QUERY = 50

# ─── State File ──────────────────────────────────────

ELITE_STATE_FILE = CACHE_DIR / "elite_harvester_state.json"


def get_elite_output_path(league: str) -> Path:
    """Return path for elite harvester output."""
    league_slug = league.lower().replace(" ", "_")
    today = date.today().isoformat()
    return CACHE_DIR / f"elite_shard_{league_slug}_{today}.jsonl"


# ─── State Management ────────────────────────────────

def load_state() -> dict:
    """Load elite harvester state from disk."""
    if ELITE_STATE_FILE.exists():
        try:
            with open(ELITE_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"completed_queries": [], "total_samples": 0,
            "query_plan_seed": "", "dead_combos": []}


def save_state(state: dict):
    """Persist elite harvester state to disk."""
    ELITE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ELITE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def build_query_plan(categories: Dict[str, Tuple[str, str]],
                     seed: str) -> List[Tuple[str, str, str, str]]:
    """Build the full query plan: list of (cat_name, item_class, cat_filter, bracket_label).

    Deterministically shuffled by seed so re-runs process items in the same order.
    """
    plan = []
    for cat_name, (cat_filter, item_class) in categories.items():
        for bracket_label, _, _, _ in ELITE_BRACKETS:
            plan.append((cat_name, item_class, cat_filter, bracket_label))

    rng = random.Random(seed)
    rng.shuffle(plan)
    return plan


# ─── Progress Bar ────────────────────────────────────

def progress_bar(current: int, total: int, samples: int,
                 elapsed: float, width: int = 30):
    """Print in-place progress bar."""
    pct = current / total if total else 0
    filled = int(width * pct)
    bar = "#" * filled + "-" * (width - filled)
    mins = elapsed / 60
    print(f"\r  [{bar}] {current}/{total} queries | "
          f"{samples} samples | {mins:.1f}m elapsed", end="", flush=True)


# ─── Main Harvester Loop ────────────────────────────

def run_elite_harvester(league: str,
                        categories: Dict[str, Tuple[str, str]],
                        dry_run: bool = False,
                        max_queries: int = 0,
                        resume: bool = False):
    """Execute the elite harvester."""

    output_file = get_elite_output_path(league)

    print(f"League: {league}")
    print(f"Categories: {len(categories)}")
    print(f"Brackets: {len(ELITE_BRACKETS)} "
          f"({len(_EXALTED_BRACKETS)} exalted + "
          f"{len(_DIVINE_BRACKETS)} divine + "
          f"{len(_MIRROR_BRACKETS)} mirror)")
    total_queries = len(categories) * len(ELITE_BRACKETS)
    print(f"Total queries: {total_queries}")
    print(f"Output: {output_file}")

    if max_queries > 0:
        print(f"Max queries: {max_queries}")

    # Build deterministic query plan
    today_seed = f"elite:{date.today().isoformat()}"

    # Load state for resumability
    state = load_state()

    if resume and state.get("query_plan_seed"):
        seed = state["query_plan_seed"]
        print(f"  Resuming with saved seed: {seed}")
    else:
        seed = today_seed
        if state.get("query_plan_seed") != seed:
            state = {"completed_queries": [], "total_samples": 0,
                     "query_plan_seed": seed, "dead_combos": []}
            save_state(state)

    plan = build_query_plan(categories, seed)

    completed = set(state["completed_queries"])
    dead_combos = set(state.get("dead_combos", []))
    remaining = [(cn, ic, cf, bl) for cn, ic, cf, bl in plan
                 if make_query_key(cn, bl) not in completed
                 and make_query_key(cn, bl) not in dead_combos]

    if not remaining:
        print(f"All {total_queries} queries already completed. "
              f"({state['total_samples']} samples collected)")
        return

    skipped_dead = len(dead_combos - completed)
    print(f"Remaining queries: {len(remaining)} "
          f"(skipping {len(completed)} completed"
          f"{f', {skipped_dead} dead' if skipped_dead else ''})")

    if dry_run:
        print("\n--- DRY RUN: Elite Query Plan ---")
        for i, (cn, ic, cf, bl) in enumerate(remaining):
            bracket = next(b for b in ELITE_BRACKETS if b[0] == bl)
            price_str = f"{bracket[1]}-{bracket[2]} {bracket[3]}"
            print(f"  {i+1:3d}. {cn:20s} {bl:15s} ({price_str})")
            if max_queries > 0 and i + 1 >= max_queries:
                print(f"  ... (capped at {max_queries})")
                break
        print(f"\nTotal: {min(len(remaining), max_queries or len(remaining))} queries")
        return

    # Initialize ModParser + ModDatabase
    print("\nLoading mod parser and database...")
    mod_parser = ModParser()
    mod_parser.load_stats()
    if not mod_parser.loaded:
        print("ERROR: ModParser failed to load stats. Check network connection.")
        sys.exit(1)

    mod_db = ModDatabase()
    if not mod_db.load(mod_parser):
        print("ERROR: ModDatabase failed to load. Check network connection.")
        sys.exit(1)

    db_stats = mod_db.get_stats()
    print(f"ModDatabase ready: bridge={db_stats['bridge_size']}, "
          f"ladders={db_stats['ladder_count']}")

    # Initialize TradeClient
    trade_client = TradeClient(league=league)

    print(f"\nStarting elite harvest...")
    session = trade_client._session
    queries_done = 0
    samples_this_run = 0
    skipped_no_mods = 0
    skipped_low_price = 0
    skipped_fake = 0
    errors = 0
    burst_count = 0
    t_start = time.time()
    effective_total = min(len(remaining), max_queries or len(remaining))

    for cat_name, item_class, cat_filter, bracket_label in remaining:
        if max_queries > 0 and queries_done >= max_queries:
            print(f"\n\nReached max queries ({max_queries}), stopping.")
            break

        query_key = make_query_key(cat_name, bracket_label)
        bracket = next(b for b in ELITE_BRACKETS if b[0] == bracket_label)
        _, price_min, price_max, price_currency = bracket

        # Update progress bar
        elapsed = time.time() - t_start
        progress_bar(queries_done, effective_total, samples_this_run, elapsed)

        print(f"\n  {cat_name} / {bracket_label} "
              f"({price_min}-{price_max} {price_currency})")

        # Burst pacing
        if burst_count >= BURST_SIZE:
            time.sleep(BURST_PAUSE)
            burst_count = 0

        # Rate limit
        trade_client._rate_limit()

        # Check for long penalty
        if trade_client._is_rate_limited():
            wait = trade_client._rate_limited_until - time.time()
            if wait > LONG_PENALTY_THRESHOLD:
                print(f"  Rate limited for {wait:.0f}s — saving state first...")
                save_state(state)
            print(f"  Rate limited, waiting {wait:.0f}s...")
            time.sleep(wait + 1)

        # Step 1: Search
        search_url, query_body = build_harvester_query(
            cat_filter, price_min, price_max, price_currency, league)

        try:
            trade_client._rate_limit()
            resp = session.post(search_url, json=query_body, timeout=10)
            trade_client._parse_rate_limit_headers(resp)
            burst_count += 1

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                if retry_after > LONG_PENALTY_THRESHOLD:
                    print(f"  429 with penalty {retry_after}s — saving state...")
                    save_state(state)
                print(f"  429 — waiting {retry_after}s...")
                time.sleep(retry_after + 1)
                burst_count = 0
                trade_client._rate_limit()
                resp = session.post(search_url, json=query_body, timeout=10)
                trade_client._parse_rate_limit_headers(resp)
                burst_count += 1

            if resp.status_code != 200:
                print(f"  Search HTTP {resp.status_code}, skipping")
                errors += 1
                queries_done += 1
                state["completed_queries"].append(query_key)
                continue

            search_data = resp.json()
            query_id = search_data.get("id")
            result_ids = search_data.get("result", [])
            total = search_data.get("total", 0)

            if not query_id or not result_ids:
                print(f"  0 results (marking dead)")
                queries_done += 1
                state["completed_queries"].append(query_key)
                if query_key not in state.get("dead_combos", []):
                    state.setdefault("dead_combos", []).append(query_key)
                save_state(state)
                continue

            available_ids = result_ids[:RESULTS_PER_QUERY]
            print(f"  {total} total results, fetching {len(available_ids)}...")

        except Exception as e:
            print(f"  Search error: {e}")
            errors += 1
            queries_done += 1
            state["completed_queries"].append(query_key)
            continue

        # Step 2: Fetch listings (batched at 10 per fetch)
        listings = []
        fetch_failed = False
        for batch_start in range(0, len(available_ids), FETCH_BATCH_SIZE):
            batch_ids = available_ids[batch_start:batch_start + FETCH_BATCH_SIZE]
            try:
                if burst_count >= BURST_SIZE:
                    time.sleep(BURST_PAUSE)
                    burst_count = 0
                trade_client._rate_limit()

                batch_listings = trade_client._do_fetch(query_id, batch_ids)
                burst_count += 1
                if batch_listings:
                    listings.extend(batch_listings)
            except Exception as e:
                print(f"  Fetch error (batch {batch_start}): {e}")
                errors += 1
                fetch_failed = True
                break

        if not listings:
            if fetch_failed:
                queries_done += 1
                state["completed_queries"].append(query_key)
                continue
            print(f"  Fetch returned no listings")
            queries_done += 1
            state["completed_queries"].append(query_key)
            save_state(state)
            continue

        # Step 3: Score each listing and write calibration records
        batch_samples = 0
        last_grade = "-"
        for listing in listings:
            price_div = extract_price_divine(listing, trade_client)
            if price_div is None or price_div <= 0:
                skipped_low_price += 1
                continue

            item = listing_to_parsed_item(listing, item_class)
            if item is None:
                continue

            n_explicit = len(listing.get("item", {}).get("explicitMods", []))

            parsed_mods = mod_parser.parse_mods(item)
            if not parsed_mods:
                skipped_no_mods += 1
                continue

            try:
                score = mod_db.score_item(item, parsed_mods)
            except Exception as e:
                logger.debug(f"Score error: {e}")
                continue

            grade = score.grade.value
            if is_fake_listing(grade, score.normalized_score, price_div,
                               n_explicit):
                skipped_fake += 1
                continue

            try:
                write_calibration_record(score, price_div, item_class,
                                         league, output_file)
                last_grade = score.grade.value
                batch_samples += 1
                samples_this_run += 1
                state["total_samples"] += 1
            except Exception as e:
                logger.debug(f"Write error: {e}")

        print(f"  Scored {batch_samples}/{len(listings)} items -> "
              f"{last_grade} (running total: {samples_this_run})")

        queries_done += 1
        state["completed_queries"].append(query_key)
        save_state(state)

    # Final progress bar
    elapsed = time.time() - t_start
    progress_bar(queries_done, effective_total, samples_this_run, elapsed)

    # Final summary
    print(f"\n\n{'='*50}")
    print(f"Elite harvest complete!")
    print(f"  Queries: {queries_done}")
    print(f"  Samples collected: {samples_this_run}")
    print(f"  Total samples (all runs today): {state['total_samples']}")
    print(f"  Skipped (no mods): {skipped_no_mods}")
    print(f"  Skipped (bad price): {skipped_low_price}")
    print(f"  Skipped (fake/price-fixer): {skipped_fake}")
    print(f"  Errors: {errors}")
    print(f"  Time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Output: {output_file}")
    print(f"{'='*50}")

    save_state(state)


# ─── CLI Entry Point ─────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Harvest high-value calibration data from the POE2 trade API")
    parser.add_argument("--league", default=DEFAULT_LEAGUE,
                        help=f"League name (default: {DEFAULT_LEAGUE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print query plan without making API calls")
    parser.add_argument("--categories",
                        help="Comma-separated category names to harvest "
                             "(default: all). Use --dry-run to see names.")
    parser.add_argument("--max-queries", type=int, default=0,
                        help="Max number of queries to run (0 = unlimited)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume an interrupted run (reuses saved seed, "
                             "skips completed queries even across days)")
    parser.add_argument("--reset", action="store_true",
                        help="Reset state and start fresh")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    if not args.verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.getLogger("mod_parser").setLevel(logging.WARNING)
        logging.getLogger("mod_database").setLevel(logging.WARNING)
        logging.getLogger("trade_client").setLevel(logging.WARNING)

    # Filter categories
    cats = CATEGORIES
    if args.categories:
        requested = [c.strip().lower() for c in args.categories.split(",")]
        cats = {}
        for name in requested:
            if name in CATEGORIES:
                cats[name] = CATEGORIES[name]
            else:
                print(f"Unknown category: {name}")
                print(f"Available: {', '.join(sorted(CATEGORIES.keys()))}")
                sys.exit(1)

    # Reset state if requested
    if args.reset:
        if ELITE_STATE_FILE.exists():
            ELITE_STATE_FILE.unlink()
        print("Elite harvester state reset.")

    run_elite_harvester(
        league=args.league,
        categories=cats,
        dry_run=args.dry_run,
        max_queries=args.max_queries,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
