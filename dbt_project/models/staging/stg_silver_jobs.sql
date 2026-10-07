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
        -- Apr 18–20 partitions predate the dedup step: no cross-source info → it was 1 source
        coalesce(source_apis, array[source]) as source_apis,
        coalesce(source_count, 1)            as source_count
    from source
    where job_id is not null
      and trim(job_id) != ''
)

select * from cleaned
