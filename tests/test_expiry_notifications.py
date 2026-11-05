"""Exercise reminders and publisher state without making AWS requests."""

import copy
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from engine.expiry_notifications import notify_expiring

spec = importlib.util.spec_from_file_location("publisher", ROOT / "scripts/policies.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)

def sns_client():
    sns = Mock()
    sns.list_subscriptions_by_topic.return_value = {
        "Subscriptions": [{"Protocol": "email", "SubscriptionArn": "arn:aws:sns:subscription"}]
    }
    return sns

def policy(days=20):
    return {
        "id": "SEC-000001", "name": "Example exception", "owner": "security",
        "ticket": "SEC-1", "reason": "Temporary exception",
        "accounts": ["111111111111", "222222222222"], "regions": ["eu-central-1"],
        "match": {"control_id": "GuardDuty.1"}, "action": "SUPPRESS",
        "expires_at": (NOW + timedelta(days=days)).isoformat(),
    }

def records(p):
    return list(publisher.records_for({p["id"]: p}, "test").values())

class Table:
    """Small stateful stand-in; scan one item per page to exercise pagination."""
    def __init__(self, items):
        self.items = {(i["PK"], i["SK"]): copy.deepcopy(i) for i in items}
        self.reject_claim = False
        self.fail_mark_at = None
        self.marks = 0
        self.requests = []

    def scan(self, **kwargs):
        keys = sorted(self.items)
        start = 0
        if "ExclusiveStartKey" in kwargs:
            key = kwargs["ExclusiveStartKey"]
            start = keys.index((key["PK"], key["SK"])) + 1
        page = keys[start:start + 1]
        result = {"Items": [copy.deepcopy(self.items[k]) for k in page]}
        if start + 1 < len(keys):
            result["LastEvaluatedKey"] = dict(zip(("PK", "SK"), page[0]))
        return result

    def update_item(self, **kwargs):
        self.requests.append(kwargs)
        item = self.items[tuple(kwargs["Key"][k] for k in ("PK", "SK"))]
        values = kwargs["ExpressionAttributeValues"]
        if ":until" in values:
            if self.reject_claim or item.get("expiry_notification") or item.get("expiry_notification_lock_until", 0) >= values[":now"]:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            item.update(expiry_notification_claim=values[":claim"], expiry_notification_lock_until=values[":until"])
        elif ":sent" in values:
            self.marks += 1
            if self.marks == self.fail_mark_at:
                raise RuntimeError("Interrupted while recording success")
            assert item["policy_hash"] == values[":hash"]
            assert item["expires_at"] == values[":expiry"]
            item["expiry_notification"] = True
        else:
            assert item["expiry_notification_claim"] == values[":claim"]
            item.pop("expiry_notification_claim")
            item.pop("expiry_notification_lock_until")

    def put_item(self, **kwargs):
        self.requests.append(kwargs)
        item = kwargs["Item"]
        self.items[item["PK"], item["SK"]] = copy.deepcopy(item)

    def delete_item(self, **kwargs):
        self.requests.append(kwargs)
        del self.items[kwargs["Key"]["PK"], kwargs["Key"]["SK"]]

def test_multi_account_policy_sends_once_across_daily_runs():
    table, sns = Table(records(policy())), sns_client()
    assert notify_expiring(table, sns, "topic", now=NOW) == {"sent": 1}
    assert notify_expiring(table, sns, "topic", now=NOW + timedelta(days=1)) == {"sent": 0}
    sns.publish.assert_called_once()
    assert all(r["expiry_notification"] for r in table.items.values())
    assert "111111111111, 222222222222" in sns.publish.call_args.kwargs["Message"]

@pytest.mark.parametrize("days, expected", [(-1, 0), (0, 0), (1, 1), (30, 1), (30.001, 0)])
def test_expiry_boundaries(days, expected):
    sns = sns_client()
    assert notify_expiring(Table(records(policy(days))), sns, "topic", now=NOW)["sent"] == expected

def test_publish_failure_retries_after_claim_expires():
    table, sns = Table(records(policy())), sns_client()
    sns.publish.side_effect = RuntimeError("SNS unavailable")
    with pytest.raises(RuntimeError):
        notify_expiring(table, sns, "topic", now=NOW)
    assert not any(r.get("expiry_notification") for r in table.items.values())
    sns.publish.side_effect = None
    assert notify_expiring(table, sns, "topic", now=NOW + timedelta(minutes=6))["sent"] == 1

def test_partial_success_repairs_flags_without_resending():
    table, sns = Table(records(policy())), sns_client()
    table.fail_mark_at = 2
    with pytest.raises(RuntimeError):
        notify_expiring(table, sns, "topic", now=NOW)
    notify_expiring(table, sns, "topic", now=NOW + timedelta(days=1))
    sns.publish.assert_called_once()
    assert all(r["expiry_notification"] for r in table.items.values())

def test_overlapping_claim_does_not_send():
    table, sns = Table(records(policy())), sns_client()
    table.reject_claim = True
    assert notify_expiring(table, sns, "topic", now=NOW)["sent"] == 0
    sns.publish.assert_not_called()

def test_inconsistent_account_versions_wait_for_next_run():
    table, sns = Table(records(policy())), sns_client()
    next(iter(table.items.values()))["policy_hash"] = "different"
    notify_expiring(table, sns, "topic", now=NOW)
    sns.publish.assert_not_called()

def test_disabled_and_empty_notifications():
    table, sns = Mock(), sns_client()
    assert notify_expiring(table, sns, "", now=NOW) == {"sent": 0}
    table.scan.assert_not_called()
    assert notify_expiring(Table([]), sns, "topic", now=NOW) == {"sent": 0}

def test_publisher_preserves_flag_when_all_accounts_change():
    original = policy()
    old = records(original)
    for item in old:
        item["expiry_notification"] = True
    table = Table(old)
    changed = {**original, "accounts": ["333333333333"], "reason": "Updated reason"}
    publisher.sync(table, publisher.records_for({changed["id"]: changed}, "new"))
    assert len(table.items) == 1
    assert next(iter(table.items.values()))["expiry_notification"] is True

def test_renewal_resets_flag_and_can_send_new_reminder():
    old = records(policy())
    for item in old:
        item["expiry_notification"] = True
    table, sns = Table(old), sns_client()
    renewed = policy(25)
    publisher.sync(table, publisher.records_for({renewed["id"]: renewed}, "new"))
    assert all(r["expiry_notification"] is False for r in table.items.values())
    assert notify_expiring(table, sns, "topic", now=NOW)["sent"] == 1

def test_publisher_guards_against_concurrent_notification_updates():
    item = records(policy())[0]
    condition, _, values = publisher.unchanged_record(item, 100)
    assert "attribute_not_exists(expiry_notification)" in condition
    assert "expiry_notification_lock_until < :now" in condition
    item["expiry_notification"] = True
    condition, _, values = publisher.unchanged_record(item, 100)
    assert "expiry_notification = :notified" in condition
    assert values[":notified"] is True

def test_publisher_blocks_active_notification_claim():
    old = records(policy())
    old[0]["expiry_notification_lock_until"] = 9999999999
    with pytest.raises(ValueError, match="reminder is in progress"):
        publisher.sync(Table(old), publisher.records_for({"SEC-000001": policy(25)}, "new"))

def test_unconfirmed_subscription_does_not_consume_reminders():
    table, sns = Table(records(policy())), sns_client()
    sns.list_subscriptions_by_topic.return_value = {
        "Subscriptions": [{"Protocol": "email", "SubscriptionArn": "PendingConfirmation"}]
    }
    with pytest.raises(RuntimeError, match="Confirm the SNS"):
        notify_expiring(table, sns, "topic", now=NOW)
    sns.publish.assert_not_called()
    assert not table.requests

def test_new_policies_start_with_false_flag():
    p = policy()
    table = Table([])
    publisher.sync(table, publisher.records_for({p["id"]: p}, "new"))
    assert all(r["expiry_notification"] is False for r in table.items.values())
