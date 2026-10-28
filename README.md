# ADA Lab

ADA Labs researches and evaluates changes to ADA through real software development. Its local browser application controls persistent campaigns, independently criticized ideas and matched benchmark trials. Stable ADA is never automatically changed.

## Start

Open **`Run.bat`**. It bootstraps the local Python environment, starts the application and opens `http://127.0.0.1:8765`. Ordinary experiments use the interface. Reopening retains history and waits for explicit continuation of pending work.

Requires Python 3.11+, Git and an installed Codex CLI signed in with the existing ChatGPT subscription. Installed desktop CLI binaries are discovered without depending on the supervising chat's PATH. Chrome/Edge or Playwright Chromium provides real browser QA. No WSL, Docker, paid API key or new cloud service is required. Unsupported capabilities remain BLOCKED.

1. In **Suites & settings**, choose an immutable suite and explicit development, Reviewer and research model/effort profiles. Equivalent profiles resume the same campaign.
2. In **Research**, generate and independently critique ideas. Inspect sources, objections and priority; pause or stop without losing reviewed ideas.
3. Test one reviewed idea or run the affordable ranked shortlist. The controller develops fresh Stable/candidate applications, executes real API/browser QA and obtains separate source/visual review.
4. Inspect per-task results, measured tokens, sources and desktop/mobile screenshots. Pause or close, then explicitly resume saved checkpoints. Back up evidence locally.

The adapter records requested and runtime-observed model/effort/provider, session IDs and direct token events. Provider-side rerouting is not independently attested. Subscription quota, tokens and configured budgets are distinct units; no paid API fallback is implemented.

An accepted ADA candidate can be reused with fresh applications without repeating its development. A browser-action interpreter/validator revision additionally requires an exact compatibility manifest and a fresh independent compatibility review; it creates a new campaign and never imports previous application results.

Currently the full automatic ADA candidate regression path supports Markdown-only changes with every executable/configuration byte pinned to trusted production. Code/configuration candidates remain BLOCKED where the required native Git/MSYS fixtures cannot execute. Generated benchmark applications still run exclusively in native isolation; see [the trust boundary](docs/architecture.md).

## Legacy CLI start

Requires Python 3.11+, Git and an installed Codex sandbox helper. Run from this canonical checkout:

```powershell
python start.py init
python start.py doctor
python start.py calibrate
python start.py evolve --model gpt-6.1-sol
```

`start.py` creates a local Python environment and installs the pinned dependencies on first use. `init` verifies both canonical remotes and discovers the sibling ADA checkout; use `--ada PATH` for an explicit location. Existing Chrome or Edge is detected; when no browser exists, `init` installs Playwright's local Chromium. No account credentials are requested by the Lab.

`evolve` prepares the next research handoff. Continue that handoff in the current coding chat. It starts no AI session. Model names are provenance labels supplied by the current session, not a provider configuration or a paid API selection.

The state core uses portable Python/filesystem operations. Windows native evaluation is exercised directly. The platform sandbox adapter probes the machine before executing artifacts and reports missing capabilities honestly. A successful run on one platform is not a promise about untested OS versions or policies.

## Legacy current-chat experiment cycle

1. `prepare` or `evolve` freezes stable ADA, the benchmark, evaluator identity and configuration, then writes a research handoff under `.lab/experiments/ID/`.
2. Read previous conclusions, form a hypothesis and develop it in `candidate-working`. Obtain independent criticism and freeze it with `candidate`.
3. `run ID --variant stable` and `run ID --variant candidate` prepare equivalent application workspaces and handoffs. Develop the applications in the current chat following the relevant ADA snapshot.
4. Record work and actual review observations with `begin`, `finish` and `review`. `submit` freezes the application. `evaluate-run` runs the independent evaluator.
5. `compare`, `report`, `history`, `status`, `pause` and `resume` retain the outcome and next action across process restarts.

Run `python start.py COMMAND --help` for arguments. [Operations](docs/operations.md) gives the exact receipt format and sequence. [Architecture](docs/architecture.md) documents the trust boundary and measurement limits. [Acceptance](docs/acceptance.md) records the owner's clarified scope.

## Repair Queue benchmark

The first versioned assignment is [Repair Queue](benchmarks/repair-queue-v1/task.md): a browser application with SQLite persistence, creation/editing, search/filtering, server validation, useful error states and a responsive UI. The starter supplies a standard HTTP adapter. The application implements its routing, business logic and persistence through `handle_request`.

Independent native workers carry real request/response bytes over a bounded transport. Real browser actions execute the actual backend and database. This avoids granting generated code network access. It evaluates the handler and UI; arbitrary server frameworks and network-stack performance are outside this benchmark's scope. The HTTP adapter has a separate real socket integration test.

`calibrate` requires the reference to pass every criterion and deliberately broken persistence, validation and filter variants to fail their intended checks. Public tests are not confidential evaluation data.

## Verification and storage

```powershell
python start.py doctor
.venv\Scripts\python.exe -m unittest discover -s tests -v
python start.py calibrate
```

`.lab/` and `.venv/` are ignored. Experiment records, artifacts, raw outputs and screenshots remain local. Keep important `.lab/` history in an appropriately protected backup; ignored files survive session/process restarts but are not a backup against disk loss. Version only reviewed, sanitized conclusions when publishing research.

No automatic promotion modifies production ADA. A successful candidate is delivered as a patch and an evidence-based recommendation.

The [first pilot](research/README.md) completed both real application runs. Both met mandatory quality; one pair does not demonstrate an efficiency advantage.

The [measured Repair Queue trial](research/study-002.md) completed two new independently developed applications, actual corrections and repeatable native evaluation. Both final applications passed; the negative-case-matrix candidate had two late validation findings versus zero in stable. Its primary hypothesis was not supported. The report includes measured stage durations, comparison limitations and exact repetition instructions.

The [standalone campaign study](research/study-003.md) completes another new clean Stable/candidate pair through the local application. Both pass all ten criteria and common independent API/browser supplements. The verbose-output instruction consumed 52.434% more observed application-workflow logical tokens; it is retained as a negative result. The report records explicit runtime profiles, startup/incomplete attempts, measured roles, pause/restart recovery and exact repetition instructions.
