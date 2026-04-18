{{ config(
    materialized='table',
    format='parquet',
    s3_data_dir='s3://jobpulse-gold-dev/models/',
    s3_data_naming='schema_table_unique'
) }}

with countries as (
    select distinct
        country
    from {{ ref('stg_silver_jobs') }}
    where country is not null
)

select
    to_hex(md5(to_utf8(lower(trim(country))))) as country_key,
    country,
    localtimestamp                              as created_at
from countries
