# How the Python code works

[Back to the guide](../readme.md)

## The three main files

| File | Responsibility |
| --- | --- |
| [handler.py](../../src/engine/handler.py) | Receive work, call AWS, and apply the decision. |
| [matcher.py](../../src/engine/matcher.py) | Decide whether a policy matches a finding. No AWS calls. |
| [policies.py](../../scripts/policies.py) | Validate YAML and synchronise policies into DynamoDB. |

`src/engine/__init__.py` marks `engine` as a Python package. Lambda's configured handler is `engine.handler.lambda_handler`.

## Where `event` comes from

AWS calls this entry point:

```python
def lambda_handler(event, context):
```

`event` contains the input for this invocation. `context` contains runtime information, including the remaining execution time.

Scheduler supplies `{"action": "reconcile"}`. The publisher supplies `reconcile` or `backfill`. The SQS integration supplies an object containing a `Records` list instead.

```python
if event.get("action") in ("reconcile", "backfill"):
    scan_findings(...)
    return {"ok": True}
```

The action tells Lambda to scan. Returning ends that invocation before it reaches the SQS loop. `ok` means the scan call finished; queued findings may still be waiting for processing.

For SQS events, the handler reads each message's JSON body. It recognises either an imported Security Hub event or the scanner's `{"kind": "recheck", "finding": ...}` message. Both call `process_finding()`.

## Following one finding through `handler.py`

1. `current_finding()` retrieves the latest copy using its finding ID and product ARN. A queued copy may be stale.
2. `process_finding()` skips archived findings, workflow states other than `NEW` and `SUPPRESSED`, and suppressions without the engine ownership marker.
3. `policies_for()` queries DynamoDB for the account/control combination and for policies without a specific control.
4. `best_match()` returns a matching policy or `None`.
5. `update_finding()` calls Security Hub's `batch_update_findings` when a change is needed.

The decision table is:

| Current state | Valid policy matches? | Result |
| --- | --- | --- |
| `NEW` | Yes | Set `SUPPRESSED` and record ownership. |
| `NEW` | No | Leave unchanged. |
| Engine-managed `SUPPRESSED` | Yes | Keep suppressed; update policy details if they changed. |
| Engine-managed `SUPPRESSED` | No | Set `NEW` and mark `ShpeManaged=false`. |
| Suppressed without engine marker | Either | Leave unchanged. |

Updates preserve existing user-defined fields and add `ShpeManaged`, `ShpePolicyId`, and, when suppressing, `ShpePolicyHash`. The note includes the owner, ticket, reason, and expiry. Reopening retains the previous policy ID for context.

If the finding is already suppressed by the same policy ID and hash, the handler skips another write. This helps avoid repeated updates when its own changes produce more finding events.

## Inside `matcher.py`

- `control_id()` reads `Compliance.SecurityControlId`, falling back to `ProductFields.ControlId`.
- `is_active()` requires action `SUPPRESS` and a valid future expiry.
- `resource_matches()` checks region, resource ID, pattern, type, and tags.
- `matches()` combines policy validity, account, optional control/severity, and resource checks.
- `best_match()` selects the alphabetically lowest matching policy ID.

All configured conditions must match. At least one resource must satisfy all resource conditions together. If a finding lists several resources, one matching resource is enough to suppress the whole finding.

The matcher reads tags and Regions from the finding's resources. It does not call EC2, S3, or a tagging API to discover additional attributes.

## How scanning continues

`scan_findings()` follows Security Hub pagination tokens. If another page exists and less than 20 seconds remain, it invokes the same Lambda asynchronously with the action and `next_token`.

`enqueue_page()` sends findings using an SQS client. `QueueUrl=QUEUE_URL` supplies the destination explicitly; creating an SQS client alone does not select a queue.

## Errors

If a finding cannot be fetched or updated, the handler records the failure against its SQS message. A message containing several findings can be retried even if some were processed successfully. Unknown message shapes are skipped by the current handler.
