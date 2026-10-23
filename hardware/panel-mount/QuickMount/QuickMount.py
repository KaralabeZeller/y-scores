"""Fusion script. Millimetre input; native Fusion geometry uses centimetres.

Mount positions derive from Waveshare RGB-Matrix-P2_5-64x64-2D.dwg.
159.8 mm shell on a 160 mm pitch: side M3 centres are 7 mm from
nominal tile edges and 35/125 mm high. Confirm against the actual batch.
"""
import adsk.core
import adsk.fusion
import json
import math
from pathlib import Path
import traceback

OUTPUT = Path.home() / 'y-scores-exports' / 'panel-mount'
THICKNESS = 2.4
HOLE = 3.4
PITCH = 160.0


def point(x, y, z=0):
    return adsk.core.Point3D.create(x / 10, y / 10, z / 10)


def value(mm):
    return adsk.core.ValueInput.createByString(f'{mm} mm')


def component(root, name):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ


def polygon(comp, name, vertices, thickness, offset=0, operation=None):
    plane = comp.xYConstructionPlane
    if offset:
        pi = comp.constructionPlanes.createInput()
        pi.setByOffset(plane, value(offset))
        plane = comp.constructionPlanes.add(pi)
    sketch = comp.sketches.add(plane)
    sketch.name = name
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        line = sketch.sketchCurves.sketchLines.addByTwoPoints(point(*a), point(*b))
        line.isFixed = True
    if operation is None:
        operation = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    extrude = comp.features.extrudeFeatures.addSimple(sketch.profiles.item(0), value(thickness), operation)
    extrude.name = name
    sketch.isVisible = False
    return extrude.bodies.item(0)


def holes(comp, name, centers, depth, plane=None):
    sketch = comp.sketches.add(plane or comp.xYConstructionPlane)
    sketch.name = name
    for xyz in centers:
        circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(
            sketch.modelToSketchSpace(point(*xyz)), HOLE / 20)
        circle.isFixed = True
    profiles = adsk.core.ObjectCollection.create()
    for profile in sketch.profiles:
        profiles.add(profile)
    cut = comp.features.extrudeFeatures.addSimple(profiles, value(depth), adsk.fusion.FeatureOperations.CutFeatureOperation)
    cut.name = name
    sketch.isVisible = False


def position(occ, x, y, z, foot=False):
    if occ.isGrounded:
        occ.isGrounded = False
    if occ.isGroundToParent:
        occ.isGroundToParent = False
    matrix = adsk.core.Matrix3D.create()
    if foot:
        # Print-space (depth,height,width) -> assembly (-width,height,depth).
        matrix.setWithCoordinateSystem(point(x,y,z), adsk.core.Vector3D.create(0,0,1),
            adsk.core.Vector3D.create(0,1,0), adsk.core.Vector3D.create(-1,0,0))
    else:
        matrix.translation = adsk.core.Vector3D.create(x/10,y/10,z/10)
    occ.transform2 = matrix


def run(context):
    app = adsk.core.Application.get()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        doc.name = 'Y-Scores quick-print panel mount - fit check'
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = design.rootComponent
        join = adsk.fusion.FeatureOperations.JoinFeatureOperation

        link = component(root, 'SEAM LINK - print 4 - 28x12x2.4 mm')
        link_body = polygon(link.component, 'Thin joining plate',
            [(1,0),(27,0),(28,1),(28,11),(27,12),(1,12),(0,11),(0,1)], THICKNESS)
        holes(link.component, 'Two M3 clearance holes - 14 mm pitch', [(7,6,0),(21,6,0)], THICKNESS)
        link_body.name = 'Seam link'

        foot = component(root, 'END FOOT - print 2 on broad side - 100x48x14 mm')
        foot_body = polygon(foot.component, 'Bottom cradle and thin rear screw flange',
            [(-40,-4),(60,-4),(60,0),(2.4,0),(2.4,44),(0,44),(0,0),(-40,0)],14)
        polygon(foot.component, 'Diagonal brace - open centre saves plastic',
            [(0,26),(27,-1),(34,-1),(0,33)],14,operation=join)
        holes(foot.component, 'M3 lower outer panel mounting hole',[(0,35,7)], THICKNESS,foot.component.yZConstructionPlane)
        foot_body.name = 'End foot - broad side on bed'

        # Export individual components before assembly transforms. The feet are
        # already oriented with a broad flat face on Z=0; no supports needed.
        exporter = design.exportManager
        for comp, filename in [(link.component,'seam-link-print-4'),(foot.component,'end-foot-print-2')]:
            options = exporter.createSTLExportOptions(comp,str(OUTPUT / (filename+'.stl')))
            options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
            options.sendToPrintUtility = False
            if not exporter.execute(options):
                raise RuntimeError('STL export failed: '+filename)

        position(link,146,29,0)
        for x,y in [(146,119),(306,29),(306,119)]:
            copy = root.occurrences.addExistingComponent(link.component,adsk.core.Matrix3D.create())
            position(copy,x,y,0)
        position(foot,14,0,0,True)
        other = root.occurrences.addExistingComponent(foot.component,adsk.core.Matrix3D.create())
        position(other,480,0,0,True)

        # Simplified panel envelopes, not manufacturing models of the electronics.
        for i in range(3):
            panel = component(root,f'REFERENCE ONLY - panel {i+1} - hide to inspect mounts')
            body = polygon(panel.component,'159.8 mm shell envelope',
                [(0.1,0.1),(159.9,0.1),(159.9,159.9),(0.1,159.9)],-14.5)
            body.opacity = 0.25
            position(panel,i*PITCH,0,0)
        # Creating later timeline features can restore uncaptured positions.
        # Set every occurrence after geometry creation, then capture once.
        for occ in list(root.occurrences):
            instance = int(occ.name.rsplit(':',1)[1])
            if occ.component.name.startswith('SEAM LINK'):
                x,y = [(146,29),(146,119),(306,29),(306,119)][instance-1]
                position(occ,x,y,0)
            elif occ.component.name.startswith('END FOOT'):
                position(occ,14 if instance==1 else 480,0,0,True)
            else:
                number = int(occ.component.name.split('panel ')[1].split(' ')[0])
                position(occ,(number-1)*160,0,0)
        design.snapshots.add()
        camera = app.activeViewport.camera
        camera.eye = point(590,310,500)
        camera.target = point(240,65,0)
        camera.upVector = adsk.core.Vector3D.create(0,1,0)
        camera.isFitView = True
        app.activeViewport.camera = camera
        app.activeViewport.fit()
        adsk.doEvents()
        if not exporter.execute(exporter.createFusionArchiveExportOptions(str(OUTPUT/'Y-Scores-quick-mount.f3d'))):
            raise RuntimeError('Fusion archive export failed')
        exporter.execute(exporter.createSTEPExportOptions(str(OUTPUT/'Y-Scores-quick-mount.step')))
        report = {'status':'created','fit_status':'Verify real panel mounting points and M3x6 screw engagement before full print',
            'link_quantity':4,'foot_quantity':2,
            'link_volume_cm3':link_body.volume,'foot_volume_cm3':foot_body.volume,
            'total_volume_cm3':4*link_body.volume+2*foot_body.volume,
            'panel_pitch_mm':160,'mount_side_inset_mm':7,'mount_heights_mm':[35,125],
            'clearance_hole_mm':HOLE,'screw_flange_mm':THICKNESS,
            'body_counts':{'link':link.component.bRepBodies.count,'foot':foot.component.bRepBodies.count},
            'occurrences':[{'name':o.name,'transform':o.transform2.asArray()} for o in root.occurrences]}
        (OUTPUT/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    except Exception:
        (OUTPUT/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        app.userInterface.messageBox(traceback.format_exc())
