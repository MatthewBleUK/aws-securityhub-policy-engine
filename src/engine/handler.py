"""Process Security Hub findings using policies stored in DynamoDB.

Handles finding events from SQS and reconciliation/backfill invocations.
Uses matcher.py to select a policy, suppresses matching findings, and
reopens engine-managed suppressions when no valid policy matches.
"""

import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

from engine.matcher import best_match, control_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TABLE = os.environ.get("POLICIES_TABLE", "SuppressionPolicies")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")

OWNER_FIELD = "ShpeManaged"
POLICY_FIELD = "ShpePolicyId"

def policies_for(finding, table):
    account = finding["AwsAccountId"]
    control = control_id(finding)
    keys = [f"ACCOUNT#{account}#CONTROL#*"]

    if control:
        keys.insert(0, f"ACCOUNT#{account}#CONTROL#{control}")

    policies = {}

    for pk in keys:
        result = table.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ConsistentRead=True,
        )

        while True:
            for item in result.get("Items", []):
                policies[item["id"]] = item

            token = result.get("LastEvaluatedKey")
            if not token:
                break

            result = table.query(
                KeyConditionExpression=Key("PK").eq(pk),
                ExclusiveStartKey=token,
                ConsistentRead=True,
            )

    return list(policies.values())

def current_finding(sh, finding):
    response = sh.get_findings(
        Filters={
            "Id": [{"Value": finding["Id"], "Comparison": "EQUALS"}],
            "ProductArn": [
                {"Value": finding["ProductArn"], "Comparison": "EQUALS"}
            ],
        },
        MaxResults=1,
    )

    return next(iter(response.get("Findings", [])), None)

def update_finding(sh, finding, status, note, policy_id, managed, policy_hash=None):
    fields = dict(finding.get("UserDefinedFields") or {})
    fields[OWNER_FIELD] = "true" if managed else "false"
    fields[POLICY_FIELD] = policy_id

    if policy_hash:
        fields["ShpePolicyHash"] = policy_hash

    result = sh.batch_update_findings(
        FindingIdentifiers=[
            {
                "Id": finding["Id"],
                "ProductArn": finding["ProductArn"],
            }
        ],
        Workflow={"Status": status},
        Note={
            "Text": note[:1024],
            "UpdatedBy": "securityhub-policy-engine",
        },
        UserDefinedFields=fields,
    )

    if result.get("UnprocessedFindings"):
        raise RuntimeError(
            f"Security Hub update was not processed: {result['UnprocessedFindings']}"
        )

    logger.info(
        "Updated finding=%s status=%s policy=%s",
        finding["Id"],
        status,
        policy_id,
    )

def process_finding(finding, sh, table):
    latest = current_finding(sh, finding)

    if latest is None:
        raise RuntimeError(
            "Finding not yet available through Security Hub GetFindings"
        )

    if latest.get("RecordState", "ACTIVE") != "ACTIVE":
        return

    state = latest.get("Workflow", {}).get("Status", "NEW")
    fields = latest.get("UserDefinedFields") or {}
    owned = fields.get(OWNER_FIELD) == "true"

    if state not in ("NEW", "SUPPRESSED"):
        return

    if state == "SUPPRESSED" and not owned:
        return

    winner = best_match(latest, policies_for(latest, table))

    if winner:
        if (
            state == "SUPPRESSED"
            and owned
            and fields.get(POLICY_FIELD) == winner["id"]
            and fields.get("ShpePolicyHash") == winner.get("policy_hash")
        ):
            return

        note = (
            f"Policy {winner['id']} | "
            f"Owner: {winner['owner']} | "
            f"Ticket: {winner['ticket']} | "
            f"Reason: {winner['reason']} | "
            f"Expires: {winner['expires_at']}"
        )

        update_finding(
            sh,
            latest,
            "SUPPRESSED",
            note,
            winner["id"],
            True,
            winner.get("policy_hash"),
        )

    elif state == "SUPPRESSED" and owned:
        previous = fields.get(POLICY_FIELD, "unknown")

        update_finding(
            sh,
            latest,
            "NEW",
            f"Policy engine: no active matching exception. Previous policy: {previous}",
            previous,
            False,
        )

def enqueue_page(queue, findings):
    for offset in range(0, len(findings), 10):
        subset = findings[offset:offset + 10]

        result = queue.send_message_batch(
            QueueUrl=QUEUE_URL,
            Entries=[
                {
                    "Id": str(index),
                    "MessageBody": json.dumps({
                        "kind": "recheck",
                        "finding": finding,
                    }),
                }
                for index, finding in enumerate(subset)
            ]
        )

        if result.get("Failed"):
            raise RuntimeError(
                f"Could not enqueue findings: {result['Failed']}"
            )

def scan_findings(event, context, sh, queue, lambda_client):
    mode = event["action"]
    filters = {
        "RecordState": [
            {"Value": "ACTIVE", "Comparison": "EQUALS"}
        ]
    }

    if mode == "reconcile":
        filters["WorkflowStatus"] = [
            {"Value": "SUPPRESSED", "Comparison": "EQUALS"}
        ]
        filters["UserDefinedFields"] = [
            {
                "Key": OWNER_FIELD,
                "Value": "true",
                "Comparison": "EQUALS",
            }
        ]
    else:
        filters["WorkflowStatus"] = [
            {"Value": "NEW", "Comparison": "EQUALS"}
        ]

    params = {
        "Filters": filters,
        "MaxResults": 100,
    }

    if event.get("next_token"):
        params["NextToken"] = event["next_token"]

    count = 0

    while True:
        response = sh.get_findings(**params)
        enqueue_page(queue, response.get("Findings", []))
        count += len(response.get("Findings", []))

        next_token = response.get("NextToken")
        if not next_token:
            break

        if context and context.get_remaining_time_in_millis() < 20000:
            result = lambda_client.invoke(
                FunctionName=FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps({
                    "action": mode,
                    "next_token": next_token,
                }).encode(),
            )

            if result.get("StatusCode") != 202:
                raise RuntimeError("Could not continue findings scan")

            break

        params["NextToken"] = next_token

    logger.info("Scan mode=%s enqueued=%d", mode, count)

def lambda_handler(event, context):
    sh = boto3.client("securityhub")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE)

    if event.get("action") in ("reconcile", "backfill"):
        scan_findings(
            event,
            context,
            sh,
            boto3.client("sqs"),
            boto3.client("lambda"),
        )
        return {"ok": True}

    failures = []

    for record in event.get("Records", []):
        finding = None
        try:
            event_body = json.loads(record["body"])

            if event_body.get("kind") == "recheck":
                findings = [event_body["finding"]]

            elif (
                event_body.get("source") == "aws.securityhub"
                and event_body.get("detail-type")
                == "Security Hub Findings - Imported"
            ):
                findings = event_body.get("detail", {}).get("findings", [])

            else:
                continue

            for finding in findings:
                process_finding(finding, sh, table)

        except Exception:
            finding_context = finding if isinstance(finding, dict) else {}
            logger.exception(
                "Failed processing SQS message %s "
                "finding_id=%s product_arn=%s finding_region=%s client_region=%s",
                record.get("messageId"),
                finding_context.get("Id"),
                finding_context.get("ProductArn"),
                finding_context.get("Region"),
                sh.meta.region_name,
            )
            failures.append({
                "itemIdentifier": record["messageId"],
            })

    return {"batchItemFailures": failures}
