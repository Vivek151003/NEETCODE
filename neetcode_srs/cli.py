from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import webbrowser
from datetime import date, timedelta
from pathlib import Path

from neetcode_srs import config, dashboard, db, problems, selector
from neetcode_srs.srs import schedule

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "state.db"
CACHE_PATH = DATA_DIR / "neetcode250.json"
CONFIG_PATH = DATA_DIR / "config.json"
IMAGES_DIR = DATA_DIR / "images"


# --- output helpers -------------------------------------------------------

_USE_COLOR = sys.stdout.isatty()

BOLD = "\033[1m" if _USE_COLOR else ""
DIM = "\033[2m" if _USE_COLOR else ""
RESET = "\033[0m" if _USE_COLOR else ""
GREEN = "\033[32m" if _USE_COLOR else ""
YELLOW = "\033[33m" if _USE_COLOR else ""
RED = "\033[31m" if _USE_COLOR else ""
CYAN = "\033[36m" if _USE_COLOR else ""

DIFFICULTY_COLOR = {"Easy": GREEN, "Medium": YELLOW, "Hard": RED}


def _color(s: str, c: str) -> str:
    if not c:
        return s
    return f"{c}{s}{RESET}"


def _print_card(card: db.Card, kind: str) -> None:
    banner = {
        "review": "Review due",
        "new": "New problem",
    }.get(kind, kind)
    diff = _color(card.difficulty, DIFFICULTY_COLOR.get(card.difficulty, ""))
    print()
    print(_color(f"  {banner}", DIM))
    print(f"  {_color(card.title, BOLD)}  [{diff}]  {DIM}{', '.join(card.topics)}{RESET}")
    print(f"  {_color(card.leetcode_url, CYAN)}")
    if kind == "review":
        streak = card.reps
        prior = card.interval_days
        print(f"  {DIM}streak: {streak} · last interval: {prior}d · ease: {card.ease:.2f}{RESET}")
        if card.notes:
            print(f"  {DIM}note: {card.notes}{RESET}")
        if card.image_path:
            print(f"  {DIM}image: {card.image_path}{RESET}")
            _open_file(card.image_path)
    print()


def _open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["start", url], shell=True, check=False)
    else:
        webbrowser.open(url)


def _open_file(path: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["start", path], shell=True, check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


def _save_image(src: str, card_id: str) -> str:
    """Copy src into IMAGES_DIR and return the absolute path string."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        parts = shlex.split(src)
        src = parts[0] if parts else src
    except ValueError:
        pass
    src_path = Path(src).expanduser().resolve()
    dest = IMAGES_DIR / f"{card_id}{src_path.suffix}"
    shutil.copy2(src_path, dest)
    return str(dest)


def _parse_today(raw: str | None) -> date:
    if raw is None:
        return date.today()
    return date.fromisoformat(raw)


# --- commands -------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    cached = problems.load_cached(CACHE_PATH)
    if cached is None or args.refresh:
        print("Fetching NeetCode 250 from neetcode.io …")
        plist = problems.fetch_neetcode250()
        problems.save_cache(CACHE_PATH, plist)
        print(f"Cached {len(plist)} problems → {CACHE_PATH}")
    else:
        plist = cached
        print(f"Using cached problem list ({len(plist)} problems). Use --refresh to re-fetch.")
    n = db.upsert_problems(conn, plist)
    print(f"Loaded {n} problems into the deck.")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    today = _parse_today(args.today)
    s = db.stats(conn, today)
    if s["total"] == 0:
        print("Deck is empty. Run `neetcode setup` first.")
        return 1
    print()
    print(f"  {_color('Deck', BOLD)}: {s['total']} total  ·  {s['new']} new  ·  "
          f"{s['learning']} learning  ·  {s['mature']} mature")
    print(f"  {_color('Due today', BOLD)}: {s['due_today']}")
    print(f"  {_color('By difficulty', BOLD)}:")
    for d, counts in s["by_difficulty"].items():
        color = DIFFICULTY_COLOR.get(d, "")
        print(f"    {_color(d, color):<20} {counts['seen']}/{counts['total']} seen")
    print()
    return 0


def cmd_today(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    today = _parse_today(args.today)
    cfg = config.load(CONFIG_PATH)
    target = cfg["daily_target"]

    pick = selector.pick_today(conn, today, daily_target=target)
    if pick.kind == "empty":
        print("Deck is empty. Run `neetcode setup` first.")
        return 1
    if pick.kind == "quota_hit":
        print(f"\n  Done for today: {pick.done_today}/{target} cards. "
              f"Come back tomorrow.")
        print(f"  {DIM}Want more? `neetcode config daily N`{RESET}\n")
        return 0
    assert pick.card is not None

    if target > 1:
        print(f"  {DIM}card {pick.done_today + 1} of {target} today{RESET}")
    _print_card(pick.card, pick.kind)
    print(f"  {DIM}y = solved · n = couldn't solve · e = trivially easy · skip{RESET}")
    prompt = f"  {_color('Answer', BOLD)} [y/n/e/skip] > "
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 130

    if answer in ("skip", "s"):
        next_due = today + timedelta(days=1)
        db.postpone(conn, pick.card, next_due)
        print(f"  {_color('Postponed', DIM)} to {next_due.isoformat()}.\n")
        return 0
    if answer not in ("y", "n", "e"):
        print("  Expected y / n / e / skip. No changes made.")
        return 2

    result = schedule(pick.card.state, answer, today)
    db.apply_review(conn, pick.card, answer, result.state, result.next_due, today)

    verb = {"y": "solved", "n": "failed", "e": "easy"}[answer]
    color = {"y": GREEN, "n": RED, "e": CYAN}[answer]
    days = result.state.interval_days
    print()
    print(f"  {_color(verb, color)} — next review in {days} day{'s' if days != 1 else ''} "
          f"({result.next_due.isoformat()}).")
    print(f"  {DIM}ease {pick.card.ease:.2f} → {result.state.ease:.2f}  ·  "
          f"streak {result.state.reps}{RESET}")

    try:
        existing = f" ({pick.card.notes})" if pick.card.notes else ""
        note_input = input(f"  {DIM}Note{existing} (Enter to skip): {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if note_input:
        db.set_note(conn, pick.card.id, note_input)

    try:
        existing_img = f" (attached)" if pick.card.image_path else ""
        img_input = input(f"  {DIM}Image path{existing_img} (Enter to skip): {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if img_input:
        try:
            saved = _save_image(img_input, pick.card.id)
            db.set_image(conn, pick.card.id, saved)
            print(f"  {DIM}Image saved.{RESET}")
        except (FileNotFoundError, OSError) as e:
            print(f"  {_color('Could not save image', RED)}: {e}")
    print()
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    rows = db.recent_reviews(conn, args.n)
    if not rows:
        print("No reviews yet.")
        return 0
    print()
    for r in rows:
        icon = {
            "y": _color("✓", GREEN),
            "n": _color("✗", RED),
            "e": _color("★", CYAN),
            "skip": _color("⋯", DIM),
        }[r["outcome"]]
        diff = _color(r["difficulty"], DIFFICULTY_COLOR.get(r["difficulty"], ""))
        when = r["reviewed_at"][:16].replace("T", " ")
        delta = (
            f"interval {r['interval_before']}d → {r['interval_after']}d"
            if r["outcome"] != "skip"
            else "postponed"
        )
        print(f"  {icon}  {when}  {r['title']:<40} [{diff}]  {DIM}{delta}{RESET}")
    print()
    return 0


_CONFIG_ALIASES = {"daily": "daily_target"}


def cmd_dashboard(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    today = _parse_today(args.today)
    if args.write_only:
        path = dashboard.render_to_file(conn, today)
        print(f"Wrote {path}")
    else:
        path = dashboard.open_dashboard(conn, today)
        print(f"Opened {path}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = config.load(CONFIG_PATH)
    if args.key is None:
        print()
        for k, v in cfg.items():
            print(f"  {k} = {v}")
        print()
        return 0
    key = _CONFIG_ALIASES.get(args.key, args.key)
    if args.value is None:
        print(cfg.get(key, "(unset)"))
        return 0
    coerced: int | str = args.value
    if key == "daily_target":
        try:
            coerced = int(args.value)
        except ValueError:
            print(f"  daily_target must be an integer, got {args.value!r}")
            return 2
        if coerced < 1:
            print("  daily_target must be >= 1")
            return 2
    try:
        updated = config.set_key(CONFIG_PATH, key, coerced)
    except KeyError as e:
        print(f"  {e}")
        return 2
    print(f"  {key} = {updated[key]}")
    return 0


def cmd_skip(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    today = _parse_today(args.today)
    cfg = config.load(CONFIG_PATH)
    pick = selector.pick_today(conn, today, daily_target=cfg["daily_target"])
    if pick.kind in ("empty", "quota_hit"):
        print("Nothing to skip.")
        return 0
    assert pick.card is not None
    next_due = today + timedelta(days=1)
    db.postpone(conn, pick.card, next_due)
    print(f"Postponed {pick.card.title} to {next_due.isoformat()}.")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    today = _parse_today(args.today)
    cfg = config.load(CONFIG_PATH)
    pick = selector.pick_today(conn, today, daily_target=cfg["daily_target"])
    if pick.kind == "empty":
        print("Deck is empty. Run `neetcode setup` first.")
        return 1
    if pick.kind == "quota_hit":
        print("\n  Done for today. No card to open.\n")
        return 0
    assert pick.card is not None
    _print_card(pick.card, pick.kind)
    _open_url(pick.card.leetcode_url)
    print(f"  {DIM}Opened in browser.{RESET}\n")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    conn = db.connect(DB_PATH)
    today = _parse_today(args.today)
    card = db.last_reviewed_today(conn, today)
    if card is None:
        print("\n  No card reviewed today. Run `neetcode` first.\n")
        return 1
    diff = _color(card.difficulty, DIFFICULTY_COLOR.get(card.difficulty, ""))
    print(f"\n  {_color(card.title, BOLD)}  [{diff}]")
    if card.notes:
        print(f"  {DIM}Current note: {card.notes}{RESET}")
    if card.image_path:
        print(f"  {DIM}Current image: {card.image_path}{RESET}")
    try:
        text = input(f"  {_color('Note', BOLD)} (Enter to keep): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if text:
        db.set_note(conn, card.id, text)
        print(f"  {DIM}Saved.{RESET}")

    try:
        existing_img = " (Enter to keep)" if card.image_path else " (Enter to skip)"
        img_input = input(f"  {_color('Image path', BOLD)}{existing_img}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if img_input:
        try:
            saved = _save_image(img_input, card.id)
            db.set_image(conn, card.id, saved)
            print(f"  {DIM}Image saved.{RESET}")
        except (FileNotFoundError, OSError) as e:
            print(f"  {_color('Could not save image', RED)}: {e}")
    print()
    return 0


# --- entrypoint -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Parent parser with the hidden --today flag, inherited by all subparsers
    # so it works in both `neetcode --today ...` and `neetcode today --today ...`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--today", help=argparse.SUPPRESS)

    p = argparse.ArgumentParser(
        prog="neetcode",
        description="Daily NeetCode 250 SRS.",
        parents=[common],
    )
    sub = p.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", parents=[common],
                             help="Fetch the NeetCode 250 list and populate the deck.")
    p_setup.add_argument("--refresh", action="store_true", help="Re-fetch even if cached.")
    p_setup.set_defaults(func=cmd_setup)

    p_stats = sub.add_parser("stats", parents=[common], help="Show deck progress.")
    p_stats.set_defaults(func=cmd_stats)

    p_today = sub.add_parser("today", parents=[common], help="Show today's card (default).")
    p_today.set_defaults(func=cmd_today)

    p_hist = sub.add_parser("history", parents=[common], help="Show recent reviews.")
    p_hist.add_argument("n", nargs="?", type=int, default=10)
    p_hist.set_defaults(func=cmd_history)

    p_skip = sub.add_parser("skip", parents=[common], help="Postpone today's card by one day.")
    p_skip.set_defaults(func=cmd_skip)

    p_open = sub.add_parser("open", parents=[common], help="Open today's card in the browser.")
    p_open.set_defaults(func=cmd_open)

    p_note = sub.add_parser("note", parents=[common],
                            help="View or edit the note for today's reviewed card.")
    p_note.set_defaults(func=cmd_note)

    p_dash = sub.add_parser("dashboard", parents=[common],
                            help="Open a local HTML progress dashboard in your browser.")
    p_dash.add_argument("--write-only", action="store_true",
                        help="Write the HTML file but don't open a browser.")
    p_dash.set_defaults(func=cmd_dashboard)

    p_cfg = sub.add_parser("config", parents=[common],
                           help="Show or set config. Example: `neetcode config daily 3`")
    p_cfg.add_argument("key", nargs="?", help="Config key (e.g. 'daily' or 'daily_target').")
    p_cfg.add_argument("value", nargs="?", help="New value (integer for daily_target).")
    p_cfg.set_defaults(func=cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_today(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
