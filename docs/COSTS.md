# Cost model and strategies

## Pricing assumptions

`backend/app/pricing/s3_pricing.json` holds **approximate** us-east-1 public list prices (first pricing tier) for Standard, Standard-IA, Intelligent-Tiering, Glacier Instant Retrieval, Glacier Flexible Retrieval and Deep Archive. For each class it lists storage $/GB-month, GET and PUT/transition request prices, retrieval $/GB, restore request fees, minimum storage duration, minimum billable size, per-object overhead (40 KB for Glacier Flexible and Deep Archive), first-byte latency, and whether a restore is required.

Prices change. Verify them against <https://aws.amazon.com/s3/pricing/>, and supply your own file with `PRICING_FILE` (same structure, more regions allowed). Data transfer, taxes, free tier, volume tiers, and Intelligent-Tiering's optional archive tiers are **not** modelled.

## Per-object total cost over a horizon of *M* months

```
billable  = max(size, min_billable_bytes) + overhead_bytes
storage   = billable_GB × price × max(M, min_storage_days / 30)
requests  = reads × GET price
retrieval = reads × (size_GB × retrieval_per_GB + restore_request_fee)
transition= transition request price (one-time, only when the class changes)
monitoring= Intelligent-Tiering fee per object-month (objects ≥ 128 KB)
total     = storage + requests + retrieval + transition + monitoring
```

Intelligent-Tiering is modelled month by month: frequent tier in the first month, infrequent from month 2 when reads are below 1/month, and archive-instant from month 4 when reads are below 0.1/month. Objects under 128 KB stay in the frequent tier and are not monitored.

Expected reads per month come from observed history (last 90 days) when the prediction is reliable. Otherwise they come from `probability × reads_per_accessed_month` (a Settings value). If nothing is known, 1 read/month is assumed.

## Strategies

| Strategy | Allowed classes | Required saving | Trusted predictions |
| --- | --- | --- | --- |
| Conservative | Standard, Standard-IA, Intelligent-Tiering | ≥ 25 % | `ok` only |
| Balanced | + Glacier Instant Retrieval | ≥ 10 % | `ok`, `low` (flagged) |
| Aggressive | + Glacier Flexible, Deep Archive | ≥ 5 % | `ok`, `low` (flagged) |

Further rules:

- When access is unknown or untrusted, only Standard and Intelligent-Tiering are considered, because the latter has no retrieval fees.
- Files with high predicted access probability never go to restore-required classes or Glacier Instant Retrieval.
- Every recommendation lists its risks: minimum duration, minimum billable size, restore latency, retrieval fees, and low-reliability prediction.

## Savings vocabulary

| Term | Meaning |
| --- | --- |
| **Verified** | Exact duplicate bytes (identical SHA-256) |
| **Measured** | Compression output sizes produced by actually running the codec, and exported consolidation file sizes |
| **Potential** | Consolidation savings assuming sources would later be replaced by the reviewed draft |
| **Estimated / simulated** | Storage-class cost differences from the model above; local files are costed as if they were in S3 Standard |
| **Actual** | Only an executed action. For S3 class changes, OptiStore verifies the new class but cost savings remain estimates until they appear in AWS billing |

Moving an object to a cheaper storage class does **not** always reduce total cost. `tests/test_optimization.py::test_retrieval_costs_can_outweigh_storage_savings` demonstrates a case where Glacier Instant Retrieval is cheaper to store but more expensive overall.
