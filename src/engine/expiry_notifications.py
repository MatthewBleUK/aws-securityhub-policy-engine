"""Send one expiry reminder per policy, sharing state across account records."""

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

def policy_groups(table):
    """Read every page before grouping the account copies of each policy."""
    groups = {}
    params = {"ConsistentRead": True}
    while True:
        page = table.scan(**params)
        for item in page.get("Items", []):
            groups.setdefault(item["id"], []).append(item)
        if not page.get("LastEvaluatedKey"):
            return groups
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]

def key_for(record):
    return {"PK": record["PK"], "SK": record["SK"]}

def mark_notified(table, records):
    for record in records:
        table.update_item(
            Key=key_for(record),
            UpdateExpression="SET expiry_notification = :sent",
            ConditionExpression="policy_hash = :hash AND expires_at = :expiry",
            ExpressionAttributeValues={
                ":sent": True,
                ":hash": record["policy_hash"],
                ":expiry": record["expires_at"],
            },
        )

def confirmed_email_subscription(sns, topic_arn):
    """Do not consume reminders while the email subscription is still pending."""
    params = {"TopicArn": topic_arn}
    while True:
        page = sns.list_subscriptions_by_topic(**params)
        if any(
            sub["Protocol"] == "email" and sub["SubscriptionArn"].startswith("arn:")
            for sub in page.get("Subscriptions", [])
        ):
            return True
        if not page.get("NextToken"):
            return False
        params["NextToken"] = page["NextToken"]

def notify_expiring(table, sns, topic_arn, warning_days=30, now=None):
    """Publish reminders; failed publishes remain eligible for a later run."""
    if not topic_arn:
        return {"sent": 0}
    if not confirmed_email_subscription(sns, topic_arn):
        raise RuntimeError("Confirm the SNS email subscription before sending expiry reminders")
    now = now or datetime.now(timezone.utc)
    sent = 0
    for policy_id, records in sorted(policy_groups(table).items()):
        records.sort(key=lambda record: (record["PK"], record["SK"]))
        policy = records[0]
        # A publisher may be partway through replacing the account copies.
        if len({record["policy_hash"] for record in records}) != 1:
            logger.warning("Skipping policy=%s while account records differ", policy_id)
            continue
        expiry = datetime.fromisoformat(policy["expires_at"].replace("Z", "+00:00"))
        if policy.get("action") != "SUPPRESS" or not now < expiry <= now + timedelta(days=warning_days):
            continue
        if any(record.get("expiry_notification", False) for record in records):
            mark_notified(table, records)
            continue

        # One deterministic account record coordinates overlapping invocations.
        claim = str(uuid4())
        try:
            table.update_item(
                Key=key_for(policy),
                UpdateExpression="SET expiry_notification_claim = :claim, expiry_notification_lock_until = :until",
                ConditionExpression=(
                    "policy_hash = :hash AND "
                    "(attribute_not_exists(expiry_notification) OR expiry_notification = :false) AND "
                    "(attribute_not_exists(expiry_notification_lock_until) OR expiry_notification_lock_until < :now)"
                ),
                ExpressionAttributeValues={
                    ":claim": claim, ":until": int(now.timestamp()) + 300,
                    ":now": int(now.timestamp()), ":hash": policy["policy_hash"], ":false": False,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue
            raise

        # Leave the five-minute claim in place on failure. A later run can retry.
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"Policy expiry reminder: {policy_id}"[:100],
            Message="\n".join([
                "This exception policy expires within the reminder window.",
                "Review it and renew or withdraw it through your policy repository.",
                "",
                f"Policy: {policy_id} - {policy['name']}",
                f"Owner: {policy['owner']}",
                f"Ticket: {policy['ticket']}",
                f"Reason: {policy['reason']}",
                f"Accounts: {', '.join(policy['accounts'])}",
                f"Regions: {', '.join(policy['regions'])}",
                f"Control: {policy['match'].get('control_id', 'All matching controls')}",
                f"Expires at: {policy['expires_at']}",
            ]),
        )
        # Mark the coordinating record first so a partial update can be repaired.
        mark_notified(table, records)
        table.update_item(
            Key=key_for(policy),
            UpdateExpression="REMOVE expiry_notification_claim, expiry_notification_lock_until",
            ConditionExpression="expiry_notification_claim = :claim",
            ExpressionAttributeValues={":claim": claim},
        )
        sent += 1
        logger.info("Expiry reminder published policy=%s", policy_id)
    return {"sent": sent}
