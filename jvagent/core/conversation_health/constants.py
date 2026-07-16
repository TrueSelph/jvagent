"""Constants for Conversation Health."""

from typing import Dict, List, Tuple

DIMENSIONS: Tuple[str, ...] = (
    "quality",
    "responsiveness",
    "friction",
    "integrity",
)

# Issue code → (primary dimension, default severity)
# Severity: low | medium | high
ISSUE_CATALOG: Dict[str, Tuple[str, str]] = {
    "slow_response": ("responsiveness", "medium"),
    "empty_or_trivial_response": ("quality", "high"),
    "idk_response": ("quality", "high"),
    "unanswered_question": ("quality", "high"),
    "human_request": ("friction", "high"),
    "repetition_loop": ("integrity", "high"),
    "prompt_injection_attempt": ("integrity", "high"),
    # Critical signals
    "toxicity": ("friction", "high"),
    "execution_failure": ("quality", "high"),
    # AI-only codes (may be written by AI Evaluation)
    "contradiction": ("integrity", "high"),
    "hallucination": ("integrity", "high"),
    "negative_sentiment": ("friction", "medium"),
    "answer_inadequate": ("quality", "high"),
}

DEFAULT_SEVERITY_DEDUCTIONS: Dict[str, int] = {
    "low": 10,
    "medium": 20,
    "high": 30,
}

# Latency bands (seconds) → severity for slow_response
DEFAULT_LATENCY_BANDS: List[Tuple[float, str]] = [
    (3.0, "low"),
    (8.0, "medium"),
    (15.0, "high"),
]

DEFERRED_TASK_TYPE = "conversation_health.ai_evaluate"

AI_BUCKET_CRITICAL = "C"
AI_BUCKET_OPTIMIZATION = "B"
AI_BUCKET_BLIND_SPOT = "A"
