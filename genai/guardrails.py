from __future__ import annotations

import json
import threading
from datetime import date
from typing import Literal, Optional

import boto3
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# LLM response schema
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    skills: list[str] = Field(default_factory=list)
    seniority: Literal["junior", "mid", "senior", "lead", "staff", "principal", "unknown"] = "unknown"
    yoe_required: Optional[int] = None

    @field_validator("skills")
    @classmethod
    def normalise_and_filter(cls, v: list[str]) -> list[str]:
        cleaned = {s.lower().strip() for s in v}
        return sorted(cleaned & SKILL_VOCAB)


# ---------------------------------------------------------------------------
# Output row schema
# ---------------------------------------------------------------------------

class EnrichmentRecord(BaseModel):
    job_id:            str
    snapshot_date:     str
    skills:            list[str]
    seniority:         str
    yoe_required:      Optional[int] = None
    match_score:       float = Field(ge=0.0, le=100.0)
    score_detail:      dict
    extraction_source: Literal["rules", "llm", "cache"]
    enriched_at:       str


# ---------------------------------------------------------------------------
# Skill vocabulary whitelist (~120 canonical lowercase terms)
# ---------------------------------------------------------------------------

SKILL_VOCAB: set[str] = {
    # Languages
    "python", "sql", "java", "scala", "r", "go", "typescript", "javascript",
    "bash", "rust", "c++", "c#", "linux", "shell scripting",
    # Streaming & Messaging
    "kafka", "flink", "kinesis", "spark streaming", "pubsub", "rabbitmq",
    # Distributed Compute
    "spark", "pyspark", "hadoop", "hdfs", "yarn", "mapreduce",
    # Orchestration
    "airflow", "databricks workflows", "prefect", "dagster", "luigi", "nifi",
    "step functions", "composer",
    # Cloud
    "aws", "gcp", "azure", "s3", "ec2", "lambda", "glue", "emr", "athena",
    "bigquery", "dataproc", "redshift", "snowflake", "databricks", "synapse",
    # Storage / Table Formats
    "delta lake", "iceberg", "hudi", "hive", "hbase",
    "data lake", "lakehouse", "data warehousing",
    # Databases
    "postgres", "mysql", "mongodb", "redis", "elasticsearch", "cassandra",
    "dynamodb",
    # ML / AI
    "dbt", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch",
    "mlflow", "xgboost", "lightgbm", "llm", "rag", "langchain",
    "vector database", "prompt engineering", "ml ops", "analytics engineering",
    # DevOps / Infra
    "docker", "kubernetes", "terraform", "ansible", "helm", "ci/cd",
    "github actions", "jenkins", "datadog", "git",
    # Serialization
    "avro", "parquet", "json", "protobuf",
    # BI / Viz
    "tableau", "power bi", "looker", "streamlit", "superset",
    # Process
    "data modeling", "etl", "elt", "rest api", "graphql",
    "microservices", "agile", "scrum",
}


# ---------------------------------------------------------------------------
# Budget tracker — daily cost cap ($0.50/day)
# ---------------------------------------------------------------------------

DAILY_CAP_USD = 0.50

HAIKU_INPUT_COST_PER_TOKEN  = 0.80  / 1_000_000
HAIKU_OUTPUT_COST_PER_TOKEN = 4.00  / 1_000_000
HAIKU_CACHE_READ_PER_TOKEN  = 0.08  / 1_000_000


class BudgetExceededError(RuntimeError):
    pass


class BudgetTracker:
    """
    Daily cost ledger stored at:
        s3://{gold_bucket}/enrichment-cache/budget-{YYYY-MM-DD}.json

    Call check_and_increment() BEFORE each LLM call.
    Call record_actual_usage() AFTER a successful response.
    Fails open (zero spend) if S3 is unreachable.
    """

    def __init__(self, gold_bucket: str, region: str = "ap-south-1"):
        self._bucket = gold_bucket
        self._s3 = boto3.client("s3", region_name=region)
        self._today = date.today().isoformat()
        self._key = f"enrichment-cache/budget-{self._today}.json"
        self._lock = threading.Lock()
        self._ledger = self._load()

    def _load(self) -> dict:
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._key)
            return json.loads(obj["Body"].read())
        except Exception:
            return {"date": self._today, "total_usd": 0.0, "calls": 0}

    def _save(self) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._key,
            Body=json.dumps(self._ledger).encode(),
            ContentType="application/json",
        )

    def current_spend(self) -> float:
        return self._ledger["total_usd"]

    def check_and_increment(self, estimated_input_tokens: int, estimated_output_tokens: int) -> None:
        estimated_cost = (
            estimated_input_tokens  * HAIKU_INPUT_COST_PER_TOKEN +
            estimated_output_tokens * HAIKU_OUTPUT_COST_PER_TOKEN
        )
        with self._lock:
            if self._ledger["total_usd"] + estimated_cost > DAILY_CAP_USD:
                raise BudgetExceededError(
                    f"Daily cap ${DAILY_CAP_USD:.2f} would be exceeded. "
                    f"Current: ${self._ledger['total_usd']:.4f}, "
                    f"estimated: ${estimated_cost:.4f}."
                )

    def record_actual_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> float:
        cost = (
            input_tokens      * HAIKU_INPUT_COST_PER_TOKEN  +
            output_tokens     * HAIKU_OUTPUT_COST_PER_TOKEN +
            cache_read_tokens * HAIKU_CACHE_READ_PER_TOKEN
        )
        with self._lock:
            self._ledger["total_usd"] += cost
            self._ledger["calls"] += 1
            self._save()
        return cost
