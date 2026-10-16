import bpy
import bmesh
import math
from mathutils import Matrix, Vector


def build_drawer_pedestal(location=(0.0, 0.0, 0.0), name="Pedestal",
                          width=0.37, depth=0.58, height=0.71,
                          drawers=3, drawer_front_h=0.19, drawer_gap=0.02,
                          plinth_height=0.06, plinth_inset=0.03,
                          front_thickness=0.02, front_margin=0.01,
                          handle_len=0.14, handle_radius=0.006,
                          bar_offset=0.03, post_len=0.025, post_radius=0.005,
                          post_spacing=0.13):
    """Drawer pedestal standing on location z: a carcass box on a recessed plinth,
    with `drawers` stacked drawer fronts on the -Y face, each front fitted with a
    horizontal bar handle supported by two posts. Returns the created objects."""

    ox, oy, oz = location

    def _obj(nm, bm, pos, mat):
        # bake the offset from `location` into the mesh, so the object origin is
        # at `location` and the mesh itself rests on z = 0 in local space.
        bmesh.ops.translate(bm,
                            vec=Vector((pos[0] - ox, pos[1] - oy, pos[2] - oz)),
                            verts=bm.verts)
        me = bpy.data.meshes.new(nm)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(nm, me)
        bpy.context.collection.objects.link(ob)
        ob.location = (ox, oy, oz)
        if mat is not None:
            ob.data.materials.append(mat)
        return ob

    def _box(nm, size, pos, mat):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=size, verts=bm.verts)
        return _obj(nm, bm, pos, mat)

    def _cyl(nm, r, length, pos, axis, mat, seg=20):
        bm = bmesh.new()
        rot = Matrix.Rotation(math.radians(90), 4, 'Y' if axis == 'X' else 'X')
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                              radius1=r, radius2=r, depth=length, matrix=rot)
        return _obj(nm, bm, pos, mat)

    def _mat(nm, rgb, metallic, rough):
        mat = bpy.data.materials.get(nm)
        if mat is None:
            mat = bpy.data.materials.new(nm)
            mat.use_nodes = True
            b = mat.node_tree.nodes["Principled BSDF"]
            b.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            b.inputs["Metallic"].default_value = metallic
            b.inputs["Roughness"].default_value = rough
            b.inputs["Specular IOR Level"].default_value = 0.35
        return mat

    body_mat = _mat("White_Paint", (0.93, 0.925, 0.905), 0.0, 0.35)
    metal_mat = _mat("Handle_Metal", (0.62, 0.63, 0.65), 1.0, 0.22)

    objs = []

    # carcass sits on top of the plinth
    body_h = height - plinth_height
    objs.append(_box(name + "_Unit", (width, depth, body_h),
                     (ox, oy, oz + plinth_height + body_h / 2.0), body_mat))
    # recessed plinth grounding the pedestal (bottom face at location z)
    objs.append(_box(name + "_Plinth",
                     (width - 2.0 * plinth_inset, depth - 2.0 * plinth_inset, plinth_height),
                     (ox, oy, oz + plinth_height / 2.0), body_mat))

    # stacked drawer fronts, centred vertically on the carcass
    front_w = width - 2.0 * front_margin
    block = drawers * drawer_front_h + (drawers - 1) * drawer_gap
    z0 = oz + plinth_height + (body_h - block) / 2.0 + drawer_front_h / 2.0
    front_y = oy - depth / 2.0 - 0.005
    bar_y = front_y - bar_offset
    post_y = bar_y + post_len / 2.0

    for i in range(drawers):
        zc = z0 + i * (drawer_front_h + drawer_gap)
        objs.append(_box("%s_Drawer_Front_%d" % (name, i + 1),
                         (front_w, front_thickness, drawer_front_h),
                         (ox, front_y, zc), body_mat))
        objs.append(_cyl("%s_Handle_Bar_%d" % (name, i + 1), handle_radius, handle_len,
                         (ox, bar_y, zc), 'X', metal_mat, seg=20))
        for j, sx in enumerate((-1.0, 1.0)):
            objs.append(_cyl("%s_Handle_Post_%d_%d" % (name, i + 1, j + 1),
                             post_radius, post_len,
                             (ox + sx * post_spacing / 2.0, post_y, zc),
                             'Y', metal_mat, seg=14))

    return objs
