# infra/

Terraform IaC for the managed control plane (AWS + Amazon Bedrock).

Planned modules: `network`, `cognito`, `rds` (Aurora Serverless v2 + RLS), `ecs`,
`bedrock-agentcore`, `eventbridge`, `stepfns`, `s3`, `secrets` (+ KMS), `waf`,
`observability` (OTel / CloudWatch / X-Ray), `cicd`. Remote state in S3 +
DynamoDB lock; GitHub OIDC for CI. One-command `terraform apply` is a
demoability goal.

See [docs/product-vision.md](../docs/product-vision.md).
