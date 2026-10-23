# Pi 5 corner holder

One printable open frame attaches to the two M3 mounts on adjoining edges of
the upper-left outer panel corner, viewed from the rear. This keeps the mounts
away from the seam links and feet. The Pi mounting pattern is 58 x 49 mm,
from the [official Pi 5 drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf).
Panel coordinates come from the Waveshare drawing linked in the parent README.

| Feature | Dimension |
| --- | --- |
| Overall part | 84.5 x 69.5 x 8.4 mm |
| Frame / raised standoffs | 2.4 mm / 6 mm above frame |
| Panel holes | 2 x diameter 3.4 mm |
| Panel hole displacement | 28 mm horizontally and 28 mm vertically |
| Pi holes | 4 x diameter 2.8 mm for M2.5 screws |
| Captive nut pockets | 5.3 mm across flats, 2.1 mm deep |

The holes are clearance holes, not printed threads. Use four standard M2.5
nuts and nominally four M2.5 x 10 screws. Insert the nuts into the underside
before attaching the holder to the panel. With a nominal 1.6 mm PCB the screws
end approximately flush with the holder's underside; verify the actual stack
and ensure tips cannot contact the panel electronics. Standard nuts are intended,
not thicker locknuts. Tighten only enough to seat the PCB on the four standoffs.

Attach the holder using two existing M3 screws, nominally M3 x 6. The 2.4 mm
flange leaves 3.6 mm nominal engagement. Confirm the screws clamp without
bottoming out. On a nominal 160 mm panel, measured from its lower-left rear
corner, the M3 centres are (7,125) and (35,153) mm. The Pi board outline starts
at (20,90) mm and spans 85 x 56 mm. Orient its 58 mm hole spacing horizontally.
Check the real panel components, bonnet, connectors and cables for clearance
before tightening; the drawing does not establish those assembled clearances.

Print flat with the nut-pocket openings on the bed and standoffs upward.
Use millimetres in the slicer. Start with 0.2 mm layers and three walls with a
0.4 mm nozzle. No supports are intended; inspect the small bridges over the
nut pockets in the slicer. Solid CAD volume is 5.26 cm³. Print time depends on
the printer and profile and has not been measured.

`Pi5Holder.py` creates a new native Fusion document and exports F3D, STEP and
STL to its `OUTPUT` directory. Add this folder in Fusion Scripts and Add-Ins
and run Pi5Holder. Dimensions are editable in the script or native features;
sketch geometry is fixed rather than driven by user parameters.

Validation: Fusion reports one solid body. Run
`python hardware/panel-mount/verify_mesh.py <export-folder> --pi5` to check the
STL's dimensions, closed manifold edges and consistent winding. Physical fit,
thermal clearance and strength remain untested: this is a first-fit prototype.
