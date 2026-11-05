# Deploying, testing, and troubleshooting

[Back to the guide](../readme.md)

Commands below run from the repository root. They are instructions for you to run. Applying Terraform and publishing policies change AWS resources or findings.

## Two separate deployments

| Change | How it reaches AWS |
| --- | --- |
| Terraform configuration or Python under `src/` | Deploy using Terraform locally or through your own infrastructure pipeline. Terraform packages `src/` into the Lambda ZIP. The supplied GitHub workflows do not deploy infrastructure or Lambda code. |
| YAML policies | `scripts/policies.py deploy`, locally or through GitHub Actions. |

The policy publisher has no dry-run mode. Test with a policy targeting one intended finding.

## Local setup

Use Python 3.12, Terraform 1.10 or newer, and AWS CLI credentials with the required permissions.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export AWS_PROFILE=your-profile
export AWS_DEFAULT_REGION=eu-central-1
aws sts get-caller-identity
```

Check the returned account before continuing. Credentials belong in your AWS profile, not in source files.

## Infrastructure configuration

Start with the [example Terraform variables](../../infrastructure/terraform.tfvars.example). From the repository root, copy it only if you do not already have a local configuration:

```bash
cp -n infrastructure/terraform.tfvars.example infrastructure/terraform.tfvars
```

Edit the local copy with your deployment settings. If you already have `terraform.tfvars`, use the example as a reference rather than replacing it.

Set your Region and actual GitHub `OWNER/REPOSITORY` in `infrastructure/terraform.tfvars`. The GitHub OIDC provider must already exist in IAM; this project creates its deployment role, not the provider.

If Security Hub is already enabled and managed elsewhere, set `manage_securityhub = false` before first deployment. This flag does not disable an existing Security Hub subscription. Do not flip it casually after this project has created the subscription: the configuration includes `prevent_destroy`.

Create `infrastructure/backend.hcl` for an existing S3 bucket:

```hcl
bucket       = "YOUR-STATE-BUCKET"
key          = "securityhub-policy-engine/terraform.tfstate"
region       = "YOUR-BUCKET-REGION"
encrypt      = true
use_lockfile = true
```

The backend Region is the bucket's Region; it can differ from the application's Region. The `key` identifies the state object and should not collide with another project's state.

```bash
terraform -chdir=infrastructure init -backend-config=backend.hcl
terraform -chdir=infrastructure fmt -check
terraform -chdir=infrastructure validate
terraform -chdir=infrastructure plan -out=deploy.tfplan
```

Review the plan, then deploy:

```bash
terraform -chdir=infrastructure apply deploy.tfplan
```

Applying a saved plan starts without another confirmation prompt. Generate a fresh plan after changes or a partially completed apply. Terraform state records what it already manages.

## Publish and test one exception

Choose an active finding whose workflow is `NEW`. Copy its account, resource Region, control ID, and resource ID into a narrowly scoped policy. Ensure the expiry is in the future.

```bash
export POLICIES_TABLE="$(terraform -chdir=infrastructure output -raw policies_table_name)"
export PROCESSOR_FUNCTION="$(terraform -chdir=infrastructure output -raw processor_function_name)"

python scripts/policies.py validate
python scripts/policies.py deploy

aws logs tail "/aws/lambda/$PROCESSOR_FUNCTION" --since 15m --follow
```

For a successful update, expect a log like:

```text
Updated finding=... status=SUPPRESSED policy=SEC-00422
```

Confirm the finding in Security Hub has the expected workflow, note, and ownership fields. A nonmatching finding should stay unchanged. The current code does not log every non-match.

To test reopening, remove or narrow the exception and publish again. If no other policy matches, the engine-owned finding should return to `NEW`. Use `--allow-empty` only when intentionally removing all policies.

## Run a scan manually

To reconsider existing `NEW` findings, including when an unchanged publication did not start a scan:

```bash
aws lambda invoke \
  --function-name "$PROCESSOR_FUNCTION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"backfill"}' \
  /tmp/shpe-backfill.json
```

To recheck engine-managed suppressions:

```bash
aws lambda invoke \
  --function-name "$PROCESSOR_FUNCTION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"reconcile"}' \
  /tmp/shpe-reconcile.json
```

Inspect the CLI response for `FunctionError`, the response file, and CloudWatch logs. Finishing the scan does not mean the queued processing has finished. `Scan mode=reconcile enqueued=0` is normal when no eligible suppressions exist.

## GitHub deployments

Configure repository Actions variables:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | Your application Region. |
| `AWS_POLICY_DEPLOY_ROLE_ARN` | Terraform output `github_policy_role_arn`. |
| `POLICIES_TABLE` | Terraform output `policies_table_name`. |
| `PROCESSOR_FUNCTION` | Terraform output `processor_function_name`. |

The publishing workflow uses OIDC to obtain temporary AWS credentials. Its role trust is restricted to the configured repository's `main` branch. The optional owner/repository numeric ID variables support repositories using immutable OIDC subject formats; the trust must match the token your repository issues.

### Set up policy publishing

Deploy the infrastructure before running the publishing workflow. The existing GitHub OIDC provider lets GitHub authenticate to AWS, and Terraform creates the policy deployment IAM role. That role can publish policies and invoke the processor; it is not an infrastructure deployment role.

1. Deploy the infrastructure using the Terraform commands above or your own pipeline.
2. Run `terraform -chdir=infrastructure output` to obtain the role ARN, table name, and processor function name.
3. In your GitHub repository, open **Settings → Secrets and variables → Actions → Variables** and add the four repository variables listed above. Use the IAM role ARN, not the OIDC provider ARN.
4. Configure branch protection or repository rules to require reviews before merging policy changes into `main`.
5. Ensure the workflows and the complete intended policy set are on `main`.

The workflows use temporary AWS credentials through OIDC. They do not need AWS access keys, Terraform state variables, separate Terraform planning/apply roles, or a `terraform-production` GitHub environment.

### Which changes deploy?

The repository includes two workflows:

| Workflow | When it runs | What it does |
| --- | --- | --- |
| `validate-policies.yml` | Pull requests changing `policies/**`, and pushes to `main` changing `policies/**`. | Installs Python dependencies and validates the policy files without contacting AWS. |
| `deploy-policies.yml` | Pushes to `main` changing `policies/**`, or a manual run on `main`. | Validates policies, assumes the AWS deployment role, and synchronises the policies into DynamoDB. |

Changes only to schemas, `scripts/policies.py`, dependencies, documentation, or workflow files do not trigger either workflow automatically. Run local validation when changing the schema or publisher. Changes under `infrastructure/` or `src/` require a separate infrastructure deployment.

The workflows do not run Terraform checks or a Python test suite. Policy deployment validates the files itself; it does not wait for the separate validation workflow to finish.

### Run policy deployment manually

In GitHub, open **Actions → Deploy Policies → Run workflow**, select **main**, and start the run. The workflow must be present on the default branch for manual dispatch to be available. Runs targeting another branch skip the deployment job, and the IAM trust also restricts authentication to `main`.

Review the committed policy files first. Deployment synchronises the complete set, including removing deployed policies whose files are absent. Expired policy files fail validation. The workflow does not pass `--allow-empty`; use the explicit local command when intentionally removing the final policy.

Policy deployment runs share a concurrency group and are queued rather than cancelling the current run. Infrastructure deployment is separate and is not coordinated by this queue.

After a successful deployment, changed policies trigger reconciliation and backfill. If the policy contents are unchanged, no scans start; use the manual scan commands above when you need to reassess findings anyway.

## Common problems

| Symptom | What to check |
| --- | --- |
| `backend.hcl` cannot be read | Put it inside `infrastructure/` when using the commands above. |
| No valid credential sources | Select a configured AWS profile and verify `aws sts get-caller-identity`. |
| No suppression occurs | Confirm publication succeeded, the finding is active and `NEW`, selectors match its actual fields, and the policy has not expired. |
| Tag policy does not match | Check tags in the finding itself; the matcher does not fetch resource tags from other services. |
| Scan succeeds but findings do not change | Inspect processing logs and the SQS dead-letter queue. Scan completion only confirms enqueueing. |
| GitHub cannot assume the role | Check the repository/branch trust, OIDC provider, role ARN variable, and `id-token: write`. |
| Expired policy blocks publishing | Remove or update the expired YAML file before publishing the full set. |

### Investigate a failed finding

The processor logs the SQS message ID and these fields when message processing fails:

| Field | Meaning |
| --- | --- |
| `finding_id` | The Security Hub finding identifier. |
| `product_arn` | The product ARN that identifies the finding source. |
| `finding_region` | The Region recorded on the finding in the message. |
| `client_region` | The Region used by the processor's Security Hub client. |

Use the finding ID and product ARN together when looking up the finding through `GetFindings`. Check the client Region as well. Fields can be empty when a message fails before a finding is decoded.

`Finding not yet available through Security Hub GetFindings` means the lookup returned no finding. `TooManyRequestsException` means the API request was throttled. Neither error means the finding was successfully processed: the handler reports the failed SQS message for retry.

The queue's visibility timeout is 360 seconds. A failed message becomes available again after that timeout, and repeated failures can send it to the dead-letter queue according to the maximum receive count of 5. The handler catches these errors and returns partial batch failures, so Lambda's invocation error metric can remain at zero even when individual messages fail. Check the processor logs, source queue, and dead-letter queue together.

The engine does not configure AWS Config recording. Security Hub controls may require additional setup before producing findings. Creating infrastructure alone also does not publish policies.

AWS resources can incur ongoing usage charges. Review billing while testing. Avoid treating `terraform destroy` as a complete undo: Security Hub has destruction protection, and destroying the engine would not reopen findings it already suppressed.


## Optional policy expiry reminders

The existing processor Lambda can send one SNS email per policy as it enters its final 30 days. A separate daily schedule invokes `{"action":"notify_expiring"}`; the reconciliation schedule continues to check existing suppressions independently.

Enable reminders in your local Terraform variables:

```hcl
expiry_notification_email = "security-team@example.com"
expiry_warning_days       = 30
```

Review and apply a Terraform plan using your normal infrastructure deployment process. SNS sends a subscription confirmation email to the recipient. Follow its confirmation link before testing reminders. The function refuses to send or mark reminders as notified until the topic has a confirmed email subscription. Leaving `expiry_notification_email` empty disables the notification schedule and SNS resources.

To test the check manually after confirming the subscription:

```bash
aws lambda invoke \
  --function-name "$PROCESSOR_FUNCTION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"notify_expiring"}' \
  /tmp/shpe-expiry-notifications.json
```

This sends real emails for eligible policies. Inspect the response file for errors and the processor logs for `Expiry reminder published policy=...`. Running it again should report zero sent reminders unless another policy becomes eligible.

The check scans the deployed policy table with pagination. It groups account records by policy ID and sends one email per policy, including all accounts, rather than one email per account or finding. It checks policies even if they currently suppress no findings. Already-expired policies are excluded.

The application stores `expiry_notification` as a boolean on each policy record. Missing flags are treated as false. Publishing preserves the flag for other edits and account changes, but resets it when the expiry changes. These fields are not YAML inputs and do not affect the policy content hash. Deploy the updated publisher as well as the Lambda; an older publisher would discard the notification metadata when replacing a record.

A temporary five-minute claim on one account record prevents overlapping reminder runs from sending the same policy simultaneously. Publishing refuses to overwrite an active claim; retry publication after five minutes if needed. If only some account records were marked successfully, a later check repairs the remaining flags without resending. Avoid running publication and manual notification checks simultaneously: table scans and multi-account publication are not atomic snapshots.

There is no additional SQS queue or notification table. SNS handles email delivery retries after accepting a publish. Failed Lambda checks appear in its error logs; a later daily run retries eligible policies after a claim expires. A publish accepted by SNS followed by failure before any success flag is saved can still produce a duplicate reminder. Acceptance by SNS does not prove inbox delivery or that the email was read. Deleting and recreating a policy removes its notification history.

The scan groups all deployed policies in memory and shares the processor's 60-second timeout. Monitor duration and errors as the policy set grows; very large sets may require a separate continuation design. These reminders do not call Security Hub or change finding workflow statuses.
