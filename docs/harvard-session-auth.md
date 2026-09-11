# Harvard Key session auth (this fork)

Canvas MCP on this fork calls Canvas REST `/api/v1` with a **Harvard Key
browser session**, not a personal Canvas access token and not an OAuth
developer key.

## Hypothesis

Canvas `/api/v1` accepts the logged-in Harvard Key session the same way the
HTML UI does: a `Cookie` header (and same-origin `fetch` in the signed-in
browser), without `Authorization: Bearer`.

School-box Chrome (CoS) already measured this for a signed-in profile:
`GET /api/v1/users/self` returned JSON with `login_required` false. This
Cloud VM does **not** have that session and must not be treated as live
Harvard proof. If a call returns `login_required: true` or HTML instead of
JSON, treat the session as unauthenticated and discard the hypothesis for
that request.

## Defaults

| Name | Default |
| --- | --- |
| `CANVAS_API_URL` | `https://canvas.harvard.edu/api/v1` |
| `CANVAS_ROLE` | `student` |
| `CANVAS_AUTH_MODE` | `session` |

`CANVAS_API_TOKEN` is optional. Do not set it for the Harvard student path.

## How to run on the School box

Use **placeholder env names only**. Never paste cookie values, Harvard Key
material, or tokens into chat, logs, README, commits, or PR text.

### 1. Cookie header (smallest path)

The Grok Bot / box runtime supplies the signed-in `Cookie` header. The server
injects it into httpx and does not log it.

```bash
export CANVAS_API_URL=https://canvas.harvard.edu/api/v1
export CANVAS_ROLE=student
export CANVAS_AUTH_MODE=session
export CANVAS_SESSION_COOKIE=<session-cookie-header>

uv run canvas-mcp-server --test
# then, for the MCP client:
uv run canvas-mcp-server
```

Or copy `env.template` to `.env` and set the same names there, then
`./start_canvas_server.sh`.

### 2. Chrome persistent profile (no cookie-DB scrape)

Reuses the School desktop Chrome profile for same-origin `/api/v1` calls
inside Playwright. This does **not** read Chrome's cookie database.

```bash
export CANVAS_API_URL=https://canvas.harvard.edu/api/v1
export CANVAS_ROLE=student
export CANVAS_AUTH_MODE=session
export CANVAS_CHROME_USER_DATA_DIR=<chrome-user-data-dir>
# optional:
# export CANVAS_CHROME_PROFILE_DIRECTORY=<profile-directory>

pip install 'canvas-mcp[browser]'
playwright install chrome

uv run canvas-mcp-server --test
```

If Chrome is already open on that profile, persistent launch may fail (profile
lock). Close Chrome, copy the user-data dir, or use `CANVAS_SESSION_COOKIE`
instead. Do not scrape cookie files from disk.

`--config` prints `Auth: session cookie (configured)` or
`Auth: chrome profile (configured)` — never the secret values.

## CoS / School local run plan

Record results on the signed-in School box only. Do not claim these from a
Cloud VM.

1. `GET /api/v1/users/self` — JSON, `login_required` false, caller id present.
2. `GET /api/v1/courses?enrollment_state=active` — JSON course list.
3. `GET /api/v1/users/self/todo` — JSON todo list (may be empty).

`canvas-mcp-server --test` performs step 1. Student tools `list_courses` /
`get_my_todo_items` cover steps 2–3 through the same session client.

## Student tools and the quiz gap

`CANVAS_ROLE=student` keeps the student + shared tool set (upcoming
assignments, todo, submission status, grades, peer-review todo, plus shared
course content).

Upstream Canvas MCP has **no dedicated quiz tools**. That gap is unchanged.
Planner/todo visibility is **not** dropped:

- `get_my_upcoming_assignments` includes planner items with
  `plannable_type` `quiz` (same as `assignment`).
- `get_my_todo_items` prints every `/users/self/todo` row Canvas returns,
  including quiz-typed items.

Quiz **taking** is still not offered (`STUDENT_WRITE_TOOLS` cannot enable it).

## Security

- Never log `Cookie`, `Authorization`, or CSRF header values.
- HTTP transport accepts `X-Canvas-Cookie` (preferred) or `X-Canvas-Token`.
  Server-side `CANVAS_SESSION_COOKIE` / `CANVAS_API_TOKEN` must stay unset in
  HTTP mode.
- Session writes may need a CSRF token; if `_csrf_token` is present in the
  supplied cookie header it is copied to `X-CSRF-Token` without logging.
