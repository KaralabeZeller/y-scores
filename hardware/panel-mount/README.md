# Quick-print panel mount

The separate [Pi 5 corner holder](Pi5Holder/README.md) uses two adjoining-edge
M3 points and four raised M2.5 mounting points for the Raspberry Pi 5.

First-fit prototype for three Waveshare RGB-Matrix-P2.5-64x64 panels in one
straight row. This is a tabletop support, with no electronics enclosure.

## Parts and hardware

| Part | Quantity | Printed dimensions |
| --- | ---: | --- |
| Seam link | 4 | 28 x 12 x 2.4 mm |
| End foot | 2 | 100 x 48 x 14 mm in print orientation |
| Panel mounting screws | 10 | M3 x 6, subject to checking the supplied screws |

The two links at each seam use the existing side mounting points. Each foot
supports the bottom edge and attaches to the lower outer side mounting point.
No nuts or heat-set inserts are required. The rear stays open for cables.

## Dimension source and fit check

Source: [Waveshare mechanical drawing](https://github.com/waveshareteam/RGB-Matrix-Px-xx/blob/main/hardware/dimensions/RGB-Matrix-Pxx-64x64/RGB-Matrix-P2_5-64x64-2D.dwg),
linked from [Waveshare's resources](https://docs.waveshare.com/RGB-Matrix-Px-64x64/Resources-And-Documents).
The drawing labels the relevant mounting points M3 and calls out M3X6. Its
159.8 mm shell is arranged on a 160 mm tile pitch, leaving a nominal 0.2 mm
seam. The side mounting centres are 146 mm across and 90 mm apart vertically.
Relative to a nominal tile origin they are x=7/153 mm, y=35/125 mm.
The separate 145 mm pattern belongs to the shell/PCB attachments and is not
used by these mounting parts.

Print one link first. Verify that the two adjacent panel mounting points are
14 mm apart and the 3.4 mm clearance holes fit. Verify the actual supplied
screw size, hole depth and rear boss height. A 6 mm screw through the 2.4 mm
flange nominally projects 3.6 mm into the panel. It must clamp the plastic
without bottoming out. Do not substitute longer screws without measuring.
The drawing cannot establish compatibility with every production batch.

## Printing and assembly

Use the individual STL files, not the assembly STEP or reference panel bodies.
Both STL files are exported in millimetres and oriented with a broad face on
the print bed. Links print flat; feet print on their sides. The geometry is
designed without support structures, apart from the tiny bridge at the foot's
horizontal screw hole. A starting profile for a 0.4 mm nozzle is 0.24 mm layers,
3 walls, 4 top/bottom layers and 15–20% infill; inspect the slicer preview.
Printer, filament and profile have not been supplied, so no print-time estimate
or machine-specific G-code is provided.

Assemble with the panels unpowered and the LED faces protected from pressure.
Align the panel edges, loosely attach both links at each seam, then tighten
gently. Fit the feet to the outer lower side holes with the bottom panel edges
resting on the bases. Check that neither cables nor PCB components contact the
parts and that the assembled display stands securely before powering it.

The six parts total approximately 22.46 cm³ as solid CAD geometry. This is not
a sliced filament or time estimate. Physical fit, load, tipping and thermal
performance have not been tested; the design is an initial indoor tabletop
prototype, not a validated transport or outdoor mounting system.

## Fusion source and exports

`QuickMount/QuickMount.py` creates native sketch/extrude/cut features in a new
Fusion document. Add its folder through Scripts and Add-Ins → Script or add-in
from device, then run QuickMount. Set `OUTPUT` in the script to the desired local
export folder. The script exports a Fusion archive, assembly STEP, and two
individual STL files; reference panel envelopes are included only in the
assembly. Sketch geometry is fixed; edit the source dimensions and rerun to
regenerate, or edit the native Fusion features. These are positioned components,
not a constrained kinematic joint assembly.

`verify_mesh.py <export-folder>` checks STL dimensions, closed manifold edges,
and consistent triangle orientation. Both initial exports passed. Keep generated
CAD exports, screenshots, downloaded drawings and conversion outputs outside Git.
