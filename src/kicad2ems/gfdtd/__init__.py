"""
gfdtd: an FDTD solver in the manner of openEMS, built to run on a GPU (Vulkan
through slangpy), with its compiled CPU core
and numpy (the reference) on the CPU. Models come in through CSXCAD's Python API
or its XML files, so a model made for openEMS runs here unchanged:

    from kicad2ems.gfdtd import ContinuousStructure, openEMS
        # instead of CSXCAD's / openEMS's
    python -m kicad2ems.gfdtd model.xml  # an openEMS XML file, as `openEMS
                                         # model.xml`

What it models (the MVP): rectilinear grids; isotropic dielectrics with
conductivity (kappa); metals as PEC, conducting sheets with conductor loss
(openEMS's model); lumped elements (R, L, C, parallel or series, with caps);
openEMS's ports (lumped, MSL, stripline, CPW, coaxial, rectangular / circular /
any waveguide, curve); a Gaussian excitation, soft or hard, E or H, weighted and
delayed; PEC, PMC, Mur and PML boundaries; Debye materials; voltage, current and
mode-matching probes; field dumps; NF2FF; the energy end criterion; ABORT files.

    csx.py       CSXCAD's structure: grid, properties, primitives, XML
    fdtd.py      openEMS's interface: settings, LumpedPort, Run, XML; the
                 command line
    ports.py     openEMS's other ports: MSL, stripline, CPW, coax, waveguides,
                 curve
    operator.py  the model on the grid: coefficients, lumped elements,
                 excitations, boundaries, probes
    engine.py    the time loop, the numpy, core and Vulkan backends, status
                 lines and probe files
    kernels_vk.py  the GPU kernels (Slang), around the update in
                 core/fdtd_core.h

What kicad2ems.fdtd (which runs kicad2ems's models here) relies on:

    ContinuousStructure, openEMS(..., backend=)
        openEMS's / CSXCAD's Python interface, as openEMS scripts use it
    engine.gpu_info(), engine.core_info()
        (usable, one line) of the GPU and the compiled CPU core
    engine.resolve_backend(name)
        'vulkan', 'core' or 'numpy' for 'auto', 'gpu', 'cpu', ...
    engine.concurrent_runs(cells, runs)
        how many runs to have on the GPU at once
    ContinuousStructure.AddConductingSheet(roughness=, finish=,
                                           finish_share=, face=)
        lossy sheets (openEMS's model): rough copper (m RMS, Hammerstad's loss
        factor, a wideband fit), a surface finish (metal layers on the copper,
        on a share of the sheet's loss), and a sheet that is one face of thick
        copper (its roughness on that copper's skin depth)
    __version__
"""
__version__ = '0.3.0'

from .csx import ContinuousStructure  # noqa: E402,F401
from .fdtd import openEMS, LumpedPort  # noqa: E402,F401
from .ports import (MSLPort, StripLinePort, CPWPort,  # noqa: E402,F401
                    CoaxialPort, WaveguidePort, RectWGPort, CircWGPort,
                    CurvePort)
