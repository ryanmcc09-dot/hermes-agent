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


def test_script_child_activates_packages_and_preserves_script_contract(tmp_path, monkeypatch):
    from cron import scheduler_script
    from pm import environments
    from cron.scheduler_worker_env import managed_script_command

    root = tmp_path / 'managed checkout'
    (root / 'pm').mkdir(parents=True)
    (root / 'generation').mkdir()
    (root / 'pm/__init__.py').write_text('')
    (root / 'pm/environments.py').write_text(
        'import sys\ndef activate_dependencies(root):\n'
        '    sys.path.insert(0, str(root / "generation"))\n')
    (root / 'generation/script_dependency.py').write_text('ready = True\n')
    scripts = tmp_path / 'profile scripts'
    scripts.mkdir()
    (scripts / 'sibling.py').write_text('ready = True\n')
    script = scripts / 'check.py'
    script.write_text('import script_dependency,sibling,json,os,sys\n'
                      'assert script_dependency.ready and sibling.ready\n'
                      'print(json.dumps([sys.argv, __file__, os.getcwd(), os.getenv("PRIVATE_KEY")]))\n')
    facts = tmp_path / 'facts.json'
    facts.write_text('{}')
    monkeypatch.setattr(environments, 'runtime_facts_path', lambda _: facts)
    monkeypatch.setattr(scheduler_script, '__file__', str(root/'cron/scheduler_script.py'))
    monkeypatch.setattr(scheduler_script.sys, 'platform', 'linux')
    monkeypatch.setenv('PRIVATE_KEY', 'must-not-be-propagated')
    command, overlay, error = scheduler_script._script_argv(script)
    assert command == managed_script_command(sys.executable, root, script) and not overlay and not error
    env = {'PATH': os.defpath, 'PYTHONSAFEPATH': '1'}
    old = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=15)
    assert old.returncode != 0 and 'script_dependency' in old.stderr
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [[str(script)], str(script), str(tmp_path), None]
    facts.unlink()
    assert scheduler_script._script_argv(script)[0] == [sys.executable, str(script)]
