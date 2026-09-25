"""Real child-process regression for sanitized managed-runtime cron launches."""
import json
import os
from pathlib import Path
import subprocess
import sys

from cron.scheduler_worker_env import external_worker_command, pin_hermes_tree_on_pythonpath


def test_committed_dependencies_activate_before_cron_package_import(tmp_path):
    root = tmp_path / 'checkout with spaces'
    for directory in ('pm', 'cron', 'selected-generation'):
        (root / directory).mkdir(parents=True)
    (root / 'pm/__init__.py').write_text('')
    (root / 'pm/environments.py').write_text(
        'import sys\n'
        'def activate_dependencies(root):\n'
        '    sys.path.insert(0, str(root / "selected-generation"))\n')
    (root / 'selected-generation/worker_dependency.py').write_text('ready = True\n')
    # cron/__init__ imports dependencies before scheduler itself runs, as in production.
    (root / 'cron/__init__.py').write_text('import worker_dependency\nassert worker_dependency.ready\n')
    (root / 'cron/scheduler.py').write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    payload, ack = tmp_path/'payload.json', tmp_path/'ack.json'
    env = pin_hermes_tree_on_pythonpath({'PATH': os.defpath, 'PYTHONSAFEPATH': '1'}, root)
    old = subprocess.run([sys.executable, '-m', 'cron.scheduler'], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=15)
    assert old.returncode != 0 and 'worker_dependency' in old.stderr
    command = external_worker_command(sys.executable, root, payload, ack)
    new = subprocess.run(command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert new.returncode == 0, new.stderr
    assert json.loads(new.stdout) == ['--external-worker-file', str(payload), '--ack-file', str(ack)]
    assert 'VIRTUAL_ENV' not in env
