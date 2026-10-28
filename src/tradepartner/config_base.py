"""Config definitions shared by every section module (R1, issue #2512)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _ClosedConfig(BaseModel):
    """Shared validation for early configuration sections."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


# Every Phase 3 section rejects unknown keys and non-finite floats. A hypothesis file pins
# `strategy.*` and `costs.*` (spec req 10), so a misspelt key must fail rather than fall back
# silently to the default, and a NaN or infinite value must fail rather than turn a
# result into NaN. Errors hide the input value by default (#1093), as `Settings` does:
# a caller that knows its input holds no secret (`execution/`'s frozen costs and risk)
# puts it back with `render_validation_errors`.
_PHASE3_MODEL_CONFIG = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
