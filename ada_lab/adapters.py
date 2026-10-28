"""Installed task-adapter boundary. Scheduling does not know product task types."""
from .files import inventory, read
from . import evaluator


class RepairQueueAdapter:
    capabilities=('python','sqlite','native-isolation','real-browser')

    def verify(self,engine,task,identity):
        path,spec=evaluator.benchmark(task['id'])
        if spec['evaluator']!='repair_queue_v1' or inventory(path)!=identity['files']:
            raise RuntimeError('Frozen task differs from calibrated Repair Queue adapter')

    def preflight(self,engine):
        result=engine.lab.doctor()
        if result['status']!='PASS':raise RuntimeError('Native application/browser capability BLOCKED')
        calibration=read(engine.lab.state/'calibration.json') if (engine.lab.state/'calibration.json').exists() else {}
        if calibration.get('status')!='PASS' or calibration.get('evaluator_version')!=evaluator.version():
            raise RuntimeError('Current evaluator needs successful native calibration')

    def execute(self,engine,*arguments):
        return engine.execute_task(*arguments)


ADAPTERS={'repair_queue_v1':RepairQueueAdapter()}
