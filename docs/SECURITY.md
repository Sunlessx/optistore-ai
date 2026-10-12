# Security notes

OptiStore is designed as a **single-user, self-hosted tool** that reads sensitive documents. The controls below aim to keep it from becoming a file-system or cloud-account proxy.

| Risk | Control |
| --- | --- |
| Arbitrary file access / path traversal | Locations must resolve inside `ALLOWED_SCAN_ROOTS` (checked on registration and again at scan time). `safe_join` / `ensure_within` reject any path that escapes its root. Symlinks are not followed. The directory browser only lists inside the roots. |
| Modification of originals | Scanning, extraction and benchmarking open files read-only. Compressed copies and exports go to `data/outputs/`. Nothing in the code deletes or overwrites source files (a test checks hashes before and after). |
| Unrestricted deletion | There is no delete-file operation. Duplicate actions are review-only. "Remove location" deletes only OptiStore's analysis records. |
| Arbitrary command execution | No shell calls anywhere. Plans can contain only five predefined action types, each implemented in code. |
| Prompt injection via document content | No LLM is used. Document text is treated as data: it is never interpreted as instructions, and it is rendered in the UI as plain text or through a Markdown renderer that builds React elements (never raw HTML). A test checks that `<script>` and `<img onerror>` are not injected. |
| Malformed or malicious files | Size limits, extension/magic-number cross-checks, capped text extraction, per-file exception isolation (one bad file never aborts a scan), and ZIP inspection from the central directory only (no extraction; high expansion ratios are flagged). |
| Cloud writes | Disabled unless `S3_WRITE_ENABLED=true`. Each write also needs plan approval with explicit risk acknowledgement, validation against live object state (archived, > 5 GB and no-op changes are refused), and post-write verification. Everything is logged, including dry runs and failures. |
| AWS credentials | Standard credential chain only (profile, env vars, IAM role). Nothing is stored by OptiStore, and nothing secret is returned to the frontend (`/aws/status` returns the account id and ARN, never keys). Use the least-privilege policies in `docs/AWS.md`. |
| Unauthenticated access | Optional `API_KEY` (constant-time comparison) for all `/api` routes except health. CORS is restricted to configured origins. The API binds to localhost in development. Do not expose it to the internet without a reverse proxy, TLS and real authentication. |
| Secrets in Git | `.env` is git-ignored; only `.env.example` (no values) is committed. Compose passwords default to clearly dev-only values. |
| Third-party data sharing | Embeddings are computed locally. Network access is needed only for the one-time model download and for AWS, if configured. |
| Response hardening | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. Unhandled errors return a generic 500 without stack traces. |

**Known gaps:** no per-user accounts or roles; the API key is stored in the browser's localStorage; the audit log is append-only by convention, not cryptographically protected; there is no rate limiting.
