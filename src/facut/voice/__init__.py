"""Consent-gated multi-profile digital voice subsystem."""

from .models import ConsentRecord, VoiceProfile, VoiceSample, VoiceSampleCandidate
from .providers import configure_provider, provider_status, synthesize_with_provider
from .presets import VOICE_STYLE_PRESETS, validate_voice_styles, voice_style_catalog
from .qc import validate_voice_profile, validate_voice_samples
from .recorder import RecordingSession, RecordingStudioServer
from .scripts import build_recording_plan
from .store import VoiceProfileStore, default_voice_home

__all__ = [
    "ConsentRecord",
    "VoiceProfile",
    "VoiceProfileStore",
    "VoiceSample",
    "VoiceSampleCandidate",
    "VOICE_STYLE_PRESETS",
    "RecordingSession",
    "RecordingStudioServer",
    "build_recording_plan",
    "configure_provider",
    "default_voice_home",
    "provider_status",
    "synthesize_with_provider",
    "validate_voice_styles",
    "validate_voice_profile",
    "validate_voice_samples",
    "voice_style_catalog",
]
