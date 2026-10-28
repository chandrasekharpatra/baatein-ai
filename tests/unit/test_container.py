from injector import Module, provider, singleton

from baatein.config import Settings
from baatein.container import build_root
from baatein.voice.languages import LanguageProfile, LanguageRegistry, ProviderSpec
from baatein.voice.providers import VoicePipelineFactory


def test_root_binds_given_settings() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert build_root(settings=settings).get(Settings) is settings


def test_pipeline_factory_is_singleton() -> None:
    injector = build_root(settings=Settings(_env_file=None))  # type: ignore[call-arg]

    assert injector.get(VoicePipelineFactory) is injector.get(VoicePipelineFactory)


def test_extra_modules_override_defaults() -> None:
    custom = LanguageRegistry(
        [LanguageProfile("klingon", ProviderSpec("x"), ProviderSpec("y"), "qapla")]
    )

    class Override(Module):
        @singleton
        @provider
        def languages(self) -> LanguageRegistry:
            return custom

    injector = build_root(Override(), settings=Settings(_env_file=None))  # type: ignore[call-arg]

    assert injector.get(LanguageRegistry) is custom
