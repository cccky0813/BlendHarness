import bpy
import bmesh
import math
from mathutils import Vector, Matrix


def build_bar_handle(location=(0.0, 0.0, 0.0), name='BarHandle',
                     span=0.14, bar_radius=0.008, standoff=0.028,
                     stub_radius=0.006, axis='Y'):
    '''Bar pull handle standing on location z.

    A vertical cylindrical bar held ``standoff`` off its mounting door face by
    two short posts. ``axis`` is the direction the bar protrudes from the door
    ('Y' or 'X'); the mounting face lies in the plane through the origin. The
    lowest point of the bar sits exactly at location z. Returns [handle].
    '''
    def _metal():
        m = bpy.data.materials.get('Metal_Steel')
        if m is None:
            m = bpy.data.materials.new('Metal_Steel')
            m.use_nodes = True
            b = m.node_tree.nodes['Principled BSDF']
            b.inputs['Base Color'].default_value = (0.78, 0.79, 0.80, 1.0)
            b.inputs['Roughness'].default_value = 0.22
            b.inputs['Metallic'].default_value = 1.0
            b.inputs['Specular IOR Level'].default_value = 0.5
            m.diffuse_color = (0.78, 0.79, 0.80, 1.0)
        return m

    def _cyl(bm, center, radius, depth, ax='Z', segs=16):
        vs = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                                   segments=segs, radius1=radius,
                                   radius2=radius, depth=depth)['verts']
        if ax == 'X':
            rot = Matrix.Rotation(math.radians(90.0), 4, 'Y')
        elif ax == 'Y':
            rot = Matrix.Rotation(math.radians(90.0), 4, 'X')
        else:
            rot = Matrix.Identity(4)
        bmesh.ops.transform(bm, matrix=rot, verts=vs)
        bmesh.ops.translate(bm, vec=Vector(center), verts=vs)
        return vs

    bm = bmesh.new()
    _cyl(bm, (0.0, standoff, 0.0), bar_radius, span, 'Z')
    for dz in (-span * 0.35, span * 0.35):
        _cyl(bm, (0.0, standoff * 0.5, dz), stub_radius, standoff, 'Y')

    rot = Matrix.Identity(4)
    if str(axis).upper() == 'X':
        rot = Matrix.Rotation(math.radians(-90.0), 4, 'Z')
    mat = (Matrix.Translation(Vector(location)) @ rot
           @ Matrix.Translation(Vector((0.0, 0.0, span * 0.5))))
    bmesh.ops.transform(bm, matrix=mat, verts=bm.verts)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.validate()
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    ob.data.materials.append(_metal())
    return [ob]
