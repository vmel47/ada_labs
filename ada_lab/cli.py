"""CLI for experiments conducted in the current coding session."""
import argparse
import json
import sys
import subprocess

from .lab import Lab
from .files import read
from . import evaluator


def main():
    parser = argparse.ArgumentParser(prog='ada-lab', description='ADA experiments operated from your current coding chat')
    parser.add_argument('--state', help='State directory within canonical .lab')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init'); init.add_argument('--ada')
    commands.add_parser('doctor'); commands.add_parser('calibrate'); commands.add_parser('history')
    for name in ('prepare', 'evolve'):
        sub = commands.add_parser(name)
        sub.add_argument('--hypothesis'); sub.add_argument('--benchmark', default='repair-queue-v1')
        sub.add_argument('--model'); sub.add_argument('--max-sessions', type=int, default=12)
    for name in ('research', 'status', 'pause', 'resume', 'report', 'compare', 'run', 'candidate', 'begin', 'finish', 'review', 'correction', 'submit', 'evaluate-run'):
        sub = commands.add_parser(name); sub.add_argument('id')
        if name in ('run', 'review', 'correction', 'submit', 'evaluate-run'):
            sub.add_argument('--variant', choices=['stable', 'candidate', 'single'], required=True)
        if name == 'compare': sub.add_argument('--other')
        if name == 'candidate': sub.add_argument('--review', required=True); sub.add_argument('--intervention', required=True)
        if name == 'begin':
            sub.add_argument('--observer', required=True); sub.add_argument('--role', choices=['lead', 'reviewer', 'research'], required=True)
            sub.add_argument('--variant', choices=['stable', 'candidate', 'single']); sub.add_argument('--model'); sub.add_argument('--source-ref', required=True)
            sub.add_argument('--timed', action='store_true'); sub.add_argument('--stage')
        if name == 'finish': sub.add_argument('--session', required=True); sub.add_argument('--receipt', required=True)
        if name == 'review': sub.add_argument('--phase', choices=['design', 'behavior', 'code'], required=True); sub.add_argument('--receipt', required=True)
        if name == 'correction': sub.add_argument('--receipt', required=True)
        if name == 'submit': sub.add_argument('--intervention-evidence')
    sub = commands.add_parser('evaluate'); sub.add_argument('artifact'); sub.add_argument('--benchmark', default='repair-queue-v1')
    args = parser.parse_args(); lab = Lab(args.state)
    try:
        name = args.command
        if name == 'init':
            result = lab.initialize(args.ada)
            from .native import browser_executable
            try:
                result['browser'] = str(browser_executable())
            except RuntimeError as error:
                if not str(error).startswith('Browser missing:'):
                    raise
                subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True, timeout=300)
                result['browser'] = str(browser_executable())
        elif name == 'doctor': result = lab.doctor()
        elif name == 'calibrate': result = evaluator.calibrate(lab.state, lab.config()['ada'])
        elif name == 'history': result = [{'id':r['id'], 'status':r['status'], 'hypothesis':r['hypothesis'], 'next_action':r['next_action']} for r in lab.history()]
        elif name == 'prepare': result = lab.prepare(args.hypothesis, args.benchmark, args.model, args.max_sessions)
        elif name == 'evolve':
            pending = [r for r in lab.history() if r['status'] not in ('completed', 'invalid') and r['benchmark'] == args.benchmark
                       and (args.model is None or r['model'] == args.model) and (args.hypothesis is None or r['hypothesis'] == args.hypothesis)]
            result = lab.resume(pending[-1]['id']) if pending else lab.prepare(args.hypothesis, args.benchmark, args.model, args.max_sessions)
        elif name == 'research': result = lab.research(args.id)
        elif name == 'run': result = lab.run(args.id, args.variant)
        elif name == 'candidate': result = lab.freeze_candidate(args.id, read(args.review), args.intervention)
        elif name == 'begin': result = lab.begin(args.id, args.observer, args.role, args.variant, args.model, args.source_ref, args.timed, args.stage)
        elif name == 'finish': result = lab.finish(args.id, args.session, read(args.receipt))
        elif name == 'review': result = lab.add_review(args.id, args.variant, args.phase, read(args.receipt))
        elif name == 'correction': result = lab.correction(args.id, args.variant, read(args.receipt))
        elif name == 'submit': result = lab.submit(args.id, args.variant, read(args.intervention_evidence) if args.intervention_evidence else None)
        elif name == 'evaluate-run': result = lab.evaluate_run(args.id, args.variant)
        elif name == 'evaluate': result = evaluator.evaluate(args.artifact, lab.state, lab.config()['ada'], args.benchmark)
        elif name == 'resume': result = lab.resume(args.id)
        elif name == 'pause': result = lab.pause(args.id)
        elif name == 'status': result = lab.load(args.id)
        elif name == 'compare': result = lab.compare(args.id, args.other)
        elif name == 'report': result = lab.report(args.id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if isinstance(result, dict) and (result.get('status') in ('FAIL', 'BLOCKED') or result.get('quality_pass') is False):
            raise SystemExit(2)
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'status':'BLOCKED', 'error':str(error) or 'Interrupted; recorded state is preserved'}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
