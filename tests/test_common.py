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
