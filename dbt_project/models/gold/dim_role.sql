{{ config(
    materialized='table',
    format='parquet',
    s3_data_dir='s3://jobpulse-gold-dev/models/',
    s3_data_naming='schema_table_unique'
) }}

with roles as (
    select distinct
        role_family,
        category
    from {{ ref('stg_silver_jobs') }}
    where role_family is not null
)

select
    to_hex(md5(to_utf8(concat(lower(trim(role_family)), '|', coalesce(lower(trim(category)), ''))))) as role_key,
    role_family,
    category,
    localtimestamp                                  as created_at
from roles
