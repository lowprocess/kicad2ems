"""
One option's field in a settings form, from its Option (kicad2ems.core.options):
the S-parameter run's form (SetupTab: kicad2ems's and the solvers' options) and
an analysis's (AnalysisPanel) both build theirs here.

    w = field(o, changed)            # its widget; changed: called on an edit
    value(w), set_value(w, v)        # its tick, text or choice
"""
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QPlainTextEdit

NONE, DEFAULT = '(none)', '(default)'  # a drop-down's item for no value


def kind(o):
    """The field of an option: 'file', 'dir', 'bool', 'float', 'int', 'list',
    'str', or a tuple of choices ('' first when it may be unset)."""
    if o.key == 'out':
        return 'dir'
    if o.path:
        return 'file'
    if o.choices:
        return (('', ) if o.default is None else ()) + tuple(o.choices)
    return o.kind


def field(o, changed, text='', lines=False, default=False, hint=''):
    """o's widget, `changed` connected to its edits: a check box (text beside
    it), a drop-down of its choices, a box of lines (lines: a list's) or a line
    (hint: its placeholder). default: the drop-down's first item is the
    option's default ('(default)', or 'X (default)'), the others the rest;
    else its choices, NONE first where it may be unset."""
    k = kind(o)
    if k == 'bool':
        w = QCheckBox(text, checked=bool(o.default), toggled=changed)
    elif isinstance(k, tuple):
        w = QComboBox()
        if default:
            w.choices = ['', *(c for c in o.choices if c != o.default)]
            first = (DEFAULT if o.default in ('', None) else
                     f"{o.default} (default)")
        else:
            w.choices, first = list(k), None
        # (a choice may be a number: Palace's ABC order 1, 2)
        w.addItems([
            first if first and not i else NONE if c in (None, '') else str(c)
            for i, c in enumerate(w.choices)
        ])
        w.currentIndexChanged.connect(changed)
    elif lines and k == 'list':
        w = QPlainTextEdit(placeholderText='one a line', textChanged=changed)
        w.setFixedHeight(70)
    else:
        w = QLineEdit(placeholderText=hint, textChanged=changed)
    return w


def value(w):
    """A field's tick, text, or choice ('' for the first of a drop-down whose
    first is no value)."""
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, QComboBox):
        return w.choices[max(w.currentIndex(), 0)]
    return w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()


def set_value(w, v):
    """A field set: a tick (a bool, or text saying one), a choice (another
    value: the first item), text (a list a line in a box of lines)."""
    if isinstance(w, QCheckBox):
        w.setChecked(v is True or str(v).strip().lower() in ('1', 'true',
                                                             'yes', 'on'))
    elif isinstance(w, QComboBox):
        w.setCurrentIndex(
            next((i for i, c in enumerate(w.choices)
                  if v not in (None, '') and str(c) == str(v)), 0))
    elif isinstance(w, QPlainTextEdit):
        w.setPlainText('\n'.join(map(str, v)) if isinstance(v, (list, tuple))
                       else '' if v is None else str(v))
    elif w.text() != ('' if v is None else str(v)):
        w.setText('' if v is None else str(v))
