# CLICD Integration (containers sync & reset-password)

This document describes the new integration added in branch `feat/clicd-reset-ssh`.

What this adds

- Server routes:
  - `GET /api/containers/[id]/sync` — proxies to CLICD GET `/api/v1/containers/{id}` and returns container info.
  - `POST /api/containers/[id]/reset-password` — calls CLICD POST `/api/v1/containers/{id}/reset-password`, and if an email is provided and SMTP is configured, sends a notification email to the user with the new SSH info.

- UI component:
  - `components/ui/ContainerCard.tsx` — a client React component that displays container fields (ip, ipv6, ssh_port, ssh_password, os, package) and provides buttons to "同步信息" and "重置SSH密码". On successful reset, it sets the card message to `*请重置SSH密码，查收邮件`.

Environment variables / Secrets (must configure in deployment / GitHub Secrets)

- CLICD_BASE (optional) — default: `http://51.159.67.180:8999`
- CLICD_API_KEY — API key for CLICD (do NOT commit this to the repo)
- SMTP_HOST — SMTP server host for sending emails
- SMTP_PORT — SMTP port (default 587)
- SMTP_USER — SMTP auth user (from)
- SMTP_PASS — SMTP auth password

Notes & assumptions

- The code uses `Authorization: Bearer <CLICD_API_KEY>` header for CLICD requests. If your CLICD instance requires a different header (e.g. `X-API-Key`), update the route code accordingly.
- The reset endpoint will send email only if an `email` field is provided in the POST body or SMTP credentials are configured in the environment.
- Passwords and API keys are never written to the repository. Configure them as Secrets in your environment.

Testing steps

1. Add the required environment variables / secrets to your deployment (or run locally with a .env file).
2. Start the app and open the dashboard / page that uses `ContainerCard`.
3. Click "同步信息" to fetch the latest container data from CLICD.
4. Click "重置SSH密码" (confirm) to trigger a password reset. Provide `email` when calling the route if you want the server to send the notification (the UI attempts to pass `userEmail` if configured).

If email sending fails, the reset operation still succeeds; the server returns a `207` with an `email_warning` describing the error.
