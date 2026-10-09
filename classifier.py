"""
Document Classifier
--------------------
Detects the Health Information (HI) type from raw PDF text and filename.
Supports: DiagnosticReport, DischargeSummary, Prescription.
Uses OpenAI for AI‑based classification (optional) with a rule‑based fallback.
"""

import logging
import os
from typing import Dict, Optional

# Conditionally import openai
try:
    import openai
except ImportError:
    openai = None

logger = logging.getLogger(__name__)


# ── Keyword signal dictionaries ───────────────────────────────────────────────

_SIGNALS: Dict[str, Dict[str, int]] = {
    "DiagnosticReport": {
        # High-confidence signals
        "2d echo": 10,
        "echocardiography": 10,
        "echo report": 10,
        "lab report": 10,
        "laboratory report": 10,
        "diagnostic report": 10,
        "pathology report": 10,
        "radiology report": 10,
        "ct scan": 10,
        "mri report": 10,
        "ultrasound": 8,
        "x-ray": 8,
        "m mode measurements": 8,
        "lvef": 7,
        "2d echo finding": 8,
        "haemoglobin": 6,
        "platelet": 6,
        "wbc count": 6,
        "rbc count": 6,
        "urine examination": 6,
        # Medium signals
        "impression": 3,
        "findings": 3,
        "report date": 3,
        "referred by": 3,
    },
    "DischargeSummary": {
        # High-confidence
        "discharge summary": 10,
        "discharge note": 10,
        "date of admission": 10,
        "date of discharge": 10,
        "ip number": 8,
        "ward": 5,
        "medications at discharge": 8,
        "advice on discharge": 8,
        "follow up": 5,
        "chief complaint": 7,
        "history of present illness": 7,
        "operative notes": 6,
        "procedure performed": 6,
        # Medium signals
        "admitted": 4,
        "discharge condition": 5,
        "icd": 4,
    },
    "Prescription": {
        "prescription": 10,
        "rx": 6,
        "tablet": 5,
        "capsule": 5,
        "syrup": 5,
        "sig:": 6,
        "refills": 5,
        "dispense": 5,
        "opd prescription": 8,
    },
}

# Filename extension signals
_FILENAME_SIGNALS: Dict[str, str] = {
    "echo": "DiagnosticReport",
    "lab": "DiagnosticReport",
    "report": "DiagnosticReport",
    "discharge": "DischargeSummary",
    "summary": "DischargeSummary",
    "rx": "Prescription",
    "prescription": "Prescription",
}


class DocumentClassifier:
    """Rule-based HI Type classifier with optional AI fallback."""

    def __init__(self, use_ai: Optional[bool] = None):
        """
        :param use_ai: If True, attempt AI classification first.
                       If None, read from env USE_AI_CLASSIFIER (true/false).
        """
        if use_ai is None:
            use_ai = os.getenv("USE_AI_CLASSIFIER", "false").lower() == "true"
        self.use_ai = use_ai

        # Set OpenAI API key if available
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if self.use_ai and not self.openai_api_key:
            logger.warning("OPENAI_API_KEY not set, disabling AI classifier")
            self.use_ai = False
        if self.use_ai and openai is None:
            logger.warning("openai package not installed, disabling AI classifier")
            self.use_ai = False

    def detect(self, text: str, filename: str = "") -> str:
        """
        Returns the most likely HI type string.
        Falls back to rule-based if AI is disabled or fails.
        """
        if self.use_ai:
            try:
                ai_result = self._detect_with_ai(text)
                if ai_result in ("DiagnosticReport", "DischargeSummary", "Prescription"):
                    logger.info(f"AI classified as {ai_result}")
                    return ai_result
                else:
                    logger.warning(f"AI returned unknown type '{ai_result}', falling back")
            except Exception as e:
                logger.error(f"AI classification failed: {e}, falling back to rules")

        # Fallback to rule-based
        return self._detect_rule_based(text, filename)

    def _detect_with_ai(self, text: str) -> str:
        """Call OpenAI (new API >=1.0.0) to classify the document."""
        # Truncate text to avoid token limits (e.g., first 2000 chars)
        truncated = text[:2000]

        prompt = (
            "You are a medical document classifier. "
            "Classify the following document as one of: DiagnosticReport, DischargeSummary, Prescription.\n"
            "Reply with exactly one word, no punctuation.\n\n"
            f"Document text:\n{truncated}"
        )

        # Use the new OpenAI client interface
        client = openai.OpenAI(api_key=self.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # or gpt-4 if available
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=10,
        )
        result = response.choices[0].message.content.strip().lower()

        # Map variations to expected strings
        if "diagnostic" in result:
            return "DiagnosticReport"
        if "discharge" in result:
            return "DischargeSummary"
        if "prescription" in result:
            return "Prescription"
        return result  # fallback to raw (will be handled by caller)

    def _detect_rule_based(self, text: str, filename: str) -> str:
        """Original keyword-based classifier."""
        text_lower = text.lower()
        filename_lower = filename.lower()

        scores: Dict[str, int] = {hi: 0 for hi in _SIGNALS}

        # Score from text keywords
        for hi_type, keywords in _SIGNALS.items():
            for kw, weight in keywords.items():
                if kw in text_lower:
                    scores[hi_type] += weight

        # Boost from filename
        for token, hi_type in _FILENAME_SIGNALS.items():
            if token in filename_lower:
                scores[hi_type] += 5

        best = max(scores, key=scores.get)
        confidence = scores[best]

        if confidence < 5:
            logger.warning(
                f"Low confidence classification ({confidence}) → defaulting to DiagnosticReport"
            )
            return "DiagnosticReport"

        logger.info(f"Classified as {best} (score={confidence}, all={scores})")
        return best
