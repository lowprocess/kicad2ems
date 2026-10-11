"""
CSXCAD's geometry file (<name>.xml: what AppCSXCAD opens and mom25d reads) written without CSXCAD.

simulation.build_fdtd uses a small part of the CSXCAD / openEMS Python API: materials (fixed or Debye),
metal and conducting sheets, polygons, extruded polygons, boxes, cylinders, lumped elements and openEMS
lumped ports. This module records those calls and writes them the way CSX.Write2XML does: the same
elements, attributes, number formats and ids, and the same colours (CSXCAD draws them from the C
library's rand(), reproduced here). For the first structure of a process the file is byte for byte the
one CSXCAD writes (tests/test_csxwrite.py compares it with a file CSXCAD wrote).

--mom uses it when openEMS's Python packages are not installed: the MoM reference needs the geometry
file, not the FDTD engine, so mom25d runs on a machine without openEMS.

    FDTD, CSX, ports = simulation.build_fdtd(m, mesh, fmin, fmax, 0, backend='xml')
    CSX.Write2XML(path)

Not covered (build_fdtd does not put them in the file it writes for MoM): field dumps, NF2FF boxes,
primitive transformations, parameterised values.
"""
import numpy as np

_AXES = {'x': 0, 'y': 1, 'z': 2}


def _ny(d):
    """Direction as 0 / 1 / 2 (CSXCAD's CheckNyDir)."""
    if isinstance(d, str):
        return _AXES[d.lower()]
    return int(d)


def _e(v):                                          # CSXCAD's ParameterScalar values: printf %e
    return '%e' % float(v)


def _g(v):                                          # TinyXML's SetDoubleAttribute: printf %g
    return '%g' % float(v)


def _esc(s):
    """An attribute value as TinyXML escapes it."""
    s = str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    s = s.replace('"', '&quot;').replace("'", '&apos;')
    return ''.join(c if ord(c) >= 32 else f'&#x{ord(c):02X};' for c in s)


class _Rand:
    """The C library's rand() (glibc's TYPE_3 additive generator, seeded with 1): CSXCAD gives every new
    property a random colour (three rand() % 256), so this keeps the colours AppCSXCAD shows."""
    def __init__(self, seed=1):
        r = [seed]
        for _ in range(30):
            hi, lo = divmod(r[-1], 127773)
            w = 16807 * lo - 2836 * hi
            r.append(w + 2147483647 if w < 0 else w)
        self._r = r + r[:3]                         # r[31..33] = r[0..2]
        for _ in range(310):                        # glibc discards the first 310 outputs
            self._step()

    def _step(self):
        r = self._r
        v = (r[-31] + r[-3]) & 0xFFFFFFFF
        r.append(v)
        del r[0]
        return v

    def __call__(self):
        return self._step() >> 1


class _El:
    """A minimal XML element, printed the way TinyXML prints (4-space indent, ' />' for empty elements)."""
    def __init__(self, tag, attrs=(), text=None):
        self.tag, self.attrs, self.text, self.children = tag, list(attrs), text, []

    def add(self, tag, attrs=(), text=None):
        el = _El(tag, attrs, text)
        self.children.append(el)
        return el

    def lines(self, depth=0):
        pad = '    ' * depth
        a = ''.join(f' {k}="{_esc(v)}"' for k, v in self.attrs)
        if self.text is not None:
            return [f'{pad}<{self.tag}{a}>{_esc(self.text)}</{self.tag}>']
        if not self.children:
            return [f'{pad}<{self.tag}{a} />']
        out = [f'{pad}<{self.tag}{a}>']
        for c in self.children:
            out += c.lines(depth + 1)
        return out + [f'{pad}</{self.tag}>']


# ---- primitives ------------------------------------------------------------------------------------------
class _Box:
    def __init__(self, start, stop, priority):
        self.a, self.b, self.priority = [float(v) for v in start], [float(v) for v in stop], int(priority)

    def element(self, parent):
        el = parent.add('Box', [('Priority', self.priority)])
        for tag, p in (('P1', self.a), ('P2', self.b)):
            el.add(tag, [('X', _e(p[0])), ('Y', _e(p[1])), ('Z', _e(p[2]))])


class _Polygon:
    def __init__(self, points, norm_dir, elevation, priority, length=None):
        xs, ys = (np.asarray(points[0], float), np.asarray(points[1], float))
        self.pts = list(zip(xs.tolist(), ys.tolist()))
        self.norm, self.elev, self.priority, self.length = _ny(norm_dir), float(elevation), int(priority), length

    def element(self, parent):
        attrs = [('Priority', self.priority), ('Elevation', _e(self.elev)), ('NormDir', self.norm),
                 ('QtyVertices', len(self.pts))]
        if self.length is not None:
            attrs.append(('Length', _e(self.length)))
        el = parent.add('Polygon' if self.length is None else 'LinPoly', attrs)
        for x, y in self.pts:
            el.add('Vertex', [('X1', _e(x)), ('X2', _e(y))])


class _Cylinder:
    def __init__(self, start, stop, radius, priority):
        self.a, self.b = [float(v) for v in start], [float(v) for v in stop]
        self.r, self.priority = float(radius), int(priority)

    def element(self, parent):
        el = parent.add('Cylinder', [('Priority', self.priority), ('Radius', _e(self.r))])
        for tag, p in (('P1', self.a), ('P2', self.b)):
            el.add(tag, [('X', _e(p[0])), ('Y', _e(p[1])), ('Z', _e(p[2]))])


# ---- properties ------------------------------------------------------------------------------------------
class _Property:
    tag, alpha, visible = 'Property', 255, True

    def __init__(self, csx, name):
        self.name, self.prims, self.id = str(name), [], None
        rgb = [csx.rand() % 256 for _ in range(3)]           # drawn when CSXCAD constructs the property
        self.color = rgb + [self.alpha]

    def AddBox(self, start, stop, priority=0):
        self.prims.append(_Box(start, stop, priority))
        return self.prims[-1]

    def AddPolygon(self, points, norm_dir, elevation, priority=0):
        self.prims.append(_Polygon(points, norm_dir, elevation, priority))
        return self.prims[-1]

    def AddLinPoly(self, points, norm_dir, elevation, length, priority=0):
        self.prims.append(_Polygon(points, norm_dir, elevation, priority, length=float(length)))
        return self.prims[-1]

    def AddCylinder(self, start, stop, radius, priority=0):
        self.prims.append(_Cylinder(start, stop, radius, priority))
        return self.prims[-1]

    def attrs(self):
        return []

    def tail(self, el):
        """Elements after the primitives."""

    def element(self, parent):
        a = [('ID', self.id), ('Name', self.name)] + ([] if self.visible else [('Visible', 0)]) + self.attrs()
        el = parent.add(self.tag, a)
        rgba = list(zip('RGBa', self.color))
        el.add('FillColor', rgba)
        el.add('EdgeColor', rgba)
        prims = el.add('Primitives')
        for p in self.prims:
            p.element(prims)
        self.tail(el)
        return el


def _vec(v, rest):
    return ','.join(_e(x) for x in [v, rest, rest])


class Material(_Property):
    tag, alpha = 'Material', 123

    def __init__(self, csx, name, epsilon=1.0, kappa=0.0, **kw):
        super().__init__(csx, name)
        if kw:
            raise NotImplementedError(f"csxwrite: material options {sorted(kw)}")
        self.epsilon, self.kappa = float(epsilon), float(kappa)
        self.poles = {}

    def attrs(self):
        return [('Isotropy', 1)]

    def _values(self):
        return [('Epsilon', _vec(self.epsilon, 1.0)), ('Mue', _vec(1.0, 1.0)), ('Kappa', _vec(self.kappa, 0.0)),
                ('Sigma', _vec(0.0, 0.0)), ('Density', _e(0.0))]

    def tail(self, el):
        el.add('Property', self._values())
        el.add('Weight', [(k, ','.join([_e(1.0)] * 3) if k != 'Density' else _e(1.0)) for k, _ in self._values()])


class CSPropDebyeMaterial(Material):
    """Debye material as built by simulation._debye_material: CSPropDebyeMaterial(pset, order, name,
    epsilon, kappa), then SetDispersiveMaterialProperty(order=i, eps_delta, eps_relax) per pole."""
    tag = 'DebyeMaterial'

    def __init__(self, pset, order, name, epsilon=1.0, kappa=0.0):
        super().__init__(pset, name, epsilon=epsilon, kappa=kappa)
        self.order = int(order)
        self.poles = {i: (0.0, 0.0) for i in range(self.order)}

    def SetDispersiveMaterialProperty(self, order, eps_delta=0.0, eps_relax=0.0):
        self.poles[int(order)] = (float(eps_delta), float(eps_relax))

    def _values(self):
        v = super()._values()
        for i in range(self.order):
            d, tau = self.poles[i]
            v += [(f'EpsilonDelta_{i + 1}', _vec(d, 0.0)), (f'EpsilonRelaxTime_{i + 1}', _vec(tau, 0.0))]
        return v


class Metal(_Property):
    tag = 'Metal'


class ConductingSheet(Metal):
    tag = 'ConductingSheet'

    def __init__(self, csx, name, conductivity=0.0, thickness=0.0):
        super().__init__(csx, name)
        self.sigma, self.t = float(conductivity), float(thickness)

    def attrs(self):
        return [('Conductivity', _e(self.sigma)), ('Thickness', _e(self.t))]


class LumpedElement(_Property):
    tag = 'LumpedElement'

    def __init__(self, csx, name, ny, caps=False, R=None, C=None, L=None, LEtype=0):
        super().__init__(csx, name)
        self.ny, self.caps, self.vals, self.letype = _ny(ny), bool(caps), dict(R=R, C=C, L=L), int(LEtype)

    def attrs(self):
        a = [('Direction', self.ny), ('Caps', int(self.caps))]
        a += [(k, _e(v)) for k, v in self.vals.items() if v is not None]
        return a + [('LEtype', _e(self.letype))]


class Excitation(_Property):
    tag = 'Excitation'

    def __init__(self, csx, name, exc_type=0, exc_val=(0, 0, 0), delay=0.0):
        super().__init__(csx, name)
        self.type, self.val, self.delay = int(exc_type), [float(v) for v in exc_val], float(delay)

    def attrs(self):
        return [('Number', 0), ('Enabled', 1), ('Frequency', _e(0.0)), ('Delay', _e(self.delay)), ('Type', self.type),
                ('Excite', ','.join(_e(v) for v in self.val)), ('PropDir', ','.join([_e(0.0)] * 3))]

    def tail(self, el):
        el.add('Weight', [('X', _e(1.0)), ('Y', _e(1.0)), ('Z', _e(1.0))])


class ProbeBox(_Property):
    tag, visible = 'ProbeBox', False

    def __init__(self, csx, name, p_type=0, weight=1.0, norm_dir=-1):
        super().__init__(csx, name)
        self.type, self.weight, self.norm = int(p_type), float(weight), int(norm_dir)

    def attrs(self):
        return [('Number', 0), ('Type', self.type), ('Weight', _g(self.weight)), ('NormDir', self.norm),
                ('StartTime', _g(0)), ('StopTime', _g(0))]


# ---- the structure ---------------------------------------------------------------------------------------
class _Grid:
    def __init__(self):
        self.unit, self.lines = 1.0, [np.zeros(0)] * 3

    def SetDeltaUnit(self, unit):
        self.unit = float(unit)

    def SetLines(self, d, lines):
        self.lines[_ny(d)] = np.unique(np.asarray(lines, float))      # CSXCAD sorts and drops duplicates

    def element(self, parent):
        g = parent.add('RectilinearGrid', [('DeltaUnit', _g(self.unit)), ('CoordSystem', 0)])
        for tag, ls in zip(('XLines', 'YLines', 'ZLines'), self.lines):
            g.add(tag, [('Qty', len(ls))], ','.join('%.15g' % v for v in ls) if len(ls) else None)


class ContinuousStructure:
    """The CSXCAD calls build_fdtd makes, recorded; Write2XML writes the file."""
    def __init__(self):
        self.rand = _Rand()
        self.props, self.grid = [], _Grid()

    def GetGrid(self):
        return self.grid

    def GetParameterSet(self):
        return self                                         # CSPropDebyeMaterial(CSX.GetParameterSet(), ...)

    def AddProperty(self, prop):
        prop.id = len(self.props)
        self.props.append(prop)
        return prop

    def AddMaterial(self, name, **kw):
        return self.AddProperty(Material(self, name, **kw))

    def AddMetal(self, name):
        return self.AddProperty(Metal(self, name))

    def AddConductingSheet(self, name, **kw):
        return self.AddProperty(ConductingSheet(self, name, **kw))

    def AddLumpedElement(self, name, ny, caps=False, **kw):
        return self.AddProperty(LumpedElement(self, name, ny, caps=caps, **kw))

    def AddExcitation(self, name, exc_type, exc_val, delay=0.0):
        return self.AddProperty(Excitation(self, name, exc_type, exc_val, delay))

    def AddProbe(self, name, p_type, weight=1.0, norm_dir=-1):
        return self.AddProperty(ProbeBox(self, name, p_type, weight, norm_dir))

    def AddDump(self, *a, **kw):
        raise NotImplementedError("csxwrite: field dumps need CSXCAD (openEMS's Python packages)")

    def tostring(self):
        root = _El('ContinuousStructure', [('CoordSystem', 0)])
        self.grid.element(root)
        root.add('BackgroundMaterial', [('Epsilon', 1), ('Mue', 1), ('Kappa', 0), ('Sigma', 0)])
        root.add('ParameterSet')
        props = root.add('Properties')
        for p in self.props:
            p.element(props)
        return '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' + '\n'.join(root.lines()) + '\n'

    def Write2XML(self, path):
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(self.tostring())
        return True


class _LumpedPort:
    def __init__(self, number, R, start, stop, ny):
        self.number, self.R, self.start, self.stop, self.ny = number, R, start, stop, ny


class openEMS:
    """The openEMS calls build_fdtd makes: the FDTD settings are not part of the CSX file; a lumped port adds
    its resistor, excitation and voltage / current probes to the structure as openEMS's LumpedPort does."""
    def __init__(self, NrTS=1_000_000, EndCriteria=1e-5, **kw):
        self.CSX = None

    def SetGaussExcite(self, f0, fc):
        pass

    def SetBoundaryCond(self, bc):
        pass

    def SetCSX(self, CSX):
        self.CSX = CSX

    def AddLumpedPort(self, port_nr, R, start, stop, p_dir, excite=0, priority=0, delay=0.0, **kw):
        CSX = self.CSX
        start, stop = np.array(start, float), np.array(stop, float)
        ny = _ny(p_dir)
        if start[ny] == stop[ny]:
            raise ValueError('LumpedPort: start and stop may not be identical in excitation direction')
        direction = float(np.sign(stop[ny] - start[ny]))
        lbl = lambda what: f"port_{what}_{port_nr}"
        res = CSX.AddLumpedElement(lbl('resist'), ny=ny, caps=True, R=R) if R > 0 else CSX.AddMetal(lbl('resist'))
        res.AddBox(start, stop, priority=priority)
        if excite != 0:
            vec = np.zeros(3)
            vec[ny] = -1 * direction * excite
            CSX.AddExcitation(lbl('excite'), exc_type=0, exc_val=vec, delay=delay).AddBox(start, stop, priority=priority)
        u0, u1 = 0.5 * (start + stop), 0.5 * (start + stop)
        u0[ny], u1[ny] = start[ny], stop[ny]
        CSX.AddProbe(lbl('ut'), p_type=0, weight=-1).AddBox(u0, u1)
        i0, i1 = start.copy(), stop.copy()
        i0[ny] = i1[ny] = 0.5 * (start[ny] + stop[ny])
        CSX.AddProbe(lbl('it'), p_type=1, weight=direction, norm_dir=ny).AddBox(i0, i1)
        return _LumpedPort(port_nr, R, start, stop, ny)
