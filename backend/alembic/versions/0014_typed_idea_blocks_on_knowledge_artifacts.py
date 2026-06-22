"""Extend knowledge artifacts as typed IdeaBlocks."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_typed_idea_blocks"
down_revision = "0013_add_doffice_raw_documents"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_artifacts", sa.Column("idea_block_type", sa.String(length=64), nullable=True))
    op.add_column("knowledge_artifacts", sa.Column("summary_text", sa.Text(), nullable=True))
    op.add_column(
        "knowledge_artifacts",
        sa.Column(
            "idea_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_artifacts",
        sa.Column(
            "evidence_chunk_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("knowledge_artifacts", sa.Column("scope_key", sa.String(length=512), nullable=True))
    op.add_column("knowledge_artifacts", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("knowledge_artifacts", sa.Column("dedup_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "knowledge_artifacts",
        sa.Column(
            "embedding_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )

    op.execute(
        """
        UPDATE knowledge_artifacts
        SET
            idea_block_type = CASE artifact_type
                WHEN 'document_profile' THEN 'document_identity'
                WHEN 'identifier_lookup' THEN 'document_identity'
                WHEN 'procedure_artifact' THEN 'implementation_plan'
                WHEN 'policy_rule_artifact' THEN 'legal_clause'
                WHEN 'table_row_artifact' THEN 'assignment_table_row'
                WHEN 'person_assignment_artifact' THEN 'assignment_table_row'
                ELSE 'summary_block'
            END,
            idea_metadata = COALESCE(structured_data, '{}'::jsonb)
                || jsonb_build_object('evidence_chunk_ids', COALESCE(source_chunk_ids, '[]'::jsonb)),
            evidence_chunk_ids = COALESCE(source_chunk_ids, '[]'::jsonb),
            content_hash = md5(COALESCE(canonical_text, '')),
            scope_key = lower(concat_ws(
                '|',
                COALESCE(normalized_identifiers->>'document_id', document_id::text),
                COALESCE(structured_data->>'issued_date', structured_data->>'ngay_vb', ''),
                COALESCE(structured_data->>'issuing_org', structured_data->>'issuer', structured_data->>'noi_ban_hanh', ''),
                COALESCE(idea_block_type, artifact_type)
            ))
        WHERE idea_block_type IS NULL
        """
    )
    op.execute(
        """
        UPDATE knowledge_artifacts
        SET dedup_hash = md5(COALESCE(scope_key, '') || '|' || COALESCE(content_hash, ''))
        WHERE dedup_hash IS NULL
        """
    )

    op.create_index(op.f("ix_knowledge_artifacts_idea_block_type"), "knowledge_artifacts", ["idea_block_type"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_scope_key"), "knowledge_artifacts", ["scope_key"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_content_hash"), "knowledge_artifacts", ["content_hash"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_dedup_hash"), "knowledge_artifacts", ["dedup_hash"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_embedding_status"), "knowledge_artifacts", ["embedding_status"], unique=False)
    op.create_index(
        "ix_knowledge_artifacts_idea_metadata_gin",
        "knowledge_artifacts",
        ["idea_metadata"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_artifacts_evidence_chunk_ids_gin",
        "knowledge_artifacts",
        ["evidence_chunk_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_artifacts_canonical_text_fts
        ON knowledge_artifacts
        USING gin (to_tsvector('simple', canonical_text))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_artifacts_idea_metadata_doc_code
        ON knowledge_artifacts ((idea_metadata->>'doc_code'))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_artifacts_idea_metadata_issued_date
        ON knowledge_artifacts ((idea_metadata->>'issued_date'))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_artifacts_idea_metadata_issuing_org
        ON knowledge_artifacts ((idea_metadata->>'issuing_org'))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_artifacts_idea_metadata_issuing_org")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_artifacts_idea_metadata_issued_date")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_artifacts_idea_metadata_doc_code")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_artifacts_canonical_text_fts")
    op.drop_index("ix_knowledge_artifacts_evidence_chunk_ids_gin", table_name="knowledge_artifacts")
    op.drop_index("ix_knowledge_artifacts_idea_metadata_gin", table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_embedding_status"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_dedup_hash"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_content_hash"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_scope_key"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_idea_block_type"), table_name="knowledge_artifacts")
    op.drop_column("knowledge_artifacts", "embedding_status")
    op.drop_column("knowledge_artifacts", "dedup_hash")
    op.drop_column("knowledge_artifacts", "content_hash")
    op.drop_column("knowledge_artifacts", "scope_key")
    op.drop_column("knowledge_artifacts", "evidence_chunk_ids")
    op.drop_column("knowledge_artifacts", "idea_metadata")
    op.drop_column("knowledge_artifacts", "summary_text")
    op.drop_column("knowledge_artifacts", "idea_block_type")
