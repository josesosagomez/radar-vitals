"""Development-only live movement recovery and extended breathing support.

The package is opt-in.  The calibrated working demo continues to use the legacy
launcher path unless ``development_motion.enabled`` is explicitly true and its
hash-bound calibration record passes preflight validation.
"""

from .config import LiveMotionSettings, validate_live_motion_preflight

__all__ = ["LiveMotionSettings", "validate_live_motion_preflight"]
