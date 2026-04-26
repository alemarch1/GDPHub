# Centralized Microsoft Presidio configuration for PII detection and anonymization.
# Replaces custom dictionary-based name masking with a production-grade NLP pipeline
# backed by spaCy NER models. Supports dual-language analysis (Italian + English)
# and custom recognizers for EU license plates and Italian fiscal codes.

import logging
from typing import List, Tuple, Optional

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from presidio_anonymizer.entities import RecognizerResult as AnonymizerRecognizerResult

# --- ENTITY CONFIGURATION ---

# All PII entity types to detect during analysis
ENTITIES_TO_DETECT = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "IT_FISCAL_CODE",
    "LICENSE_PLATE",
]

# Anonymization operators: each entity type gets a descriptive fixed label
OPERATORS = {
    "PERSON":         OperatorConfig("replace", {"new_value": "<PERSON>"}),
    "EMAIL_ADDRESS":  OperatorConfig("replace", {"new_value": "<EMAIL>"}),
    "PHONE_NUMBER":   OperatorConfig("replace", {"new_value": "<PHONE>"}),
    "IBAN_CODE":      OperatorConfig("replace", {"new_value": "<IBAN>"}),
    "CREDIT_CARD":    OperatorConfig("replace", {"new_value": "<CREDIT_CARD>"}),
    "IP_ADDRESS":     OperatorConfig("replace", {"new_value": "<IP_ADDRESS>"}),
    "IT_FISCAL_CODE": OperatorConfig("replace", {"new_value": "<FISCAL_CODE>"}),
    "LICENSE_PLATE":  OperatorConfig("replace", {"new_value": "<LICENSE_PLATE>"}),
    "DEFAULT":        OperatorConfig("replace", {"new_value": "<PII>"}),
}

# --- CUSTOM RECOGNIZERS: EU LICENSE PLATES ---

# These patterns cover Italian and major EU plate formats.
# Ported from the original regex collection in 1_extract_text.py.
_LICENSE_PLATE_PATTERNS = [
    Pattern("it_plate",       r"\b[A-Z]{2}\d{3}[A-Z]{2}\b",                                          score=0.7),
    Pattern("de_plate",       r"\b[A-ZÄÖÜ]{1,3}-[A-Z]{1,2} \d{1,4}\b",                               score=0.6),
    Pattern("uk_plate_1",     r"\b[A-Z]{2}\d{2} [A-Z]{3}\b",                                         score=0.6),
    Pattern("uk_plate_2",     r"\b[A-Z]{1,2}\d{2}[A-Z]{3}\b",                                        score=0.6),
    Pattern("fr_plate",       r"\b[A-Z]{2}-\d{3}-[A-Z]{2}\b",                                        score=0.7),
    Pattern("es_plate",       r"\b\d{4} [A-Z]{3}\b",                                                 score=0.5),
    Pattern("generic_3_3",    r"\b[A-Z]{3}-\d{3}\b",                                                 score=0.5),
    Pattern("generic_2_5",    r"\b[A-Z]{2}\d{5}\b",                                                  score=0.5),
    Pattern("nl_plate",       r"\b(?:[A-Z]{2}-\d{2}-\d{2}|\d{2}-[A-Z]{2}-\d{2}|\d{2}-\d{2}-[A-Z]{2})\b", score=0.6),
    Pattern("generic_mixed",  r"\b[A-Z]{1,3}\d{3}[A-Z]{1,3}\b",                                     score=0.5),
    Pattern("generic_2_num",  r"\b[A-Z]{2} \d{1,6}\b",                                               score=0.4),
    Pattern("generic_dash",   r"\b[A-Z]{1,2}-\d{1,4}-[A-Z]{1,2}\b",                                  score=0.5),
    Pattern("be_plate",       r"\b\d{2}-[A-Z]{2}-\d{2}\b",                                           score=0.6),
    Pattern("be_plate_2",     r"\b\d{2}-[A-Z]{1,2}-\d{1,6}\b",                                       score=0.5),
    Pattern("generic_4_dash", r"\b[A-Z]{2}-\d{4}-[A-Z]{2}\b",                                        score=0.6),
    Pattern("generic_4",      r"\b[A-Z]{1,2}\d{4}[A-Z]{1,2}\b",                                     score=0.5),
    Pattern("generic_2_4",    r"\b[A-Z]{2}\d{4}\b",                                                  score=0.4),
    Pattern("fl_plate",       r"\bFL-\d{1,6}\b",                                                     score=0.6),
]


def _build_license_plate_recognizer() -> PatternRecognizer:
    """Creates a PatternRecognizer for European license plate formats."""
    return PatternRecognizer(
        supported_entity="LICENSE_PLATE",
        patterns=_LICENSE_PLATE_PATTERNS,
        supported_language="it",  # Will be added for both languages
        name="EuLicensePlateRecognizer",
    )


def _build_license_plate_recognizer_en() -> PatternRecognizer:
    """Same recognizer registered for English language analysis."""
    return PatternRecognizer(
        supported_entity="LICENSE_PLATE",
        patterns=_LICENSE_PLATE_PATTERNS,
        supported_language="en",
        name="EuLicensePlateRecognizerEN",
    )

# --- ENGINE FACTORIES ---

def create_analyzer() -> AnalyzerEngine:
    """
    Creates and returns a fully configured Presidio AnalyzerEngine.

    - Uses spaCy NLP backend with it_core_news_lg (Italian) and en_core_web_lg (English)
    - Registers custom recognizers for EU license plates
    - Supports dual-language analysis (it + en)
    """
    # Configure NLP engine with both Italian and English models
    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [
            {"lang_code": "it", "model_name": "it_core_news_lg"},
            {"lang_code": "en", "model_name": "en_core_web_lg"},
        ],
    }

    provider = NlpEngineProvider(nlp_configuration=nlp_configuration)
    nlp_engine = provider.create_engine()

    # Build the analyzer with dual-language support
    analyzer = AnalyzerEngine(
        nlp_engine=nlp_engine,
        supported_languages=["it", "en"],
    )

    # Register custom recognizers
    analyzer.registry.add_recognizer(_build_license_plate_recognizer())
    analyzer.registry.add_recognizer(_build_license_plate_recognizer_en())

    logging.info("Presidio AnalyzerEngine initialized (it + en, with custom license plate recognizer).")
    return analyzer


def create_anonymizer() -> AnonymizerEngine:
    """Creates and returns a Presidio AnonymizerEngine."""
    return AnonymizerEngine()

# --- MAIN ANONYMIZATION INTERFACE ---

def anonymize_text(
    text: str,
    analyzer: AnalyzerEngine,
    anonymizer: AnonymizerEngine,
    language: str = "it",
    score_threshold: float = 0.5
) -> Tuple[str, bool]:
    """
    Analyzes text for PII and anonymizes all detected entities.

    Args:
        text: The raw text to analyze and anonymize.
        analyzer: A configured AnalyzerEngine instance.
        anonymizer: A configured AnonymizerEngine instance.
        language: Primary language for analysis ("it" or "en").
        score_threshold: Minimum confidence score for entity detection.

    Returns:
        A tuple of (anonymized_text, pii_was_detected).
        pii_was_detected is True if at least one PII entity was found and masked.
    """
    if not text or not text.strip():
        return text, False

    try:
        # Run analysis on the primary language
        results = analyzer.analyze(
            text=text,
            language=language,
            entities=ENTITIES_TO_DETECT,
            score_threshold=score_threshold,
        )

        # Also run English analysis as fallback (catches English names, emails etc.)
        # Exclude IT_FISCAL_CODE from English pass (no recognizer exists for it in EN)
        if language != "en":
            en_entities = [e for e in ENTITIES_TO_DETECT if e != "IT_FISCAL_CODE"]
            en_results = analyzer.analyze(
                text=text,
                language="en",
                entities=en_entities,
                score_threshold=score_threshold,
            )
            # Merge results, but filter out cross-language NER confusion:
            # - Skip entities that span > 60% of the text (full-sentence misclassification)
            # - Skip entities that overlap with existing primary-language results
            existing_spans = {(r.start, r.end) for r in results}
            max_span_len = len(text) * 0.6
            for r in en_results:
                span_len = r.end - r.start
                if span_len > max_span_len:
                    continue  # Likely cross-language NER confusion
                if (r.start, r.end) not in existing_spans:
                    # Also check for partial overlap with existing results
                    overlaps = any(
                        not (r.end <= er.start or r.start >= er.end)
                        for er in results
                    )
                    if not overlaps:
                        results.append(r)

        if not results:
            return text, False

        # Convert analyzer RecognizerResult to anonymizer RecognizerResult
        # to satisfy type invariance (the two libraries define separate classes)
        anonymizer_results: List[AnonymizerRecognizerResult] = [
            AnonymizerRecognizerResult(
                entity_type=r.entity_type,
                start=r.start,
                end=r.end,
                score=r.score,
            )
            for r in results
        ]

        anonymized = anonymizer.anonymize(
            text=text,
            analyzer_results=anonymizer_results,
            operators=OPERATORS,
        )
        return anonymized.text, True

    except Exception as e:
        logging.error(f"Presidio anonymization error: {e}")
        # On error, return text unmodified (fail-open to avoid data loss)
        return text, False
