"""Match Security Hub findings against suppression policies.

Checks policy expiry, account, region, control, severity, and resource
selectors. When multiple policies match, selects the lowest policy ID
alphabetically.
"""

from datetime import datetime, timezone
from fnmatch import fnmatchcase

def control_id(finding):
    return (
        finding.get("Compliance", {}).get("SecurityControlId")
        or finding.get("ProductFields", {}).get("ControlId")
        or ""
    )

def is_active(policy, now=None):
    now = now or datetime.now(timezone.utc)

    try:
        expiry = datetime.fromisoformat(
            policy["expires_at"].replace("Z", "+00:00")
        )

        return (
            policy.get("action") == "SUPPRESS"
            and expiry.tzinfo is not None
            and expiry > now
        )

    except (ValueError, TypeError, KeyError, AttributeError):
        return False

def resource_matches(resource, conditions, regions):
    if regions and resource.get("Region") not in regions:
        return False

    actual_id = resource.get("Id", "")
    resource_id = conditions.get("resource_id")

    if resource_id and resource_id not in (
        actual_id,
        actual_id.rsplit("/", 1)[-1],
        actual_id.rsplit(":", 1)[-1],
    ):
        return False

    pattern = conditions.get("resource_pattern")
    if pattern and not fnmatchcase(actual_id, pattern):
        return False

    if (
        conditions.get("resource_type")
        and resource.get("Type") != conditions["resource_type"]
    ):
        return False

    tags = resource.get("Tags") or {}

    if any(
        tags.get(key) != value
        for key, value in conditions.get("tags", {}).items()
    ):
        return False

    return True

def matches(finding, policy, now=None):
    if not is_active(policy, now):
        return False

    if finding.get("AwsAccountId") not in policy.get("accounts", []):
        return False

    conditions = policy.get("match", {})
    required_control = conditions.get("control_id")

    if required_control and required_control != control_id(finding):
        return False

    if (
        conditions.get("severity")
        and conditions["severity"] != finding.get("Severity", {}).get("Label")
    ):
        return False

    resources = finding.get("Resources") or []

    return any(
        resource_matches(resource, conditions, policy.get("regions", []))
        for resource in resources
    )

def best_match(finding, policies, now=None):
    matching = [
        policy
        for policy in policies
        if matches(finding, policy, now)
    ]

    return min(matching, key=lambda p: p["id"]) if matching else None
