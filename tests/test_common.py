"""Shared test helpers for the per-brand LinkedIn test files."""
import os, sys, subprocess, yaml
from pathlib import Path

PROJECT_ROOT = Path('/opt/linkedin')
SEO_ROOT     = Path('/opt/seo')
COMMONLIB    = Path('/opt/commonlib')
PKG_DIR      = PROJECT_ROOT / 'linkedin_generation'
PYTHON_BIN   = '/opt/venv/bin/python'
CAMPAIGN_YAML = PROJECT_ROOT / 'config' / 'linkedin_campaign.yaml'

class TestRun:
    def __init__(self, brand):
        self.brand = brand
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, name, ok, detail=''):
        if ok:
            print(f'PASS [{self.brand}] {name}')
            self.passed += 1
        else:
            print(f'FAIL [{self.brand}] {name}{("  - " + detail) if detail else ""}')
            self.failed += 1
            self.failures.append(name)

    def summary(self):
        total = self.passed + self.failed
        print()
        print(f'=== {self.brand}: {self.passed}/{total} passed ({self.failed} failures) ===')
        if self.failures:
            for f in self.failures: print(f'  - {f}')
        return 0 if self.failed == 0 else 1

def py_help_ok(scheduler_filename):
    """Run scheduler --help via miniconda env, returns True if exit 0."""
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{PROJECT_ROOT}:{SEO_ROOT}:{COMMONLIB}'
    result = subprocess.run([PYTHON_BIN, str(PKG_DIR / scheduler_filename), '--help'],
                            capture_output=True, env=env, timeout=30)
    return result.returncode == 0

def load_campaign():
    with open(CAMPAIGN_YAML) as f:
        return yaml.safe_load(f)

def py_compile_ok(filename):
    result = subprocess.run([PYTHON_BIN, '-m', 'py_compile', str(PKG_DIR / filename)],
                            capture_output=True, timeout=10)
    return result.returncode == 0

def bash_n_ok(script_path):
    result = subprocess.run(['bash', '-n', str(script_path)], capture_output=True, timeout=10)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Undefined-name guard (2026-09-29).
#
# py_compile only parses: a name that is CALLED but never imported compiles
# fine and raises NameError at run time. That has now killed the 07:00 TNT post
# twice - recent_post_history on 2026-09-17, recent_themes on 2026-09-29 - and
# the second one also showed the quieter failure mode: repeats_recent_story was
# called inside , so the duplicate-story gate was silently
# dead for however long the import had been missing.
#
# This collects every name BOUND anywhere in the file (imports, assignments,
# def/class, arguments, for/with/except targets, comprehensions, global/nonlocal)
# and flags loads of anything else that is not a builtin. Shadowing across
# scopes makes it deliberately permissive: it never invents a failure, it only
# catches the name that exists nowhere in the file.
# ---------------------------------------------------------------------------
import ast as _ast, builtins as _builtins


def undefined_names(py_path):
    tree = _ast.parse(Path(py_path).read_text())
    bound = set()
    for n in _ast.walk(tree):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            bound.add(n.name)
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda)):
            a = n.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None:
                    bound.add(arg.arg)
        elif isinstance(n, _ast.ImportFrom):
            for al in n.names:
                bound.add(al.asname or al.name)
        elif isinstance(n, _ast.Import):
            for al in n.names:
                bound.add((al.asname or al.name).split('.')[0])
        elif isinstance(n, _ast.Name) and isinstance(n.ctx, (_ast.Store, _ast.Del)):
            bound.add(n.id)
        elif isinstance(n, _ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (_ast.Global, _ast.Nonlocal)):
            bound.update(n.names)
    used = {n.id for n in _ast.walk(tree)
            if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load)}
    return sorted(u for u in used
                  if u not in bound and not hasattr(_builtins, u)
                  and not (u.startswith('__') and u.endswith('__')))


def undefined_names_in_package():
    """Every generator module, worst offender first. Returns {relpath: [names]}."""
    out = {}
    for p in sorted(PKG_DIR.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        missing = undefined_names(p)
        if missing:
            out[str(p.relative_to(PKG_DIR))] = missing
    return out
