# Writing and publishing policies

[Back to the guide](../readme.md)

## A policy is a temporary exception

Create a `.yaml` file under `policies/`. For example:

```yaml
id: SEC-00422
name: Development instance exception
owner: cloud-security
ticket: SEC-422
reason: Temporary risk accepted while the development instance is replaced
accounts:
  - "123456789012"
regions:
  - eu-central-1
match:
  control_id: EC2.13
  resource_id: "arn:aws:ec2:eu-central-1:123456789012:instance/i-example"
action: SUPPRESS
expires_at: "2027-12-31T23:59:59Z"
```

Replace the sample account, resource, and expiry with your intended values. Use a future expiry and quote account IDs and timestamps. Start with an exact resource selector so you can verify a narrow match.

`owner`, `ticket`, and `reason` explain the decision; the engine records them in the finding note. They do not verify that approval happened.

## Available matching fields

| Field | Meaning |
| --- | --- |
| `accounts` | The finding's AWS account must be in this list. |
| `regions` | A matching resource's Region must be in this list. |
| `match.control_id` | An exact control ID, such as `EC2.13`. |
| `match.resource_id` | Exact resource ID, or its final slash/colon component. Prefer a full ARN for clarity. |
| `match.resource_pattern` | Case-sensitive shell-style pattern, such as `arn:aws:s3:::dev-*`; not a regular expression. |
| `match.resource_type` | Exact finding resource type, such as `AwsEc2Instance`. |
| `match.tags` | Every specified tag must match the finding's resource tags. |
| `match.severity` | Exact finding severity label, such as `HIGH`. |

Omitted optional selectors do not restrict the match. Policies require at least one selector in `match`. Multiple matching policies are resolved by alphabetical policy ID, not specificity or expiry date.

## Validation

From the repository root, with Python dependencies installed:

```bash
python scripts/policies.py validate
```

[policy.schema.json](../../schemas/policy.schema.json) describes required fields, types, allowed values, and unknown-field rejection. The script loads YAML into Python dictionaries and checks them with `Draft202012Validator`. The class name identifies the JSON Schema 2020-12 rules it follows. `FormatChecker()` enables timestamp format checks.

Python then separately checks for future expiry and duplicate policy IDs. Validation does not contact AWS or prove that a policy matches a real finding.

An expired YAML policy blocks publication until you remove or update it. An already-published policy still expires at runtime because the matcher checks its timestamp on every evaluation.

## What publication does

The script builds DynamoDB keys like:

```text
PK = ACCOUNT#123456789012#CONTROL#EC2.13
SK = SEC-00422
```

For policies without a control, `CONTROL#*` is a literal key used for the general-policy lookup. It is not a DynamoDB wildcard query. One record is written for each account in the policy.

Publication stores a content hash and source commit. Changed policies get an incremented version and deployment timestamp. Unchanged hashes are skipped. Conditional writes detect conflicting changes, but publishing the full set is not one atomic transaction.

The local policy directory is the desired set: records absent from it are deleted from DynamoDB. Use the full intended policy set when deploying, not a temporary directory containing only your new exception.

After any changes, the publisher requests reconciliation and backfill asynchronously. Successful publication does not prove those scans or subsequent finding updates succeeded; check Lambda logs.

## Removal and expiry

Remove a policy file and publish to withdraw it. Reconciliation reopens its engine-managed findings only if no other valid policy matches.

Removing the final deployed policy requires an explicit option:

```bash
python scripts/policies.py deploy --allow-empty
```

An expiry alone does not delete the DynamoDB record or immediately update findings. Later processing rejects the expired policy, including during the daily reconciliation scan.

See [operations](../operations/readme.md) for publishing commands and GitHub configuration.
