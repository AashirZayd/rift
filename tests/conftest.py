"""Shared pytest configuration: Hypothesis profiles (SPEC §22)."""

import os

from hypothesis import settings

settings.register_profile("ci", max_examples=500, derandomize=True, deadline=None)
settings.register_profile("dev", max_examples=50)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
