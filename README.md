# AWS Security Hub Policy Engine

AWS Security Hub Policy Engine provides a centralized way to manage security exceptions across AWS accounts using YAML policies.

Instead of creating and maintaining individual Security Hub automation rules, security teams can define exceptions in GitHub, review changes through pull requests, and automatically suppress findings that match approved policies.

Each exception has a defined scope, owner, justification, and expiration date, making it easier to track accepted risks and prevent outdated suppressions from remaining active.

## Problem Statement

Not every security finding requires immediate remediation. Some risks are temporarily accepted, while others are addressed through compensating controls.

Managing these exceptions across multiple AWS accounts becomes increasingly difficult as the number of findings grows. Native Security Hub automation rules are also limited to 100 per administrator account.

This project addresses those challenges by managing security exceptions as version-controlled YAML policies rather than individual automation rules, allowing for greater scalability while maintaining a Git-based audit trail of policy changes.

## How it works

<p>
  <img src="./doc/image.png" width="650" alt="Architecture diagram">
</p>

Security exceptions are defined as YAML policies, with GitHub serving as the source of truth for policy management. Changes are reviewed through pull requests, and once approved and merged, GitHub Actions automatically deploys the policies to DynamoDB.

An EventBridge rule captures `Security Hub Findings - Imported` events from AWS Security Hub and forwards them to an SQS queue for processing by a Lambda function. The function evaluates each finding against active policies and automatically suppresses those that match.

Findings are reevaluated when they are updated or when policies change. A daily scheduled check also reviews findings previously suppressed by the engine to ensure their exceptions are still valid. When a policy expires or no longer applies, affected findings are reopened if no other active policy matches.

## Example policy

```yaml
id: SEC-00421
name: Example policy
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

Policies can target AWS resources using these selectors:

| Match by | Example |
| --- | --- |
| Resource ARN or ID | `arn:aws:s3:::example-bucket` |
| Resource tags | `Environment: Development` |
| AWS account | `123456789012` |
| AWS Region | `eu-central-1` |
| Resource type | `AwsEc2Instance` |
| Resource ID patterns | `arn:aws:s3:::dev-*` |

## Tech Stack

- Language & Runtime: Python, AWS Lambda
- Event Processing: Amazon EventBridge, Amazon SQS
- Data Storage: Amazon DynamoDB
- Security: AWS Security Hub
- Infrastructure: Terraform
- CI/CD: GitHub Actions

For a walkthrough of the architecture, code, policies, and deployment, continue with the [documentation guide](doc/readme.md).