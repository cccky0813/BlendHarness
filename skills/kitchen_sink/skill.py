import bpy
import bmesh
import math
from mathutils import Vector, Matrix


def build_kitchen_sink(location=(0.0, 0.0, 0.0), name='Sink',
                       width=0.60, depth=0.42, height=0.24,
                       wall_thickness=0.02, faucet_gap=0.03):
    '''Kitchen sink with a faucet, resting on location z.

    A rectangular steel basin (bottom centred on the location, rim at
    location.z + height) built from a floor slab plus four walls, and a faucet
    standing on the rim plane just behind the basin. Origin is at location.
    Returns [sink, faucet].
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

    def _box(bm, x0, x1, y0, y1, z0, z1):
        vs = bmesh.ops.create_cube(bm, size=1.0)['verts']
        for v in vs:
            v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
            v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
            v.co.z = z0 + (v.co.z + 0.5) * (z1 - z0)
        return vs

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

    def _finish(bm, obj_name):
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        me = bpy.data.meshes.new(obj_name)
        bm.to_mesh(me)
        bm.free()
        me.validate()
        ob = bpy.data.objects.new(obj_name, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(_metal())
        return ob

    t = wall_thickness
    x0, x1 = -width * 0.5, width * 0.5
    y0, y1 = -depth * 0.5, depth * 0.5
    top = height

    bm = bmesh.new()
    _box(bm, x0, x1, y0, y1, 0.0, t)
    _box(bm, x0, x1, y0, y0 + t, t, top)
    _box(bm, x0, x1, y1 - t, y1, t, top)
    _box(bm, x0, x0 + t, y0 + t, y1 - t, t, top)
    _box(bm, x1 - t, x1, y0 + t, y1 - t, t, top)
    bmesh.ops.translate(bm, vec=Vector(location), verts=bm.verts)
    sink = _finish(bm, name)

    fy = y0 - faucet_gap
    bm = bmesh.new()
    _cyl(bm, (0.0, fy, top), 0.045, 0.012, 'Z')
    _cyl(bm, (0.0, fy, top + 0.12), 0.024, 0.24, 'Z')
    _cyl(bm, (0.0, fy + 0.12, top + 0.23), 0.020, 0.24, 'Y')
    _cyl(bm, (0.0, fy + 0.23, top + 0.205), 0.018, 0.05, 'Z')
    _cyl(bm, (-0.06, fy, top + 0.23), 0.012, 0.09, 'X')
    bmesh.ops.translate(bm, vec=Vector(location), verts=bm.verts)
    faucet = _finish(bm, name + '_Faucet')
    return [sink, faucet]
