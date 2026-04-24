"""
Glue Python Shell job — downloads the dbt project from S3, repairs
the silver Athena table partitions, then runs `dbt run`.

Invoked by Step Functions after the bronze→silver Glue ETL job.
"""
import argparse
import json
import os
import sys
import time
import zipfile

import boto3

# ---------------------------------------------------------------------------
# Argument parsing (Glue passes job params as --key value)
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument("--silver_bucket",  required=True)
parser.add_argument("--gold_bucket",    required=True)
parser.add_argument("--region",         default="ap-south-1")
parser.add_argument("--workgroup",      default="jobpulse-dev")
parser.add_argument("--gold_database",  default="jobpulse_gold_dev")
parser.add_argument("--silver_database", default="jobpulse_silver_dev")
parser.add_argument("--silver_table",   default="silver_jobs")
args, _ = parser.parse_known_args()

REGION        = args.region
WORKGROUP     = args.workgroup
GOLD_DB       = args.gold_database
SILVER_DB     = args.silver_database
SILVER_TABLE  = args.silver_table
SILVER_BUCKET = args.silver_bucket
GOLD_BUCKET   = args.gold_bucket

DBT_ZIP_KEY    = "dbt-project/dbt_project.zip"
DBT_LOCAL_DIR  = "/tmp/dbt_project"
DBT_PROJECT_DIR = os.path.join(DBT_LOCAL_DIR, "dbt_project")
PROFILES_DIR  = "/tmp/dbt_profiles"
S3_STAGING    = f"s3://{GOLD_BUCKET}/athena-results/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def download_and_extract_dbt_project():
    s3 = boto3.client("s3", region_name=REGION)
    zip_path = "/tmp/dbt_project.zip"
    print(f"Downloading s3://{SILVER_BUCKET}/{DBT_ZIP_KEY} → {zip_path}")
    s3.download_file(SILVER_BUCKET, DBT_ZIP_KEY, zip_path)
    os.makedirs(DBT_LOCAL_DIR, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(DBT_LOCAL_DIR)
    print(f"Extracted dbt project to {DBT_LOCAL_DIR}")


def write_glue_profiles():
    """Generate profiles.yml that uses the Glue IAM role (no aws_profile_name)."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    profile = {
        "jobpulse": {
            "target": "glue",
            "outputs": {
                "glue": {
                    "type": "athena",
                    "region_name": REGION,
                    "s3_staging_dir": S3_STAGING,
                    "schema": GOLD_DB,
                    "database": "awsdatacatalog",
                    "work_group": WORKGROUP,
                    "threads": 4,
                    "num_retries": 3,
                }
            },
        }
    }
    import yaml
    profiles_path = os.path.join(PROFILES_DIR, "profiles.yml")
    with open(profiles_path, "w") as f:
        yaml.dump(profile, f, default_flow_style=False)
    print(f"Wrote Glue profiles.yml to {profiles_path}")


def repair_silver_partitions():
    """Run MSCK REPAIR TABLE so Athena sees all Hive partitions written by Glue."""
    athena = boto3.client("athena", region_name=REGION)
    query = f"MSCK REPAIR TABLE `{SILVER_DB}`.`{SILVER_TABLE}`"
    print(f"Running: {query}")
    response = athena.start_query_execution(
        QueryString=query,
        WorkGroup=WORKGROUP,
        QueryExecutionContext={"Database": SILVER_DB},
        ResultConfiguration={"OutputLocation": S3_STAGING},
    )
    exec_id = response["QueryExecutionId"]

    for _ in range(60):  # up to ~2 min
        result = athena.get_query_execution(QueryExecutionId=exec_id)
        state = result["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)

    if state != "SUCCEEDED":
        reason = result["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
        raise RuntimeError(f"MSCK REPAIR TABLE failed [{state}]: {reason}")
    print("Partitions repaired.")


def run_dbt():
    import subprocess
    import shutil

    base_args = [
        "--project-dir", DBT_PROJECT_DIR,
        "--profiles-dir", PROFILES_DIR,
    ]

    print(f"dbt project dir: {DBT_PROJECT_DIR} (exists: {os.path.exists(DBT_PROJECT_DIR)})")
    print(f"profiles dir: {PROFILES_DIR} (exists: {os.path.exists(PROFILES_DIR)})")

    # Check dbt installation
    dbt_path = shutil.which("dbt")
    print(f"dbt location: {dbt_path}")
    result = subprocess.run(["dbt", "--version"], capture_output=True, text=True)
    print(f"dbt version: {result.stdout}")

    print("\n=== Running: dbt deps ===")
    result = subprocess.run(["dbt", "deps"] + base_args, env=os.environ.copy())
    if result.returncode != 0:
        raise RuntimeError(f"dbt deps failed with code {result.returncode}")

    print("\n=== Running: dbt run ===")
    result = subprocess.run(["dbt", "run"] + base_args, env=os.environ.copy())
    if result.returncode != 0:
        raise RuntimeError(f"dbt run failed with code {result.returncode}")

    print("\n=== dbt run complete ===")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    download_and_extract_dbt_project()
    write_glue_profiles()
    repair_silver_partitions()
    run_dbt()
    print(json.dumps({"status": "OK", "gold_database": GOLD_DB}))
