with source as (
    select * from {{ source('silver', 'silver_jobs') }}
),

cleaned as (
    select
        job_id,
        source,
        snapshot_date,
        title,
        company_name,
        category,
        role_family,
        job_type,
        apply_url,
        salary_raw,
        location_raw,
        country,
        state,
        tags,
        publication_date,
        description,
        ingested_at,
        source_apis,
        source_count
    from source
    where job_id is not null
      and trim(job_id) != ''
)

select * from cleaned
