from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

EASE_START = 2.5
EASE_MIN = 1.3
EASE_MAX = 2.8
EASE_STEP_UP = 0.05
EASE_STEP_UP_EASY = 0.15
EASE_STEP_DOWN = 0.2

# On a wrong answer, the minimum interval before the card resurfaces.
# Failing a problem should not bring it back tomorrow — the brain needs
# a couple of days to forget and re-encounter cleanly.
WRONG_MIN_INTERVAL = 3

# First-shot graduation: answering `y` on a fresh card (reps == 0) skips
# the 1-day probe and jumps ahead. A first-time correct solve is strong
# evidence — spending another 30+ min on it tomorrow is low-value.
FIRST_SHOT_Y_INTERVAL = 4

# Easy graduation: `e` on a fresh card jumps even further, for problems
# that felt trivial on first encounter.
FIRST_SHOT_E_INTERVAL = 7

# Anki-style "easy bonus" multiplier on top of interval × ease when `e`
# is pressed on an already-learned card.
EASY_BONUS = 1.3


@dataclass(frozen=True)
class CardState:
    ease: float
    interval_days: int
    reps: int


@dataclass(frozen=True)
class Schedule:
    state: CardState
    next_due: date


def schedule(current: CardState, outcome: str, today: date) -> Schedule:
    if outcome not in ("y", "n", "e"):
        raise ValueError(f"outcome must be 'y', 'n', or 'e', got {outcome!r}")

    if outcome == "y":
        reps = current.reps + 1
        if current.reps == 0:
            interval = FIRST_SHOT_Y_INTERVAL
        else:
            interval = max(1, round(current.interval_days * current.ease))
        ease = min(current.ease + EASE_STEP_UP, EASE_MAX)
    elif outcome == "e":
        reps = current.reps + 1
        if current.reps == 0:
            interval = FIRST_SHOT_E_INTERVAL
        else:
            interval = max(1, round(current.interval_days * current.ease * EASY_BONUS))
        ease = min(current.ease + EASE_STEP_UP_EASY, EASE_MAX)
    else:  # "n"
        reps = 0
        interval = WRONG_MIN_INTERVAL
        ease = max(current.ease - EASE_STEP_DOWN, EASE_MIN)

    next_state = CardState(ease=ease, interval_days=interval, reps=reps)
    return Schedule(state=next_state, next_due=today + timedelta(days=interval))


def initial_state() -> CardState:
    return CardState(ease=EASE_START, interval_days=0, reps=0)
