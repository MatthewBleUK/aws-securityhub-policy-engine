# Understanding the policy engine

This project turns YAML exception policies into automatic decisions about Security Hub findings. A matching policy suppresses a finding. When no valid exception remains, the engine can reopen a finding it previously suppressed.

Suppression changes the finding's workflow status. It does not fix the underlying AWS resource.

## Start here

Read these guides in order, or open the topic you need:

1. [How the AWS services fit together](architecture/readme.md)
2. [How the Python code works](code/readme.md)
3. [Writing and publishing policies](policies/readme.md)
4. [Deploying, testing, and troubleshooting](operations/readme.md)

## Three ways work starts

| Trigger | What happens |
| --- | --- |
| A new or updated finding arrives | EventBridge routes it through SQS to Lambda for a policy check. |
| The daily schedule runs | Lambda queues active findings that the engine has suppressed for another policy check. |
| You publish policy changes | Lambda checks existing engine-managed suppressions and existing `NEW` findings. |

All three paths use the same matching code.

## Terms used in these guides

| Term | Meaning |
| --- | --- |
| Finding | A security issue reported to Security Hub. |
| Policy | A YAML document describing an exception and its expiry. |
| Handler | The Python function AWS calls when Lambda runs. |
| Reconciliation | Checking whether existing suppressions still agree with current policies. |
| Backfill | Applying current policies to existing `NEW` findings. |
| Engine-managed | A finding marked `ShpeManaged=true` by this application. |

## What the current implementation does

The current code makes real Security Hub updates: there is no dry-run setting. It stores policies in DynamoDB and records suppression ownership on the finding itself. There is no separate suppression-history table.

Approval is a GitHub review process, not a field checked by the matcher. Configure repository protection if a review must be required before publishing.

These guides describe the repository code. Editing a local Python or Terraform file does not change the deployed Lambda until you apply Terraform again.
