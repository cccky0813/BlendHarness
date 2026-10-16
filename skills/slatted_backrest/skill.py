import bpy
import bmesh


def build_slatted_backrest(location=(0.0, 0.0, 0.0), name="Backrest",
                           width=0.45, post_size=0.04, post_depth=0.04,
                           height=0.94, slat_count=3, slat_width=0.055,
                           slat_depth=0.028, slat_gap=0.05,
                           slat_bottom=0.50, slat_top=0.885,
                           wood_color=(0.44, 0.27, 0.145, 1.0), bevel=0.0025):
    """Two posts with evenly pitched vertical slats between them.

    The posts run from z=0 (the resting plane) up to height; slats sit slightly
    proud of the post front face, which points -Y. Joined into one object and
    moved so the bottom sits at location. Returns [joined backrest object].
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

    def _box(obname, sx, sy, sz, loc):
        mesh = bpy.data.meshes.new(obname)
        obj = bpy.data.objects.new(obname, mesh)
        bpy.context.collection.objects.link(obj)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co.x *= sx
            v.co.y *= sy
            v.co.z *= sz
        if bevel > 0.0:
            bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=bevel,
                            segments=2, profile=0.5, affect='EDGES',
                            clamp_overlap=True)
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(mat)
        obj.location = loc
        return obj

    post_x = width / 2.0 - post_size / 2.0
    parts = []
    for suffix, x in (("Left", -post_x), ("Right", post_x)):
        parts.append(_box(name + "_Post_" + suffix, post_size, post_depth,
                          height, (x, 0.0, height / 2.0)))

    pitch = slat_width + slat_gap
    slat_y = (slat_depth - post_depth) / 2.0 + 0.004
    for i in range(slat_count):
        x = (i - (slat_count - 1) / 2.0) * pitch
        parts.append(_box(name + "_Slat_" + str(i + 1), slat_width, slat_depth,
                          slat_top - slat_bottom,
                          (x, slat_y, (slat_top + slat_bottom) / 2.0)))

    bpy.ops.object.select_all(action='DESELECT')
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    back = bpy.context.view_layer.objects.active
    back.name = name
    back.data.name = name + "Mesh"
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    back.location = location
    return [back]
