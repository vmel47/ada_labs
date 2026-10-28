"""Deterministic experiment records operated by the user's current coding session."""
import difflib
import json
from pathlib import Path
import platform
import shutil
import uuid
import math
import time
import hashlib
from datetime import datetime

from .files import ROOT, copy_tree, digest, git, identity, inventory, lock, now, read, safe_id, tree_digest, write
from . import evaluator, sandbox
from .stateio import write

SCHEMA = 2
VARIANTS = ('stable', 'candidate', 'single')
COMPARISON_POLICY = {'version': 2, 'seconds_absolute_tolerance': 1.0,
                     'seconds_relative_tolerance': 0.02,
                     'scope': 'Observed directions only; one pair does not establish reliability'}


class Lab:
    def __init__(self, state=None):
        self.state = Path(state or ROOT / '.lab').resolve()
        if not self.state.is_relative_to(ROOT / '.lab'):
            raise ValueError('State must remain inside the ignored canonical .lab directory')

    def config(self):
        return read(self.state / 'config.json')

    def initialize(self, ada=None):
        ada = Path(ada or ROOT.parent / 'Ada').resolve()
        for root, expected in [(ROOT, 'https://github.com/vmel47/ada_labs'), (ada, 'https://github.com/vmel47/ada')]:
            if git(root, 'remote', 'get-url', 'origin').removesuffix('.git') != expected:
                raise ValueError('Incorrect canonical remote: ' + str(root))
        config = {'schema': SCHEMA, 'ada': str(ada), 'execution': 'current-session', 'created': now()}
        target = self.state / 'config.json'
        if target.exists() and read(target).get('schema') == SCHEMA:
            return read(target)
        write(target, config)
        return config

    def folder(self, identifier):
        return self.state / 'experiments' / safe_id(identifier)

    def load(self, identifier):
        record = read(self.folder(identifier) / 'record.json')
        if record.get('schema') != SCHEMA:
            raise ValueError('Record belongs to an earlier implementation prototype')
        return record

    def save(self, record):
        record['updated'] = now()
        write(self.folder(record['id']) / 'record.json', record)

    def history(self):
        records = [read(path) for path in sorted((self.state / 'experiments').glob('*/record.json'))]
        return [record for record in records if record.get('schema') == SCHEMA]

    def doctor(self):
        result = sandbox.doctor(self.state, self.config()['ada'])
        try:
            from .native import browser_executable
            result['browser'] = str(browser_executable())
        except Exception as error:
            result['status'] = 'BLOCKED'
            result['browser_error'] = str(error)
        result['note'] = 'Process isolation probe only; calibrate verifies actual browser behavior.'
        return result

    def prepare(self, hypothesis=None, benchmark='repair-queue-v1', model=None, max_sessions=12):
        if max_sessions < 1:
            raise ValueError('Session admission limit must be positive')
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError('Model provenance must be a nonempty label or unknown')
        stable = identity(self.config()['ada'])
        if stable['status']:
            raise ValueError('Stable ADA has uncommitted changes; preserve them before preparing a clean experiment')
        path, spec = evaluator.benchmark(benchmark)
        identifier = 'exp-' + uuid.uuid4().hex[:12]
        folder = self.folder(identifier)
        folder.mkdir(parents=True)
        copy_tree(self.config()['ada'], folder / 'stable')
        copy_tree(path, folder / 'benchmark')
        record = {'schema': SCHEMA, 'id': identifier, 'created': now(), 'status': 'prepared',
            'hypothesis': hypothesis or 'Executable contracts frozen before implementation may reduce corrections while preserving all quality requirements.',
            'stable': stable, 'stable_path': self.config()['ada'], 'benchmark': benchmark,
            'benchmark_revision': tree_digest(path), 'criteria_digest': digest(spec['criteria']),
            'evaluator_version': evaluator.version(), 'model': model,
            'environment': {'platform': platform.platform(), 'python': platform.python_version(), 'transport': 'native-stdio-v1'},
            'limits': {'max_sessions': max_sessions}, 'sessions': [], 'runs': {}, 'research': [],
            'comparison_policy': dict(COMPARISON_POLICY),
            'autonomy': {'human_interventions': 0, 'clarifications': 0, 'manual_recoveries': 0, 'testing_delegated_to_user': 0},
            'next_action': 'Read research handoff in the current coding session; create and independently review an isolated ADA candidate.'}
        self.save(record)
        return self.research(identifier)

    def verify(self, record):
        folder = self.folder(record['id'])
        if identity(record['stable_path']) != record['stable']:
            raise RuntimeError('Stable ADA changed after preparation')
        if tree_digest(folder / 'stable') != record['stable']['source_digest']:
            raise RuntimeError('Frozen stable snapshot changed')
        path, spec = evaluator.benchmark(record['benchmark'])
        if tree_digest(path) != record['benchmark_revision'] or tree_digest(folder / 'benchmark') != record['benchmark_revision']:
            raise RuntimeError('Frozen benchmark changed; prepare a new experiment')
        if evaluator.version() != record['evaluator_version']:
            raise RuntimeError('Evaluator changed; prepare a new experiment')
        if record.get('candidate'):
            candidate = record['candidate']
            if tree_digest(folder / 'candidate') != candidate['source_digest'] or digest((folder / 'candidate.patch').read_text(encoding='utf-8')) != candidate['patch_digest']:
                raise RuntimeError('Frozen candidate or patch changed')

    def research(self, identifier):
        record = self.load(identifier)
        self.verify(record)
        folder = self.folder(identifier)
        working = folder / 'candidate-working'
        if not working.exists():
            copy_tree(folder / 'stable', working)
        packet = {'experiment': identifier, 'hypothesis': record['hypothesis'],
            'stable_revision': record['stable']['commit'], 'working_copy': str(working),
            'previous_conclusions': [{'id': r['id'], 'hypothesis': r['hypothesis'],
                'comparison': {key: r.get('comparison', {}).get(key) for key in ('outcome', 'quality', 'recommendation')}}
                for r in self.history() if r['id'] != identifier][-10:],
            'instructions': ['Work in the current coding chat. ADA Lab does not start another AI runtime.',
                'Inspect ADA and history; propose a falsifiable change and seek independent design criticism.',
                'Implement the change only in working_copy. Keep production ADA unchanged.',
                'Record actual session/tool references and observed resources; leave unknowns null.',
                'Freeze a reviewed candidate, then prepare the same benchmark for stable and candidate.']}
        write(folder / 'research-handoff.json', packet)
        return packet

    def begin(self, identifier, observer, role, variant=None, model=None, source_ref=None, timed=False, stage=None):
        if role not in ('lead', 'reviewer', 'research') or variant not in (*VARIANTS, None):
            raise ValueError('Invalid session role or variant')
        if not observer or not source_ref:
            raise ValueError('Actual observer and source reference are required')
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            if record['status'] == 'paused':
                raise RuntimeError('Experiment is paused')
            if len(record['sessions']) >= record['limits']['max_sessions']:
                raise RuntimeError('Session admission budget exhausted')
            if role == 'lead' and record['model'] and model and record['model'] != model:
                raise ValueError('Model differs from frozen experiment model')
            if record['model'] is None and model:
                if record['sessions']:
                    raise ValueError('Cannot establish model identity after unlabelled work began')
                record['model'] = model
            entry = {'id': 'session-' + uuid.uuid4().hex[:10], 'observer': observer, 'role': role,
                'variant': variant, 'model': model or record['model'], 'source_ref': source_ref, 'status': 'active',
                'started': now(), 'input_tokens': None, 'output_tokens': None, 'cached_input_tokens': None,
                'model_calls': None, 'monetary_cost': None, 'wall_seconds': None}
            if timed:
                entry['timer'] = {'start_ns': time.monotonic_ns(), 'utc_start': entry['started'],
                                  'stage': stage, 'source': 'controller dispatch-to-completion monotonic clock'}
            record['sessions'].append(entry)
            record['status'] = 'working'
            self.save(record)
            return entry

    def finish(self, identifier, session_id, receipt):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            session = next(s for s in record['sessions'] if s['id'] == session_id)
            if session['status'] != 'active':
                raise ValueError('Session already closed; previous accounting is immutable')
            if receipt.get('status') not in ('completed', 'failed', 'interrupted') or not receipt.get('source_ref'):
                raise ValueError('Observed status and source reference are required')
            receipt = dict(receipt)
            finished = now()
            if session.get('timer'):
                if receipt.get('wall_seconds') is not None:
                    raise ValueError('A controller-timed segment cannot override its measured duration')
                timer = session['timer']
                end_ns = time.monotonic_ns()
                elapsed = (end_ns - timer['start_ns']) / 1_000_000_000
                utc_elapsed = (datetime.fromisoformat(finished) - datetime.fromisoformat(timer['utc_start'])).total_seconds()
                continuous = elapsed >= 0 and abs(utc_elapsed - elapsed) <= 5 and not receipt.get('recovered_after_restart')
                timer.update(end_ns=end_ns, utc_end=finished, continuity_verified=continuous)
                receipt['wall_seconds'] = elapsed if continuous else None
                receipt['timing_source'] = timer['source'] if continuous else 'Clock continuity uncertain; duration unknown'
            for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'model_calls', 'monetary_cost', 'wall_seconds'):
                value = receipt.get(key)
                if value is not None and (type(value) not in (int, float) or value < 0):
                    raise ValueError('Invalid measurement: ' + key)
                if value is not None and (not math.isfinite(value) or (key not in ('monetary_cost', 'wall_seconds') and type(value) is not int)):
                    raise ValueError('Measurement must be finite and token/call counts must be integers: ' + key)
                session[key] = value
            session.update(status=receipt['status'], finished=finished, receipt=receipt)
            self.save(record)
            return session

    @staticmethod
    def review_receipt(receipt, expected_digest, implementers=()):
        required = ('observer', 'session_ref', 'source_ref', 'status', 'subject_digest', 'evidence', 'findings')
        if any(key not in receipt for key in required):
            raise ValueError('Incomplete independent review receipt')
        if not isinstance(receipt['evidence'], list) or not isinstance(receipt['findings'], list):
            raise ValueError('Review evidence and findings must be arrays')
        if receipt['status'] not in ('PASS', 'FAIL', 'BLOCKED') or receipt['subject_digest'] != expected_digest:
            raise ValueError('Review status or subject binding is invalid')
        if receipt['observer'] in implementers or not all(receipt[key] for key in ('observer', 'session_ref', 'source_ref', 'evidence')):
            raise ValueError('Independent observation and real source references are required')
        if receipt['status'] == 'PASS' and receipt['findings']:
            raise ValueError('PASS cannot have unresolved findings')
        return receipt

    def freeze_candidate(self, identifier, review, intervention):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            if record.get('candidate'):
                return record['candidate']
            folder = self.folder(identifier)
            source = folder / 'candidate-working'
            before, after = inventory(folder / 'stable'), inventory(source)
            if before == after:
                raise ValueError('Candidate must contain an actual source change')
            implementers = [s['observer'] for s in record['sessions'] if s['role'] == 'research']
            self.review_receipt(review, tree_digest(source), implementers)
            if review['status'] != 'PASS' or not intervention.strip():
                raise ValueError('Independent design PASS and observable intervention are required')
            candidate = folder / 'candidate'
            if candidate.exists():
                if tree_digest(candidate) != tree_digest(source):
                    raise ValueError('Interrupted candidate freeze has a different digest')
            else:
                copy_tree(source, candidate)
            patch = []
            for name in sorted(set(before) | set(after)):
                if before.get(name) == after.get(name):
                    continue
                old = (folder / 'stable' / name).read_text(encoding='utf-8').splitlines(True) if name in before else []
                new = (source / name).read_text(encoding='utf-8').splitlines(True) if name in after else []
                patch.extend(difflib.unified_diff(old, new, fromfile='a/' + name, tofile='b/' + name))
            patch = ''.join(patch)
            (folder / 'candidate.patch').write_text(patch, encoding='utf-8', newline='\n')
            record['candidate'] = {'source_digest': tree_digest(candidate), 'base_commit': record['stable']['commit'],
                'patch_digest': digest(patch), 'review': review, 'intervention': intervention}
            record['next_action'] = 'Prepare stable and candidate benchmark workspaces in the current coding session.'
            self.save(record)
            return record['candidate']

    def run(self, identifier, variant):
        if variant not in VARIANTS:
            raise ValueError('Unknown workflow')
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            if record['status'] == 'paused':
                raise RuntimeError('Experiment is paused')
            if variant == 'candidate' and not record.get('candidate'):
                raise ValueError('Freeze an independently reviewed candidate first')
            folder = self.folder(identifier)
            workspace = folder / (variant + '-workspace')
            if not workspace.exists():
                copy_tree(folder / 'benchmark' / 'starter', workspace)
            if not (workspace / 'TASK.md').exists():
                shutil.copyfile(folder / 'benchmark' / 'task.md', workspace / 'TASK.md')
            if variant != 'single' and not (workspace / 'ada').exists():
                copy_tree(folder / variant, workspace / 'ada')
            record['runs'].setdefault(variant, {'status': 'prepared', 'workspace': str(workspace), 'reviews': {}, 'attempts': [],
                'corrections': [], 'autonomy': {'human_interventions': 0, 'clarifications': 0,
                'manual_recoveries': 0, 'testing_delegated_to_user': 0}})
            packet = {'experiment': identifier, 'variant': variant, 'workspace': str(workspace),
                'task': str(workspace / 'TASK.md'), 'benchmark_revision': record['benchmark_revision'],
                'ada_revision': record['stable']['commit'] if variant == 'stable' else record.get('candidate', {}).get('source_digest') if variant == 'candidate' else None,
                'intervention': record.get('candidate', {}).get('intervention') if variant == 'candidate' else None,
                'instructions': ['Continue development in the current coding chat; this command starts no AI process.',
                    'Freeze PLAN.md and obtain independent design criticism before implementation.',
                    'Implement the application, execute real checks, then obtain independent behavior and exact-source review.',
                    'Register actual session references and submit the developed workspace. Independent evaluator runs afterward.']}
            write(folder / (variant + '-handoff.json'), packet)
            record['next_action'] = 'Obtain independent design criticism of ' + variant + ' PLAN.md before implementation.'
            self.save(record)
            return packet

    def add_review(self, identifier, variant, phase, receipt):
        if phase not in ('design', 'behavior', 'code'):
            raise ValueError('Unknown review phase')
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            run = record['runs'][variant]
            workspace = Path(run['workspace'])
            if phase == 'design' and (workspace / 'app.py').exists():
                raise ValueError('Design criticism must be recorded before app.py implementation')
            expected = digest((workspace / 'PLAN.md').read_text(encoding='utf-8')) if phase == 'design' else tree_digest(workspace)
            implementers = [s['observer'] for s in record['sessions'] if s['role'] == 'lead' and s['variant'] == variant]
            self.review_receipt(receipt, expected, implementers)
            run.setdefault('review_history', []).append({'phase': phase, 'receipt': receipt, 'recorded': now()})
            run['reviews'][phase] = receipt
            record['next_action'] = ('Correct the independent findings and obtain fresh review.' if receipt['status'] != 'PASS' else
                {'design': 'Implement and independently verify ' + variant + ' application.',
                 'behavior': 'Obtain independent exact-source review of ' + variant + '.',
                 'code': 'Submit and evaluate the reviewed ' + variant + ' artifact.'}[phase])
            self.save(record)
            return receipt

    def correction(self, identifier, variant, receipt):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            run = record['runs'][variant]
            required = ('before_digest', 'after_digest', 'source_ref', 'findings_ref')
            if any(not receipt.get(key) for key in required):
                raise ValueError('Correction requires actual source, finding and before/after identities')
            if receipt['after_digest'] != tree_digest(run['workspace']) or receipt['before_digest'] == receipt['after_digest']:
                raise ValueError('Correction must bind an actual changed workspace')
            findings = [entry['receipt'] for entry in run.get('review_history', []) if entry['receipt']['status'] == 'FAIL']
            if receipt.get('origin') == 'self-check':
                failure = receipt.get('failure', {})
                if type(failure.get('returncode')) is not int or failure['returncode'] == 0 or not failure.get('argv') or failure.get('subject_digest') != receipt['before_digest']:
                    raise ValueError('Self-correction requires a real failed command bound to the previous source')
            elif not any(entry['subject_digest'] == receipt['before_digest'] for entry in findings):
                raise ValueError('No recorded independent failure against the previous source')
            corrections = run.setdefault('corrections', [])
            if receipt not in corrections:
                corrections.append(receipt)
            self.save(record)
            return receipt

    def submit(self, identifier, variant, intervention_evidence=None):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            if record['status'] == 'paused':
                raise RuntimeError('Experiment is paused')
            run = record['runs'][variant]
            source = Path(run['workspace'])
            task = self.folder(identifier) / 'benchmark' / 'task.md'
            if (source / 'TASK.md').read_bytes() != task.read_bytes():
                raise ValueError('Frozen task was modified')
            wrapper = self.folder(identifier) / 'benchmark' / 'starter' / 'server.py'
            if (source / 'server.py').read_bytes() != wrapper.read_bytes():
                raise ValueError('Supplied HTTP adapter was modified')
            if variant != 'single':
                implementers = [s['observer'] for s in record['sessions'] if s['role'] == 'lead' and s['variant'] == variant]
                if not implementers:
                    raise ValueError('Register actual development session provenance before submission')
                expected = record['stable']['source_digest'] if variant == 'stable' else record['candidate']['source_digest']
                if tree_digest(source / 'ada') != expected:
                    raise ValueError('Workspace ADA changed')
                plan = digest((source / 'PLAN.md').read_text(encoding='utf-8'))
                for phase, subject in [('design', plan), ('behavior', tree_digest(source)), ('code', tree_digest(source))]:
                    receipt = run['reviews'].get(phase, {})
                    self.review_receipt(receipt, subject, implementers)
                    if receipt['status'] != 'PASS':
                        raise ValueError('Mandatory independent review did not pass: ' + phase)
            if variant == 'candidate' and (not intervention_evidence or not intervention_evidence.get('source_ref') or not intervention_evidence.get('observation')):
                raise ValueError('Observed candidate intervention evidence is required')
            attempt = {'id': uuid.uuid4().hex[:10], 'started': now(), 'status': 'submitted',
                'artifact_digest': tree_digest(source), 'intervention_evidence': intervention_evidence}
            artifact = self.folder(identifier) / 'artifacts' / (variant + '-' + attempt['id'])
            copy_tree(source, artifact)
            attempt['artifact'] = str(artifact)
            run['attempts'].append(attempt)
            run.pop('evaluation', None)
            run.update(status='submitted', artifact=str(artifact), artifact_digest=attempt['artifact_digest'])
            record['next_action'] = 'Independently evaluate the frozen ' + variant + ' artifact.'
            self.save(record)
            return attempt

    def evaluate_run(self, identifier, variant):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            if record['status'] == 'paused':
                raise RuntimeError('Experiment is paused')
            run = record['runs'][variant]
            if not run.get('artifact'):
                raise ValueError('Submit an independently reviewed artifact before evaluation')
            if tree_digest(run['artifact']) != run['artifact_digest']:
                raise ValueError('Submitted artifact changed')
            calibration = read(self.state / 'calibration.json')
            if calibration.get('status') != 'PASS' or calibration.get('evaluator_version') != record['evaluator_version'] or calibration.get('benchmark_revision') != record['benchmark_revision']:
                raise ValueError('Current independent evaluator calibration is required')
            if run.get('evaluation') and run['evaluation'].get('evaluator_version') == record['evaluator_version'] and run['evaluation'].get('artifact_digest') == run['artifact_digest']:
                return run['evaluation']
            invocation = {'status': 'running', 'started': now()}
            run['attempts'][-1].setdefault('evaluations', []).append(invocation)
            self.save(record)
            clock = time.monotonic()
            try:
                result = evaluator.evaluate(run['artifact'], self.state, record['stable_path'], record['benchmark'])
                run['evaluation'] = result
                run['attempts'][-1]['evaluation'] = result
                run['attempts'][-1]['status'] = 'evaluated'
                run['status'] = 'evaluated'
                record['status'] = 'working'
                record['next_action'] = 'Develop/evaluate the remaining workflow or compare the completed pair.'
                invocation.update(status='completed', accounting=result['accounting'], evidence=result['evidence'])
            except BaseException as error:
                status = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'blocked'
                run['attempts'][-1].update(status=status, error=str(error) or status)
                record.update(status=status, blocker=str(error) or status)
                invocation.update(status=status, error=str(error) or status)
                raise
            finally:
                invocation.update(finished=now(), wall_seconds=time.monotonic() - clock)
                self.save(record)
            return result

    @staticmethod
    def observed_environment(run):
        evaluation = run.get('evaluation', {})
        result = {'browser': evaluation.get('browser_version'), 'python': None, 'sandbox': None, 'backend': None}
        reference = evaluation.get('preflight')
        if reference and Path(reference).is_file():
            probe = read(reference)
            result.update(python=probe.get('python'), sandbox=probe.get('codex_version'), backend=probe.get('backend'))
        return result

    def pause(self, identifier):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            record['status'] = 'paused'
            self.save(record)
        return {'status': 'paused', 'id': identifier, 'note': 'Current coding chat is controlled by its user; no background AI process exists.'}

    def resume(self, identifier):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            self.verify(record)
            if record['status'] in ('paused', 'blocked'):
                record['status'] = 'working'
                self.save(record)
            return {'id': identifier, 'status': record['status'], 'next_action': record['next_action'],
                    'runs': record['runs'], 'active_sessions': [s for s in record['sessions'] if s['status'] == 'active']}

    @staticmethod
    def efficiency_comparison(left, right, policy):
        dimensions = ['development_wall_seconds', 'review_wall_seconds', 'correction_attempts',
                      'input_tokens', 'output_tokens', 'model_calls', 'monetary_cost']
        comparisons = {}
        values = [(key, left.get(key), right.get(key)) for key in dimensions]
        for key in ('human_interventions', 'clarifications', 'manual_recoveries', 'testing_delegated_to_user'):
            values.append((key, left['autonomy'].get(key) if left['autonomy_scope'] == 'variant' else None,
                           right['autonomy'].get(key) if right['autonomy_scope'] == 'variant' else None))
        for key, baseline, candidate in values:
            item = {'baseline': baseline, 'candidate': candidate, 'delta': None, 'percent': None,
                    'direction': 'unknown', 'lower_is_better': True, 'tolerance': None}
            if baseline is not None and candidate is not None:
                delta = candidate - baseline
                tolerance = max(policy['seconds_absolute_tolerance'], abs(baseline) * policy['seconds_relative_tolerance']) if key.endswith('_seconds') else 0
                item.update(delta=delta, percent=100 * delta / baseline if baseline else None,
                            tolerance=tolerance, direction='equal' if abs(delta) <= tolerance else 'improved' if delta < 0 else 'regressed')
            comparisons[key] = item
        directions = {item['direction'] for item in comparisons.values()} - {'unknown', 'equal'}
        outcome = ('Trade-off observed' if directions == {'improved', 'regressed'} else
                   'Improvement observed' if directions == {'improved'} else
                   'Regression observed' if directions == {'regressed'} else
                   'No demonstrated advantage' if any(item['direction'] == 'equal' for item in comparisons.values()) else
                   'Inconclusive result')
        return outcome, comparisons

    def compare(self, identifier, other=None):
        ids = sorted(set([identifier, other or identifier]))
        from contextlib import ExitStack
        with ExitStack() as stack:
            for value in ids:
                stack.enter_context(lock(self.folder(value) / 'controller.lock'))
            left = self.load(identifier)
            right = self.load(other) if other else left
            differences = {key: {'left': left.get(key), 'right': right.get(key)} for key in
                ('benchmark_revision', 'criteria_digest', 'evaluator_version', 'model', 'environment', 'limits', 'comparison_policy') if left.get(key) != right.get(key)}
            runs = [left['runs'].get('stable', {}), right['runs'].get('candidate', {})]
            observed_environments = [self.observed_environment(run) for run in runs]
            if observed_environments[0] != observed_environments[1] and all(run.get('evaluation') for run in runs):
                differences['observed_execution_environment'] = {'left': observed_environments[0], 'right': observed_environments[1]}
            incomplete = False
            invalid = False
            reasons = []
            for run in runs:
                if not run.get('artifact') or not run.get('evaluation'):
                    incomplete = True
                elif tree_digest(run['artifact']) != run['artifact_digest'] or run['evaluation']['artifact_digest'] != run['artifact_digest']:
                    invalid = True
                    reasons.append('Artifact identity changed')
            for record, run in zip((left, right), runs):
                evaluation = run.get('evaluation')
                if evaluation and any(evaluation.get(key) != record[key] for key in ('benchmark_revision', 'criteria_digest', 'evaluator_version')):
                    invalid = True
                    reasons.append('Evaluation does not match frozen benchmark, criteria or evaluator')
                observed = {session.get('model') for session in record['sessions'] if session.get('variant') in ('stable', 'candidate') and session.get('role') == 'lead'}
                if observed and observed != {record['model']}:
                    invalid = True
                    reasons.append('Observed session models differ from the frozen configuration')
            reviewer_models = [{s.get('model') for s in record['sessions'] if s.get('variant') == arm and s.get('role') == 'reviewer'} for record, arm in ((left,'stable'),(right,'candidate'))]
            if all(reviewer_models) and reviewer_models[0] != reviewer_models[1]:
                invalid = True
                reasons.append('Independent reviewer models differ across arms')
            blocked = any(any(item['status'] == 'BLOCKED' for item in r.get('evaluation', {}).get('criteria', {}).values()) for r in runs)
            quality = [r.get('evaluation', {}).get('quality_pass') for r in runs]
            active = any(session['status'] == 'active' for record in (left, right) for session in record['sessions'])
            measurements = {'left': self.measurements(left, 'stable'), 'right': self.measurements(right, 'candidate')}
            metric_comparisons = {}
            policy = left.get('comparison_policy', {'version': 1})
            if differences or invalid:
                outcome = 'Invalid experiment'
                if differences:
                    reasons.append('Frozen configurations differ; interpret their differences before comparing outcomes')
            elif left['model'] is None or right['model'] is None:
                outcome = 'Inconclusive result'
                reasons.append('Development model identity is unknown')
            elif incomplete or blocked or active:
                outcome = 'Inconclusive result'
                reasons.append('Registered work is still active' if active else 'Required artifact/evaluation evidence is incomplete' if incomplete else 'Evaluator capability is BLOCKED, not an observed application regression')
            elif quality == [True, False]:
                outcome = 'Regression observed'
                reasons.append('Stable meets mandatory quality; candidate does not')
            elif quality == [False, True]:
                outcome = 'Improvement observed'
                reasons.append('Candidate meets mandatory quality; stable does not')
            elif quality == [True, True] and policy['version'] >= 2:
                outcome, metric_comparisons = self.efficiency_comparison(measurements['left'], measurements['right'], policy)
                reasons.append('Both meet mandatory quality; conclusion describes only the separately comparable observed efficiency dimensions')
                if any(item['direction'] == 'unknown' for item in metric_comparisons.values()):
                    reasons.append('Unknown dimensions remain non-comparable and do not establish overall efficiency')
            else:
                outcome = 'No demonstrated advantage'
                reasons.append('Both meet mandatory quality; one pair and incomplete provider telemetry cannot establish efficiency' if quality == [True, True] else 'Neither application meets mandatory quality')
            result = {'left': identifier, 'right': right['id'], 'outcome': outcome, 'quality': quality,
                'observed_execution_environments': observed_environments,
                'differences': differences, 'reasons': reasons, 'measurements': measurements,
                'comparison_policy': policy, 'metric_comparisons': metric_comparisons,
                'comparison_source_digest': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'uncertainty': 'One pair cannot establish reliable superiority. Sequential order and public known tests can bias observations. Session provenance and context independence must be checked against actual chat/tool records.',
                'recommendation': 'Retain evidence, refine or repeat under matched conditions. Production ADA promotion requires separate review.'}
            if not other:
                previous = left.get('comparison')
                if previous and previous != result:
                    left.setdefault('comparison_history', []).append({'archived': now(), 'comparison': previous})
                left['comparison'] = result
                left['next_action'] = result['recommendation']
                if not incomplete and not blocked and not active:
                    left['status'] = 'invalid' if invalid or differences else 'completed'
                    left.setdefault('finished', now())
                self.save(left)
            return result

    def measurements(self, record, variant=None):
        sessions = [s for s in record['sessions'] if variant is None or s['variant'] == variant]
        result = {'work_segments': len(sessions), 'agent_sessions': None,
            'declared_observers': len({s['observer'] for s in sessions}),
            'failed_work_segments': sum(s['status'] in ('failed', 'interrupted') for s in sessions),
            'categories': {role: sum(s['role'] == role for s in sessions) for role in ('research', 'lead', 'reviewer')},
            'autonomy': record['runs'].get(variant, {}).get('autonomy', record['autonomy']),
            'autonomy_scope': 'variant' if variant in record['runs'] and 'autonomy' in record['runs'][variant] else 'legacy experiment-wide'}
        for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'model_calls', 'monetary_cost', 'wall_seconds'):
            values = [s[key] for s in sessions]
            result[key] = sum(values) if values and all(v is not None for v in values) else None
        for role, label in [('lead', 'development'), ('reviewer', 'review'), ('research', 'research')]:
            selected = [session for session in sessions if session['role'] == role]
            times = [session['wall_seconds'] for session in selected]
            result[label + '_wall_seconds'] = sum(times) if times and all(value is not None for value in times) and all(session['status'] != 'active' for session in selected) else None
            result[label + '_timing_coverage'] = {'segments': len(selected), 'known': sum(value is not None for value in times),
                                                  'scope': 'Sum of matched work segments, not lifecycle elapsed or inference-only time'}
        result['latest_evaluation'] = {key: run.get('evaluation', {}).get('accounting') for key, run in record['runs'].items() if variant is None or key == variant}
        history = []
        for key, run in record['runs'].items():
            if variant is not None and key != variant:
                continue
            for attempt in run.get('attempts', []):
                invocations = attempt.get('evaluations')
                if invocations:
                    history.extend({'variant': key, 'submission': attempt['id'], **entry} for entry in invocations)
                elif attempt.get('evaluation'):
                    accounting = attempt['evaluation'].get('accounting', {})
                    history.append({'variant': key, 'submission': attempt['id'], 'status': attempt['status'],
                        'accounting': accounting, 'wall_seconds': None,
                        'timing_coverage': 'Legacy evaluation-only duration; preflight duration unavailable'})
                else:
                    history.append({'variant': key, 'submission': attempt['id'], 'status': attempt['status'], 'wall_seconds': None})
        result['evaluation_history'] = history
        times = [entry.get('wall_seconds') for entry in history]
        result['total_evaluation_wall_seconds'] = sum(times) if times and all(value is not None for value in times) else None
        result['correction_attempts'] = sum(len(run.get('corrections', [])) for key, run in record['runs'].items() if variant is None or key == variant)
        result['self_check_corrections'] = sum(entry.get('origin') == 'self-check' for key, run in record['runs'].items() if variant is None or key == variant for entry in run.get('corrections', []))
        result['environment_retries'] = [event for session in sessions for event in session.get('receipt', {}).get('environment_retries', [])]
        result['independent_review_observations'] = sum(len(run.get('reviews', {})) for key, run in record['runs'].items() if variant is None or key == variant)
        result['coverage'] = 'Recorded current-session work only; unknown provider usage is not estimated.'
        return result

    def report(self, identifier):
        with lock(self.folder(identifier) / 'controller.lock'):
            record = self.load(identifier)
            result = {'id': identifier, 'status': record['status'], 'hypothesis': record['hypothesis'],
                'started': record['created'], 'finished': record.get('finished'),
                'stable_revision': record['stable']['commit'], 'candidate': record.get('candidate'),
                'benchmark_revision': record['benchmark_revision'], 'comparison': record.get('comparison'),
                'measurements': self.measurements(record), 'next_action': record['next_action'],
                'configuration': {key: record.get(key) for key in ('model', 'environment', 'benchmark', 'benchmark_revision',
                    'criteria_digest', 'evaluator_version', 'comparison_policy')},
                'runs': {variant: {'status': run['status'], 'artifact': run.get('artifact'),
                    'artifact_digest': run.get('artifact_digest'), 'criteria': run.get('evaluation', {}).get('criteria', {}),
                    'quality_pass': run.get('evaluation', {}).get('quality_pass'),
                    'reviews': {phase: receipt['status'] for phase, receipt in run.get('reviews', {}).items()},
                    'measurements': self.measurements(record, variant)} for variant, run in record['runs'].items()},
                'blocker': record.get('blocker'), 'analysis': record.get('analysis'),
                'study_design': record.get('study_design'),
                'repeat': {'prepare_argv': ['python', 'start.py', 'prepare', '--benchmark', record['benchmark'], '--model', record['model'] or 'CURRENT_MODEL'],
                    'required_benchmark_revision': record['benchmark_revision'],
                    'instructions': 'Use a new experiment ID and clean starter/context for each variant; preserve frozen task/criteria, model/tools and policy, record real work and review, submit/evaluate then compare. See docs/operations.md.'}}
            write(self.folder(identifier) / 'report.json', result)
            return result
