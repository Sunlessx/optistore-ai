# API usage

Interactive OpenAPI documentation: <http://localhost:8000/docs>. All endpoints are under `/api`. If `API_KEY` is set, add `-H "X-API-Key: <key>"` to every call except `/api/health`.

Errors are returned as JSON, `{"detail": "..."}`, with these status codes: 400 for invalid input or business rules, 401 for a missing or invalid key, 403 for a path or bucket that is not authorised, 404 for not found, 409 for a state conflict (for example, executing an unapproved action), 422 for schema validation failures, and 503 for an unavailable dependency.

```bash
API=http://localhost:8000/api

# Register and scan a folder (must be inside ALLOWED_SCAN_ROOTS)
curl -X POST $API/locations -H "Content-Type: application/json" \
     -d '{"kind":"local","path":"C:/Users/me/Documents/OptIA/sample_data"}'
curl -X POST $API/scans -H "Content-Type: application/json" -d '{"location_id":1}'
curl $API/scans/1                                   # progress: status, phase, processed/total
curl -X POST $API/scans/1/cancel                    # cancellation

# Inventory (paginated, filterable, sortable)
curl "$API/files?page=1&page_size=50&category=document&sort=size&order=desc"
curl "$API/files/stats"
curl "$API/files/12"                                # metadata, text preview, duplicates, similar, compression, prediction

# Duplicates and similarity
curl $API/duplicates/summary
curl $API/duplicates
curl "$API/similarity?min_score=0.75"
curl "$API/compare?a=19&b=20"                       # shared / unique / conflicts

# Semantic search
curl "$API/search?q=Find%20the%20contract%20renewal%20conditions&limit=5"
curl -X POST "$API/search/reindex?force=true"

# Consolidation
curl -X POST $API/consolidations -H "Content-Type: application/json" \
     -d '{"file_ids":[19,21,20],"title":"Project Alpha"}'
curl -X PUT  $API/consolidations/1 -H "Content-Type: application/json" -d '{"markdown":"# Edited..."}'
curl -X POST $API/consolidations/1/export -H "Content-Type: application/json" -d '{"format":"md"}'
curl -OJ $API/consolidations/1/download

# Compression
curl -X POST $API/compression/files/12
curl -X POST $API/compression/batch -H "Content-Type: application/json" -d '{"limit":100}'
curl "$API/compression/results?best_only=true"

# Machine learning
curl -X POST $API/ml/train -H "Content-Type: application/json" -d '{"dataset":"synthetic","n_files":1500,"horizon_days":30}'
curl -X POST $API/ml/access-events/import -F "file=@access_log.csv"       # CSV columns: path,timestamp
curl -X POST $API/ml/predict -H "Content-Type: application/json" -d '{}'

# Costs and forecasting
curl -X POST $API/costs/simulate -H "Content-Type: application/json" -d '{"strategy":"balanced","horizon_months":12}'
curl -X POST $API/costs/compare  -H "Content-Type: application/json" -d '{}'
curl -X POST $API/costs/what-if  -H "Content-Type: application/json" \
     -d '{"object_size_mb":100,"object_count":1000,"reads_per_object_per_month":0,"strategy":"aggressive"}'
curl "$API/forecast?months=12&growth_pct=5"

# Plans: generate -> approve -> dry run -> execute
curl -X POST $API/plans -H "Content-Type: application/json" -d '{"strategy":"balanced"}'
curl -X POST $API/plans/1/actions/3/decision -H "Content-Type: application/json" -d '{"approve":true}'
curl -X POST $API/plans/1/actions/3/execute  -H "Content-Type: application/json" -d '{"dry_run":true}'
curl -X POST $API/plans/1/actions/3/execute  -H "Content-Type: application/json" -d '{"dry_run":false}'
#   lossy or cloud actions: {"approve":true,"acknowledge_risks":true}
curl -X POST $API/plans/1/execute -H "Content-Type: application/json" -d '{"dry_run":false}'   # all approved

# AWS, audit trail, settings, dashboard
curl $API/aws/status
curl $API/aws/buckets
curl "$API/operations?page=1&status=failure"
curl $API/settings
curl -X PUT $API/settings -H "Content-Type: application/json" -d '{"values":{"similarity_threshold":0.8}}'
curl $API/dashboard
```
