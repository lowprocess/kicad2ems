"""
FDTD, shared by every solver on an FDTD engine (openEMS, gfdtd) and by the mesh preview: what the board becomes
on a rectilinear grid, and how runs on an engine with openEMS's Python interface go.

    boundaries   the six boundary conditions (auto: PEC under a solid bottom plane), the closed-box check
    mesh         the automatic rectilinear mesh (auto_mesh)
    dielectric   dielectric loss: a conductivity at the band centre, or wideband Debye poles
    model        the CSX model (build_fdtd), the excitation, port selection
    engines      engines by name ('openems', 'gfdtd:cupy') -> their ContinuousStructure / openEMS classes
    run          one run per excited port, end criteria (PortWatch), progress, RunResult
    progress     the progress / ETA line from an engine's status lines
    antenna      antenna-mode runs: air, excitation and the NF2FF box (the analysis: fields.antenna)
    readback     the model as an engine would get it, recorded (parity checks)
    plugin       FDTD, the solvers' common plugin class, and their shared settings
"""
