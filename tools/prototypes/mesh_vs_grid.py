"""The quad-mesh prototype (xsection_quadmesh) against the grid solver on the benchmark cross-sections."""
import time, math, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import xsection_trimesh as femx, xsection_quadmesh as qmesh
from kicad2ems.lines import xsection as X
from kicad2ems.lines import formulas as F, coupled as K
k=1/math.cosh(math.pi*0.15/0.8); cohn=30*math.pi/math.sqrt(4.3)*F.ellipk(k)/F.ellipk(math.sqrt(1-k*k))
cases=[('strip',lambda: X.stripline(0.15,0.2,0.2,4.3), cohn, 0),
 ('ms',lambda: X.microstrip(0.35,0.2,4.3), F.microstrip(0.35,0.2,4.3)[0], 0),
 ('ms t35',lambda: X.microstrip(0.4,0.2104,4.4,0.035), F.microstrip(0.4,0.2104,4.4,0.035)[0], 0),
 ('strip t35',lambda: X.stripline(0.15,0.2,0.2,4.3,t=0.035), F.stripline(0.15,0.435,4.3,0.035)[0], 0),
 ('cpwg',lambda: X.microstrip(0.5,1.6,4.4,s=0.5), F.cpwg(0.5,0.5,1.6,4.4)[0], 0),
 ('cms zo',lambda: X.coupled_microstrip(0.15,0.15,0.2,4.4), K.coupled_microstrip(0.15,0.15,0.2,4.4)[1], 1),
 ('5 lines',lambda: X.microstrip([0.6]*5,0.8,4.4,gap=[0.58,0.74,0.74,0.58]), None, 0),
 ('mask pair',lambda: X.coupled_microstrip(0.13,0.25,0.0994,4.45,0.035,mask=0.01,er_mask=3.8), None, 1)]
z = lambda r, mode: r.single()[0] if mode == 0 else r.pair()[1]
X.CrossSection(dielectrics=[], planes=[0.0]).conductor(-1,1,0.5).solve(); qmesh.solve(X.microstrip(0.3,0.2,4))
tg=tm=0
for name,mk,ref,mode in cases:
    xs=mk(); t=time.perf_counter(); rg=xs.solve(); dg=time.perf_counter()-t
    t=time.perf_counter(); rm,n1=qmesh.solve(xs); dm=time.perf_counter()-t
    vg, vm = z(rg,mode), z(rm,mode); tg+=dg; tm+=dm
    e=lambda v: '' if ref is None else '%+.2f%%'%((v/ref-1)*100)
    print(f"{name:10s} grid {vg:7.3f} {e(vg):7s} {rg.nodes:6d} n {dg*1e3:5.0f} ms | mesh {vm:7.3f} {e(vm):7s} {rm.nodes:6d} n ({n1}) {dm*1e3:5.0f} ms | mesh/grid {(vm/vg-1)*100:+.2f}%")
print(f'total grid {tg*1e3:.0f} ms, mesh {tm*1e3:.0f} ms')
