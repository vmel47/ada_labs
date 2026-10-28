# Repair Queue v1 — frozen development task

Build a working local full-stack repair desk application. Use Python 3.11+ standard library for the server, SQLite persistence, and HTML/CSS/JavaScript for the browser. No package downloads, external services, accounts, or network dependencies. Start with the supplied empty project; do not copy another implementation. You may write your own tests.

Deliver `app.py` and any local static assets. A standard HTTP adapter `server.py` is supplied in the starter; keep it unchanged. Start the resulting application with `python server.py --port PORT --data PATH`. It binds **127.0.0.1** only. The data argument is the SQLite database filename; create it if missing. `GET /health` returns HTTP 200 JSON. `GET /` serves the UI. No external fonts/scripts. Document how to run and test.

`app.py` exports `handle_request(method, target, body, headers, database)`, returning `(status, response_headers, response_body_bytes)`. `method` and `target` are strings; `body` is bytes, `headers` a dictionary, and `database` a filename. The handler performs the real routing, validation, database operations and static-file responses. It must not start a server or perform I/O at import time. Avoid stdout logging (reserved for the isolated transport); diagnostics may use stderr.

For isolated evaluation, the supplied HTTP adapter is replaced by a byte transport carrying the same real request and response values. Browser requests still execute your actual backend and SQLite operations. The test does not use mocked application responses. This benchmark measures the application handler and UI; alternative HTTP server implementations and network-stack performance are outside its scope.

## Application behavior

Each repair has an integer `id`, `customer` (trimmed, 1–80 characters), `device` (trimmed, 1–120), `issue` (trimmed, 1–500), `status` (`new`, `in_progress`, `done`), and `priority` (`low`, `normal`, `high`). New repairs default to status `new` and priority `normal`. Maintain data across reload and process restart. Edits update the original record without duplicates.

- `GET /api/repairs` returns a JSON array, newest id first. Optional `q` searches customer/device/issue case-insensitively. Optional `status` and `priority` filters intersect with search. Empty filters mean all records.
- `POST /api/repairs` accepts JSON record fields and returns the created record with HTTP 201.
- `PUT /api/repairs/ID` accepts a full replacement of the five mutable fields, returns the updated record with HTTP 200; missing record returns 404 JSON.
- Validation is authoritative on the server. Wrong types (including null), missing/blank required fields, invalid enums, overlong strings, malformed JSON, and non-object JSON return HTTP 400 JSON with an `error` string and do not mutate data. Unknown fields may be ignored. Other missing routes return 404.

The UI supports creation, editing all fields, search, combined status/priority filtering, loading/empty/error states, and responsive layout. Forms need visible labels and keyboard access. Saving an invalid record or failed network request shows an understandable error and retains entered values for retry. Show user input as text, never executable markup. No horizontal overflow at 390px viewport width.

## Browser automation contract

Use `data-testid` attributes on the following unique controls: `customer`, `device`, `issue`, `status`, `priority`, `save`, `search`, `status-filter`, `priority-filter`, `error`. The first five are form inputs (status and priority are native selects); `save` is a button. Filters are native selects with an empty-valued All option. The error element is visible and nonempty on a failed save. Each displayed record has `data-testid="repair-row"` and `data-id="ID"`; its edit button has `data-testid="edit"`. Row text includes customer, device, issue, status and priority. Search/filter changes must apply without a page navigation. A hidden error element when no error is acceptable.

## Evaluation

The list status element must have `data-testid="list-state"` and `data-state="loading"`, `"empty"`, `"error"` or `"ready"` matching the current list state. Loading, empty and error states display explanatory text. Invalid save, including whitespace-only customer, shows a visible `error` message without losing other input.

Independent HTTP and real-browser behavior checks, including process restart, determine acceptance. Every criterion in benchmark.json is mandatory. Visual aesthetics beyond legibility, usability and responsive layout are not scored. Tests are public calibration assets; they are not secret holdouts. Lab results are written separately from your workspace; do not try to read, alter, or manufacture them.
