# AWS S3 integration

## Read-only policy (analysis)

Replace `my-bucket` with the bucket(s) you want analysed. `sts:GetCallerIdentity` needs no permission and is used for the connection check.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ListBuckets", "Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*" },
    { "Sid": "ListObjects", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::my-bucket" },
    { "Sid": "ReadObjects", "Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::my-bucket/*" }
  ]
}
```

`s3:GetObject` is used only to download objects up to `S3_MAX_DOWNLOAD_MB` (default 10 MB) for hashing, text extraction and compression benchmarks. Without it, analysis falls back to metadata only (`HeadObject` is covered by `GetObject`).

## Optional write statement (approved storage-class changes)

```json
{
  "Sid": "ChangeStorageClass",
  "Effect": "Allow",
  "Action": ["s3:PutObject", "s3:GetObjectTagging", "s3:PutObjectTagging"],
  "Resource": "arn:aws:s3:::my-bucket/*"
}
```

Then set `S3_WRITE_ENABLED=true`. A storage-class change is a `CopyObject` of the object onto itself with `StorageClass=<target>`, `MetadataDirective=COPY` and `TaggingDirective=COPY`, followed by a `HeadObject` verification.

Behaviour and limits:

- Objects in `GLACIER` or `DEEP_ARCHIVE` must be restored first. They are refused.
- Objects larger than 5 GB need a multipart copy, which is not implemented. They are refused; use a lifecycle rule instead.
- With bucket versioning enabled, the copy creates a new version and the old one remains billable until it expires. Combine with a lifecycle rule for non-current versions.
- The copy resets the object's `LastModified`, which affects lifecycle rules based on age.
- Minimum-duration charges of the previous class still apply.

## Reading S3 server access logs

To use real access history, enable [server access logging](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html) on the analysed bucket, targeting a separate log bucket. Grant `s3:ListBucket` and `s3:GetObject` on the log bucket, then use *AWS S3 > Import access history* (or `POST /api/ml/access-events/fetch-s3-logs`). Logs arrive with a delay of minutes to hours, and S3 delivers them on a best-effort basis.

## Configuration

```bash
aws configure --profile optistore-readonly
# .env
AWS_PROFILE=optistore-readonly
AWS_REGION=us-east-1
ALLOWED_S3_BUCKETS=my-bucket
```

In Docker, mount `~/.aws` read-only (see the commented line in `docker-compose.yml`) or pass environment credentials. On AWS compute, prefer an IAM role.

## Testing

`backend/tests/test_s3.py` runs every S3 code path against **moto** (in-memory S3): listing, scanning, archived-object handling, the bucket allow-list, dry runs, the write gate, verified transitions, and the plan approval → failure logging path. These tests have **not** been run against a real AWS account. Remaining manual checks with real credentials: the connection status, bucket listing with a restricted policy, a scan of a bucket with mixed classes, and a dry-run then real transition on a test object.
