import bpy
import bmesh


def build_wooden_dining_chair(location=(0.0, 0.0, 0.0), name="DiningChair",
                              seat_width=0.45, seat_depth=0.45, seat_height=0.45,
                              seat_thickness=0.035, leg_size=0.04,
                              back_height=0.94, slat_count=3, slat_width=0.055,
                              slat_depth=0.028, slat_gap=0.05, slat_bottom=0.50,
                              slat_top=0.885, stretcher_size=0.03,
                              stretcher_height=0.13,
                              wood_color=(0.44, 0.27, 0.145, 1.0), bevel=0.0025):
    """Wooden dining chair: 4 legs, square seat, slatted back, side stretchers.

    Built around the origin with the floor at z=0 and the front facing -Y, then
    moved so the lowest point sits at location. Returns [joined chair object].
    """
    mat = bpy.data.materials.get("NaturalWood")
    if mat is None:
        mat = bpy.data.materials.new("NaturalWood")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = tuple(wood_color)
        bsdf.inputs["Roughness"].default_value = 0.55
        bsdf.inputs["Metallic"].default_value = 0.0
        bsdf.inputs["Specular IOR Level"].default_value = 0.3
        bsdf.inputs["Sheen Weight"].default_value = 0.1

    def _box(obname, sx, sy, sz, loc, bev=bevel):
        mesh = bpy.data.meshes.new(obname)
        obj = bpy.data.objects.new(obname, mesh)
        bpy.context.collection.objects.link(obj)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co.x *= sx
            v.co.y *= sy
            v.co.z *= sz
        if bev > 0.0:
            bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=bev,
                            segments=2, profile=0.5, affect='EDGES',
                            clamp_overlap=True)
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(mat)
        obj.location = loc
        return obj

    seat_bot = seat_height - seat_thickness
    leg_x = seat_width / 2.0 - leg_size / 2.0
    leg_y = seat_depth / 2.0 - leg_size / 2.0
    front_y = -leg_y
    back_y = leg_y

    parts = []
    parts.append(_box(name + "_Seat", seat_width, seat_depth, seat_thickness,
                      (0.0, 0.0, (seat_height + seat_bot) / 2.0),
                      bev=bevel * 1.6))

    for suffix, x in (("FrontLeft", -leg_x), ("FrontRight", leg_x)):
        parts.append(_box(name + "_Leg_" + suffix, leg_size, leg_size, seat_bot,
                          (x, front_y, seat_bot / 2.0)))

    for suffix, x in (("Left", -leg_x), ("Right", leg_x)):
        parts.append(_box(name + "_BackPost_" + suffix, leg_size, leg_size,
                          back_height, (x, back_y, back_height / 2.0)))

    pitch = slat_width + slat_gap
    slat_y = back_y + (slat_depth - leg_size) / 2.0 + 0.004
    for i in range(slat_count):
        x = (i - (slat_count - 1) / 2.0) * pitch
        parts.append(_box(name + "_BackSlat_" + str(i + 1), slat_width, slat_depth,
                          slat_top - slat_bottom,
                          (x, slat_y, (slat_top + slat_bottom) / 2.0)))

    for suffix, x in (("Left", -leg_x), ("Right", leg_x)):
        parts.append(_box(name + "_Stretcher_" + suffix, stretcher_size,
                          seat_depth - leg_size, stretcher_size,
                          (x, 0.0, stretcher_height)))

    bpy.ops.object.select_all(action='DESELECT')
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    chair = bpy.context.view_layer.objects.active
    chair.name = name
    chair.data.name = name + "Mesh"
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    chair.location = location
    return [chair]
