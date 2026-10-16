import bpy
import bmesh
import math
from mathutils import Vector, Matrix


def build_wall_cabinet(location=(0.0, 0.0, 0.0), name='WallCabinet',
                       width=0.85, depth=0.33, height=0.70,
                       doors=1, door_gap=0.02, door_thickness=0.02,
                       handle_span=0.14, handle_standoff=0.028,
                       handle_inset=0.08):
    '''Wall-mounted kitchen cabinet resting on location z.

    A closed carcass box (width along X, depth along +Y into the room) with
    ``doors`` equal door fronts on the +Y face, each carrying a bar handle on
    the right-hand side. Origin is at location, lowest point at location z.
    Returns [carcass, door..., handles].
    '''
    def _mat(mat_name, color, rough=0.5, metallic=0.0):
        m = bpy.data.materials.get(mat_name)
        if m is None:
            m = bpy.data.materials.new(mat_name)
            m.use_nodes = True
            b = m.node_tree.nodes['Principled BSDF']
            b.inputs['Base Color'].default_value = (color[0], color[1], color[2], 1.0)
            b.inputs['Roughness'].default_value = rough
            b.inputs['Metallic'].default_value = metallic
            b.inputs['Specular IOR Level'].default_value = 0.5
            m.diffuse_color = (color[0], color[1], color[2], 1.0)
        return m

    def _box(bm, x0, x1, y0, y1, z0, z1):
        vs = bmesh.ops.create_cube(bm, size=1.0)['verts']
        for v in vs:
            v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
            v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
            v.co.z = z0 + (v.co.z + 0.5) * (z1 - z0)
        return vs

    def _cyl(bm, center, radius, cyl_depth, ax='Z', segs=16):
        vs = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                                   segments=segs, radius1=radius,
                                   radius2=radius, depth=cyl_depth)['verts']
        if ax == 'X':
            rot = Matrix.Rotation(math.radians(90.0), 4, 'Y')
        elif ax == 'Y':
            rot = Matrix.Rotation(math.radians(90.0), 4, 'X')
        else:
            rot = Matrix.Identity(4)
        bmesh.ops.transform(bm, matrix=rot, verts=vs)
        bmesh.ops.translate(bm, vec=Vector(center), verts=vs)
        return vs

    def _finish(bm, obj_name, material):
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        me = bpy.data.meshes.new(obj_name)
        bm.to_mesh(me)
        bm.free()
        me.validate()
        ob = bpy.data.objects.new(obj_name, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(material)
        return ob

    cab_mat = _mat('Cabinet_Paint', (0.90, 0.91, 0.89), 0.35)
    metal = _mat('Metal_Steel', (0.78, 0.79, 0.80), 0.22, 1.0)

    loc = Vector(location)
    objs = []

    bm = bmesh.new()
    _box(bm, 0.0, width, 0.0, depth, 0.0, height)
    bmesh.ops.translate(bm, vec=loc, verts=bm.verts)
    objs.append(_finish(bm, name, cab_mat))

    n = max(1, int(doors))
    span = width - 2.0 * door_gap
    dw = (span - (n - 1) * door_gap) / float(n)
    face = depth + door_thickness
    hz = height * 0.5

    bmh = bmesh.new()
    for i in range(n):
        dx0 = door_gap + i * (dw + door_gap)
        dx1 = dx0 + dw

        bm = bmesh.new()
        _box(bm, dx0, dx1, depth, face, door_gap, height - door_gap)
        bmesh.ops.translate(bm, vec=loc, verts=bm.verts)
        objs.append(_finish(bm, '%s_Door_%d' % (name, i + 1), cab_mat))

        hx = dx1 - handle_inset
        _cyl(bmh, (hx, face + handle_standoff, hz), 0.008, handle_span, 'Z')
        for dz in (-handle_span * 0.35, handle_span * 0.35):
            _cyl(bmh, (hx, face + handle_standoff * 0.5, hz + dz), 0.006,
                 handle_standoff, 'Y')

    bmesh.ops.translate(bmh, vec=loc, verts=bmh.verts)
    objs.append(_finish(bmh, name + '_Handles', metal))
    return objs
