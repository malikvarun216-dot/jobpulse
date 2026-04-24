{{ config(
    materialized='table',
    format='parquet',
    s3_data_dir='s3://jobpulse-gold-dev/models/',
    s3_data_naming='schema_table_unique'
) }}

with jobs as (
    select * from {{ ref('stg_silver_jobs') }}
),

companies as (
    select * from {{ ref('dim_company') }}
),

roles as (
    select * from {{ ref('dim_role') }}
),

countries as (
    select * from {{ ref('dim_country') }}
)

select
    j.job_id,
    c.company_key,
    r.role_key,
    co.country_key,
    j.snapshot_date,
    j.publication_date,
    j.title,
    j.apply_url,
    j.job_type,
    j.salary_raw,
    j.tags,
    cast(null as double) as match_score,
    j.source,
    j.source_count,
    j.ingested_at,
    j.description
from jobs j
left join companies c
    on to_hex(md5(to_utf8(lower(trim(j.company_name))))) = c.company_key
left join roles r
    on to_hex(md5(to_utf8(concat(lower(trim(j.role_family)), '|', coalesce(lower(trim(j.category)), ''))))) = r.role_key
left join countries co
    on to_hex(md5(to_utf8(lower(trim(j.country))))) = co.country_key
