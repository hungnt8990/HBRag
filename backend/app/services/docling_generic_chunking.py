"""Compatibility wrapper for the generic Docling chunking primitives.

The old implementation file is kept for backward-compatible imports/tests, but the
application now exposes the pipeline as Docling router v1. Specialized chunkers live
under app.services.chunkers and the generic Docling functions are imported through
this module in new code.
"""

from app.services.docling_v6_chunking import *  # noqa: F401,F403
