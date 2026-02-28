"""
analysis/sentiment.py — Sentiment analysis engine for news and social media.

Uses VADER (Valence Aware Dictionary and sEntiment Reasoner) for fast,
dictionary-based sentiment scoring. No model download or GPU required.

VADER scores are normalized from the native [-1, 1] compound range to
[0, 100] where:
  - 0-45   = strongly bearish
  - 45-55  = neutral
  - 55-100 = strongly bullish

A future upgrade path to FinBERT (transformer-based financial NLP) is
supported via the SENTIMENT_MODEL config option.

Usage:
    analyzer = SentimentAnalyzer()
    score = analyzer.score_text("Bitcoin surges to new all-time high!")
    result = analyzer.analyze_market("kalshi:KXBTC-25DEC", "crypto", headlines)
"""

from __future__ import annotations

import re
from typing import List, Optional


def _load_vader():
    """Lazy-load VADER to avoid import errors if not installed yet."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return SentimentIntensityAnalyzer()
    except ImportError:
        return None


# Category-specific keyword boosters — these words carry extra weight
# in prediction market contexts that VADER's general dictionary misses.
DOMAIN_BOOSTERS: dict = {
    "sports": {
        "injured": -0.4, "injury": -0.3, "questionable": -0.2,
        "out": -0.15, "doubtful": -0.3, "suspended": -0.35,
        "starting": 0.2, "active": 0.15, "cleared": 0.25,
        "dominant": 0.2, "struggling": -0.2, "slump": -0.25,
    },
    "crypto": {
        "halt": -0.4, "crash": -0.5, "dump": -0.4, "rekt": -0.45,
        "moon": 0.35, "rally": 0.3, "surge": 0.35, "adoption": 0.25,
        "sec": -0.1, "ban": -0.4, "hack": -0.5, "exploit": -0.45,
        "etf": 0.3, "institutional": 0.2, "approval": 0.35,
        "fud": -0.2, "bullish": 0.35, "bearish": -0.35,
    },
    "weather": {
        "severe": -0.3, "warning": -0.25, "watch": -0.15,
        "record": 0.1, "extreme": -0.2, "unseasonable": -0.1,
        "mild": 0.1, "clear": 0.15, "sunny": 0.1,
    },
}


class SentimentAnalyzer:
    """
    Sentiment scoring engine combining VADER with domain-specific keyword
    boosting for prediction market context.
    """

    def __init__(self) -> None:
        self._vader = _load_vader()
        self._vader_available = self._vader is not None

    def score_text(self, text: str, category: str = "") -> float:
        """
        Score a single piece of text.

        Returns:
            float in [0, 100] where 50 = neutral, >50 = bullish, <50 = bearish
        """
        if not text or not text.strip():
            return 50.0

        text_clean = text.strip().lower()

        # VADER base score
        if self._vader_available:
            scores = self._vader.polarity_scores(text)
            compound = scores["compound"]  # in [-1, 1]
        else:
            # Fallback: simple keyword counting if VADER not available
            compound = self._simple_keyword_score(text_clean)

        # Apply domain-specific boosters
        if category and category.lower() in DOMAIN_BOOSTERS:
            boosters = DOMAIN_BOOSTERS[category.lower()]
            for word, boost in boosters.items():
                if re.search(r"\b" + re.escape(word) + r"\b", text_clean):
                    compound = max(-1.0, min(1.0, compound + boost))

        # Normalize from [-1, 1] to [0, 100]
        return round((compound + 1.0) / 2.0 * 100.0, 2)

    def score_batch(self, texts: List[str], category: str = "") -> float:
        """
        Score a list of texts and return the weighted average.

        More recent texts (end of list) are weighted more heavily
        using exponential decay with factor 0.9.
        """
        if not texts:
            return 50.0

        scores = [self.score_text(t, category) for t in texts]

        # Recency weighting: last item gets weight 1.0, prior items decay by 0.9
        n = len(scores)
        weights = [0.9 ** (n - 1 - i) for i in range(n)]
        total_weight = sum(weights)

        if total_weight <= 0:
            return 50.0

        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return round(weighted_sum / total_weight, 2)

    def analyze_market(
        self,
        market_id: str,
        category: str,
        news_items: List[str],
        additional_context: Optional[str] = None,
    ) -> dict:
        """
        Produce a full sentiment analysis result for a market.

        Args:
            market_id: Market identifier (for logging)
            category: 'sports' | 'crypto' | 'weather'
            news_items: List of headlines or text snippets
            additional_context: Optional extra text (e.g. analyst note)

        Returns:
            {
                'sentiment_score': float,    # 0-100
                'direction': str,            # 'bullish' | 'bearish' | 'neutral'
                'source_count': int,
                'top_items': List[str],      # first 3 items for display
                'score_distribution': dict,  # {bullish, neutral, bearish} counts
                'confidence': float,         # 0-100, higher when more sources agree
            }
        """
        all_items = list(news_items)
        if additional_context:
            all_items.append(additional_context)

        if not all_items:
            return {
                "sentiment_score": 50.0,
                "direction": "neutral",
                "source_count": 0,
                "top_items": [],
                "score_distribution": {"bullish": 0, "neutral": 0, "bearish": 0},
                "confidence": 0.0,
            }

        # Score each item individually for distribution analysis
        individual_scores = [self.score_text(item, category) for item in all_items]

        # Aggregate score with recency weighting
        aggregate_score = self.score_batch(all_items, category)

        # Direction determination
        if aggregate_score > 58:
            direction = "bullish"
        elif aggregate_score < 42:
            direction = "bearish"
        else:
            direction = "neutral"

        # Score distribution — how many sources agree?
        distribution = {"bullish": 0, "neutral": 0, "bearish": 0}
        for s in individual_scores:
            if s > 58:
                distribution["bullish"] += 1
            elif s < 42:
                distribution["bearish"] += 1
            else:
                distribution["neutral"] += 1

        # Confidence = % of sources agreeing with final direction
        total = len(individual_scores)
        agreeing = distribution[direction]
        confidence = (agreeing / total * 100.0) if total > 0 else 0.0

        # Boost confidence when more sources are available
        if total >= 5:
            confidence = min(100.0, confidence * 1.1)

        return {
            "sentiment_score": aggregate_score,
            "direction": direction,
            "source_count": total,
            "top_items": all_items[:3],
            "score_distribution": distribution,
            "confidence": round(confidence, 1),
        }

    def _simple_keyword_score(self, text: str) -> float:
        """
        Fallback scorer when VADER is unavailable.
        Simple positive/negative word counting. Returns [-1, 1].
        """
        positive_words = {
            "good", "great", "win", "won", "positive", "up", "rise",
            "gain", "profit", "success", "strong", "high", "record",
            "best", "beat", "exceed", "outperform", "surpass",
        }
        negative_words = {
            "bad", "loss", "lost", "negative", "down", "fall", "drop",
            "fail", "weak", "low", "miss", "underperform", "injury",
            "suspend", "cancel", "crash", "decline",
        }

        words = re.findall(r"\b\w+\b", text.lower())
        pos = sum(1 for w in words if w in positive_words)
        neg = sum(1 for w in words if w in negative_words)
        total = pos + neg

        if total == 0:
            return 0.0
        return (pos - neg) / total
