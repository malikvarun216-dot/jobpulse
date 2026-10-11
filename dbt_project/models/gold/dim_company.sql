{{ config(
    materialized='table',
    format='parquet',
    s3_data_dir='s3://jobpulse-gold-dev/models/',
    s3_data_naming='schema_table_unique'
) }}

-- One row per company_key. The key is md5(lower(trim(name))), so "GitLab", "Gitlab" and
-- "gitlab" share a key. Before Chat 25 this was DISTINCT company_name: 3 rows per key, and
-- the fact join fanned out 1.15M rows into 2.01M (unique test failed: 368 keys).
-- Display name = the most common spelling.
with names as (
    select
        lower(trim(company_name)) as company_norm,
        trim(company_name)        as company_name,
        count(*)                  as n
    from {{ ref('stg_silver_jobs') }}
    where company_name is not null
      and trim(company_name) != ''
    group by 1, 2
)

select
    to_hex(md5(to_utf8(company_norm))) as company_key,
    max_by(company_name, n)            as company_name,
    localtimestamp                     as created_at
from names
group by company_norm
