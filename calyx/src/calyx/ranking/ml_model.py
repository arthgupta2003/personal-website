"""ML-based ranking model — distills Claude's composite scores from deterministic features.

Usage:
  from calyx.ranking.ml_model import MLRanker
  ranker = MLRanker()
  ranker.train(db)           # train from attended history
  score = ranker.predict(event_features)

Also runnable as a script: uv run python scripts/train_model.py
"""

from __future__ import annotations

import json
import logging
import math
import pickle
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_PATH = Path("state/ml_model.pkl")

# Feature names used for training and prediction
FEATURE_NAMES = [
    "interest_score",
    "social_score",
    "urgency_score",
    "logistics_score",
    "friend_score",
    "discovery_score",
    "quality_score",
    "score",  # Claude composite score
    "vibe_social",
    "vibe_intellectual",
    "vibe_mixed",
    "has_location",
    "is_online",
    "has_price",
    "has_image",
]


def _extract_features(row: dict) -> list[float]:
    """Extract ML features from a rankings+events row dict."""
    vibe = row.get("vibe") or "mixed"
    return [
        float(row.get("interest_score") or 0),
        float(row.get("social_score") or 0),
        float(row.get("urgency_score") or 0),
        float(row.get("logistics_score") or 0),
        float(row.get("friend_score") or 0),
        float(row.get("discovery_score") or 0),
        float(row.get("quality_score") or 0),
        float(row.get("score") or 0),
        1.0 if vibe == "social" else 0.0,
        1.0 if vibe == "intellectual" else 0.0,
        1.0 if vibe == "mixed" else 0.0,
        1.0 if row.get("location_name") else 0.0,
        float(row.get("is_online") or 0),
        1.0 if row.get("price") else 0.0,
        1.0 if row.get("image_url") else 0.0,
    ]


class MLRanker:
    """Lightweight logistic regression that predicts attendance probability."""

    def __init__(self):
        self.model = None
        self.trained = False
        self.n_samples = 0
        self.feature_importances: dict[str, float] = {}

    def train(self, db) -> dict:
        """Train from attended vs non-attended kept events in DB. Returns training stats."""
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
        except ImportError:
            logger.warning("scikit-learn not installed; ML ranking disabled")
            return {"error": "scikit-learn not installed"}

        # Fetch all kept rankings joined with events and attended labels
        rows = db.conn.execute(
            """SELECT rk.*, e.location_name, e.is_online, e.price, e.image_url,
                      CASE WHEN a.event_id IS NOT NULL THEN 1 ELSE 0 END as label
               FROM rankings rk
               JOIN events e ON e.event_id = rk.event_id AND e.run_id = rk.run_id
               JOIN runs r ON r.id = rk.run_id
               LEFT JOIN attended a ON a.event_id = rk.event_id AND a.user_id = r.user_id
               WHERE rk.keep = 1"""
        ).fetchall()

        if len(rows) < 10:
            return {"error": f"Not enough data: only {len(rows)} kept events"}

        X = [_extract_features(dict(r)) for r in rows]
        y = [r["label"] for r in rows]

        positive = sum(y)
        if positive < 2:
            return {"error": f"Not enough attended events: only {positive}"}

        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=500, class_weight="balanced")),
        ])
        pipe.fit(X, y)

        self.model = pipe
        self.trained = True
        self.n_samples = len(rows)

        # Extract feature importances from LR coefficients
        coefs = pipe.named_steps["lr"].coef_[0]
        self.feature_importances = {
            name: round(float(coef), 4)
            for name, coef in zip(FEATURE_NAMES, coefs)
        }

        # Save model
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump({"model": pipe, "feature_importances": self.feature_importances,
                         "n_samples": self.n_samples}, f)

        logger.info("ML model trained on %d samples (%d positive)", len(rows), positive)
        return {
            "n_samples": len(rows),
            "n_positive": positive,
            "feature_importances": self.feature_importances,
        }

    def load(self) -> bool:
        """Load saved model from disk. Returns True if successful."""
        if not _MODEL_PATH.exists():
            return False
        try:
            with open(_MODEL_PATH, "rb") as f:
                data = pickle.load(f)
            self.model = data["model"]
            self.feature_importances = data.get("feature_importances", {})
            self.n_samples = data.get("n_samples", 0)
            self.trained = True
            return True
        except Exception as e:
            logger.warning("Failed to load ML model: %s", e)
            return False

    def predict_proba(self, row: dict) -> float:
        """Return attendance probability (0-1) for a single event row."""
        if not self.trained or self.model is None:
            return 0.5
        features = [_extract_features(row)]
        try:
            proba = self.model.predict_proba(features)[0][1]
            return float(proba)
        except Exception:
            return 0.5

    def predict_score(self, row: dict) -> float:
        """Return ML score 0-100 for a single event row."""
        return round(self.predict_proba(row) * 100, 1)

    def get_top_features(self) -> list[tuple[str, float]]:
        """Return features sorted by absolute importance."""
        return sorted(
            self.feature_importances.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
