# Deployment readiness and low-cost path

The current application is a local learning workbench. It is suitable for a local demonstration with the documented defects and is not ready to expose directly to the internet. The proposed first public release is the deterministic synthetic-data experience with OpenAI mode disabled, after completing the remaining calibration, live-quality and hosting requirements in [the September 7 review](REVIEW_2026-09-07.md). Period export and history defects are repaired in the working tree.

## Implemented: hosted public-demo mode (offline planner)

`PUBLIC_DEMO_ORIGIN` switches the server from the loopback workbench to the hosted synthetic demo. It is the smallest change that makes the existing app safe to put behind a platform's TLS proxy; it does not replace the release sequence below for anything beyond a demo.

| Setting | Effect |
| --- | --- |
| `PUBLIC_DEMO_ORIGIN` (required; comma-separated https origins) | Bind `0.0.0.0:$PORT` (default 7860) with a threading server. The loopback `Host` rule is dropped (the proxy owns the hostname); every `POST` must carry an `Origin` in this list, in addition to the session token. |
| `PUBLIC_DEMO_EMBED_ORIGINS` (optional) | `Content-Security-Policy: frame-ancestors` so the page can be framed by those sites. A Hugging Face Space page frames its own app, so include `https://huggingface.co` there. Default stays `'none'`. |
| `PUBLIC_DEMO_LIVE_MODEL=1` + `OPENAI_API_KEY` | Allows OpenAI mode with the **server's** key under the existing reservation ledger. Off by default: the UI removes the OpenAI option and the server answers `400` to live requests. |
| Visitor keys | Never read in public mode. `X-OpenAI-Key` is ignored server-side and the key field is removed client-side. |

Investigations and PDFs run one at a time under a lock (the DuckDB handles and in-memory caches are not shared across threads); static files and `/api/health` do not wait. Two in-memory limits apply to every `POST`: a per-visitor token bucket (10 requests at once, then one every 6 seconds; the visitor is the first `X-Forwarded-For` hop in public mode, the socket peer locally) and the service-wide 60-requests-per-minute window. With one instance both are effectively service-wide; neither is a durable abuse control. In public live mode every provider request carries a `safety_identifier`: a salted SHA-256 digest of the visitor address (`VISITOR_ID_SALT` sets the salt), never the address itself. The model-budget ledger lives at `MODEL_BUDGET_PATH` (default `artifacts/model-budget.sqlite3` inside the container, which resets with the container); on Cloud Run a mounted Cloud Storage bucket keeps it across restarts (below). Keep the provider-side spend limit as the real cap regardless.

### Deploy to Google Cloud Run (free tier, scale to zero) — recommended

`.github/workflows/deploy-cloudrun.yml` deploys `main` from source (Cloud Build reads the `Dockerfile`) whenever the repository has the variable `GCP_PROJECT_ID` and the secret `GCP_SA_KEY`. It allow-lists every URL the service answers on — the legacy `<service>-<hash>-<region code>.a.run.app` form and the deterministic `<service>-<project number>.<region>.run.app` form, since which one `gcloud` reports as primary varies — as the comma-separated `PUBLIC_DEMO_ORIGIN`, sets `PUBLIC_DEMO_EMBED_ORIGINS` (variable, default `https://justfetz.github.io`), deploys with `--min-instances 0 --max-instances 1 --memory 1Gi --concurrency 20 --timeout 180`, and curls `/api/health`. Optional variables: `GCP_REGION` (default `us-central1`), `CLOUD_RUN_SERVICE` (default `corrugator-shift-investigator`), `CLOUD_RUN_MAX_INSTANCES` (default `1`; the server serializes investigations, so a second instance only doubles the cost ceiling and splits the limiters), `BUDGET_BUCKET` (below).

#### Durable budget ledger (`BUDGET_BUCKET`)

Set the repository variable `BUDGET_BUCKET` to a Cloud Storage bucket name and the workflow mounts it at `/mnt/ledger` (gen2 execution environment, Cloud Storage FUSE) and sets `MODEL_BUDGET_PATH=/mnt/ledger/model-budget.sqlite3`, so the monthly reservation ledger survives container restarts and redeploys. One-time setup (Cloud Shell; the service runs as the default compute service account unless you changed it):

```bash
PROJECT_ID=felix-august; REGION=us-central1; BUCKET="${PROJECT_ID}-investigator-ledger"
gcloud storage buckets create "gs://${BUCKET}" --project "$PROJECT_ID" --location "$REGION" --uniform-bucket-level-access
NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" --role roles/storage.objectUser
```

Then set `BUDGET_BUCKET=<bucket name>` in the repository variables and redeploy. SQLite on a FUSE mount is safe here only because there is a single writer: one instance (`CLOUD_RUN_MAX_INSTANCES=1`) and the server's investigation lock. Do not raise the instance count with the bucket mounted. Without `BUDGET_BUCKET` the ledger stays in the container and resets with it, which is acceptable while live mode is off (`PUBLIC_DEMO_LIVE_MODEL=0`, the default).

One-time project setup (Cloud Shell):

```bash
PROJECT_ID=<globally-unique-id>          # e.g. corrugator-demo-<something>
gcloud projects create "$PROJECT_ID" && gcloud config set project "$PROJECT_ID"
# attach billing to the project in the console (Billing → link account); the demo stays inside the free tier
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud iam service-accounts create github-deployer --display-name "GitHub Actions deployer"
SA="github-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin roles/storage.admin \
            roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" --role "$ROLE" --quiet >/dev/null
done
NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
  --role roles/cloudbuild.builds.builder --quiet >/dev/null   # the build runs as the default compute service account
gcloud iam service-accounts keys create key.json --iam-account "$SA" && cat key.json
```

Paste the key file's contents into the GitHub secret `GCP_SA_KEY`, set the variable `GCP_PROJECT_ID`, delete `key.json`, then merge or run **Deploy Cloud Run**. The service-account key is a shortcut for a one-person project; Workload Identity Federation is the keyless alternative when this matters. Set a billing budget alert on the project regardless.

### Deploy to a Hugging Face Space (Docker Spaces require a PRO subscription since July 2026)

1. Create a Space with the **Docker** SDK (e.g. `justfetz/corrugator-shift-investigator`), public visibility.
2. In the Space's Settings → Variables add `PUBLIC_DEMO_ORIGIN` = the Space's direct URL (`https://<owner>-<space>.hf.space`) and `PUBLIC_DEMO_EMBED_ORIGINS` = `https://huggingface.co,https://justfetz.github.io`.
3. In this GitHub repository add the secret `HF_TOKEN` (a Hugging Face token with write access) and the repository variable `HF_SPACE` = `<owner>/<space>`.
4. Push to `main` or run the **Deploy Space** workflow. It force-pushes the tree with `deploy/huggingface/README.md` as the Space README (the front matter tells the Space it is a Docker app on port 7860).
5. Open the Space; `/api/health` returns `{"ok": true, "public": true}`.

The same container runs on Fly.io or Render with `PORT` injected; set `PUBLIC_DEMO_ORIGIN` to the service's https origin.

## Recommended first host

Use one small Google Cloud Run service for the public offline demo. Cloud Run can scale to zero and charges for consumed resources after its free tier; exact cost depends on region, traffic, build storage and network use. The official pricing page currently lists a request-based free tier, but treat that as a discount rather than a promise of a zero bill: <https://cloud.google.com/run/pricing>.

Cloud Run requires the container to listen on `0.0.0.0` and the injected `PORT`; the default workbench binds to `127.0.0.1`, and public-demo mode (above) is what makes the container contract hold. See the official container contract: <https://docs.cloud.google.com/run/docs/container-contract>.

## Release sequence

1. Replace the built-in HTTP server with a production ASGI application and server if traffic ever warrants it. Preserve the existing request schemas, response shapes and tests. (Public-demo mode uses the standard-library threading server with a serialized investigation lock, which is enough for a demo.)
2. Container with a non-root user, a health endpoint and `0.0.0.0:$PORT` binding: done (`Dockerfile`, `/api/health`; `/healthz` also answers, but Google's front end reserves that path on `*.run.app` and never forwards it, so the smoke test and container healthcheck use `/api/health`).
3. Offline-only public configuration: done (`PUBLIC_DEMO_ORIGIN` without `PUBLIC_DEMO_LIVE_MODEL`).
4. Configured canonical origins instead of the loopback Host/Origin rules: done. The static-file allowlist, content-security policy, bounded JSON and text-only rendering are unchanged.
5. Make PDF generation stateless or use a durable, short-lived report reference. The present report cache is process memory; another instance or a restart will not have it.
6. Put a durable edge or datastore-backed rate limit in front of `/api/ask` and `/api/report`. The per-visitor token bucket and the 60-request window are in memory (service-wide with one instance) and are not a public abuse control.
7. If anonymous public querying is allowed with the live model on, add an abuse challenge and validate it on the server. Cloudflare says Turnstile client tokens require server-side Siteverify validation, are single-use and expire after five minutes: <https://developers.cloudflare.com/turnstile/get-started/server-side-validation/>. Not needed while the demo is offline-only.
8. Minimum instances zero and maximum one: done in the workflow. Google documents maximum instances as a cost guard and notes the limit can briefly be exceeded: <https://docs.cloud.google.com/run/docs/configuring/max-instances>.
9. Add cloud billing alerts, request/error/latency dashboards, dependency and container scanning, deploy logs, and an immediate disable/rollback procedure. Load-test the period report and PDF endpoints with synthetic requests.
10. Map the custom domain only after staging passes the security and behavior checks. Cloud Run terminates TLS for the service; keep the application HTTP-only behind that boundary as required by the container contract.

## Adding live OpenAI later

Keep the README and `/guide#asking-questions` guidance visible at the question entry point. Before enabling hosted live mode, evaluate broad requests such as “How can we be better?”, unsupported savings estimates, and requests to prescribe machine adjustments. Check that outputs remain grounded in the selected scope, unavailable evidence stays unavailable, and users can distinguish recorded losses from proven causes or achievable gains. The current model selects tools and writes an optional daily interpretation; application code calculates evidence, validates fact references and inserts numeric values. Documentation is guidance, not an enforcement mechanism, and a completed tool run is not proof of useful advice.

Require user identity before enabling paid model calls. Use a separate OpenAI project/API key for this site, keep it on the backend, and add durable per-user, per-IP and service-wide limits before each provider request. Do not expose the site's key in browser code. OpenAI's current guidance says never ship an API key in a client application and recommends spend thresholds and key rotation: <https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety> and <https://help.openai.com/en/articles/8304786>.

Store the server key in a managed secret service with least-privilege access and audited rotation. Google Secret Manager's guidance recommends least privilege, separate environments, version pinning and access logs: <https://docs.cloud.google.com/secret-manager/docs/best-practices>.

Send a stable hashed account or session identifier to the model provider if anonymous previews are later allowed; OpenAI documents `safety_identifier` for linking abuse signals without sending direct identity: <https://help.openai.com/en/articles/5428082>.

The local BYOK field is a learning feature. Do not carry browser-entered visitor keys into the public launch. A hosted BYOK proxy still receives those credentials and creates additional trust, logging and disclosure obligations.

## Outstanding decisions

- Public-demo release also requires the order/quantity calibration described in METRICS.md. Order identities and remaining quantities now reconcile across shifts. Quantity sampling, cutoff alignment, accepted delivery and replacement orders still need further calibration. Keep requested waste/location categories distinct from proven causes.
- The daily LLM narrative is implemented with field-reference validation and deterministic fallback. Follow WORKBENCH.md and run live quality evaluation before enabling it publicly.

- Confirm the plant's actual 4-4-5 fiscal-year start date and week-start convention. September 1, 2026 is only a demo anchor.
- Confirm plant timezone and daylight-saving treatment for the 07:00 production-day boundary.
- Decide whether public access is fully anonymous, invitation-only or account-based. This changes the correct rate-limit and model-budget design.
- Choose the final host, region, domain and acceptable monthly cost ceiling.
- Decide whether PDF reports must survive restarts and multiple instances.
- Select an email provider, verify a sender/domain, and define consent, unsubscribe and retention rules before adding delivery.
- Run one deliberate live OpenAI evaluation and record observed quality, latency and cost before enabling it for anyone else.
- Expand synthetic scenario calibration and add more than one month before treating trends as representative.
- Define a privacy and retention policy before connecting real production data. No private plant records belong in the public demo.

## Current known limitations

- Automated checks pass, including quantity/time audits, narrative validation/fallback, period PDFs and retained comparison history. They do not certify model prose quality, all question semantics or visual PDF layout. See REVIEW_2026-09-07.md and TESTING.md.
- The web server, session token, rate limit, report cache and model-budget database are single-process local implementations.
- The data covers September 2026 only. Prior calendar/accounting periods are correctly shown as unavailable.
- The synthetic paper-run-size distribution is plausible demo behavior supplied by code, not a measured plant distribution.
- Notes and reason codes are synthetic observations; recurrence does not prove causation.
- Email is not implemented. Live OpenAI access is implemented locally but has not been validated with a real key.
- Local browser checks verified calendar-month and 4-4-5 heatmap shading, complete day details, daily offline investigation, OpenAI mode controls, and the question-guidance link on September 6, 2026. Broader browser and assistive-technology QA remains before public release. The implementation follow-up records the newer test and browser results.
