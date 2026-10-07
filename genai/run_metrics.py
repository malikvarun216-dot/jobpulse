"""
Publishes how long a Glue Python Shell runner took, as a CloudWatch custom metric.

Glue publishes no duration metric for Python Shell jobs, so the runner reports its own.
monitoring.tf alarms when it passes 70% of the job's timeout (Chat 25) — a warning before
the job starts timing out, instead of finding out from a dead pipeline.

The clock starts when the script starts, so Glue's ~1 min pip install is not included.
"""

import boto3

NAMESPACE = "JobPulse"


def publish_duration(job_name: str, seconds: float, region: str) -> None:
    """Never fails the job: a missing metric is less bad than a failed run."""
    try:
        boto3.client("cloudwatch", region_name=region).put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{
                "MetricName": "JobDurationSeconds",
                "Dimensions": [{"Name": "JobName", "Value": job_name}],
                "Value": seconds,
                "Unit": "Seconds",
            }],
        )
        print(f"[metrics] {NAMESPACE}/JobDurationSeconds {job_name}={seconds:.0f}s")
    except Exception as exc:
        print(f"[metrics] could not publish duration: {exc}")
