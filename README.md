# AWS Security Hub Policy Engine

A policy-as-code solution for automated AWS Security Hub finding suppression.

Define security exceptions as simple YAML policies in Git, review them through pull requests, and automatically apply approved policies to Security Hub findings.

Instead of managing a separate native automation rule for every exception, this project aims to support large numbers of exceptions with clear ownership, expiration dates, and an audit trail.

## Why build it?

Imagine managing multiple AWS accounts with thousands of security findings. Some risks have already been reviewed and temporarily accepted, while others are mitigated by compensating controls.

Instead of suppressing findings individually, you define a policy once. The engine automatically suppresses matching findings, with team approvals and expiration dates, while avoiding AWS Security Hub's automation rule limits.  

**What this project aims to provide:**

- **Scale:** Manage many exceptions without one native Security Hub rule per exception and bypass the 100 automation rule limit.
- **Control:** Keep policies in Git, with reviews before they go live.
- **Expiry:** Automatically revisit findings when an exception expires.
- **Traceability:** Know which policy suppressed a finding and why.
- **Safety:** All policies must be approved.

## How it works

1. **Create a policy** in a YAML file and submit it for review.
2. **Deploy the approved policy** to DynamoDB using GitHub Actions upon PR merge.
3. **Receive a finding** from Security Hub through EventBridge and SQS.
4. **Check for a match:** Lambda reads relevant policies from DynamoDB and compares them with the finding.
5. **Apply the decision:** Suppress a match and record why.
6. **Recheck later:** When an exception expires or changes, reassess engine-owned suppressions. Reopen a finding only when no other approved exception applies.

## Example policy

This example suppresses one Security Hub control on a tagged development resource until the end of 2026:

```yaml
id: SEC-00421
name: Development exception
owner: cloud-security
ticket: SEC-421
reason: Risk accepted for resources in development

accounts:
  - "123456789012"
regions:
  - eu-central-1

match:
  control_id: EC2.13
  tags:
    Environment: Development

action: SUPPRESS
expires_at: "2026-12-31T23:59:59Z"
```

## Resource matching support

Policies can target **individual AWS resources or groups of resources** using these selectors:

| Match by | Example |
| --- | --- |
| **Resource ARN or ID** | `arn:aws:s3:::example-bucket` |
| **Resource tags** | `Environment: Development` |
| **AWS account** | `123456789012` |
| **AWS Region** | `eu-central-1` |
| **Resource type** | `AwsEc2Instance` |
| **Resource ID patterns** | `arn:aws:s3:::dev-*` |

## Tech stack

| Component | Purpose |
| --- | --- |
| **Python / AWS Lambda** | Evaluate suppression policies |
| **Amazon EventBridge + SQS** | Deliver findings reliably |
| **Amazon DynamoDB** | Store policies and suppression records |
| **AWS Security Hub** | Source of findings and suppression updates |
| **Terraform** | Deploy AWS infrastructure |
| **GitHub Actions** | Validate, review and publish policies |

For a walkthrough of the architecture, code, policies, and deployment, continue with the [documentation guide](doc/readme.md).