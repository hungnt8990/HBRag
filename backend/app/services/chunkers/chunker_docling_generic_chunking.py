"""Compatibility wrapper for the generic Docling chunking primitives.

The application exposes the pipeline as Docling router v1. Generic Docling
functions are re-exported here so chunking code imports them from the chunkers
package alongside the specialized chunkers.
"""

from app.services.chunkers.chunker_docling_v6_chunking import *  # noqa: F401,F403
