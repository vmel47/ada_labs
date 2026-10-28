# Repair Queue v1: measured matched trial

Experiment `exp-7ee8f26bb6f9` develops two new applications from clean starters. Production ADA stays at `f65d8bd1cae4f19ee90336097dacf6eb33b4b512`. Neither previous pilot application nor the benchmark reference is an input to either application Lead.

The complete cycle finished: reviewed ADA change, two independently developed and executed applications, independent behavior/source review, actual corrective work, native evaluation, measurements and saved comparison. Both final applications pass. The candidate did **not** support its primary hypothesis: its run had two late validation findings versus zero in stable. Both findings were corrected and independently closed. Keep the ADA change experimental.

## Results

| Measurement | Stable ADA | Experimental ADA |
|---|---:|---:|
| Final mandatory quality | 10/10 PASS | 10/10 PASS |
| Independent late validation/routing findings | 0 | 2 |
| Lead planning + implementation + correction | 559.669 s | 680.657 s |
| Independent design + final/correction review | 959.434 s | 428.066 s |
| Recorded source corrective revisions | 1 | 2 |
| Revisions for independent findings | 0 | 1 |
| Human interventions / clarifications / manual recoveries / user QA | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| Provider tokens, calls, session totals, cost | unknown | unknown |

Candidate development took 120.987 seconds more (+21.62%). Recorded review time was 531.367 seconds less (−55.38%), but its scope includes different setup and follow-up work: stable paid the supplementary-suite setup cost, while candidate review included symmetric extra testing of both apps. These are observed stage durations, not a causal efficiency estimate. Candidate planning also includes controller context/handoff overhead, and stable implementation includes four recorded environment retries. No time was retrospectively subtracted.

Policy v2 therefore reports **Trade-off observed**: development time and correction count were worse, recorded review time lower, and final mandatory quality equal. This unweighted directional label does not override the adverse primary defect observation or establish an architectural improvement. One stable-first pair cannot establish repeatable superiority.

| Frozen mandatory criterion | Stable | Candidate after correction |
|---|---|---|
| API create/read/edit and missing records | PASS | PASS |
| Server validation and rejected-write nonmutation | PASS | PASS |
| Search intersecting status and priority filters | PASS | PASS |
| SQLite persistence across actual process restart | PASS | PASS |
| Browser create/edit matching backend state | PASS | PASS |
| Browser search/filter behavior | PASS | PASS |
| Failed save error and retained form values | PASS | PASS |
| Responsive labeled controls at 390px | PASS | PASS |
| Loading, empty and list-load error states | PASS | PASS |
| Invalid browser submission and input retention | PASS | PASS |

Both also passed the same eleven supplementary API groups and five supplementary UI scenarios. Initial mandatory/supplementary success did not hide independent review findings: five later JSON/Unicode cases passed stable but exposed two candidate defects. Lone surrogate input raised an unhandled exception; nonstandard JSON constants `NaN`/`Infinity` were accepted and changed the database. After correction, the original five cases and a fourteen-case POST/PUT expansion (including valid Unicode and rejected-write nonmutation) passed both. The ten benchmark criteria were never changed.

Stable's single recorded correction fixed a test-owned SQLite connection cleanup problem. Candidate's two revisions fixed an application SQLite connection leak, then both independent validation findings. Separate test-fixture, browser wait, scratch naming, bookkeeping and Reviewer-harness retries remain in local evidence; they are not additional application-source revisions. The candidate's first self-check full before digest is retrospective; its selected-set digest was independently reproduced, but no contemporaneous full per-file manifest existed. Final accepted source and the independent-finding correction have fresh source bindings.

No current execution blocker remains. Native calibration passed the reference and rejected the deliberately broken persistence, validation and filter controls. All 27 Lab regression tests passed; the independent exact-source Lab review passed. Demonstrated runtime: Windows 11, Python 3.14.7, Chrome 156.0.8078.48, native offline isolation and the frozen handler byte transport. This does not certify untested platforms. [Sanitized identities, stage measurements and comparison](study-002.json) retain exact values; the local report retains underlying evidence pointers.

The experimental change adds a requirements-linked negative-case matrix to ADA's existing preparation and final handoff. It changes no product requirement, workflow role or acceptance gate. Its primary hypothesis is fewer late independent validation/routing findings and related corrections; development and review time are secondary observations. The stable preparation already includes substantial negative-case coverage in prose, so a matrix does not introduce exclusively new case knowledge.

## Repetition

Use the canonical ADA Labs checkout and a clean sibling ADA checkout at the revision above. Keep the frozen benchmark files unchanged. The exact benchmark, evaluator, candidate and application identities are in the companion sanitized JSON. Use the same available model and tools for both new application Leads, separate fresh contexts, and one independent Reviewer. This experiment uses the current coding chat; the Lab launches no AI process.

```powershell
python start.py init
python start.py doctor
.venv\Scripts\python.exe -m unittest discover -s tests -v
python start.py calibrate
python start.py prepare --benchmark repair-queue-v1 --model gpt-6.1-sol --max-sessions 20 --hypothesis "An explicit negative-case matrix during criteria preparation reduces late validation and routing defects without relaxing quality."
```

Use the returned new ID; never reuse an evaluated application as a new result. `research ID` identifies the isolated candidate working directory. Apply the reviewed `negative-case-preparation.patch` there, obtain an independent exact-source/design receipt, then freeze it with `candidate ID --review RECEIPT --intervention DESCRIPTION`. Prepare each clean workspace with `run ID --variant stable` and `run ID --variant candidate`.

The frozen stable README has CRLF lines and the reviewed candidate README uses LF. To reproduce the exact candidate bytes, normalize only the isolated README before applying the patch. Replace `NEW_ID` below with the actual new ID; do not target production ADA:

```powershell
.venv\Scripts\python.exe -c "from pathlib import Path; p=Path('.lab/experiments/NEW_ID/candidate-working/README.md'); p.write_text(p.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')"
git apply --directory=.lab/experiments/NEW_ID/candidate-working research/negative-case-preparation.patch
```

This sequence was checked on a fresh copy of stable and reproduced candidate digest `d97757ec38e55cf01701d206711af63dd5aece6f5c1029e313eded4d45d48eb6`. Applying the text patch directly to CRLF source fails; ignoring whitespace alone leaves different bytes. Both failed repetition probes are retained locally.

Before dispatching each planning, implementation or correction stage, register `begin ID --observer ACTUAL_OBSERVER --role lead --variant VARIANT --model gpt-6.1-sol --source-ref ACTUAL_REFERENCE --timed --stage STAGE`. Close it immediately with `finish`; do not estimate tokens or override measured seconds. Record independent review stages separately. Freeze each plan and register its independent design review before implementation. Follow the assigned ADA, implement the entire unchanged task, and execute applicable real checks through verified native isolation. The candidate must retain matrix-to-command/result evidence. Record actual failed-check corrections with their before/after source bindings.

The independent Reviewer runs the ten mandatory API/browser criteria, meaningful supplementary checks and exact-source review. Prefer freezing supplementary checks before inspecting either implementation and reuse them for both variants. In this trial the eleven supplementary API groups were frozen before source inspection. Five additional UI scenarios were added after stable source review, then the same final script was applied to both applications; this is a disclosed selection limitation. Register behavior/code receipts, then `submit ID --variant VARIANT` (candidate also needs `--intervention-evidence FILE`), `evaluate-run ID --variant VARIANT`, `compare ID`, and `report ID`. See [receipt formats and commands](../docs/operations.md).

All generated application execution must remain inside successfully probed native isolation. If a mandatory capability is unavailable, preserve BLOCKED; do not substitute an assertion or weaken the benchmark. Trusted ADA protocol regressions can be checked separately only after verifying their exact trusted source. This does not authorize running generated application code on the host.

## Storage and interpretation

The complete local record is `.lab/experiments/exp-7ee8f26bb6f9/`: frozen task/ADA inputs, clean-origin workspaces, plans, matrix, application snapshots, configuration, work segments, corrections, independent review receipts and `report.json`. Native evaluation evidence is retained under `.lab/evaluations/`. These ignored files survive process restarts but require a separate protected backup against disk loss. Published files contain only sanitized source identities and conclusions.

Final immutable applications are in `artifacts/stable-5934e5abec/` and `artifacts/candidate-3340ffd95e/` under that experiment. Initial candidate failure observations and source are retained separately. To inspect or reevaluate these existing results, use `python start.py report exp-7ee8f26bb6f9` or `python start.py evaluate ARTIFACT_PATH`; reevaluation of an existing artifact is not a new development experiment.

Comparison policy v2 was fixed before this pair. Quality is a mandatory gate. Known efficiency dimensions are compared separately, with a timing tie band of the larger of one second or two percent of baseline. Unknown tokens, provider calls, provider session totals and cost remain null. A direction observed in one pair is not proof of repeatable superiority. Stable-first order, public tests, reviewer reuse, suite setup and controller/tool overhead limit causal interpretation. Repeat with counterbalanced order before considering any production ADA change.
