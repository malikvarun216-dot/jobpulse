import io
import time

import boto3
import pandas as pd

WORKGROUP = "jobpulse-dev"
DATABASE = "jobpulse_gold_dev"
REGION = "ap-south-1"


def run_query(sql: str) -> pd.DataFrame:
    """
    Execute SQL on Athena workgroup 'jobpulse-dev', poll to completion,
    and return results as a DataFrame.

    Credentials are read from ~/.aws/credentials (default profile).
    Result CSV is written by Athena to s3://jobpulse-gold-dev/athena-results/.
    """
    athena = boto3.client("athena", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )
    execution_id = resp["QueryExecutionId"]

    delay = 1.0
    while True:
        status_resp = athena.get_query_execution(QueryExecutionId=execution_id)
        state = status_resp["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            output_loc = status_resp["QueryExecution"]["ResultConfiguration"]["OutputLocation"]
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status_resp["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
            raise RuntimeError(f"Athena query {state}: {reason}")

        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)

    # s3://bucket/key/path/file.csv
    path = output_loc.replace("s3://", "")
    bucket, key = path.split("/", 1)

    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    return pd.read_csv(io.BytesIO(body))
