#!/usr/bin/env python3
"""The stack's lines of code over time, from each repository's git history: the data behind the code-growth report
and the file-graph animation.

    python tools/code_growth.py                 # the checkouts side by side (../pcbqs, ../mom25d, ...); writes ./code_growth/
    python tools/code_growth.py --out DIR       # somewhere else
    python tools/code_growth.py --rev HEAD      # each checkout's HEAD instead of origin/main

It writes three files:
- loc_by_commit.json: {rows, meta}, a row per commit per repository (repo, t, sha, msg, src, native, tests, tools,
  docs, code, phase), code being src + native + tests + tools.
- extractions.json: {rows, meta}, when gfdtd, mom25d, dfsynth and kicad2ems-ipc lived inside kicad2ems
  (insideFrom, insideUntil).
- filegraph.json: {repos, files, events}, each file's lines over time: files are [repo index, path, kind] and events
  [t, message, repo, [[file index, lines], ...]] in time order.

How it counts:
- Every commit on the revision's first-parent history, with git's per-file line counts (--numstat, --no-renames)
  added up as the history goes. Lines are physical lines, blank lines and comments included.
- docs: .md and .rst. tests: under tests/ or test/, test_*.py, conftest.py. tools: tools/, examples/, validation/,
  benchmark-results/, docs/, pcm/, shell scripts and top-level .py. native: C, C++, CUDA, Slang and GLSL. src: the
  rest of the Python. Other files (data, images, boards, Touchstone) are not counted.
- A commit's time is its committer time in UTC, held so it never goes backwards along the history (rebased and
  merged commits can carry older dates).
- gfdtd, mom25d, dfsynth and kicad2ems-ipc were developed inside kicad2ems and extracted with their history, so
  their early commits repeat code kicad2ems's own commits already count. Those rows are phase 'inside kicad2ems',
  and a whole-stack total leaves a repository out while it is inside (extractions.json). The windows come from the
  kicad2ems commits that moved each package in or out (MOVES). In filegraph.json those repositories' files appear
  when they move out of kicad2ems, and mom25d's leave when it moves in.
- mom25d was a git submodule of kicad2ems for 11 hours on 2026-10-10; neither history holds its code for that time,
  so the totals dip there.
"""
import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                         # the checkouts' folder
REPOS = ['kicad2ems', 'mom25d', 'pcbqs', 'gfdtd', 'kicad2ems-gui', 'kicad2ems-fem', 'kicad2ems-fdtd',
         'kicad2ems-ipc', 'dfsynth', 'emsdev', 'kicad2ems-project']
NATIVE = {'.cpp', '.cc', '.c', '.h', '.hpp', '.cu', '.cuh', '.slang', '.comp', '.glsl', '.wgsl'}
PY = {'.py', '.pyx'}
SHELL = {'.sh', '.cmd', '.ps1', '.bat'}
DOC = {'.md', '.rst'}
KINDS = ['src', 'native', 'tests', 'tools', 'docs']
# the kicad2ems commits that moved a package out: the start of their subject
MOVES = {'gfdtd': 'gfdtd moves to its own repository', 'mom25d': 'mom25d moves back to its own repository',
         'kicad2ems-ipc': 'The KiCad plugin moves to its own repository',
         'dfsynth': 'dfsynth moves to its own repository'}
MOM_IMPORT = 'Import mom25d (2.5D MoM solver) with its history'      # and the one that moved mom25d in


def kind(path):
    """The kind of code a path is, or None when it is not counted."""
    m = re.search(r'(\.[A-Za-z0-9]+)$', path)
    ext = m.group(1).lower() if m else ''
    if ext in DOC:
        return 'docs'
    if ext not in NATIVE | PY | SHELL:
        return None
    parts = path.split('/')
    name = parts[-1]
    if 'tests' in parts or 'test' in parts or name.startswith('test_') or name == 'conftest.py':
        return 'tests'
    if parts[0] in ('tools', 'examples', 'validation', 'benchmark-results', 'docs', 'pcm') or ext in SHELL \
            or (len(parts) == 1 and ext in PY):
        return 'tools'
    return 'native' if ext in NATIVE else 'src'


def commits(repo, rev):
    """[(time, sha, subject, {path: lines after the commit})] along the first-parent history, oldest first."""
    out = subprocess.run(['git', '-C', str(ROOT / repo), 'log', rev, '--first-parent', '--reverse', '--no-renames',
                          '--numstat', '--diff-merges=first-parent', '--format=@@%H%x09%cI%x09%s'],
                         capture_output=True, text=True, check=True).stdout
    state, latest, result = defaultdict(int), None, []
    for block in out.split('@@')[1:]:
        head, *changes = block.split('\n')
        sha, when, subject = head.split('\t', 2)
        t = datetime.fromisoformat(when).astimezone(timezone.utc)
        latest = t if latest is None or t > latest else latest
        changed = {}
        for line in changes:
            if not line.strip():
                continue
            added, deleted, path = line.split('\t', 2)
            if added == '-' or not kind(path):                     # binary, or not counted
                continue
            state[path] += int(added) - int(deleted)
            changed[path] = max(0, state[path])
        result.append((latest.strftime('%Y-%m-%dT%H:%M:%SZ'), sha[:10], subject, changed, dict(state)))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--out', type=Path, default=Path('code_growth'), help='folder for the three files')
    ap.add_argument('--rev', default='origin/main', help="each repository's revision (default origin/main)")
    a = ap.parse_args()
    history = {r: commits(r, a.rev) for r in REPOS}

    k2e = history['kicad2ems']
    def when(prefix):
        return next(c for c in k2e if c[2].startswith(prefix))
    moved = {r: when(p) for r, p in MOVES.items()}
    mom_in = when(MOM_IMPORT)[0]
    windows = {r: (mom_in if r == 'mom25d' else history[r][0][0], c[0]) for r, c in moved.items()}
    inside = lambda r, t: r in windows and windows[r][0] <= t < windows[r][1]

    rows = []
    for r in REPOS:
        for t, sha, subject, _, state in history[r]:
            n = dict.fromkeys(KINDS, 0)
            for path, lines in state.items():
                n[kind(path)] += lines
            rows.append({'repo': r, 't': t, 'sha': sha, 'msg': subject[:100], **n,
                         'code': n['src'] + n['native'] + n['tests'] + n['tools'],
                         'phase': 'inside kicad2ems' if inside(r, t) else 'own repository'})

    revs = ', '.join(f"lowprocess/{r} at " + subprocess.run(['git', '-C', str(ROOT / r), 'rev-parse', '--short', a.rev],
                     capture_output=True, text=True).stdout.strip() for r in REPOS)
    meta = [{'label': 'code_growth.py', 'value': Path(__file__).read_text(encoding='utf-8'), 'type': 'code',
             'lang': 'python'}, {'label': 'Input', 'value': f'{a.rev} of {revs}'}]
    extractions = [{'repo': r, 'insideFrom': windows[r][0], 'insideUntil': windows[r][1],
                    'movedOutBy': c[1] + ' ' + c[2][:100]} for r, c in moved.items()]

    # each file's lines over time, the extracted packages' files counted once
    fid, files, events = {}, [], []
    def file_id(r, path):
        if (r, path) not in fid:
            fid[r, path] = len(files)
            files.append([REPOS.index(r), path, KINDS.index(kind(path))])
        return fid[r, path]
    for r in REPOS:
        w, out = windows.get(r), False
        for t, sha, subject, changed, state in history[r]:
            if w and w[0] <= t < w[1]:
                continue
            if w and t >= w[1] and not out:                        # the package arrives whole when it moves out
                out = True
                events.append([w[1], f'{r} moves out of kicad2ems', r,
                               [[file_id(r, p), n] for p, n in state.items() if n > 0]])
                continue
            if changed:
                events.append([t, subject[:90], r, [[file_id(r, p), n] for p, n in changed.items()]])
        if r == 'mom25d':                                          # and leaves when it moves into kicad2ems
            before = [c for c in history[r] if c[0] < w[0]]
            if before:
                events.append([w[0], 'mom25d is imported into kicad2ems', r,
                               [[file_id(r, p), 0] for p, n in before[-1][4].items() if n > 0]])
    events.sort(key=lambda e: e[0])

    a.out.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (a.out / name).write_text(json.dumps(obj, separators=(',', ':')), encoding='utf-8')
    dump('loc_by_commit.json', {'rows': rows, 'meta': meta})
    dump('extractions.json', {'rows': extractions, 'meta': meta})
    dump('filegraph.json', {'repos': REPOS, 'files': files, 'events': events})
    last = {r: history[r][-1][4] for r in REPOS}
    code = sum(n for s in last.values() for p, n in s.items() if kind(p) != 'docs')
    print(f'{len(rows)} commits, {len(files)} files, {code:,} lines of code now; written to {a.out}/')


if __name__ == '__main__':
    main()
