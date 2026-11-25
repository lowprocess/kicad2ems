# Manufacturers' S-parameter files

Put a part's own Touchstone file here under the name below and the
simulation uses it in place of its datasheet model (`python run.py budget`
prints, for every part, which one it used). Nothing else has to change.

| file here | part | where to get it |
|---|---|---|
| `LFCN-2250+.s2p` | Mini-Circuits LFCN-2250+ low-pass | the part's page on minicircuits.com, "S-Parameters" (a zip: take the +25 °C file) |
| `PMA3-83LN+.s2p` | Mini-Circuits PMA3-83LN+ LNA | the same; choose the 6 V file |
| `PE43711.s2p` | pSemi PE43711 step attenuator, 0 dB state | psemi.com, the part's page, "S-parameters" (behind a licence click-through) |
| `PE42540.s2p` | pSemi PE42540 switch, RFC to the selected port | the same |

These files are not in the repository, and the simulation was not built with
them: minicircuits.com refuses scripted downloads, and pSemi's models are
under a licence that has to be accepted by the person using them and that
forbids redistribution. `.gitignore` keeps what you put here out of git for
the same reason.

The SIM-83+ mixer has no S-parameter file that describes it as a mixer. Its
conversion loss, isolations, compression and spur levels are fields of
`sasim.rf.Mixer`: replace them with the datasheet's curves or your own bench
measurements.

Only |S21| is used, interpolated in dB and held past the file's first and
last frequency.
