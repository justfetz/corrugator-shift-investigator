# Deployment readiness and low-cost path

The current application is a local learning workbench. It is suitable for a local demonstration with the documented defects and is not ready to expose directly to the internet. The proposed first public release is the deterministic synthetic-data experience with OpenAI mode disabled, after completing the remaining calibration, live-quality and hosting requirements in [the September 7 review](REVIEW_2026-09-07.md). Period export and history defects are repaired in the working tree.

## Recommended first host

Use one small Google Cloud Run service for the public offline demo. Cloud Run can scale to zero and charges for consumed resources after its free tier; exact cost depends on region, traffic, build storage and network use. The official pricing page currently lists a request-based free tier, but treat that as a discount rather than a promise of a zero bill: <https://cloud.google.com/run/pricing>.

Cloud Run requires the container to listen on `0.0.0.0` and the injected `PORT`, while this workbench deliberately binds to `127.0.0.1`. That is one reason the current server must not be deployed unchanged. See the official container contract: <https://docs.cloud.google.com/run/docs/container-contract>.

## Release sequence

1. Replace the built-in single-threaded HTTP server with a production ASGI application and server. Preserve the existing request schemas, response shapes and tests.
2. Add a container with a non-root user, pinned production dependencies, a health endpoint and `0.0.0.0:$PORT` binding. Build and run it locally before creating cloud resources.
3. Add a `PUBLIC_DEMO_OFFLINE_ONLY` configuration. In public mode, remove the OpenAI option from configuration/UI and reject live-model requests in application code.
4. Replace the exact loopback Host/Origin rules with one configured canonical HTTPS origin. Never trust arbitrary forwarded host values. Keep the static-file allowlist, content-security policy, bounded JSON and text-only rendering.
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
