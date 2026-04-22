from datetime import date, timedelta

import pytest

from neetcode_srs.srs import (
    CardState,
    EASE_MAX,
    EASE_MIN,
    EASY_BONUS,
    FIRST_SHOT_E_INTERVAL,
    FIRST_SHOT_Y_INTERVAL,
    WRONG_MIN_INTERVAL,
    initial_state,
    schedule,
)


TODAY = date(2026, 4, 22)


def test_initial_state_defaults():
    s = initial_state()
    assert s.ease == 2.5
    assert s.interval_days == 0
    assert s.reps == 0


def test_first_shot_y_graduates_past_the_one_day_probe():
    """First correct answer on a fresh card jumps straight to 4 days."""
    result = schedule(initial_state(), "y", TODAY)
    assert result.state.reps == 1
    assert result.state.interval_days == FIRST_SHOT_Y_INTERVAL == 4
    assert result.next_due == TODAY + timedelta(days=4)
    assert result.state.ease == 2.55


def test_first_shot_e_jumps_further_than_y():
    result = schedule(initial_state(), "e", TODAY)
    assert result.state.reps == 1
    assert result.state.interval_days == FIRST_SHOT_E_INTERVAL == 7
    assert result.next_due == TODAY + timedelta(days=7)
    assert result.state.ease == 2.5 + 0.15  # bigger ease bump on easy


def test_subsequent_y_multiplies_interval_by_ease():
    s = schedule(initial_state(), "y", TODAY).state     # reps=1, i=4, ease=2.55
    result = schedule(s, "y", TODAY + timedelta(days=4))
    assert result.state.reps == 2
    assert result.state.interval_days == round(4 * 2.55)  # 10
    assert abs(result.state.ease - 2.60) < 1e-9


def test_subsequent_e_applies_easy_bonus():
    s = schedule(initial_state(), "y", TODAY).state      # reps=1, i=4, ease=2.55
    result = schedule(s, "e", TODAY + timedelta(days=4))
    assert result.state.reps == 2
    # ease multiplier × easy bonus
    assert result.state.interval_days == round(4 * 2.55 * EASY_BONUS)  # 13


def test_wrong_resets_with_three_day_minimum_regardless_of_prior_streak():
    s = initial_state()
    s = schedule(s, "y", TODAY).state
    s = schedule(s, "y", TODAY + timedelta(days=4)).state
    s = schedule(s, "e", TODAY + timedelta(days=14)).state
    wrong = schedule(s, "n", TODAY + timedelta(days=40))
    assert wrong.state.reps == 0
    assert wrong.state.interval_days == WRONG_MIN_INTERVAL == 3


def test_wrong_from_fresh_card_is_three_days_not_one():
    result = schedule(initial_state(), "n", TODAY)
    assert result.state.interval_days == 3
    assert result.state.reps == 0
    assert result.next_due == TODAY + timedelta(days=3)


def test_ease_decreases_on_wrong_then_recovers_on_correct():
    s = initial_state()
    s = schedule(s, "n", TODAY).state
    assert abs(s.ease - 2.3) < 1e-9
    s = schedule(s, "y", TODAY + timedelta(days=3)).state
    assert abs(s.ease - 2.35) < 1e-9


def test_ease_floor():
    s = CardState(ease=EASE_MIN, interval_days=1, reps=0)
    s = schedule(s, "n", TODAY).state
    assert s.ease == EASE_MIN


def test_ease_ceiling_on_easy():
    s = CardState(ease=EASE_MAX, interval_days=10, reps=5)
    s = schedule(s, "e", TODAY).state
    assert s.ease == EASE_MAX


def test_invalid_outcome_raises():
    with pytest.raises(ValueError):
        schedule(initial_state(), "maybe", TODAY)
