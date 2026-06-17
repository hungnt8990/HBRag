"""Create Qdrant payload indexes used by enriched embedding metadata.

The application also tries to create these on startup/indexing. This script is
provided for manual one-off maintenance when you want to prepare an existing
collection before re-indexing.
"""
from __future__ import annotations

import asyncio

from qdrant_client.models import PayloadSchemaType

from app.services.vector_store import get_vector_store

KEYWORD_FIELDS = [
    "document_id",
    "owner_org_id",
    "owner_org_path",
    "scope",
    "classification",
    "business_domains",
    "project_codes",
    "allowed_org_paths",
    "allowed_role_names",
    "allowed_group_codes",
    "allowed_user_ids",
    "denied_org_paths",
    "denied_role_names",
    "denied_group_codes",
    "denied_user_ids",
    "identifiers",
    "doc_codes",
    "dates",
    "platform",
    "phase",
    "change_type",
    "content_type",
    "change_topic",
    "screen_names",
]


async def main() -> None:
    store = get_vector_store()
    client = store._client  # maintenance script: use the configured client directly
    for field in KEYWORD_FIELDS:
        try:
            await client.create_payload_index(
                collection_name=store.collection_name,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
                wait=True,
            )
            print(f"created payload index: {field}")
        except Exception as exc:  # Qdrant raises when index already exists
            message = str(exc).casefold()
            if "already" in message or "exists" in message:
                print(f"payload index already exists: {field}")
                continue
            raise


if __name__ == "__main__":
    asyncio.run(main())
