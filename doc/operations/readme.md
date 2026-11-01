# Deploying, testing, and troubleshooting

[Back to the guide](../readme.md)

Commands below run from the repository root. They are instructions for you to run. Applying Terraform and publishing policies change AWS resources or findings.

## Two separate deployments

| Change | How it reaches AWS |
| --- | --- |
| Terraform configuration or Python under `src/` | Terraform plan and apply. Terraform packages `src/` into the Lambda ZIP. |
| YAML policies | `scripts/policies.py deploy`, locally or through GitHub Actions. |

The current implementation has no dry-run mode. Test with a policy targeting one intended finding.

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

## GitHub publishing

Configure repository Actions variables:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | Your application Region. |
| `AWS_POLICY_DEPLOY_ROLE_ARN` | Terraform output `github_policy_role_arn`. |
| `POLICIES_TABLE` | Terraform output `policies_table_name`. |
| `PROCESSOR_FUNCTION` | Terraform output `processor_function_name`. |

The publishing workflow uses OIDC to obtain temporary AWS credentials. Its role trust is restricted to the configured repository's `main` branch. The optional owner/repository numeric ID variables support repositories using immutable OIDC subject formats; the trust must match the token your repository issues.

Publishing runs on relevant policy/schema/publisher changes pushed to `main`, or through manual workflow dispatch. Use `main` for manual runs to match the role trust. Configure branch protection and required reviews to enforce approval.

The validation workflow checks policy validity and Terraform formatting/configuration. Despite the job name, it does not currently run a Python test suite. Neither workflow deploys infrastructure changes.

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

The engine does not configure AWS Config recording. Security Hub controls may require additional setup before producing findings. Creating infrastructure alone also does not publish policies.

AWS resources can incur ongoing usage charges. Review billing while testing. Avoid treating `terraform destroy` as a complete undo: Security Hub has destruction protection, and destroying the engine would not reopen findings it already suppressed.
