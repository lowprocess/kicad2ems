"""
kicad2ems.fdtd: the FDTD solvers of kicad2ems, openEMS and gfdtd, on one shared
FDTD layer: what the board becomes on a rectilinear grid, and how runs on an
engine with openEMS's Python interface go. kicad2ems.core.solvers lists the
solvers (openems, gfdtd); they reach the core only through
kicad2ems.core.api.solver.

    boundaries  the six boundary conditions (auto: PEC under a solid bottom
                plane), the closed-box check
    mesh        the automatic rectilinear mesh (auto_mesh)
    dielectric  dielectric loss: a conductivity at the band centre, or wideband
                Debye poles
    model       the CSX model (build_fdtd), the excitation, port selection
    engines     engines by name ('openems', 'gfdtd:gpu') -> their
                ContinuousStructure / openEMS classes
    run         one run per excited port, end criteria (PortWatch), progress,
                RunResult
    progress    the progress / ETA line from an engine's status lines
    antenna     antenna-mode runs: air, excitation and the NF2FF box (the
                analysis: kicad2ems's fields.antenna)
    readback    the model as an engine would get it, recorded (parity checks)
    solver      FDTD, the solvers' common class, and their shared settings
    openems     the solver 'openems' (alias fdtd) on openEMS's engine: its
                import check, openems_venv, --converge
    gfdtd       the solver 'gfdtd' on gfdtd's engine (GPU via Vulkan, its compiled
                CPU core, or numpy)
"""
__version__ = '0.3.0'
