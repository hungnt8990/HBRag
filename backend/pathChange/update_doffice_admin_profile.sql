-- Update DOffice ingestion profile config in PostgreSQL.
-- Run from backend root, for Docker Postgres:
--   Get-Content .\pathChange\update_doffice_admin_profile.sql | docker compose exec -T postgres psql -U hbrag -d hbrag
-- Or local psql:
--   psql postgresql://hbrag:hbrag_password@localhost:5432/hbrag -f .\pathChange\update_doffice_admin_profile.sql

BEGIN;

WITH profile_patch AS (
  SELECT
    $doffice_normalizer${
  "source_summary_max_chars": 4000,
  "disable_llm_enrichment": true,
  "non_llm_chunk_types": [
    "document_summary",
    "document_header",
    "document_body",
    "table_parent",
    "table_row",
    "table_group",
    "footer_signature"
  ],
  "table_context_window_lines": 16,
  "table_name_rules": [
    {
      "name": "procurement_winning_context",
      "contains_all": [
        "nha thau",
        "trung thau"
      ],
      "exclude_any": [
        "khong trung thau"
      ],
      "table_name": "Thông tin nhà thầu trúng thầu"
    },
    {
      "name": "procurement_non_winning_context",
      "contains_all": [
        "nha thau",
        "khong trung thau"
      ],
      "table_name": "Thông tin nhà thầu không trúng thầu"
    },
    {
      "name": "package_info_context",
      "contains_all": [
        "thong tin",
        "goi thau"
      ],
      "table_name": "Thông tin gói thầu"
    }
  ],
  "table_schemas": [
    {
      "name": "procurement_winning",
      "enabled": true,
      "match": {
        "context_contains_any": [
          "nha thau trung thau",
          "thong tin nha thau trung thau"
        ],
        "header_contains_any": [
          "ten nha thau",
          "ma so thue",
          "gia trung thau"
        ]
      },
      "row_start_min_fields": 3,
      "min_row_non_empty_fields": 5,
      "fields": [
        {
          "key": "row_number",
          "label": "STT",
          "patterns": [
            "^\\d{1,4}$"
          ],
          "metadata_key": "row_number"
        },
        {
          "key": "contractor_name",
          "label": "Tên nhà thầu",
          "patterns": [
            "cong ty|cty|tnhh|co phan|doanh nghiep|lien danh"
          ],
          "metadata_key": "feature_name"
        },
        {
          "key": "tax_code",
          "label": "Mã số thuế",
          "patterns": [
            "^\\d{10,14}$"
          ],
          "metadata_key": "tax_code"
        },
        {
          "key": "bid_price",
          "label": "Giá dự thầu (VNĐ)",
          "metadata_key": "bid_price"
        },
        {
          "key": "bid_price_after_discount",
          "label": "Giá dự thầu sau giảm giá (nếu có) (VNĐ)",
          "metadata_key": "bid_price_after_discount"
        },
        {
          "key": "winning_price",
          "label": "Giá trúng thầu (VNĐ)",
          "metadata_key": "winning_price"
        },
        {
          "key": "package_execution_time",
          "label": "Thời gian thực hiện gói thầu",
          "metadata_key": "package_execution_time"
        },
        {
          "key": "contract_execution_time",
          "label": "Thời gian thực hiện hợp đồng",
          "metadata_key": "contract_execution_time"
        },
        {
          "key": "other_content",
          "label": "Nội dung khác (nếu có)",
          "metadata_key": "other_content"
        }
      ],
      "row_key_fields": [
        "contractor_name",
        "tax_code"
      ],
      "group_rows": false
    },
    {
      "name": "procurement_non_winning",
      "enabled": true,
      "match": {
        "context_contains_any": [
          "nha thau khong trung thau"
        ],
        "header_contains_any": [
          "ten nha thau",
          "ma so thue",
          "ly do"
        ]
      },
      "row_start_min_fields": 3,
      "min_row_non_empty_fields": 4,
      "fields": [
        {
          "key": "row_number",
          "label": "STT",
          "patterns": [
            "^\\d{1,4}$"
          ],
          "metadata_key": "row_number"
        },
        {
          "key": "contractor_name",
          "label": "Tên nhà thầu",
          "patterns": [
            "cong ty|cty|tnhh|co phan|doanh nghiep|lien danh"
          ],
          "metadata_key": "feature_name"
        },
        {
          "key": "tax_code",
          "label": "Mã số thuế",
          "patterns": [
            "^\\d{10,14}$"
          ],
          "metadata_key": "tax_code"
        },
        {
          "key": "reason",
          "label": "Lý do nhà thầu không trúng thầu",
          "metadata_key": "change_content"
        }
      ],
      "row_key_fields": [
        "contractor_name",
        "tax_code"
      ],
      "group_rows": false
    }
  ],
  "feature_change_table": {
    "feature_aliases": [
      "chuc nang",
      "man hinh",
      "module",
      "giao dien"
    ],
    "change_aliases": [
      "noi dung",
      "hieu chinh",
      "bo sung",
      "mo ta",
      "giai doan"
    ],
    "group_rows": true
  },
  "table_role_aliases": {
    "row_number": [
      "stt",
      "tt"
    ],
    "platform": [
      "nen tang",
      "doi tuong",
      "ung dung",
      "he thong"
    ],
    "feature_name": [
      "chuc nang",
      "chuc nang man hinh",
      "man hinh",
      "ten chuc nang",
      "module",
      "giao dien man hinh"
    ],
    "screen_name": [
      "man hinh",
      "chuc nang man hinh",
      "giao dien man hinh"
    ],
    "change_content": [
      "noi dung",
      "noi dung hieu chinh",
      "hieu chinh bo sung",
      "mo ta"
    ],
    "phase": [
      "giai doan",
      "phase"
    ]
  }
}$doffice_normalizer$::jsonb AS doffice_normalizer,
    $doffice_base${
  "chunk_mode": "doffice_structured",
  "chunk_size": 1800,
  "chunk_overlap": 120,
  "top_k": 12,
  "candidate_k": 80,
  "answer_mode": "hybrid",
  "answer_style": "policy_explainer",
  "max_context_chars": 10000,
  "heading_rules": [],
  "doffice_normalizer": {
    "source_summary_max_chars": 4000,
    "disable_llm_enrichment": true,
    "non_llm_chunk_types": [
      "document_summary",
      "document_header",
      "document_body",
      "table_parent",
      "table_row",
      "table_group",
      "footer_signature"
    ],
    "table_context_window_lines": 16,
    "table_name_rules": [
      {
        "name": "procurement_winning_context",
        "contains_all": [
          "nha thau",
          "trung thau"
        ],
        "exclude_any": [
          "khong trung thau"
        ],
        "table_name": "Thông tin nhà thầu trúng thầu"
      },
      {
        "name": "procurement_non_winning_context",
        "contains_all": [
          "nha thau",
          "khong trung thau"
        ],
        "table_name": "Thông tin nhà thầu không trúng thầu"
      },
      {
        "name": "package_info_context",
        "contains_all": [
          "thong tin",
          "goi thau"
        ],
        "table_name": "Thông tin gói thầu"
      }
    ],
    "table_schemas": [
      {
        "name": "procurement_winning",
        "enabled": true,
        "match": {
          "context_contains_any": [
            "nha thau trung thau",
            "thong tin nha thau trung thau"
          ],
          "header_contains_any": [
            "ten nha thau",
            "ma so thue",
            "gia trung thau"
          ]
        },
        "row_start_min_fields": 3,
        "min_row_non_empty_fields": 5,
        "fields": [
          {
            "key": "row_number",
            "label": "STT",
            "patterns": [
              "^\\d{1,4}$"
            ],
            "metadata_key": "row_number"
          },
          {
            "key": "contractor_name",
            "label": "Tên nhà thầu",
            "patterns": [
              "cong ty|cty|tnhh|co phan|doanh nghiep|lien danh"
            ],
            "metadata_key": "feature_name"
          },
          {
            "key": "tax_code",
            "label": "Mã số thuế",
            "patterns": [
              "^\\d{10,14}$"
            ],
            "metadata_key": "tax_code"
          },
          {
            "key": "bid_price",
            "label": "Giá dự thầu (VNĐ)",
            "metadata_key": "bid_price"
          },
          {
            "key": "bid_price_after_discount",
            "label": "Giá dự thầu sau giảm giá (nếu có) (VNĐ)",
            "metadata_key": "bid_price_after_discount"
          },
          {
            "key": "winning_price",
            "label": "Giá trúng thầu (VNĐ)",
            "metadata_key": "winning_price"
          },
          {
            "key": "package_execution_time",
            "label": "Thời gian thực hiện gói thầu",
            "metadata_key": "package_execution_time"
          },
          {
            "key": "contract_execution_time",
            "label": "Thời gian thực hiện hợp đồng",
            "metadata_key": "contract_execution_time"
          },
          {
            "key": "other_content",
            "label": "Nội dung khác (nếu có)",
            "metadata_key": "other_content"
          }
        ],
        "row_key_fields": [
          "contractor_name",
          "tax_code"
        ],
        "group_rows": false
      },
      {
        "name": "procurement_non_winning",
        "enabled": true,
        "match": {
          "context_contains_any": [
            "nha thau khong trung thau"
          ],
          "header_contains_any": [
            "ten nha thau",
            "ma so thue",
            "ly do"
          ]
        },
        "row_start_min_fields": 3,
        "min_row_non_empty_fields": 4,
        "fields": [
          {
            "key": "row_number",
            "label": "STT",
            "patterns": [
              "^\\d{1,4}$"
            ],
            "metadata_key": "row_number"
          },
          {
            "key": "contractor_name",
            "label": "Tên nhà thầu",
            "patterns": [
              "cong ty|cty|tnhh|co phan|doanh nghiep|lien danh"
            ],
            "metadata_key": "feature_name"
          },
          {
            "key": "tax_code",
            "label": "Mã số thuế",
            "patterns": [
              "^\\d{10,14}$"
            ],
            "metadata_key": "tax_code"
          },
          {
            "key": "reason",
            "label": "Lý do nhà thầu không trúng thầu",
            "metadata_key": "change_content"
          }
        ],
        "row_key_fields": [
          "contractor_name",
          "tax_code"
        ],
        "group_rows": false
      }
    ],
    "feature_change_table": {
      "feature_aliases": [
        "chuc nang",
        "man hinh",
        "module",
        "giao dien"
      ],
      "change_aliases": [
        "noi dung",
        "hieu chinh",
        "bo sung",
        "mo ta",
        "giai doan"
      ],
      "group_rows": true
    },
    "table_role_aliases": {
      "row_number": [
        "stt",
        "tt"
      ],
      "platform": [
        "nen tang",
        "doi tuong",
        "ung dung",
        "he thong"
      ],
      "feature_name": [
        "chuc nang",
        "chuc nang man hinh",
        "man hinh",
        "ten chuc nang",
        "module",
        "giao dien man hinh"
      ],
      "screen_name": [
        "man hinh",
        "chuc nang man hinh",
        "giao dien man hinh"
      ],
      "change_content": [
        "noi dung",
        "noi dung hieu chinh",
        "hieu chinh bo sung",
        "mo ta"
      ],
      "phase": [
        "giai doan",
        "phase"
      ]
    }
  }
}$doffice_base$::jsonb AS base_config
)
INSERT INTO ingestion_profile_configs (profile_name, config, created_at, updated_at)
SELECT
  'doffice_admin',
  base_config,
  NOW(),
  NOW()
FROM profile_patch
ON CONFLICT (profile_name) DO UPDATE
SET
  config = jsonb_set(
    COALESCE(ingestion_profile_configs.config, '{}'::jsonb),
    '{doffice_normalizer}',
    (SELECT doffice_normalizer FROM profile_patch),
    true
  ),
  updated_at = NOW();

COMMIT;

-- Verify after update.
SELECT
  profile_name,
  config #>> '{doffice_normalizer,source_summary_max_chars}' AS source_summary_max_chars,
  config #>> '{doffice_normalizer,disable_llm_enrichment}' AS disable_llm_enrichment,
  jsonb_array_length(COALESCE(config #> '{doffice_normalizer,table_schemas}', '[]'::jsonb)) AS table_schema_count,
  updated_at
FROM ingestion_profile_configs
WHERE profile_name = 'doffice_admin';
