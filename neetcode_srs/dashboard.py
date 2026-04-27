"""
Single-file HTML progress dashboard for neetcode-srs.

Generates a self-contained report from the live SQLite DB: GitHub-style
activity heatmap, streak and completion stats, difficulty breakdown, and a
recent-reviews log. Opens it in the default browser.
"""
from __future__ import annotations

import html as _html
import json
import sqlite3
import tempfile
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from neetcode_srs import db


# --- data assembly --------------------------------------------------------

def build_data(conn: sqlite3.Connection, today: date) -> dict:
    window_start = today - timedelta(days=371)
    rows = conn.execute(
        """
        SELECT date(reviewed_at) AS day, outcome, COUNT(*) AS cnt
        FROM reviews
        WHERE date(reviewed_at) >= ?
        GROUP BY day, outcome
        """,
        (window_start.isoformat(),),
    ).fetchall()

    days: dict[str, dict] = {}
    for r in rows:
        d = r["day"]
        days.setdefault(d, {"y": 0, "n": 0, "e": 0, "skip": 0, "graded": 0})
        days[d][r["outcome"]] = r["cnt"]
    for d in days.values():
        d["graded"] = d["y"] + d["n"] + d["e"]

    # Current streak: consecutive days back from today with ≥1 graded review.
    # Today not yet done doesn't break the streak — we look from yesterday.
    streak = 0
    cursor = today
    if days.get(today.isoformat(), {}).get("graded", 0) == 0:
        cursor = today - timedelta(days=1)
    while days.get(cursor.isoformat(), {}).get("graded", 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)

    # Totals from the full reviews table, not just the 52-week window.
    totals = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN outcome='y' THEN 1 ELSE 0 END), 0) AS ny,
            COALESCE(SUM(CASE WHEN outcome='n' THEN 1 ELSE 0 END), 0) AS nn,
            COALESCE(SUM(CASE WHEN outcome='e' THEN 1 ELSE 0 END), 0) AS ne
        FROM reviews
        """
    ).fetchone()
    total_y, total_n, total_e = totals["ny"], totals["nn"], totals["ne"]
    total_graded = total_y + total_n + total_e
    accuracy = round(100 * (total_y + total_e) / total_graded) if total_graded else None

    # Deck stats + difficulty breakdown.
    s = db.stats(conn, today)
    attempted = s["total"] - s["new"]

    recent = db.recent_reviews(conn, limit=25)

    # Most-recent review datetime (for subhead).
    last_row = conn.execute(
        "SELECT reviewed_at FROM reviews ORDER BY id DESC LIMIT 1"
    ).fetchone()
    last_reviewed_at = last_row["reviewed_at"] if last_row else None

    # Stats shown in the heatmap header (past-year window only)
    submissions_past_year = sum(d["graded"] for d in days.values())
    active_days_past_year = sum(1 for d in days.values() if d["graded"] > 0)
    max_streak_past_year = 0
    _run = 0
    _cur = window_start
    while _cur <= today:
        if days.get(_cur.isoformat(), {}).get("graded", 0) > 0:
            _run += 1
            if _run > max_streak_past_year:
                max_streak_past_year = _run
        else:
            _run = 0
        _cur += timedelta(days=1)

    return {
        "today": today,
        "attempted": attempted,
        "total": s["total"],
        "streak": streak,
        "total_reviews": total_graded,
        "accuracy": accuracy,
        "days": days,
        "difficulty": s["by_difficulty"],
        "recent": recent,
        "last_reviewed_at": last_reviewed_at,
        "counts": {"y": total_y, "n": total_n, "e": total_e},
        "submissions_past_year": submissions_past_year,
        "active_days_past_year": active_days_past_year,
        "max_streak_past_year": max_streak_past_year,
    }


# --- heatmap grid ---------------------------------------------------------

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _level_for_count(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count == 2:
        return 2
    if count == 3:
        return 3
    return 4


def _build_heatmap(today: date, days: dict) -> dict:
    # Columns × 7 rows, Sunday at top. Anchor the first column to the
    # Sunday on/before (today - 52 weeks), and extend the grid to the end of
    # next month so "next month" always appears as the rightmost label.
    target_start = today - timedelta(days=52 * 7)
    offset = (target_start.weekday() + 1) % 7  # Mon=0 → 1, Sun=6 → 0
    grid_start = target_start - timedelta(days=offset)

    next_month_num = today.month % 12 + 1
    next_month_year = today.year + (1 if today.month == 12 else 0)
    next_month_first = date(next_month_year, next_month_num, 1)
    # Extend to the first Sunday of next month so the month label appears once,
    # with only one week of future cells after it.
    days_to_sunday = (6 - next_month_first.weekday()) % 7
    first_sunday_next = next_month_first + timedelta(days=days_to_sunday)
    num_cols = (first_sunday_next - grid_start).days // 7 + 1

    cells_html: list[str] = []
    # Track month ranges so labels can be centered under their full column span.
    month_ranges: list[tuple[int, int, str]] = []  # (start_col, end_col, label)
    month_col_start = 0
    last_month_seen = None

    for col in range(num_cols):
        col_start = grid_start + timedelta(days=col * 7)
        if col_start.month != last_month_seen:
            if last_month_seen is not None:
                month_ranges.append((month_col_start, col - 1, _MONTHS[last_month_seen - 1]))
            month_col_start = col
            last_month_seen = col_start.month

        for row in range(7):
            d = grid_start + timedelta(days=col * 7 + row)
            if d > today:
                cells_html.append(
                    f'<div class="cell cell-future" '
                    f'style="grid-column:{col + 1};grid-row:{row + 1};'
                    f'animation-delay:{col * 8}ms"></div>'
                )
                continue
            count = days.get(d.isoformat(), {}).get("graded", 0)
            level = _level_for_count(count)
            label = f'{d.strftime("%a %b %-d, %Y")} — {count} review{"s" if count != 1 else ""}'
            cells_html.append(
                f'<div class="cell" data-level="{level}" '
                f'data-label="{_html.escape(label, quote=True)}" '
                f'style="grid-column:{col + 1};grid-row:{row + 1};'
                f'animation-delay:{col * 8}ms"></div>'
            )

    if last_month_seen is not None:
        month_ranges.append((month_col_start, num_cols - 1, _MONTHS[last_month_seen - 1]))

    # grid-column: start_line / end_line (1-indexed, end is exclusive)
    months_html = "".join(
        f'<span style="grid-column:{s + 1}/{e + 2}">{lbl}</span>'
        for s, e, lbl in month_ranges
    )

    return {
        "cells_html": "".join(cells_html),
        "months_html": months_html,
        "grid_start": grid_start,
        "num_cols": num_cols,
    }


# --- html rendering -------------------------------------------------------

_CSS = r"""
:root {
  --bg: #09090B;
  --bg-lift: rgba(255, 255, 255, 0.03);
  --bg-card: rgba(18, 18, 20, 0.72);

  --border: rgba(255, 255, 255, 0.07);
  --border-light: rgba(255, 255, 255, 0.14);

  /* Zinc palette */
  --ink: #A1A1AA;       /* zinc-400 */
  --ink-strong: #F4F4F5; /* zinc-100 */
  --ink-dim: #52525B;   /* zinc-600 */

  /* Orange accent */
  --accent: #F97316;              /* orange-500 */
  --accent-dark: #C2410C;         /* orange-700 */
  --accent-glow: rgba(249, 115, 22, 0.25);
  --accent-subtle: rgba(249, 115, 22, 0.08);

  --sage: #10B981;
  --amber: #F59E0B;
  --terra: #EF4444;
  --gold: #FBBF24;

  /* Heatmap cells — orange tinted levels */
  --cell-0: #2a2a2a;
  --cell-1: rgba(249, 115, 22, 0.18);
  --cell-2: rgba(249, 115, 22, 0.42);
  --cell-3: rgba(249, 115, 22, 0.70);
  --cell-4: #F97316;
}

* { box-sizing: border-box; }

html { background: var(--bg); }
body {
  margin: 0;
  min-height: 100vh;
  background-color: var(--bg);
  background-image:
    radial-gradient(ellipse at 50% -10%, rgba(249, 115, 22, 0.12), transparent 55%),
    linear-gradient(to right, rgba(255, 255, 255, 0.03) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
  background-size: 100% 100%, 40px 40px, 40px 40px;
  background-position: center 0, center center, center center;
  color: var(--ink);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  position: relative;
  overflow-x: hidden;
}

.grain {
  position: fixed;
  inset: -50%;
  pointer-events: none;
  z-index: 0;
  opacity: 0.04;
  mix-blend-mode: screen;
}

main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 80px 40px 120px;
  position: relative;
  z-index: 1;
}

/* ---------- header ---------- */

header.masthead {
  margin-bottom: 72px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  align-items: center;
  text-align: center;
}

.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: 100px;
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  color: var(--ink);
  font-weight: 500;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
  transition: all 0.3s ease;
}

.eyebrow:hover {
  background: rgba(255,255,255,0.04);
  border-color: var(--border-light);
}

.eyebrow .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 10px var(--accent-glow);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 var(--accent-glow); }
  70% { box-shadow: 0 0 0 8px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}

h1.display {
  font-family: 'Outfit', sans-serif;
  font-weight: 600;
  font-size: clamp(40px, 6vw, 56px);
  line-height: 1.1;
  letter-spacing: -0.03em;
  margin: 0;
  color: var(--ink-strong);
}
h1.display em {
  font-style: normal;
  color: transparent;
  background: linear-gradient(135deg, #ffffff 0%, #71717A 100%);
  -webkit-background-clip: text;
  background-clip: text;
}

.subhead {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  font-size: 14px;
  color: var(--ink);
}
.subhead span {
  display: flex;
  align-items: center;
  gap: 8px;
}
.subhead span:not(:last-child)::after {
  content: "•";
  color: var(--ink-dim);
}
.subhead em {
  font-style: normal;
  color: var(--ink-strong);
  font-weight: 500;
}

/* ---------- bento grid ---------- */

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 24px;
  margin-bottom: 24px;
}

.card {
  background: var(--bg-card);
  backdrop-filter: blur(40px);
  -webkit-backdrop-filter: blur(40px);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 32px;
  position: relative;
  overflow: hidden;
  box-shadow: 
    0 12px 32px -12px rgba(0,0,0,0.8),
    inset 0 1px 1px rgba(255,255,255,0.06);
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.card:hover {
  transform: translateY(-4px);
  border-color: var(--border-light);
  box-shadow: 
    0 24px 48px -12px rgba(0,0,0,1),
    inset 0 1px 1px rgba(255,255,255,0.1);
}

.card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
  opacity: 0;
  transition: opacity 0.4s ease;
}

.card:hover::before {
  opacity: 1;
}

/* ---------- hero stats ---------- */

.stat-card {
  grid-column: span 4;
  display: flex;
  flex-direction: column;
}

@media (max-width: 900px) {
  .stat-card { grid-column: span 12; }
}

.stat-icon {
  width: 44px; height: 44px;
  border-radius: 12px;
  background: var(--accent-subtle);
  border: 1px solid rgba(249, 115, 22, 0.2);
  box-shadow: inset 0 1px 1px rgba(255,255,255,0.05);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 32px;
  color: var(--accent);
  transition: all 0.4s ease;
}

.stat-card:hover .stat-icon {
  transform: scale(1.05);
  background: rgba(255,255,255,0.05);
  border-color: var(--border-light);
}

.stat-icon.emerald { color: var(--sage); }
.stat-icon.amber { color: var(--amber); }

.stat-icon svg {
  width: 20px; height: 20px;
  filter: drop-shadow(0 2px 4px rgba(0,0,0,0.2));
}

.stat-num {
  font-family: 'Outfit', sans-serif;
  font-weight: 500;
  font-size: clamp(32px, 4vw, 48px);
  line-height: 1;
  letter-spacing: -0.02em;
  color: var(--ink-strong);
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.stat-num .unit {
  font-family: 'Inter', sans-serif;
  font-size: 15px;
  font-weight: 500;
  color: var(--ink);
}

.stat-num .denom {
  font-family: 'Inter', sans-serif;
  font-size: 0.5em;
  color: var(--ink-dim);
  font-weight: 400;
}

.stat-label {
  font-family: 'Inter', sans-serif;
  font-size: 15px;
  font-weight: 500;
  color: var(--ink-strong);
  margin-bottom: 4px;
}

.stat-sub {
  font-size: 13px;
  color: var(--ink);
}

/* ---------- heatmap ---------- */

.heatmap-card {
  grid-column: span 12;
  padding: 40px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 32px;
}

.section-head-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.section-head h2 {
  font-family: 'Outfit', sans-serif;
  font-weight: 500;
  font-size: 20px;
  letter-spacing: -0.01em;
  margin: 0;
  color: var(--ink-strong);
  display: flex;
  align-items: center;
  gap: 12px;
}

.section-head .meta {
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  color: var(--ink);
}

.heatmap-frame {
  overflow-x: auto;
  padding-bottom: 8px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,0.12) transparent;
}
.heatmap-frame::-webkit-scrollbar {
  height: 3px;
}
.heatmap-frame::-webkit-scrollbar-track {
  background: transparent;
}
.heatmap-frame::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,0.12);
  border-radius: 3px;
}

.heatmap-inner {
  display: inline-flex;
  gap: 6px;
}
.heatmap-day-labels {
  display: grid;
  grid-template-rows: repeat(7, 16px);
  row-gap: 3px;
  font-family: 'Inter', sans-serif;
  font-size: 10px;
  font-weight: 500;
  color: var(--ink-dim);
  align-items: center;
  padding-bottom: 22px; /* matches months row height below */
}
.heatmap-day-labels span {
  line-height: 16px;
  white-space: nowrap;
}
.heatmap-cols {
  display: inline-flex;
  flex-direction: column;
  gap: 6px;
}
.heatmap-months {
  display: grid;
  grid-template-columns: repeat(53, 16px);
  column-gap: 3px;
  font-family: 'Inter', sans-serif;
  font-size: 11px;
  font-weight: 400;
  color: var(--ink-dim);
}
.heatmap-months span {
  white-space: nowrap;
  text-align: center;
}
.heatmap-grid {
  display: grid;
  grid-template-columns: repeat(53, 16px);
  grid-template-rows: repeat(7, 16px);
  column-gap: 3px;
  row-gap: 3px;
}

.cell {
  background: var(--cell-0);
  border-radius: 3px;
  position: relative;
  cursor: pointer;
  transition: transform 0.15s cubic-bezier(0.16, 1, 0.3, 1),
              box-shadow 0.15s ease;
  animation: cellIn 800ms cubic-bezier(0.16, 1, 0.3, 1) both;
}
.cell-future {
  background: var(--cell-0);
  cursor: default;
}
.cell[data-level="1"] { background: var(--cell-1); }
.cell[data-level="2"] { background: var(--cell-2); box-shadow: inset 0 1px 0 rgba(255,255,255,0.1); }
.cell[data-level="3"] { background: var(--cell-3); box-shadow: inset 0 1px 0 rgba(255,255,255,0.2); }
.cell[data-level="4"] {
  background: var(--cell-4);
  box-shadow: 0 0 10px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.35);
}

.cell:not(.cell-future):hover {
  transform: scale(1.6);
  z-index: 20;
  outline: 1.5px solid rgba(249, 115, 22, 0.8);
  outline-offset: 0;
}

/* ---------- JS-driven floating tooltip ---------- */
#hm-tip {
  position: fixed;
  z-index: 9999;
  pointer-events: none;
  background: rgba(24, 24, 27, 0.95);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 7px 13px;
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  font-weight: 500;
  color: #F4F4F5;
  white-space: nowrap;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6), 0 2px 8px rgba(0, 0, 0, 0.4);
  opacity: 0;
  transform: translateY(4px);
  transition: opacity 0.12s ease, transform 0.12s ease;
  /* caret — a small orange dot on the bottom */
}
#hm-tip.visible {
  opacity: 1;
  transform: translateY(0);
}
#hm-tip .tip-count {
  color: var(--accent);
  font-weight: 600;
  margin-left: 5px;
}

/* ---------- heatmap header (LeetCode style) ---------- */

.heatmap-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 12px;
}

.heatmap-top-left {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  color: var(--ink-strong);
}

.heatmap-submissions-count {
  font-family: 'Outfit', sans-serif;
  font-weight: 700;
  font-size: 22px;
  color: var(--ink-strong);
}

.heatmap-info-icon {
  font-size: 13px;
  color: var(--ink-dim);
  cursor: default;
  user-select: none;
}

.heatmap-top-right {
  display: flex;
  align-items: center;
  gap: 20px;
  font-size: 14px;
  color: var(--ink);
}

.heatmap-stat strong {
  color: var(--ink-strong);
  font-weight: 600;
}

.heatmap-current-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink-strong);
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease;
}
.heatmap-current-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
}
.heatmap-current-btn .chevron {
  font-size: 10px;
  opacity: 0.7;
}

/* ---------- two column section ---------- */

.diff-card {
  grid-column: span 5;
}

.log-card {
  grid-column: span 7;
}

@media (max-width: 900px) {
  .diff-card, .log-card { grid-column: span 12; }
}

.diff-list {
  display: flex;
  flex-direction: column;
  gap: 28px;
}
.diff-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.diff-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.diff-label {
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink-strong);
  display: flex;
  gap: 10px;
  align-items: center;
}
.diff-label::before {
  content: "";
  width: 8px; height: 8px; border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 12px currentColor;
}
.diff-row[data-diff="easy"] .diff-label { color: var(--sage); }
.diff-row[data-diff="medium"] .diff-label { color: var(--amber); }
.diff-row[data-diff="hard"] .diff-label { color: var(--terra); }

.bar-track {
  height: 6px;
  background: rgba(255,255,255,0.03);
  border-radius: 100px;
  position: relative;
  overflow: hidden;
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.5);
}
.bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 100px;
  transform-origin: left;
  animation: barIn 1s cubic-bezier(0.16, 1, 0.3, 1) both;
  box-shadow: inset 0 1px 1px rgba(255,255,255,0.4);
}
.diff-row[data-diff="easy"] .bar-fill { background: var(--sage); box-shadow: inset 0 1px 1px rgba(255,255,255,0.4), 0 0 16px rgba(16, 185, 129, 0.4); }
.diff-row[data-diff="medium"] .bar-fill { background: var(--amber); box-shadow: inset 0 1px 1px rgba(255,255,255,0.4), 0 0 16px rgba(245, 158, 11, 0.4); }
.diff-row[data-diff="hard"] .bar-fill { background: var(--terra); box-shadow: inset 0 1px 1px rgba(255,255,255,0.4), 0 0 16px rgba(239, 68, 68, 0.4); }

.diff-count {
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink-strong);
}
.diff-count .slash {
  color: var(--ink-dim);
  font-weight: 400;
  margin: 0 2px;
}
.diff-count .tot {
  color: var(--ink);
}

/* ---------- log ---------- */

.log {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.log li {
  display: grid;
  grid-template-columns: 44px 1fr auto;
  gap: 16px;
  align-items: center;
  padding: 12px 16px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 12px;
  transition: all 0.3s ease;
}
.log li:hover {
  background: rgba(255,255,255,0.02);
  border-color: var(--border);
}
.log .mark {
  width: 44px; height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
}
.log .outcome-y .mark { background: rgba(16, 185, 129, 0.1); color: var(--sage); border: 1px solid rgba(16, 185, 129, 0.2); }
.log .outcome-n .mark { background: rgba(239, 68, 68, 0.1); color: var(--terra); border: 1px solid rgba(239, 68, 68, 0.2); }
.log .outcome-e .mark { background: rgba(251, 191, 36, 0.1); color: var(--gold); border: 1px solid rgba(251, 191, 36, 0.2); }
.log .outcome-skip .mark { background: rgba(255, 255, 255, 0.03); color: var(--ink); border: 1px solid rgba(255, 255, 255, 0.08); }

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.log .title {
  font-family: 'Inter', sans-serif;
  color: var(--ink-strong);
  font-size: 14px;
  font-weight: 500;
}
.log .row-meta {
  display: flex;
  gap: 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  align-items: center;
}
.row-meta .diff-tag { 
  font-size: 12px;
  font-weight: 500;
}
.row-meta .diff-tag[data-diff="Easy"] { color: var(--sage); }
.row-meta .diff-tag[data-diff="Medium"] { color: var(--amber); }
.row-meta .diff-tag[data-diff="Hard"] { color: var(--terra); }
.row-meta .interval {
  font-family: 'Inter', sans-serif;
  color: var(--ink-dim);
}
.log-time {
  font-size: 13px;
  color: var(--ink-dim);
  font-weight: 400;
}

.log .empty {
  padding: 40px;
  color: var(--ink);
  text-align: center;
  font-weight: 500;
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: 12px;
}

/* ---------- ending ---------- */

footer.colophon {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 32px;
  margin-top: 32px;
  border-top: 1px solid var(--border);
  font-size: 13px;
  color: var(--ink-dim);
}
.colophon .mark-brand {
  font-family: 'Outfit', sans-serif;
  font-weight: 500;
  font-size: 14px;
  color: var(--ink);
  letter-spacing: -0.01em;
}

/* ---------- animations ---------- */

@keyframes cellIn {
  from { opacity: 0; transform: scale(0.5); }
  to { opacity: 1; transform: scale(1); }
}
@keyframes barIn {
  from { transform: scaleX(0); }
  to { transform: scaleX(1); }
}
@keyframes revealUp {
  from { opacity: 0; transform: translateY(32px); }
  to { opacity: 1; transform: none; }
}

.masthead { animation: revealUp 1s cubic-bezier(0.16, 1, 0.3, 1) both; }
.card { animation: revealUp 1s cubic-bezier(0.16, 1, 0.3, 1) both; }

.stat-card:nth-child(1) { animation-delay: 0.1s; }
.stat-card:nth-child(2) { animation-delay: 0.2s; }
.stat-card:nth-child(3) { animation-delay: 0.3s; }
.heatmap-card { animation-delay: 0.4s; }
.diff-card { animation-delay: 0.5s; }
.log-card { animation-delay: 0.6s; }

/* Streak glow — uses orange accent */
.stat-card.is-streak.has-streak .stat-icon {
  background: rgba(249, 115, 22, 0.12);
  border-color: rgba(249, 115, 22, 0.35);
  color: var(--accent);
  box-shadow: 0 0 20px rgba(249, 115, 22, 0.2), inset 0 1px 1px rgba(255,255,255,0.08);
}
.stat-card.is-streak.has-streak .stat-num {
  color: var(--accent);
}
"""


_GRAIN_SVG = (
    '<svg class="grain" xmlns="http://www.w3.org/2000/svg" '
    'preserveAspectRatio="none">'
    '<filter id="noiseFilter"><feTurbulence type="fractalNoise" '
    'baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"/>'
    '<feColorMatrix values="0 0 0 0 1  0 0 0 0 0.92  0 0 0 0 0.75  '
    '0 0 0 0.55 0"/></filter>'
    '<rect width="100%" height="100%" filter="url(#noiseFilter)"/></svg>'
)


_MARK = {"y": "✓", "e": "★", "n": "✗", "skip": "—"}


def _fmt_recent_row(r: dict) -> str:
    outcome = r["outcome"]
    mark = _MARK.get(outcome, "·")
    when = r["reviewed_at"][:16].replace("T", " ")
    if outcome == "skip":
        interval_txt = "postponed"
    else:
        interval_txt = f"{r['interval_before']}d → {r['interval_after']}d"
    return (
        f'<li class="outcome-{outcome}">'
        f'<div class="mark">{mark}</div>'
        f'<div class="log-content">'
        f'<div class="title">{_html.escape(r["title"])}</div>'
        f'<div class="row-meta">'
        f'<span class="diff-tag" data-diff="{r["difficulty"]}">{r["difficulty"]}</span>'
        f'<span class="interval">{interval_txt}</span>'
        f'</div>'
        f'</div>'
        f'<div class="log-time">{when}</div>'
        f'</li>'
    )


_HM_JS = r"""
(function () {
  var tip = document.getElementById('hm-tip');
  var hideTimer = null;

  function showTip(cell, e) {
    var label = cell.dataset.label;
    if (!label || cell.classList.contains('cell-future')) return;
    // label format: "Mon Apr 27, 2026 \u2014 3 reviews"
    var idx = label.indexOf(' \u2014 ');
    var dateStr = idx >= 0 ? label.slice(0, idx) : label;
    var countStr = idx >= 0 ? label.slice(idx + 3) : '';
    tip.innerHTML = dateStr +
      (countStr ? '<span class="tip-count">' + countStr + '</span>' : '');
    clearTimeout(hideTimer);
    positionTip(e);
    tip.classList.add('visible');
  }

  function positionTip(e) {
    var pad = 14;
    var tw = tip.offsetWidth;
    var th = tip.offsetHeight;
    var x = e.clientX - tw / 2;
    var y = e.clientY - th - pad;
    if (x < 8) x = 8;
    if (x + tw > window.innerWidth - 8) x = window.innerWidth - 8 - tw;
    if (y < 8) y = e.clientY + pad;
    tip.style.left = x + 'px';
    tip.style.top  = y + 'px';
  }

  function hideTip() {
    hideTimer = setTimeout(function () {
      tip.classList.remove('visible');
    }, 60);
  }

  document.querySelectorAll('.heatmap-grid').forEach(function (grid) {
    grid.addEventListener('mouseover', function (e) {
      var cell = e.target.closest('.cell');
      if (cell) showTip(cell, e);
    });
    grid.addEventListener('mousemove', function (e) {
      var cell = e.target.closest('.cell');
      if (cell && tip.classList.contains('visible')) positionTip(e);
    });
    grid.addEventListener('mouseleave', hideTip);
    grid.addEventListener('mouseout', function (e) {
      if (!e.target.closest('.cell')) hideTip();
    });
  });
})();
"""


def render_html(data: dict) -> str:
    today: date = data["today"]
    hm = _build_heatmap(today, data["days"])

    # Header + subhead
    attempted = data["attempted"]
    total = data["total"]
    streak = data["streak"]
    accuracy = data["accuracy"]
    total_reviews = data["total_reviews"]

    subhead_right = f"{attempted} of {total} attempted"
    if accuracy is not None:
        subhead_right += f" · {accuracy}% solved"

    # Hero
    has_streak_cls = " has-streak" if streak > 0 else ""
    hero_html = f'''
      <div class="card stat-card is-streak{has_streak_cls}">
        <div class="stat-icon amber">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>
        </div>
        <div class="stat-label">Current Streak</div>
        <div class="stat-num">{streak}<span class="unit">days</span></div>
        <div class="stat-sub">consecutive days practiced</div>
      </div>
      <div class="card stat-card">
        <div class="stat-icon emerald">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        </div>
        <div class="stat-label">Problems Attempted</div>
        <div class="stat-num">{attempted}<span class="denom">/ {total}</span></div>
        <div class="stat-sub">of the neetcode two-fifty</div>
      </div>
      <div class="card stat-card">
        <div class="stat-icon">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
        </div>
        <div class="stat-label">Total Sessions</div>
        <div class="stat-num">{total_reviews}<span class="unit">reviews</span></div>
        <div class="stat-sub">answers recorded in the log</div>
      </div>
    '''

    # Difficulty rows
    diff_rows = []
    for d in ("Easy", "Medium", "Hard"):
        counts = data["difficulty"].get(d, {"seen": 0, "total": 0})
        seen = counts["seen"]
        tot = counts["total"]
        pct = (seen / tot * 100) if tot else 0
        diff_rows.append(f'''
          <div class="diff-row" data-diff="{d.lower()}">
            <div class="diff-header">
              <span class="diff-label">{d}</span>
              <span class="diff-count">{seen}<span class="slash"> / </span><span class="tot">{tot}</span></span>
            </div>
            <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>
          </div>
        ''')

    # Recent log
    if data["recent"]:
        recent_html = "<ol class='log'>" + "".join(_fmt_recent_row(r) for r in data["recent"]) + "</ol>"
    else:
        recent_html = '<ol class="log"><li class="empty">No entries yet. Open a card — your first mark goes here.</li></ol>'

    submissions_past_year = data["submissions_past_year"]
    active_days_past_year = data["active_days_past_year"]
    max_streak_past_year = data["max_streak_past_year"]

    generated_stamp = today.strftime("%b %-d, %Y").upper()

    last_at = data.get("last_reviewed_at")
    last_note = ""
    if last_at:
        last_note = f' · last entry {last_at[:10]}'

    # Assemble
    html_doc = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>neetcode · logbook</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>{_CSS}</style>
</head>
<body>
  {_GRAIN_SVG}
  <main>

    <header class="masthead">
      <div class="eyebrow">
        <span class="dot"></span>
        <span>Logbook · NeetCode 250</span>
      </div>
      <h1 class="display">Daily <em>Practice</em> Record</h1>
      <div class="subhead">
        <span>Generated <em>{generated_stamp}</em>{last_note}</span>
        <span>{subhead_right}</span>
      </div>
    </header>

    <div class="dashboard-grid">
      {hero_html}

      <div class="card heatmap-card">
        <div class="heatmap-top">
          <div class="heatmap-top-left">
            <span class="heatmap-submissions-count">{submissions_past_year}</span>
            <span>submissions in the past one year</span>
            <span class="heatmap-info-icon" title="Total reviews completed in the past 52 weeks">&#9432;</span>
          </div>
          <div class="heatmap-top-right">
            <span class="heatmap-stat">Total active days: <strong>{active_days_past_year}</strong></span>
            <span class="heatmap-stat">Max streak: <strong>{max_streak_past_year}</strong></span>
            <button class="heatmap-current-btn">Current <span class="chevron">&#9660;</span></button>
          </div>
        </div>
        <div class="heatmap-frame">
          <div class="heatmap-cols">
            <div class="heatmap-grid" style="grid-template-columns:repeat({hm['num_cols']},16px)">{hm["cells_html"]}</div>
            <div class="heatmap-months" style="grid-template-columns:repeat({hm['num_cols']},16px)">{hm["months_html"]}</div>
          </div>
        </div>
      </div>

      <div class="card diff-card">
        <div class="section-head">
          <h2>By Difficulty</h2>
          <span class="meta">Coverage</span>
        </div>
        <div class="diff-list">
          {''.join(diff_rows)}
        </div>
      </div>

      <div class="card log-card">
        <div class="section-head">
          <h2>Recent Log</h2>
          <span class="meta">Latest 25 entries</span>
        </div>
        {recent_html}
      </div>
    </div>

    <footer class="colophon">
      <span>— end of record</span>
      <span class="mark-brand">neetcode srs</span>
      <span>{generated_stamp}</span>
    </footer>

  </main>

  <!-- Heatmap floating tooltip -->
  <div id="hm-tip"></div>

  <script>{_HM_JS}</script>
</body>
</html>
'''
    return html_doc


def render_to_file(conn: sqlite3.Connection, today: date | None = None) -> Path:
    if today is None:
        today = date.today()
    data = build_data(conn, today)
    html = render_html(data)
    out = Path(tempfile.gettempdir()) / "neetcode-dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


def open_dashboard(conn: sqlite3.Connection, today: date | None = None) -> Path:
    path = render_to_file(conn, today)
    webbrowser.open(f"file://{path}")
    return path
