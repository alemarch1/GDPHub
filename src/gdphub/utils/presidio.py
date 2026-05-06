# Centralized Microsoft Presidio configuration for PII detection and anonymization.
# Replaces custom dictionary-based name masking with a production-grade NLP pipeline
# backed by spaCy NER models. Supports dual-language analysis (Italian + English)
# and custom recognizers for EU license plates and Italian fiscal codes.

import logging
import os
import warnings
from typing import List, Tuple

# Suppress noisy third-party warnings:
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
warnings.filterwarnings("ignore", message=".*CUDA path could not be detected.*")

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from presidio_anonymizer.entities import RecognizerResult as AnonymizerRecognizerResult

# --- ENTITY CONFIGURATION ---

# All PII entity types to detect during analysis (global + locale-specific)
ENTITIES_TO_DETECT = [
    # Global
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION",
    "CREDIT_CARD", "CRYPTO", "IBAN_CODE", "IP_ADDRESS", "MAC_ADDRESS",
    "DATE_TIME", "MEDICAL_LICENSE", "NRP", "URL",
    # Italy
    "IT_FISCAL_CODE", "IT_VAT_CODE", "IT_IDENTITY_CARD",
    "IT_PASSPORT", "IT_DRIVER_LICENSE",
    # Spain (Europe)
    "ES_NIF",
    # United Kingdom (Europe)
    "UK_NHS",
    # United States
    "US_SSN", "US_PASSPORT", "US_DRIVER_LICENSE",
    "US_BANK_NUMBER", "US_ITIN", "US_MBI", "US_NPI",
    # Custom
    "LICENSE_PLATE",
]

# Anonymization operators: each entity type gets a descriptive fixed label
OPERATORS = {
    # Global
    "PERSON":            OperatorConfig("replace", {"new_value": "<PERSON>"}),
    "EMAIL_ADDRESS":     OperatorConfig("replace", {"new_value": "<EMAIL>"}),
    "PHONE_NUMBER":      OperatorConfig("replace", {"new_value": "<PHONE>"}),
    "LOCATION":          OperatorConfig("replace", {"new_value": "<LOCATION>"}),
    "CREDIT_CARD":       OperatorConfig("replace", {"new_value": "<CREDIT_CARD>"}),
    "CRYPTO":            OperatorConfig("replace", {"new_value": "<CRYPTO>"}),
    "IBAN_CODE":         OperatorConfig("replace", {"new_value": "<IBAN>"}),
    "IP_ADDRESS":        OperatorConfig("replace", {"new_value": "<IP_ADDRESS>"}),
    "MAC_ADDRESS":       OperatorConfig("replace", {"new_value": "<MAC_ADDRESS>"}),
    "DATE_TIME":         OperatorConfig("replace", {"new_value": "<DATE>"}),
    "MEDICAL_LICENSE":   OperatorConfig("replace", {"new_value": "<MEDICAL_LICENSE>"}),
    "NRP":               OperatorConfig("replace", {"new_value": "<NRP>"}),
    "URL":               OperatorConfig("replace", {"new_value": "<URL>"}),
    # Italy
    "IT_FISCAL_CODE":    OperatorConfig("replace", {"new_value": "<FISCAL_CODE>"}),
    "IT_VAT_CODE":       OperatorConfig("replace", {"new_value": "<VAT_CODE>"}),
    "IT_IDENTITY_CARD":  OperatorConfig("replace", {"new_value": "<ID_CARD>"}),
    "IT_PASSPORT":       OperatorConfig("replace", {"new_value": "<PASSPORT>"}),
    "IT_DRIVER_LICENSE": OperatorConfig("replace", {"new_value": "<DRIVER_LICENSE>"}),
    # Spain
    "ES_NIF":            OperatorConfig("replace", {"new_value": "<NIF>"}),
    # UK
    "UK_NHS":            OperatorConfig("replace", {"new_value": "<NHS>"}),
    # US
    "US_SSN":            OperatorConfig("replace", {"new_value": "<SSN>"}),
    "US_PASSPORT":       OperatorConfig("replace", {"new_value": "<PASSPORT>"}),
    "US_DRIVER_LICENSE": OperatorConfig("replace", {"new_value": "<DRIVER_LICENSE>"}),
    "US_BANK_NUMBER":    OperatorConfig("replace", {"new_value": "<BANK_ACCOUNT>"}),
    "US_ITIN":           OperatorConfig("replace", {"new_value": "<ITIN>"}),
    "US_MBI":            OperatorConfig("replace", {"new_value": "<MBI>"}),
    "US_NPI":            OperatorConfig("replace", {"new_value": "<NPI>"}),
    # Custom
    "LICENSE_PLATE":     OperatorConfig("replace", {"new_value": "<LICENSE_PLATE>"}),
    "DEFAULT":           OperatorConfig("replace", {"new_value": "<PII>"}),
}

# --- CUSTOM RECOGNIZERS: EU LICENSE PLATES ---

# These patterns cover Italian and major EU plate formats.
# Ported from the original regex collection in extract_text.py.
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

# --- CUSTOM RECOGNIZERS: MAC ADDRESS (truly global, hardware identifier) ---

_MAC_ADDRESS_PATTERNS = [
    # Standard formats: 00:1A:2B:3C:4D:5E or 00-1A-2B-3C-4D-5E
    Pattern("mac_colon_dash", r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b", score=0.85),
    # Cisco-style: 001A.2B3C.4D5E
    Pattern("mac_cisco",      r"\b(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}\b",  score=0.7),
]

# --- CUSTOM RECOGNIZERS: US MBI (Medicare Beneficiary Identifier) ---
# 11-character format: char1=1-9, char4/7/10/11=digits, others=letters from a restricted set
# Letters exclude: B, I, L, O, S, Z (to avoid confusion with digits)
_MBI_LETTER_CLASS = "[AC-HJ-KM-NP-RT-Y]"
_MBI_ALNUM_CLASS  = "[AC-HJ-KM-NP-RT-Y0-9]"
_MBI_CORE = (
    r"[1-9]" + _MBI_LETTER_CLASS + _MBI_ALNUM_CLASS + r"\d"
    + _MBI_LETTER_CLASS + _MBI_ALNUM_CLASS + r"\d"
    + _MBI_LETTER_CLASS + _MBI_LETTER_CLASS + r"\d{2}"
)
# Dashed format: 1AC-D3F-GH45 (4-3-4 grouping is the official CMS layout)
_MBI_DASHED = (
    r"[1-9]" + _MBI_LETTER_CLASS + _MBI_ALNUM_CLASS + r"\d-"
    + _MBI_LETTER_CLASS + _MBI_ALNUM_CLASS + r"\d-"
    + _MBI_LETTER_CLASS + _MBI_LETTER_CLASS + r"\d{2}"
)
_US_MBI_PATTERNS = [
    Pattern("us_mbi_compact", r"\b" + _MBI_CORE + r"\b",  score=0.7),
    Pattern("us_mbi_dashed",  r"\b" + _MBI_DASHED + r"\b", score=0.75),
]
_US_MBI_CONTEXT = ["mbi", "medicare", "beneficiary"]

# --- CUSTOM RECOGNIZERS: US NPI (National Provider Identifier) ---
# 10-digit number, first digit is 1 or 2 (entity types). Pure regex has false positives,
# so score is conservative — context words boost confidence.
_US_NPI_PATTERNS = [
    Pattern("us_npi_10digit", r"\b[12]\d{9}\b", score=0.3),
]
_US_NPI_CONTEXT = ["npi", "national provider", "provider identifier"]

# --- CUSTOM RECOGNIZERS: ES_NIF cross-language clone ---
# Presidio bundles EsNifRecognizer at supported_language="es" only.
# Cloning the regex for it+en lets us detect Spanish IDs without loading a Spanish spaCy model.
# NIF format: 8 digits + checksum letter (e.g. "12345678A")
_ES_NIF_PATTERNS = [
    Pattern("es_nif", r"\b\d{8}[A-HJ-NP-TV-Z]\b", score=0.6),
]
_ES_NIF_CONTEXT = ["nif", "dni", "documento", "identidad"]


def _make_pattern_recognizer(
    entity: str, patterns, name: str, language: str, context=None
) -> PatternRecognizer:
    """Helper to create a PatternRecognizer for a given language."""
    return PatternRecognizer(
        supported_entity=entity,
        patterns=patterns,
        supported_language=language,
        name=name,
        context=context,
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

    # Register custom recognizers — license plates (it + en)
    analyzer.registry.add_recognizer(_build_license_plate_recognizer())
    analyzer.registry.add_recognizer(_build_license_plate_recognizer_en())

    # Register MAC_ADDRESS for both languages (truly global, hardware identifier)
    for _lang in ("it", "en"):
        analyzer.registry.add_recognizer(_make_pattern_recognizer(
            "MAC_ADDRESS", _MAC_ADDRESS_PATTERNS, f"MacAddressRecognizer_{_lang.upper()}", _lang
        ))

    # Register US-specific custom recognizers (US_MBI, US_NPI) for the EN pass.
    # We also register them for IT so a stray US identifier in an Italian document is caught.
    for _lang in ("it", "en"):
        analyzer.registry.add_recognizer(_make_pattern_recognizer(
            "US_MBI", _US_MBI_PATTERNS, f"UsMbiRecognizer_{_lang.upper()}", _lang,
            context=_US_MBI_CONTEXT,
        ))
        analyzer.registry.add_recognizer(_make_pattern_recognizer(
            "US_NPI", _US_NPI_PATTERNS, f"UsNpiRecognizer_{_lang.upper()}", _lang,
            context=_US_NPI_CONTEXT,
        ))

    # Register ES_NIF for it + en (Presidio's bundled recognizer is es-only)
    for _lang in ("it", "en"):
        analyzer.registry.add_recognizer(_make_pattern_recognizer(
            "ES_NIF", _ES_NIF_PATTERNS, f"EsNifRecognizer_{_lang.upper()}", _lang,
            context=_ES_NIF_CONTEXT,
        ))

    logging.info(
        "Presidio AnalyzerEngine initialized (it + en) with custom recognizers: "
        "LICENSE_PLATE, MAC_ADDRESS, US_MBI, US_NPI, ES_NIF."
    )
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
        # Exclude IT-only entities from the EN pass (their recognizers are registered only at "it")
        if language != "en":
            _IT_ONLY = {
                "IT_FISCAL_CODE", "IT_VAT_CODE", "IT_IDENTITY_CARD",
                "IT_PASSPORT", "IT_DRIVER_LICENSE",
            }
            en_entities = [e for e in ENTITIES_TO_DETECT if e not in _IT_ONLY]
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
