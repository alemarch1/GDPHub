# Centralized, typed configuration for GDPHub.
#
# Why this module exists
# ----------------------
# Defaults used to live in three places at once:
#   1) ``api.py::_seed_defaults`` (large inline dict for fresh-DB seeding)
#   2) ``seed_config.py`` (one-shot import from a legacy ``config.json``)
#   3) Per-script fallbacks scattered across each pipeline step
#
# This file is the **single source of truth**. ``Settings.seed_dict()`` returns
# the canonical default payload used by ``api.py`` to bootstrap a fresh DB,
# and the Pydantic models below provide a typed accessor for callers that
# want validation. The legacy ``get_config()`` API continues to work
# unchanged — the new layer is additive.

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

# --- TYPED SECTION MODELS -------------------------------------------------


class GpuProfile(BaseModel):
    """Ollama runtime parameters for a given VRAM tier."""
    num_predict: int = 64
    temperature: float = 0.2
    num_ctx: int = 2048
    num_batch: int = 256
    top_p: float = 0.9
    top_k: int = 40


class MailExtractSettings(BaseModel):
    """Settings stored under the ``"extract_mail"`` config key."""
    query: str = ""
    max_emails: int = 50
    import_override_days: int = 0
    delete_after_processing: bool = False
    import_override_ignore_processed: bool = False


class TextExtractSettings(BaseModel):
    """Settings stored under the ``"extract_text"`` config key."""
    tesseract_path: str = ""
    max_workers: int = 4


class ClassifySettings(BaseModel):
    """Settings stored under the ``"classify_text"`` config key."""
    ollama_url: str = "http://localhost:11434"
    ollama_model_default: str = "gemma3:4b"
    title_max_length: int = 500
    text_max_length: int = 1500
    timeout_seconds: int = 60
    api_request_timeout: int = 45
    ollama_options: GpuProfile = Field(default_factory=GpuProfile)


class RopaExtractSettings(BaseModel):
    """Settings stored under the ``"extract_ropa"`` config key."""
    ropa_folder: str = "./data/ROPA"


class Settings(BaseModel):
    """Root configuration model. Maps one-to-one onto rows in the
    ``Configuration`` table; each top-level field corresponds to a single
    JSON-encoded value keyed by the field's storage alias.
    """
    active_source: str = "local"
    input_folder: str = "./data/input"
    database_folder: str = "./data/output"
    log_folder: str = "./logs"
    log_level: str = "INFO"
    gpu_profile: str = "12gb"

    gpu_profiles: Dict[str, GpuProfile] = Field(
        default_factory=lambda: {
            "8gb":  GpuProfile(num_ctx=1536, num_batch=128),
            "12gb": GpuProfile(num_ctx=2048, num_batch=256),
            "24gb": GpuProfile(num_ctx=4096, num_batch=512),
        }
    )

    mail: MailExtractSettings = Field(default_factory=MailExtractSettings)
    extract_text: TextExtractSettings = Field(default_factory=TextExtractSettings)
    classify_text: ClassifySettings = Field(default_factory=ClassifySettings)
    extract_ropa: RopaExtractSettings = Field(default_factory=RopaExtractSettings)

    # The DB stores some sections under historical keys with file-extension
    # suffixes (e.g. ``"extract_mail"``). The mapping below is the only
    # place that knows the on-disk key shape.
    @staticmethod
    def storage_key_for(field: str) -> str:
        return {
            "mail":          "extract_mail",
            "extract_text":  "extract_text",
            "classify_text": "classify_text",
            "extract_ropa":  "extract_ropa",
        }.get(field, field)


def _model_dump(obj: BaseModel) -> Dict[str, Any]:
    """Pydantic v1/v2-compatible dict export."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()  # type: ignore[attr-defined]


# --- SEED PAYLOAD ---------------------------------------------------------


def seed_dict() -> Dict[str, Any]:
    """Return the canonical defaults used to populate a freshly-created DB.

    Output keys mirror the historical layout of the ``Configuration`` table —
    importantly, sections like ``"extract_mail"``, ``"extract_text"``,
    ``"classify_text"``, ``"extract_ropa"`` keep their original keys for
    on-disk compatibility with existing installations.
    """
    s = Settings()
    return {
        "active_source": s.active_source,
        "input_folder": s.input_folder,
        "database_folder": s.database_folder,
        "log_folder": s.log_folder,
        "log_level": s.log_level,
        "gpu_profile": s.gpu_profile,
        "gpu_profiles": {k: _model_dump(v) for k, v in s.gpu_profiles.items()},
        Settings.storage_key_for("mail"): _model_dump(s.mail),
        Settings.storage_key_for("extract_text"): _model_dump(s.extract_text),
        Settings.storage_key_for("classify_text"): _model_dump(s.classify_text),
        Settings.storage_key_for("extract_ropa"): _model_dump(s.extract_ropa),
    }


def gpu_profile_migration_pairs() -> list[tuple[str, Any]]:
    """The (key, default) pairs added on existing DBs that pre-date the GPU
    profile feature. Mirrors the historical migration block in ``api.py``.
    """
    s = Settings()
    return [
        ("gpu_profile", s.gpu_profile),
        ("gpu_profiles", {k: _model_dump(v) for k, v in s.gpu_profiles.items()}),
    ]
