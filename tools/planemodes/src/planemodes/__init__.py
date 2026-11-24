"""planemodes: plane-pair cavity eigenmodes of printed circuit boards.

    from planemodes import load_board, StudyConfig, run_study
    board = load_board('board.kicad_pcb')          # or an ODB++ dir / .tgz / .zip
    results = run_study(board, StudyConfig(fmin=1e9, fmax=10e9))
"""
from .model import Board, Layer, Via
from .readers import load_board
from .study import StudyConfig, run_study
from .mesh import MeshConfig
from .solver import SolverConfig

__all__ = ['Board', 'Layer', 'Via', 'load_board', 'StudyConfig', 'run_study',
           'MeshConfig', 'SolverConfig']
__version__ = '0.1.0'
