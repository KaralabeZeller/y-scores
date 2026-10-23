"""Native Fusion part: Pi 5 to two adjoining Waveshare panel M3 mounts.

Coordinates refer to a nominal 160 mm panel, viewed from its rear.
Holder uses the upper-left corner: M3 (7,125), (35,153).
Pi PCB lower-left (20,90); holes 58 x 49, 3.5 mm from its left/bottom.
"""
import adsk.core
import adsk.fusion
import json
import math
from pathlib import Path
import traceback

OUTPUT = Path.home() / 'y-scores-exports' / 'pi5-holder'
BASE = 2.4
STANDOFF = 6.0
PI_HOLES = [(23.5,93.5),(81.5,93.5),(23.5,142.5),(81.5,142.5)]
PANEL_HOLES = [(7,125),(35,153)]
NUT_AF = 5.3
NUT_DEPTH = 2.1


def pt(x,y,z=0):
    return adsk.core.Point3D.create(x/10,y/10,z/10)


def val(mm):
    return adsk.core.ValueInput.createByString(f'{mm} mm')


def sketch_at(comp, name, z=0):
    plane = comp.xYConstructionPlane
    if z:
        inp = comp.constructionPlanes.createInput()
        inp.setByOffset(plane,val(z))
        plane = comp.constructionPlanes.add(inp)
    sk = comp.sketches.add(plane)
    sk.name = name
    return sk


def outline(sk, points):
    for a,b in zip(points,points[1:]+points[:1]):
        sk.sketchCurves.sketchLines.addByTwoPoints(pt(*a),pt(*b)).isFixed=True


def extrude(comp, sk, depth, operation):
    profiles = adsk.core.ObjectCollection.create()
    for profile in sk.profiles:
        profiles.add(profile)
    feature=comp.features.extrudeFeatures.addSimple(profiles,val(depth),operation)
    feature.name=sk.name
    sk.isVisible=False
    return feature


def poly(comp,name,points,depth,operation,z=0):
    sk=sketch_at(comp,name,z)
    outline(sk,points)
    return extrude(comp,sk,depth,operation)


def circles(comp,name,centres,diameter,depth,operation,z=0):
    sk=sketch_at(comp,name,z)
    for x,y in centres:
        sk.sketchCurves.sketchCircles.addByCenterRadius(pt(x,y),diameter/20).isFixed=True
    return extrude(comp,sk,depth,operation)


def run(context):
    app=adsk.core.Application.get()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    try:
        doc=app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        doc.name='Y-Scores Pi 5 corner holder - fit prototype'
        design=adsk.fusion.Design.cast(app.activeProduct)
        design.designType=adsk.fusion.DesignTypes.ParametricDesignType
        comp=design.rootComponent
        new=adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        join=adsk.fusion.FeatureOperations.JoinFeatureOperation
        cut=adsk.fusion.FeatureOperations.CutFeatureOperation

        # A single open ring with 8 mm rails, plus two thin mounting ears.
        poly(comp,'Frame blank - 2.4 mm',[(19.5,89.5),(85.5,89.5),(85.5,146.5),(19.5,146.5)],BASE,new)
        poly(comp,'Open underside - 50 x 41 mm',[(27.5,97.5),(77.5,97.5),(77.5,138.5),(27.5,138.5)],BASE,cut)
        poly(comp,'Side M3 mounting ear',[(3,119),(26,119),(26,131),(3,131),(1,129),(1,121)],BASE,join)
        poly(comp,'Top M3 mounting ear',[(29,140),(41,140),(41,157),(39,159),(31,159),(29,157)],BASE,join)
        circles(comp,'Four 6 mm board standoffs',PI_HOLES,6,STANDOFF,join,BASE)
        circles(comp,'M3 panel screw clearance 3.4 mm',PANEL_HOLES,3.4,BASE,cut)

        # Open-bottom nut pockets; nut upper face seats at z=2.1 mm.
        # Cut pockets first, then circular holes above them to avoid splitting
        # the bottom profile into nested regions.
        sk=sketch_at(comp,'M2.5 captive nuts - 5.3 mm across flats')
        radius=NUT_AF/math.sqrt(3)
        for x,y in PI_HOLES:
            outline(sk,[(x+radius*math.cos(math.pi*i/3),y+radius*math.sin(math.pi*i/3)) for i in range(6)])
        extrude(comp,sk,NUT_DEPTH,cut)
        circles(comp,'M2.5 clearance 2.8 mm - 58 x 49 pattern',PI_HOLES,2.8,BASE+STANDOFF-NUT_DEPTH,cut,NUT_DEPTH)
        body=comp.bRepBodies.item(0)
        body.name='Pi 5 holder - print flat - insert four M2.5 nuts underneath'
        assert comp.bRepBodies.count==1 and body.isSolid
        camera=app.activeViewport.camera
        camera.eye=pt(160,-45,160)
        camera.target=pt(45,125,3)
        camera.upVector=adsk.core.Vector3D.create(0,0,1)
        camera.isFitView=True
        app.activeViewport.camera=camera
        app.activeViewport.fit()
        adsk.doEvents()

        exporter=design.exportManager
        options=exporter.createSTLExportOptions(body,str(OUTPUT/'Pi5-corner-holder.stl'))
        options.meshRefinement=adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
        options.sendToPrintUtility=False
        assert exporter.execute(options)
        assert exporter.execute(exporter.createSTEPExportOptions(str(OUTPUT/'Pi5-corner-holder.step')))
        assert exporter.execute(exporter.createFusionArchiveExportOptions(str(OUTPUT/'Pi5-corner-holder.f3d')))
        box=body.boundingBox
        report={'status':'created','fit_status':'Physical fit not yet tested',
            'solid_bodies':comp.bRepBodies.count,'volume_cm3':body.volume,
            'bounds_mm':[(box.maxPoint.x-box.minPoint.x)*10,(box.maxPoint.y-box.minPoint.y)*10,(box.maxPoint.z-box.minPoint.z)*10],
            'panel_holes_mm':PANEL_HOLES,'panel_delta_mm':[28,28],
            'panel_hole_diameter_mm':3.4,'pi_holes_mm':PI_HOLES,'pi_hole_diameter_mm':2.8,
            'pi_pattern_mm':[58,49],'base_mm':BASE,'standoff_above_frame_mm':STANDOFF,
            'nut_af_mm':NUT_AF,'nut_pocket_depth_mm':NUT_DEPTH,
            'nominal_fasteners':{'panel':'2 x M3x6','pi':'4 x M2.5x10 + 4 standard M2.5 nuts'},
            'pi_board_outline_mm':[20,90,85,56],
            'pi_source':'https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf'}
        (OUTPUT/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    except Exception:
        (OUTPUT/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        app.userInterface.messageBox(traceback.format_exc())
