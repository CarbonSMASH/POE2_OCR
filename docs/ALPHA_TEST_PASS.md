# LAMA Alpha Test Pass — v0.2.6

**Date:** 2026-02-24
**Tester:** _______________
**Build:** dev branch (latest)

> Run through each section. Mark PASS / FAIL / SKIP. Note any issues in the Comments column.

---

## 1. App Launch & Window

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 1.1 | Double-click `LAMA.bat` | Splash screen appears, fades to dashboard within ~5s | | |
| 1.2 | Check system tray | LAMA icon appears in tray | | |
| 1.3 | Right-click tray icon | Menu shows: Show Dashboard, Start/Stop Overlay, Quit | | |
| 1.4 | Click X (close) on title bar | Window hides to tray (does NOT quit) | | |
| 1.5 | Click "Show Dashboard" in tray | Window reappears | | |
| 1.6 | Click "Quit" in tray | App fully exits, no zombie processes | | |

### Window Sizing

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 1.7 | Find "Size" dropdown in footer bar | Dropdown shows Default, Large, XL, Fill Screen | | |
| 1.8 | Select "Large (1400x900)" | Window resizes and content scales up | | |
| 1.9 | Select "XL (1700x1000)" | Window resizes larger, text/cards scale up further | | |
| 1.10 | Select "Fill Screen" | Window maximizes to fill screen | | |
| 1.11 | Select "Default (1100x750)" | Window returns to original size, content scales back to normal | | |
| 1.12 | Drag window edge to resize | Window resizes freely (grab near edge, ~6px border) | | |
| 1.13 | Close and relaunch app | Window reopens at last used size | | |
| 1.14 | Click maximize button (□ in title bar) | Window fills screen | | |

---

## 2. Overlay Scanner

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 2.1 | Click "Start Overlay" button (or tray > Start Overlay) | Status badge turns green "Scanning", log shows startup | | |
| 2.2 | In POE2, hover over a rare item and press Ctrl+C | Overlay appears near cursor with grade + price | | |
| 2.3 | Wait for overlay duration (default 2s) | Overlay fades away | | |
| 2.4 | Hover a unique item, Ctrl+C | Overlay shows price range for uniques | | |
| 2.5 | Ctrl+Shift+C (Deep Query) on a rare | After 2-5s, overlay shows trade API results (top listings) | | |
| 2.6 | Click "Stop Overlay" | Status badge disappears, log shows shutdown | | |
| 2.7 | Ctrl+C on an item with overlay stopped | Nothing happens (no overlay, no crash) | | |

### Overlay Settings

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 2.8 | Change Font Size slider (try 10 and 20) | Overlay text size changes on next Ctrl+C | | |
| 2.9 | Change Duration slider to 5s | Overlay stays visible ~5 seconds | | |
| 2.10 | Switch theme: POE2 Gothic → Classic | Overlay style changes (serif → sans, grunge gone) | | |
| 2.11 | Switch theme back to POE2 Gothic | Gothic styling returns | | |
| 2.12 | Try each Display Preset (Minimal/Quick/Standard/Expert) | Overlay shows different levels of detail | | |
| 2.13 | Toggle "Show DPS/Defense" off | DPS info hidden on next weapon/armor scan | | |

---

## 3. Character Viewer

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 3.1 | Click "Character" tab (right side) | Character Lookup panel shows | | |
| 3.2 | Enter your account name + character name, click Lookup | Character profile loads: level, class, equipment, skills | | |
| 3.3 | Expand an equipment slot (click it) | Mod list shows with colored tier badges [T1], [T2], etc. | | |
| 3.4 | Check tier badge colors | T1=green, T2=gold, T3=amber, T4+=muted | | |
| 3.5 | Check roll quality bars | Thin bar next to each mod showing 0-100% roll quality | | |
| 3.6 | Click "View Popular Items" on an equipment slot | Drill-down shows: Your Item, Meta Insight, Stat Check | | |
| 3.7 | Click Back arrow to return | Returns to equipment overview | | |

### Build Insights

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 3.8 | After lookup, check Build Insights panel (above equipment) | Shows archetype tags (e.g., "spell · lightning · life") | | |
| 3.9 | Check Equipment Tiers section | Per-slot average tier + T1 count shown | | |
| 3.10 | Check Missing Keystones | Lists popular keystones you don't have, with % usage | | |
| 3.11 | Check "Room to Improve" section | Shows weakest slots with context (e.g., "T3/7 Fire Res") | | |
| 3.12 | Check "Low-Priority Mods" section (if present) | Muted amber text, not aggressive red | | |
| 3.13 | Verify ascendancy is correct | Class shown matches your actual class (not swapped) | | |

### Saved Characters

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 3.14 | After lookup, close and reopen app | Most recent character auto-loads on launch | | |
| 3.15 | Check "Recent Characters" list | Previously looked-up characters shown | | |
| 3.16 | Click a saved character | Auto-lookups that character | | |
| 3.17 | Click X next to a saved character | Character removed from list | | |

---

## 4. Loot Filter

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 4.1 | Click "Loot Filter" tab | Filter settings panel shows | | |
| 4.2 | Change Strictness (Strict/Normal/Lenient) | Setting saves (check no error) | | |
| 4.3 | Toggle section visibility (e.g., hide Catalysts) | Section toggle saves | | |
| 4.4 | Click "Update Filter" button | Filter file written to My Games/Path of Exile 2/ | | |
| 4.5 | Check output file exists | `.filter` file present in output directory | | |

---

## 5. Watchlist

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 5.1 | Click "Watchlist" tab | Watchlist panel with empty slots shows | | |
| 5.2 | Click + to add a query | Query builder opens | | |
| 5.3 | Set up a simple query (e.g., Divine Orb) | Query saves to slot | | |
| 5.4 | Wait for poll interval | Results appear with prices + seller names | | |
| 5.5 | Toggle "Online Only" | Results filter to online sellers only | | |
| 5.6 | Delete a query (trash icon) | Query removed from slot | | |

---

## 6. Markets

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 6.1 | Click "Markets" tab | Market data loads (may take a few seconds) | | |
| 6.2 | Check KPI cards at top | Shows currency values with sparklines | | |
| 6.3 | Check exchange rate table | Currencies listed with divine values | | |
| 6.4 | Click category filter pills (Core, Fragments, etc.) | Table filters to selected category | | |

---

## 7. Bug Reporter

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 7.1 | Click "Bug Report" in footer | Bug report modal opens | | |
| 7.2 | Check fields present | Title, Description, Discord Handle (optional) | | |
| 7.3 | Fill in title + description + Discord handle | Fields accept input | | |
| 7.4 | Click "Send Report" | "Report sent!" confirmation appears | | |
| 7.5 | Check Discord alpha-bugs channel | Report appears with title, description, contact, system info | | |
| 7.6 | Try Ctrl+Shift+B while overlay is running | Bug report dialog opens (tkinter version) | | |
| 7.7 | Submit another report within 30s | Should be blocked by cooldown | | |

---

## 8. Feedback & Feature Request

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 8.1 | Click "Feedback" in footer | Feedback modal opens | | |
| 8.2 | Submit feedback | Confirmation shown | | |
| 8.3 | Click "Feature Request" in footer | Feature request modal opens | | |
| 8.4 | Submit feature request | Confirmation shown | | |

---

## 9. Settings Persistence

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 9.1 | Change league in footer dropdown | League updates, price cache refreshes | | |
| 9.2 | Toggle "Auto-start overlay on launch" off | Setting saves | | |
| 9.3 | Close and reopen app | All settings preserved from previous session | | |
| 9.4 | Check "Launch on Windows startup" toggle | Toggling adds/removes registry entry | | |

---

## 10. Welcome Tour (First Run)

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 10.1 | In Overlay Settings, click "Replay Tour" | Welcome modal reappears | | |
| 10.2 | Click "Start Tour" | Guided tour begins, highlights UI elements | | |
| 10.3 | Click Next through each step | Each step highlights different feature | | |
| 10.4 | Press Escape during tour | Tour dismisses | | |
| 10.5 | Click "Skip" on welcome modal | Tour skipped, doesn't reappear | | |

---

## 11. App Restart

| # | Test | Expected | Result | Comments |
|---|------|----------|--------|----------|
| 11.1 | Click "Restart App" in footer | Confirmation dialog appears | | |
| 11.2 | Confirm restart | App closes and relaunches cleanly | | |
| 11.3 | Check overlay state after restart | Overlay stopped (or auto-started if setting enabled) | | |

---

## Known Issues / Notes

- Discord Alt+C keybind conflict: Discord's clip shortcut can trigger when pressing Alt near LAMA. Fix: disable/rebind Discord's clip keybind in Discord Settings > Clips.
- Stash Viewer shows "Coming Soon" — pending GGG OAuth approval.
- Edge resize border is subtle (6px) — use the Size dropdown in footer for easier resizing.

---

## Test Environment

| Field | Value |
|-------|-------|
| OS | |
| Screen Resolution | |
| POE2 Running | Yes / No |
| LAMA Version | |
| Date | |
| Duration | |

---

**Overall Result:** PASS / FAIL

**Summary:**


**Blockers:**

