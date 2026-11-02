# Deploying, testing, and troubleshooting

[Back to the guide](../readme.md)

Commands below run from the repository root. They are instructions for you to run. Applying Terraform and publishing policies change AWS resources or findings.

## Two separate deployments

| Change | How it reaches AWS |
| --- | --- |
| Terraform configuration or Python under `src/` | GitHub Actions plans on PRs and applies after merging to `main` and approval. Manual Terraform commands also work. Terraform packages `src/` into the Lambda ZIP. |
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

### Which changes deploy?

Each workflow has one purpose:

- `validate.yml` checks policies and Terraform.
- `deploy.yml` checks changed files and runs the appropriate deployment workflows.
- `deploy-terraform.yml` plans Terraform and applies the saved plan after approval.
- `deploy-policies.yml` validates and publishes policies.

The deployment selection is:

| Change | Pull request | Push/merge to `main` |
| --- | --- | --- |
| `infrastructure/**` or `src/**` | Terraform plan | Terraform plan, then approval and apply |
| `policies/**`, `schemas/**`, `scripts/policies.py`, or `requirements.txt` | Validation in `validate.yml` | Publish policies |
| Both groups | Plan and validation | Apply Terraform first, then publish policies |
| Documentation only | Validation | No deployment |

Changes to `deploy.yml` select both deployments. Changes to either deployment workflow select that deployment. `deploy-terraform.yml` and `deploy-policies.yml` are reusable workflows called by `deploy.yml`.

In Actions, run **Deploy** manually on `main` to deploy both, with Terraform first. Other branches cannot deploy. A failed, cancelled, or rejected Terraform apply blocks policy publishing when both were selected. Policy-only runs do not require Terraform approval.

Main deployments are queued together, including while waiting for approval, so newer deployments cannot overtake an older deployment. The workflow uses GitHub's [`queue: max` concurrency option](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) to retain pending runs rather than replace them. If a deployment fails, resolve it and rerun it before approving later infrastructure deployments; use a manual **Deploy** run if the latest complete configuration needs republishing.

`validate.yml` continues to check policy validity and Terraform formatting/configuration on PRs and pushes to `main`. It does not run a Python test suite. Fork PRs run validation but skip the AWS-backed Terraform plan.

### Terraform setup in GitHub

Before enabling infrastructure deployments:

1. Create the **terraform-production** GitHub environment. Restrict deployment branches to `main` and configure **required reviewers**. The YAML references the environment; it does not create an approval rule. Without required reviewers, apply starts automatically. Check that your repository's GitHub plan supports [environment approvals](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).
2. Provision separate AWS OIDC roles for planning and applying. These roles, the OIDC provider, and the state bucket must already exist; the existing policy-publishing role cannot deploy infrastructure. Bootstrap them using your existing AWS administration process.
3. Configure the repository Actions variables below. Keep the existing policy variables listed above.

| Variable | Value |
| --- | --- |
| `AWS_TERRAFORM_PLAN_ROLE_ARN` | Plan role: resource read access, state read access, and S3 lock-file permissions. No infrastructure mutation permissions. |
| `AWS_TERRAFORM_APPLY_ROLE_ARN` | Apply role: permissions to manage this project's resources, pass its Lambda IAM role, and read/write its Terraform state and lock file. |
| `TF_STATE_BUCKET` | Existing state bucket, matching your local `backend.hcl`. |
| `TF_STATE_KEY` | Existing state key, for example `securityhub-policy-engine/terraform.tfstate`. |
| `TF_STATE_REGION` | State bucket Region. |
| `TF_VARS_JSON` | Optional JSON object containing Terraform input overrides. Match your existing local inputs; see below. |

The workflow defaults `aws_region` to `AWS_REGION` and `github_repository` to the current repository. Other inputs use their Terraform defaults unless overridden in `TF_VARS_JSON`. For example, if Security Hub is already managed elsewhere:

```json
{
  "manage_securityhub": false,
  "project_name": "securityhub-policy-engine"
}
```

Include any nondefault inputs from your local `terraform.tfvars`, including numeric GitHub owner/repository IDs if used. Overrides take precedence over the workflow defaults. The backend must point to the **same existing state**, and the variables must describe the same deployment, before the first automated apply. Keep `POLICIES_TABLE` and `PROCESSOR_FUNCTION` aligned with the Terraform outputs if resource names change.

For repositories using the standard GitHub OIDC subject, the plan role must trust `repo:OWNER/REPOSITORY:pull_request` and `repo:OWNER/REPOSITORY:ref:refs/heads/main`. The apply role must trust `repo:OWNER/REPOSITORY:environment:terraform-production`. Both must require audience `sts.amazonaws.com`. Adapt subjects for immutable/custom OIDC formats if enabled. The policy role keeps its existing `main` branch trust.

AWS-backed planning executes Terraform from same-repository PRs and can read state. Limit that role to planning permissions and only allow trusted contributors to create branches in the repository. Fork PRs never receive planning credentials through this workflow.

Review the **Create Terraform plan** step in the run logs before approving **Terraform apply**. Main runs save the plan together with the Lambda ZIP and apply that exact plan after approval. PR plans are previews; merging creates a new plan for the actual `main` commit. Plan artifacts can contain sensitive values and are retained for one day; approve within that period or rerun the workflow to generate a fresh plan.

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
