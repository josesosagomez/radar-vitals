"""M2 prospective acquisition and engineering-preflight contracts.

The package is intentionally separate from :mod:`src.m4.manifest`: M4 schema version 2
describes historical study semantics, while M2 schema version 3 describes the prospective
three-arm design.  Importing this package never opens a prospective reference file.
"""

from .manifest import load_manifest
from .manifest_v3 import ManifestError, Mode, ProspectiveSession

__all__ = ["ManifestError", "Mode", "ProspectiveSession", "load_manifest"]
