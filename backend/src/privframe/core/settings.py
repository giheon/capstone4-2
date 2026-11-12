"""Credentials and paths for the 2.0 core, read from the environment or the repository .env file.

Key names follow each provider's own convention (OPENROUTER_API_KEY, LANGSMITH_API_KEY …) so the same .env
works for LangChain/LangSmith tooling; PrivFrame-specific paths use the PRIVFRAME_ prefix.
"""
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[4]


class CoreSettings(BaseSettings):
    # an entry left empty in .env ("ANTHROPIC_API_KEY=") counts as not set
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", populate_by_name=True, env_ignore_empty=True)

    openrouter_api_key: SecretStr | None = Field(None, validation_alias="OPENROUTER_API_KEY")
    anthropic_api_key: SecretStr | None = Field(None, validation_alias="ANTHROPIC_API_KEY")
    gemini_api_key: SecretStr | None = Field(None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    langsmith_api_key: SecretStr | None = Field(None, validation_alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field("privframe-dev", validation_alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str | None = Field(None, validation_alias="LANGSMITH_ENDPOINT")
    law_oc: SecretStr | None = Field(None, validation_alias="LAW_OC")
    pipeline_dir: Path = Field(ROOT / "configs" / "pipeline", validation_alias="PRIVFRAME_PIPELINE_DIR")
    data_dir: Path = Field(ROOT / "data", validation_alias="PRIVFRAME_DATA_DIR")
    # where local models run: auto picks Apple MPS or CUDA when present, else CPU
    torch_device: str = Field("auto", validation_alias="PRIVFRAME_TORCH_DEVICE")
    # ask OpenRouter to route only to zero-data-retention endpoints (narrows the provider list; see R1 · R4)
    openrouter_zdr: bool = Field(False, validation_alias="PRIVFRAME_OPENROUTER_ZDR")

    def present(self) -> dict[str, bool]:
        """Which credentials are configured, without exposing their values."""
        return {
            "OPENROUTER_API_KEY": self.openrouter_api_key is not None,
            "ANTHROPIC_API_KEY": self.anthropic_api_key is not None,
            "LANGSMITH_API_KEY": self.langsmith_api_key is not None,
            "LAW_OC": self.law_oc is not None,
            "GEMINI_API_KEY": self.gemini_api_key is not None,
        }


def secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None
