import os, sys, subprocess
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(scope='session')
def demo_files(tmp_path_factory):
    """Generated demo boards: (transition base path, differential base path)."""
    d = tmp_path_factory.mktemp('boards')
    gen = os.path.join(ROOT, 'examples', 'make_demo_board.py')
    subprocess.check_call([sys.executable, gen, str(d / 'demo')])
    subprocess.check_call([sys.executable, gen, str(d / 'diff'), 'diff'])
    return str(d / 'demo'), str(d / 'diff')
