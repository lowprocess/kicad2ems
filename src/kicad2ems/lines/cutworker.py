"""
A worker process of the line model's field solves (reference.solve_deferred starts them: python -m
kicad2ems.lines.cutworker). On its stdin, pickled lists of cross-sections, each ('cut' | 'cpwg' | 'group', the
arguments of reference._solve_cut, _solve_cpwg or _solve_group); on its stdout, for each list, the pickled [(item,
(Z0, eeff))] ((C, C_air) for a coupled group), None for one the solver could not solve. It ends when its stdin
closes.
"""
import pickle
import sys


def main():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr                                 # nothing else may write into the results
    from .reference import _job
    while True:
        try:
            items = pickle.load(inp)
        except EOFError:
            return 0
        pickle.dump([_job(item) for item in items], out)
        out.flush()


if __name__ == '__main__':
    sys.exit(main())
