# Deployment readiness and low-cost path

The application runs two ways: the loopback learning workbench, and the hosted synthetic demo on Google Cloud Run with the Anthropic planner behind the guards below (the OpenAI key is never mounted there). What remains before anything beyond a demo is the release sequence further down; the September 7 review's calibration and export defects are repaired.

## Implemented: hosted public-demo mode (offline planner)

`PUBLIC_DEMO_ORIGIN` switches the server from the loopback workbench to the hosted synthetic demo. It is the smallest change that makes the existing app safe to put behind a platform's TLS proxy; it does not replace the release sequence below for anything beyond a demo.

| Setting | Effect |
| --- | --- |
| `PUBLIC_DEMO_ORIGIN` (required; comma-separated https origins) | Bind `0.0.0.0:$PORT` (default 7860) with a threading server. The loopback `Host` rule is dropped (the proxy owns the hostname); every `POST` must carry an `Origin` in this list, in addition to the session token. |
| `PUBLIC_DEMO_EMBED_ORIGINS` (optional) | `Content-Security-Policy: frame-ancestors` so the page can be framed by those sites. A Hugging Face Space page frames its own app, so include `https://huggingface.co` there. Default stays `'none'`. |
| `PUBLIC_DEMO_LIVE_MODEL=1` + `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` | Allows that provider's mode with the **server's** key under the existing reservation ledger. Off by default: the UI removes the live options and the server answers `400` to live requests. `/api/config` → `live_modes` lists what is enabled. |
| Visitor keys | Never read in public mode. `X-OpenAI-Key` and `X-Anthropic-Key` are ignored server-side and the key field is removed client-side. |

Investigations and PDFs run one at a time under a lock (the DuckDB handles and in-memory caches are not shared across threads); static files and `/api/health` do not wait. The in-memory 60-requests-per-minute window is per process, which on a single-container host is effectively service-wide; it is still not an abuse control. The model-budget ledger lives in the container's `artifacts/` directory and resets with the container unless the host provides persistent storage — keep the provider-side spend limit as the real cap.

**Anthropic on Cloud Run.** The deploy workflow mounts provider keys from Secret Manager **by name** and passes the guards through from repository variables; no key is ever stored in GitHub. Put the key in Secret Manager once (a key used only by this demo, with a console spend limit) and let the Cloud Run runtime service account read it:

```bash
printf '%s' "$KEY" | gcloud secrets create anthropic-api-key --data-file=-
gcloud secrets add-iam-policy-binding anthropic-api-key --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" --role roles/secretmanager.secretAccessor
```

Then set repository **variables** (not secrets): `ANTHROPIC_SECRET_NAME` = the Secret Manager secret name (`anthropic-api-key` above; empty = Anthropic is not mounted), `OPENAI_SECRET_NAME` (unset = OpenAI not mounted; the historical default `openai-api-key` must now be set explicitly), `PUBLIC_DEMO_LIVE_MODEL=1` to enable any live provider at all, and optionally `ANTHROPIC_MODEL`, `DEMO_RATE_LIMIT_PER_MIN`, `DEMO_DAILY_CAP`, `DEMO_MAX_TURNS` (the Worker's values are 20 / 400 / 14; see [ANTHROPIC_DEMO.md](ANTHROPIC_DEMO.md)). The workflow always sets `TRUST_PROXY=1` on Cloud Run, because its front end terminates TLS and appends the visitor's address as the rightmost `X-Forwarded-For` entry; without it every visitor would share the proxy's address and the per-IP limit would behave as a service-wide limit. With `PUBLIC_DEMO_LIVE_MODEL` unset the workflow clears the service's secrets. The daily counter lives in the same `artifacts/model-budget.sqlite3` file as the monthly budget, so with `--max-instances 2` and no persistent volume each instance counts separately and a redeploy resets both; the console spend limit remains the hard stop.

### Deploy to Google Cloud Run (free tier, scale to zero) — recommended

`.github/workflows/deploy-cloudrun.yml` deploys `main` from source (Cloud Build reads the `Dockerfile`) whenever the repository has the variable `GCP_PROJECT_ID` and the secret `GCP_SA_KEY`. It allow-lists every URL the service answers on — the legacy `<service>-<hash>-<region code>.a.run.app` form and the deterministic `<service>-<project number>.<region>.run.app` form, since which one `gcloud` reports as primary varies — as the comma-separated `PUBLIC_DEMO_ORIGIN`, sets `PUBLIC_DEMO_EMBED_ORIGINS` (variable, default `https://justfetz.github.io`), deploys with `--min-instances 0 --max-instances 2 --memory 1Gi --concurrency 20 --timeout 180`, names the revision `main-<sha7>-<run>-<attempt>` and routes 100% of the traffic to that name (below), and curls `/api/health` (and, when live mode is on, checks that `/api/config` reports a live mode). Optional variables: `WORKBENCH_SECRET` (any string; rotates the shared session token, see Current known limitations), `GCP_REGION` (default `us-central1`), `CLOUD_RUN_SERVICE` (default `corrugator-shift-investigator`), and the live-mode set above: `PUBLIC_DEMO_LIVE_MODEL`, `ANTHROPIC_SECRET_NAME`, `OPENAI_SECRET_NAME`, `ANTHROPIC_MODEL`, `DEMO_RATE_LIMIT_PER_MIN`, `DEMO_DAILY_CAP`, `DEMO_MAX_TURNS`. Secrets are mounted as `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from Secret Manager (`<name>:latest`) at start-up; the only GitHub secret is `GCP_SA_KEY`.

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

Responses over 1 KB are gzipped when the client accepts it (an answer is 20–145 KB raw, 3–21 KB on the wire; the PDF is left alone), and `app.js`, `style.css` and the pages carry an `ETag` with `Cache-Control: no-cache`, so a returning visitor's browser revalidates them with `If-None-Match` and gets a 304 instead of a download (issue #33). API responses stay `no-store`. The page builds the trace table two animation frames after the answer, so the tables and charts paint first (a frame callback runs before its own paint).

The image builds the two synthetic months once (`python -m investigator.prebuilt /app/prebuilt` in the Dockerfile, issue #19) and the server loads them at start in about 150 ms instead of generating for about 2 s; `GET /api/health` reports `store.source` (`prebuilt` or `generated`) and `store.boot_ms`. A prebuilt folder that does not match the code or the inputs is ignored, so a stale image falls back to generating rather than serving wrong data.

Paste the key file's contents into the GitHub secret `GCP_SA_KEY`, set the variable `GCP_PROJECT_ID`, delete `key.json`, then merge or run **Deploy Cloud Run**. The service-account key is a shortcut for a one-person project; Workload Identity Federation is the keyless alternative when this matters. Set a billing budget alert on the project regardless.

#### Preview URL per pull request

`.github/workflows/preview.yml` (issue #41) gives every same-repository pull request its own URL before anything reaches visitors. On open, push and reopen it deploys the PR merged onto `main` as a **tagged revision with no traffic** — `gcloud run deploy … --no-traffic --tag pr-<n> --revision-suffix pr-<n>-<sha7>-<run>` — which Cloud Run serves at `https://pr-<n>---<service host>` while the live demo keeps 100% of the traffic on its own revision. The revision scales to zero like the service, so a preview costs nothing while nobody looks at it. The workflow posts the URL as one PR comment (edited in place on every push). On close it removes the tag, which is what stops the URL from serving, and deletes the revision — with one Cloud Run rule to know: a service's *latest created* revision cannot be deleted, and a fresh preview is exactly that until the next deploy of any kind. So every preview run (deploy or close) sweeps all orphaned `<service>-pr-<n>-` revisions — no tag, no traffic, not the one just deployed — and reports, rather than fails on, the one Cloud Run still protects; the next `main` deploy or preview deletes it. Untagged, zero-traffic revisions cost nothing in the meantime. Fork PRs never reach the job: they cannot read `GCP_SA_KEY` and the job's `if` requires the head repository to be this one. The close job checks out `main`, not the PR head, because the helper script it runs is tooling the PR branch may predate (#53).

The preview revision inherits the serving template (scaling, demo guards, mounted secrets, `WORKBENCH_SECRET`) and changes one variable. The server's `Origin` check is an exact match, so `PUBLIC_DEMO_ORIGIN` on the preview revision is production's list followed by the `pr-<n>---` form of each entry; `.github/scripts/preview_origins.py` computes it from `gcloud run services describe` and drops any tag URL an earlier preview left behind, so the list never grows. Production's order is kept, so `origins[0]` — the session-token seed when `WORKBENCH_SECRET` is unset — is the same on both. The next `main` deploy sets `PUBLIC_DEMO_ORIGIN` outright and so resets the template. One side effect to know: the `main` workflow collects every `*.run.app` URL in the service status into its allow-list, which now includes any live tag URL; harmless (same service), and it disappears once the tags are gone.

The smoke test proves three things or fails the run: the tag carries 0% and traffic still sums to 100% on the production revisions; `GET /api/health` answers; and a `POST /api/ask` sent with `Origin: <preview URL>` and the session token returns 200, so the allow-list change works. Previews deploy from source like `main` (Cloud Build), about three to five minutes per push. `gcloud run deploy --tag` with a tag already on another revision moves it; if two pushes race, the per-PR concurrency group cancels the older run, while a close queues behind an in-flight deploy rather than cancelling it (so the tag it then removes is the final one). Two events can also arrive seconds apart for one PR (a reopen carries the head GitHub knew at close time, then a synchronize carries the new one) and the run for the older head may be the one that survives, so every deploy run first compares the event's head with the live PR head and, when it moved, fetches the current `refs/pull/<n>/merge` and deploys that; the comment names the head it deployed. A cancelled run can leave a revision that the next push or the close job deletes, since cleanup goes by the `<service>-pr-<n>-` name prefix.

**Runs of different pull requests, and the `main` deploy, write the same service and are not serialized against each other.** Cloud Run updates are optimistically locked: `gcloud` reads the service, builds the image when deploying from source, reads the service again (its source says why: "building takes a while which leaves a long time for potential write conflicts") and writes it back naming the version it just read. When another run's write lands in that last second, Cloud Run answers `ABORTED: Conflict for resource '<service>': version '…' was specified but current version is '…'` and `gcloud` exits 1 without retrying; PR #50's preview failed this way after its 50-second build while PR #54's close job was removing its tag ([run 16, attempt 1](https://github.com/justfetz/corrugator-shift-investigator/actions/runs/42676968504)), and a plain re-run passed. GitHub serializes runs only within one concurrency group (`preview-pr-<n>` per pull request, `cloud-run-deploy` for `main`), and one group for all of them would not do: GitHub keeps at most one pending run per group and cancels the earlier pending one when another arrives, so a third pull request's push would cancel the second's preview outright, and every preview would also wait behind a three-minute `main` deploy. So the writes are retried instead. Every `gcloud` command that changes the service — the preview deploy, the close job's `--remove-tags`, every `revisions delete`, and on `main` the deploy, the allow-list `services update` and the traffic step — runs through `.github/scripts/cloud_run_update.sh`, which runs the command again when Cloud Run rejected its write with that `ABORTED: Conflict for resource` message, and only then: four attempts in all, with a growing pause between them (15–30 s, then 30–45 s, then 45–60 s; the jitter so that two runs that collided do not retry in step). A rejected write changed nothing, so the repeat is the same command: `gcloud` reads the current version, and a `--source` deploy rebuilds, about a minute. Each retry is a `::notice::` annotation on the run; when the fourth attempt is rejected too, the step fails with `gcloud`'s status under a `::warning::`, and a re-run of the job is the remedy. Any other error is not retried. The two sweeps can also race for the same orphan revision: a `revisions delete` that fails is followed by a `revisions describe`, and a revision that is already gone (the describe answers NOT_FOUND) counts as deleted, while a describe that fails for any other reason is treated as the failed delete was, a warning in the deploy job and a failure in the close job (before, the close job failed on `could not be found` when the other run's sweep had won). `tests/test_preview_workflow.py` runs the helper under `bash` against a stand-in `gcloud` that rejects the first write, and checks that every write in both workflows goes through it. One bootstrap rule follows from the close job taking its scripts from `main`: a pull request that adds a script the close job calls (this one did) has to merge; closed unmerged, its close job checks out a `main` without the script and fails before removing the tag, which `gcloud run services update-traffic <service> --remove-tags pr-<n>` then does by hand.

**The one change previews force on the `main` deploy.** gcloud documents that after any `--no-traffic` deploy the service's traffic is pinned to a *named* revision, and later deploys without a traffic flag get 0% — the live demo would freeze on the revision that was serving when the first preview ran, and the health check would keep passing against it. So `deploy-cloudrun.yml` names the revision it deploys (`--revision-suffix main-<sha7>-<run>-<attempt>`; the allow-list `services update`, when it runs, names its own revision `…-urls`) and after the deploy runs `gcloud run services update-traffic --to-revisions <that revision>=100`, then asserts that this revision carries 100%. Not `--to-latest`: LATEST follows whichever revision became ready last, and a preview can land between the deploy and the traffic step (the runs are not serialized, see above), so `--to-latest` would have put production's traffic on a pull request's revision until the next deploy of `main`, with every check still green. Percentages only: other PRs' tags survive. If a `main` deploy ever fails on the assertion, `gcloud run services describe <service> --format='yaml(status.traffic)'` shows what is serving.

### Deploy to a Hugging Face Space (Docker Spaces require a PRO subscription since July 2026)

1. Create a Space with the **Docker** SDK (e.g. `justfetz/corrugator-shift-investigator`), public visibility.
2. In the Space's Settings → Variables add `PUBLIC_DEMO_ORIGIN` = the Space's direct URL (`https://<owner>-<space>.hf.space`) and `PUBLIC_DEMO_EMBED_ORIGINS` = `https://huggingface.co,https://justfetz.github.io`.
3. In this GitHub repository add the secret `HF_TOKEN` (a Hugging Face token with write access) and the repository variable `HF_SPACE` = `<owner>/<space>`.
4. Push to `main` or run the **Deploy Space** workflow. It force-pushes the tree with `deploy/huggingface/README.md` as the Space README (the front matter tells the Space it is a Docker app on port 7860).
5. Open the Space; `/api/health` returns `{"ok": true, "public": true, "store": {"source": "prebuilt", "boot_ms": ..., "dataset_hash": "..."}}`.

The same container runs on Fly.io or Render with `PORT` injected; set `PUBLIC_DEMO_ORIGIN` to the service's https origin.

## Recommended first host

Use one small Google Cloud Run service for the public offline demo. Cloud Run can scale to zero and charges for consumed resources after its free tier; exact cost depends on region, traffic, build storage and network use. The official pricing page currently lists a request-based free tier, but treat that as a discount rather than a promise of a zero bill: <https://cloud.google.com/run/pricing>.

Cloud Run requires the container to listen on `0.0.0.0` and the injected `PORT`; the default workbench binds to `127.0.0.1`, and public-demo mode (above) is what makes the container contract hold. See the official container contract: <https://docs.cloud.google.com/run/docs/container-contract>.

## Release sequence

1. Replace the built-in HTTP server with a production ASGI application and server if traffic ever warrants it. Preserve the existing request schemas, response shapes and tests. (Public-demo mode uses the standard-library threading server with a serialized investigation lock, which is enough for a demo.)
2. Container with a non-root user, a health endpoint and `0.0.0.0:$PORT` binding: done (`Dockerfile`, `/api/health`; `/healthz` also answers, but Google's front end reserves that path on `*.run.app` and never forwards it, so the smoke test and container healthcheck use `/api/health`).
3. Offline-only public configuration: done (`PUBLIC_DEMO_ORIGIN` without `PUBLIC_DEMO_LIVE_MODEL`). Server-held Anthropic live mode with per-IP and daily round guards: done, behind the `DEMO_*` variables, and exercised against the real endpoint by the recorded live goldens.
4. Configured canonical origins instead of the loopback Host/Origin rules: done. The static-file allowlist, content-security policy, bounded JSON and text-only rendering are unchanged.
5. Make PDF generation stateless or use a durable, short-lived report reference. The present report cache is process memory; another instance or a restart will not have it.
6. Put a durable edge or datastore-backed rate limit in front of `/api/ask` and `/api/report`. The current in-memory 60-request window is per process and is not a public abuse control.
7. If anonymous public querying is allowed, add an abuse challenge and validate it on the server. Cloudflare says Turnstile client tokens require server-side Siteverify validation, are single-use and expire after five minutes: <https://developers.cloudflare.com/turnstile/get-started/server-side-validation/>.
8. Deploy staging with minimum instances zero and a small service-level maximum instance count. Google documents maximum instances as a cost guard and recommends starting at three, while noting that the limit can briefly be exceeded: <https://docs.cloud.google.com/run/docs/configuring/max-instances>.
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
- Run one deliberate live OpenAI evaluation (`evals/run.py --live --provider openai`) and record its fixtures before offering that provider anywhere but the local workbench.
- Expand synthetic scenario calibration and add more than one month before treating trends as representative.
- Define a privacy and retention policy before connecting real production data. No private plant records belong in the public demo.

## Current known limitations

- Automated checks pass, including quantity/time audits, narrative validation/fallback, period PDFs and retained comparison history. They do not certify model prose quality, all question semantics or visual PDF layout. See REVIEW_2026-09-07.md and TESTING.md.
- The web server, rate limit, report cache and model-budget database are single-process local implementations. Since issue #17 the session token is **shared across instances and restarts** in public mode (derived from `WORKBENCH_SECRET`, or from the demo origin when that is unset; the value is public by design, since `/api/config` serves it, and the CSRF defence is the Origin allowlist plus the custom header), the browser refreshes `/api/config` once and resends on a 403, and a conversation id this instance has never seen starts a fresh conversation (the answer carries `session_reset: true` and the page says so) instead of failing with "Session expired". The PDF report cache is still per instance: with two instances, a PDF requested from the instance that did not produce the answer returns 400 "Report expired; run the investigation again"; issue #20 makes the report a GET of the same URL state, which removes the cache.
- The data covers September 2026 only. Prior calendar/accounting periods are correctly shown as unavailable.
- The synthetic paper-run-size distribution is plausible demo behavior supplied by code, not a measured plant distribution.
- Notes and reason codes are synthetic observations; recurrence does not prove causation.
- Email is not implemented. Live OpenAI access is implemented locally and has no recorded fixtures; the Anthropic path is the one scored by the golden set.
- Local browser checks verified calendar-month and 4-4-5 heatmap shading, complete day details, daily offline investigation, OpenAI mode controls, and the question-guidance link on September 6, 2026. Broader browser and assistive-technology QA remains before public release. The implementation follow-up records the newer test and browser results.
