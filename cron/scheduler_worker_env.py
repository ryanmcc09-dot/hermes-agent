"""Cron: import path of the restart-safe external worker.

The worker bootstraps PM dependencies before entering ``cron.scheduler``. Its command
does not use ``hermes_cli.main``; pin the gateway's checkout onto ``sys.path`` first.
Historically it imported ``cron`` only through the implicit ``-m``
cwd entry. That entry is gone under ``PYTHONSAFEPATH`` and useless when the venv's
editable install maps a moved/deleted checkout -- the worker then dies with
"No module named 'cron'" before its ownership ack (#112729, hypothesised cause).

The shared subprocess sanitizer strips Hermes-owned PYTHONPATH entries because user
children must not see our tree. This child IS Hermes, so the pin is applied *after* the
env is built, on the sanitized env. Dependencies are activated from PM's committed
selection inside the child; secret scrubbing and dropped venv markers stand.
"""

from __future__ import annotations

import os
import sysconfig
from pathlib import Path


def external_worker_command(python: str, repo_root: Path, payload: Path, ack: Path) -> list[str]:
    """Activate the committed dependency generation before importing cron.

    A managed gateway's sys.executable is its bare embedded interpreter. The
    sanitized child intentionally loses runtime site-packages; pinning source
    alone cannot import scheduler dependencies. Use PM's boot-time activation,
    not a package install or the interactive CLI's update/recovery flow.
    """
    bootstrap = (
        "from pathlib import Path; "
        "from pm.environments import activate_dependencies; "
        f"activate_dependencies(Path({str(repo_root.resolve())!r})); "
        "import runpy; runpy.run_module('cron.scheduler', run_name='__main__')"
    )
    return [python, "-c", bootstrap, "--external-worker-file", str(payload), "--ack-file", str(ack)]


def managed_script_command(python: str, repo_root: Path, script: Path) -> list[str]:
    """Restore committed dependencies in a sanitized Python script child.

    No environment variables or secrets are copied from the gateway. Preserve
    normal script argv, sibling imports, cwd and exit behavior after PM activation.
    """
    bootstrap = (
        "import os, sys, runpy; from pathlib import Path; "
        f"sys.path.insert(0, {str(repo_root.resolve())!r}); "
        "from pm.environments import activate_dependencies; "
        f"activate_dependencies(Path({str(repo_root.resolve())!r})); "
        "script = sys.argv[1]; sys.argv = sys.argv[1:]; "
        "sys.path.insert(0, os.path.dirname(os.path.abspath(script))); "
        "runpy.run_path(script, run_name='__main__')"
    )
    return [python, "-c", bootstrap, str(script)]


def _installed_purelib() -> Path | None:
    try:
        return Path(sysconfig.get_paths()["purelib"]).resolve()
    except (KeyError, OSError):
        return None


def pin_hermes_tree_on_pythonpath(worker_env: dict, repo_root: Path) -> dict:
    """Prepend ``repo_root`` to the worker env's own PYTHONPATH (never ``os.environ``'s).

    Skipped when ``repo_root`` is the interpreter's ``purelib``: under a wheel / pipx /
    uv-tool install ``cron/`` lives in site-packages itself, which is already importable,
    and pinning it would move site-packages ahead of the stdlib on ``sys.path``.
    """
    root = str(repo_root)
    if _installed_purelib() == Path(root).resolve():
        return worker_env
    existing = [e for e in worker_env.get("PYTHONPATH", "").split(os.pathsep) if e]
    worker_env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([root, *existing]))
    return worker_env
