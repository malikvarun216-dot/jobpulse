{{ config(
    materialized='table',
    format='parquet',
    s3_data_dir='s3://jobpulse-gold-dev/models/',
    s3_data_naming='schema_table_unique'
) }}

with companies as (
    select distinct
        company_name
    from {{ ref('stg_silver_jobs') }}
    where company_name is not null
      and trim(company_name) != ''
)

select
    to_hex(md5(to_utf8(lower(trim(company_name))))) as company_key,
    company_name,
    localtimestamp                                  as created_at
from companies
