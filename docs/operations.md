# Operating from the current coding session

The coding assistant runs these commands while working. The owner can simply ask the current chat to continue improving ADA. No second Codex session or separate model login is started.

## Prepare and research

```powershell
python start.py evolve --model CURRENT_MODEL --max-sessions 12
python start.py research EXPERIMENT_ID
python start.py begin EXPERIMENT_ID --observer CURRENT_CHAT --role research --model CURRENT_MODEL --source-ref ACTUAL_CHAT_OR_TOOL_REFERENCE
```

`--max-sessions` is a retained CLI name for the number of registered work admissions. It does not represent an automatically measured provider-session count. Work handoffs contain previous conclusions and exact paths. Develop a hypothesis in `candidate-working`; never edit stable ADA during a trial.

After actual independent candidate criticism:

```powershell
python start.py candidate ID --review REVIEW.json --intervention "Observable change and how it will be exercised"
python start.py run ID --variant stable
python start.py run ID --variant candidate
```

These commands prepare files and freeze records; the current coding chat performs the development.

## Reviews and application submission

Freeze `PLAN.md` in the prepared workspace. Register an actual lead work segment. An independent Reviewer challenges it before `app.py` implementation. Review receipt format:

```json
{
  "observer": "ACTUAL_INDEPENDENT_OBSERVER",
  "session_ref": "ACTUAL_SESSION_REFERENCE",
  "source_ref": "ACTUAL_TOOL_OR_CHAT_REFERENCE",
  "status": "PASS",
  "subject_digest": "BOUND_DIGEST",
  "evidence": [{"command": "actual command", "expected": "observable result", "actual": "observed result"}],
  "findings": []
}
```

The design subject is `digest(PLAN.md UTF-8 text)` from `ada_lab.files`. Final behavior/code subjects are `tree_digest(workspace)`. Candidate criticism binds `tree_digest(candidate-working)`. Use FAIL/BLOCKED and open findings honestly; PASS with unresolved findings is rejected. Keep receipts in ignored `.lab/`, not application source.

```powershell
python start.py review ID --variant stable --phase design --receipt DESIGN.json
```

Implement and run real checks, then obtain independent behavior and exact source review. The Reviewer chooses its own checks and does not merely repeat the implementer's success report.

```powershell
python start.py review ID --variant stable --phase behavior --receipt BEHAVIOR.json
python start.py review ID --variant stable --phase code --receipt CODE.json
python start.py submit ID --variant stable
python start.py evaluate-run ID --variant stable
```

Repeat for candidate. Candidate submission additionally requires `--intervention-evidence FILE`, containing an actual `source_ref` and `observation`; retain the underlying observations beside it. Repeated `evaluate-run` on the same frozen evaluated artifact reuses its evidence. A correction is a new submission and new independently reviewed source digest; old attempts remain retained.

Record each implementation correction, including before the first submission, with `correction ID --variant stable --receipt FILE`. The receipt needs `before_digest`, `after_digest`, actual `source_ref` and `findings_ref`. The previous digest must have an independent FAIL in retained review history and the new digest must match the actual workspace. `correction_attempts` counts these revisions, not submissions or model calls. Earlier review observations remain in history when a new revision supersedes them.

## Close work and recover

`finish ID --session SEGMENT_ID --receipt FILE` closes a registered segment. The receipt needs observed `status` (`completed`, `failed`, `interrupted`) and `source_ref`. Optional nonnegative measurements are `input_tokens`, `output_tokens`, `cached_input_tokens`, `model_calls`, `monetary_cost`, `wall_seconds`; omitted values remain null. Do not estimate unavailable provider usage or invent cost from subscription limits. Closed accounting cannot be silently rewritten.

For prospective development measurements, add `--timed --stage planning` (or `implementation`, `correction`, `design-review`, `final-review`) to `begin` before dispatching the work. Finish that segment immediately at stage completion, omit `wall_seconds`, and let the controller measure the monotonic interval. Use separate lead and reviewer segments; do not leave a lead segment open while waiting for review and then call its whole interval development time. Unknown token/call/cost fields remain null. Timed segments cannot override their clock result. An uncertain clock leaves duration unknown; interrupted work retains its observed duration when continuity is verified. Do not reconstruct the previous pilot's missing measurements.

Self-check corrections can also be registered with `correction`: set `origin` to `self-check` and include `failure` with the actual nonzero `returncode`, `argv`, and previous `subject_digest`, plus the normal before/after and source/finding references. Expected initial failures in a deliberately unimplemented contract are not corrective defects. Both self-check and independent-finding corrective revisions remain visible.

Comparison v2 is frozen before new work. It reports each measured direction/delta separately, including equal quality with different time or correction counts; missing metrics remain unknown. Its one-second-or-two-percent timing tie band is a predefined reporting rule, not proof of statistical significance. Historical v1 records and prior conclusions remain retained.

```powershell
python start.py pause ID
python start.py resume ID
python start.py status ID
python start.py compare ID
python start.py compare ID --other OTHER_ID
python start.py history
python start.py report ID
```

Pause prevents subsequent recorded work/submission; it does not stop the owner's chat. Resume reports actual retained next steps and unfinished segments. It never replays an AI call. Comparison displays configuration differences and incomplete evidence. Reports stay local; manually review any summary before publication to the public canonical repository.

Add benchmarks through `benchmarks/registry.json`, a versioned task/starter/reference/criteria folder, and a corresponding independent evaluator implementation. Never amend frozen criteria to hide a failed run; create a new version/revision and repeat the relevant work.
