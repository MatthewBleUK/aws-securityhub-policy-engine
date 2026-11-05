"""Validate policy files and deploy them to DynamoDB.

Checks YAML policies against the schema and validates expiry dates.
Creates or updates policies in DynamoDB and removes policies whose files
have been deleted. After changes, triggers reconciliation and backfill
to reassess existing findings using the updated policies.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "policies"
SCHEMA = ROOT / "schemas" / "policy.schema.json"

def load_policies():
    schema = json.loads(SCHEMA.read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    policies = {}

    for file in sorted(POLICY_DIR.rglob("*.yaml")):
        policy = yaml.safe_load(file.read_text())
        errors = list(validator.iter_errors(policy))

        if errors:
            raise ValueError(f"{file}: {errors[0].message}")

        expiry = datetime.fromisoformat(
            policy["expires_at"].replace("Z", "+00:00")
        )

        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError(
                f"{file}: expires_at must be in the future and timezone-aware"
            )

        if policy["id"] in policies:
            raise ValueError(f"Duplicate policy id {policy['id']}")

        policies[policy["id"]] = policy

    return policies

def records_for(policies, commit):
    result = {}

    for policy in policies.values():
        content = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(content.encode()).hexdigest()

        for account in policy["accounts"]:
            pk = (
                f"ACCOUNT#{account}#CONTROL#"
                f"{policy['match'].get('control_id', '*')}"
            )
            key = (pk, policy["id"])

            result[key] = {
                "PK": pk,
                "SK": policy["id"],
                **policy,
                "policy_hash": digest,
                "source_commit": commit,
            }

    return result

def scan_all(table):
    items = []
    args = {"ConsistentRead": True}

    while True:
        result = table.scan(**args)
        items.extend(result.get("Items", []))

        if "LastEvaluatedKey" not in result:
            break

        args["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    return {(item["PK"], item["SK"]): item for item in items}

def unchanged_record(record, now):
    """Do not overwrite a reminder sent or claimed since the publisher read it."""
    condition = (
        "#hash = :expected AND "
        "(attribute_not_exists(expiry_notification_lock_until) OR expiry_notification_lock_until < :now)"
    )
    values = {":expected": record["policy_hash"], ":now": now}
    if "expiry_notification" in record:
        condition += " AND expiry_notification = :notified"
        values[":notified"] = record["expiry_notification"]
    else:
        condition += " AND attribute_not_exists(expiry_notification)"
    return condition, {"#hash": "policy_hash"}, values

def sync(table, desired, allow_empty=False):
    existing = scan_all(table)

    if not desired and existing and not allow_empty:
        raise ValueError(
            "Refusing to remove all deployed policies without --allow-empty"
        )

    # Notification state is shared by all account copies of a policy.
    notified = {
        (item["id"], item["expires_at"])
        for item in existing.values()
        if item.get("expiry_notification", False)
    }
    now = int(datetime.now(timezone.utc).timestamp())
    if any(item.get("expiry_notification_lock_until", 0) >= now for item in existing.values()):
        raise ValueError("An expiry reminder is in progress; retry publication in five minutes")

    changed = 0
    removed = 0

    for key, item in desired.items():
        old = existing.get(key)

        if old and old.get("policy_hash") == item["policy_hash"]:
            continue

        item["expiry_notification"] = (item["id"], item["expires_at"]) in notified
        item["version"] = int(old.get("version", 0)) + 1 if old else 1
        item["deployed_at"] = datetime.now(timezone.utc).isoformat()

        if old:
            condition, names, values = unchanged_record(old, now)
        else:
            condition = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            names = None
            values = None

        request = {
            "Item": item,
            "ConditionExpression": condition,
        }

        if names:
            request["ExpressionAttributeNames"] = names
            request["ExpressionAttributeValues"] = values

        table.put_item(**request)
        print(f"{'UPDATE' if old else 'CREATE'} {key[1]} {key[0]}")
        changed += 1

    for key, old in existing.items():
        if key not in desired:
            condition, names, values = unchanged_record(old, now)
            table.delete_item(
                Key={"PK": key[0], "SK": key[1]},
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )

            print(f"REMOVE {key[1]} {key[0]}")
            removed += 1

    return changed, removed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "deploy"])
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()

    policies = load_policies()
    print(f"Validated {len(policies)} policies")

    if args.command == "validate":
        return

    table_name = os.environ["POLICIES_TABLE"]
    function_name = os.environ["PROCESSOR_FUNCTION"]
    commit = os.getenv("GITHUB_SHA", "local")

    table = boto3.resource("dynamodb").Table(table_name)

    changed, removed = sync(
        table,
        records_for(policies, commit),
        args.allow_empty,
    )

    print(f"Published: {changed} upserts, {removed} removals")

    if changed or removed:
        response = boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"action": "reconcile"}).encode(),
        )

        if response["StatusCode"] != 202:
            raise RuntimeError("Could not start reconciliation")

        response = boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"action": "backfill"}).encode(),
        )

        if response["StatusCode"] != 202:
            raise RuntimeError("Could not start backfill")

if __name__ == "__main__":
    try:
        main()
    except (ValueError, ClientError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
