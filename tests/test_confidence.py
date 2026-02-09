"""Tests for confidence scoring module."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from mca.orchestrator.confidence import (
    SPIKE_THRESHOLD,
    WEIGHTS,
    ConfidenceScore,
    calculate_confidence,
    should_spike,
    _similar_success_score,
    _failure_rate_score,
    _novelty_score,
    _recent_success_rate,
    _find_similar_outcomes,
)


# ── ConfidenceScore dataclass ─────────────────────────────────────────────────

class TestConfidenceScore:
    def test_dataclass_fields(self):
        score = ConfidenceScore(
            total=75, similar_success=80, failure_rate=70,
            novelty=60, similar_count=3, recent_success_rate=0.8,
        )
        assert score.total == 75
        assert score.similar_success == 80
        assert score.failure_rate == 70
        assert score.novelty == 60
        assert score.similar_count == 3
        assert score.recent_success_rate == 0.8


# ── should_spike ──────────────────────────────────────────────────────────────

class TestShouldSpike:
    def test_below_threshold_spikes(self):
        score = ConfidenceScore(total=30, similar_success=20, failure_rate=40,
                                novelty=20, similar_count=0, recent_success_rate=0.5)
        assert should_spike(score) is True

    def test_at_threshold_no_spike(self):
        score = ConfidenceScore(total=50, similar_success=50, failure_rate=50,
                                novelty=50, similar_count=2, recent_success_rate=0.5)
        assert should_spike(score) is False

    def test_above_threshold_no_spike(self):
        score = ConfidenceScore(total=80, similar_success=90, failure_rate=70,
                                novelty=80, similar_count=5, recent_success_rate=0.9)
        assert should_spike(score) is False

    def test_zero_spikes(self):
        score = ConfidenceScore(total=0, similar_success=0, failure_rate=0,
                                novelty=0, similar_count=0, recent_success_rate=0.0)
        assert should_spike(score) is True


# ── _similar_success_score ────────────────────────────────────────────────────

class TestSimilarSuccessScore:
    def test_all_completed(self):
        outcomes = [
            {"tags": ["task-outcome", "completed"]},
            {"tags": ["task-outcome", "completed"]},
            {"tags": ["task-outcome", "completed"]},
        ]
        assert _similar_success_score(outcomes) == 100

    def test_all_failed(self):
        outcomes = [
            {"tags": ["task-outcome", "failed"]},
            {"tags": ["task-outcome", "failed"]},
        ]
        assert _similar_success_score(outcomes) == 0

    def test_mixed(self):
        outcomes = [
            {"tags": ["task-outcome", "completed"]},
            {"tags": ["task-outcome", "failed"]},
            {"tags": ["task-outcome", "completed"]},
            {"tags": ["task-outcome", "failed"]},
        ]
        assert _similar_success_score(outcomes) == 50

    def test_empty_returns_neutral_low(self):
        assert _similar_success_score([]) == 30

    def test_single_success(self):
        outcomes = [{"tags": ["task-outcome", "completed"]}]
        assert _similar_success_score(outcomes) == 100


# ── _failure_rate_score ───────────────────────────────────────────────────────

class TestFailureRateScore:
    def test_all_successes(self):
        store = MagicMock()
        store.conn.execute.return_value.fetchall.return_value = [
            (True,), (True,), (True,), (True,), (True,),
        ]
        assert _failure_rate_score(store) == 100

    def test_all_failures(self):
        store = MagicMock()
        store.conn.execute.return_value.fetchall.return_value = [
            (False,), (False,), (False,),
        ]
        assert _failure_rate_score(store) == 0

    def test_mixed(self):
        store = MagicMock()
        store.conn.execute.return_value.fetchall.return_value = [
            (True,), (False,), (True,), (True,), (False,),
        ]
        assert _failure_rate_score(store) == 60

    def test_no_history_returns_neutral(self):
        store = MagicMock()
        store.conn.execute.return_value.fetchall.return_value = []
        assert _failure_rate_score(store) == 50

    def test_db_error_returns_neutral(self):
        store = MagicMock()
        store.conn.execute.side_effect = Exception("DB down")
        assert _failure_rate_score(store) == 50


# ── _novelty_score ────────────────────────────────────────────────────────────

class TestNoveltyScore:
    def test_many_similar(self):
        assert _novelty_score(5) == 80

    def test_three_similar(self):
        assert _novelty_score(3) == 80

    def test_two_similar(self):
        assert _novelty_score(2) == 50

    def test_one_similar(self):
        assert _novelty_score(1) == 50

    def test_none_similar(self):
        assert _novelty_score(0) == 20


# ── _recent_success_rate ──────────────────────────────────────────────────────

class TestRecentSuccessRate:
    def test_all_success(self):
        store = MagicMock()
        store.conn.execute.return_value.fetchall.return_value = [
            (True,), (True,), (True,),
        ]
        assert _recent_success_rate(store) == 1.0

    def test_no_history(self):
        store = MagicMock()
        store.conn.execute.return_value.fetchall.return_value = []
        assert _recent_success_rate(store) == 0.0

    def test_db_error(self):
        store = MagicMock()
        store.conn.execute.side_effect = Exception("fail")
        assert _recent_success_rate(store) == 0.0


# ── calculate_confidence ──────────────────────────────────────────────────────

class TestCalculateConfidence:
    def test_high_confidence(self):
        """All factors positive → high score."""
        store = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        # vector_search returns 3 completed outcomes
        store.vector_search.return_value = [
            {"tags": ["task-outcome", "completed"], "content": "did thing 1"},
            {"tags": ["task-outcome", "completed"], "content": "did thing 2"},
            {"tags": ["task-outcome", "completed"], "content": "did thing 3"},
        ]
        # run_metrics: all success
        store.conn.execute.return_value.fetchall.return_value = [
            (True,), (True,), (True,), (True,), (True,),
        ]

        score = calculate_confidence(store, embedder, "add a function")
        assert score.total >= 80
        assert score.similar_count == 3
        assert should_spike(score) is False

    def test_low_confidence_no_history(self):
        """No similar tasks, no run history → low score."""
        store = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        store.vector_search.return_value = []  # no similar
        store.conn.execute.return_value.fetchall.return_value = []  # no history

        score = calculate_confidence(store, embedder, "do something brand new")
        # similar_success=30(×0.5=15) + failure_rate=50(×0.2=10) + novelty=20(×0.3=6) = 31
        assert score.total == 31
        assert score.similar_count == 0
        assert should_spike(score) is True

    def test_mixed_outcomes(self):
        """Some failures in history → moderate score."""
        store = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768

        store.vector_search.return_value = [
            {"tags": ["task-outcome", "completed"], "content": "ok"},
            {"tags": ["task-outcome", "failed"], "content": "bad"},
        ]
        store.conn.execute.return_value.fetchall.return_value = [
            (True,), (False,), (True,),
        ]

        score = calculate_confidence(store, embedder, "fix a bug")
        assert 30 <= score.total <= 70
        assert score.similar_count == 2

    def test_embedder_failure_graceful(self):
        """If embedder fails, similar_count=0 but still returns a score."""
        store = MagicMock()
        embedder = MagicMock()
        embedder.embed.side_effect = Exception("Ollama down")

        store.conn.execute.return_value.fetchall.return_value = [(True,)] * 5

        score = calculate_confidence(store, embedder, "anything")
        assert score.similar_count == 0
        assert score.similar_success == 30  # neutral-low default

    def test_total_clamped_to_0_100(self):
        """Score should always be 0-100 regardless of inputs."""
        store = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 768
        store.vector_search.return_value = []
        store.conn.execute.return_value.fetchall.return_value = []

        score = calculate_confidence(store, embedder, "test")
        assert 0 <= score.total <= 100


# ── Weights sanity ────────────────────────────────────────────────────────────

class TestWeights:
    def test_weights_sum_to_1(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001

    def test_spike_threshold_reasonable(self):
        assert 20 <= SPIKE_THRESHOLD <= 80
