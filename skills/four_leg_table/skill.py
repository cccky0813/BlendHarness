import bpy
import bmesh
from mathutils import Vector


def build_four_leg_table(location=(0.0, 0.0, 0.0), name="Table",
                         top_width=1.50, top_depth=0.72, top_thickness=0.04,
                         leg_height=0.71, leg_size=0.06,
                         leg_inset_x=0.05, leg_inset_y=0.03):
    """Rectangular wooden table or desk: a top slab resting on four square legs,
    the legs inset from the top corners. Bottom is at location z. Returns the objects."""

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

    def _oak():
        mat = bpy.data.materials.get("Oak_Wood")
        if mat is None:
            mat = bpy.data.materials.new("Oak_Wood")
            mat.use_nodes = True
            nt = mat.node_tree
            bsdf = nt.nodes["Principled BSDF"]
            bsdf.inputs["Roughness"].default_value = 0.45
            bsdf.inputs["Specular IOR Level"].default_value = 0.4
            tc = nt.nodes.new("ShaderNodeTexCoord")
            mp = nt.nodes.new("ShaderNodeMapping")
            mp.inputs["Scale"].default_value = (1.0, 6.0, 1.0)
            wave = nt.nodes.new("ShaderNodeTexWave")
            wave.wave_type = 'BANDS'
            wave.bands_direction = 'Y'
            wave.inputs["Scale"].default_value = 2.5
            wave.inputs["Distortion"].default_value = 9.0
            wave.inputs["Detail"].default_value = 2.0
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].color = (0.30, 0.155, 0.062, 1.0)
            ramp.color_ramp.elements[1].color = (0.505, 0.305, 0.135, 1.0)
            nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
            nt.links.new(mp.outputs["Vector"], wave.inputs["Vector"])
            nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
            nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        return mat

    oak = _oak()

    objs = [_box(name + "_Top", (top_width, top_depth, top_thickness),
                 (ox, oy, oz + leg_height + top_thickness / 2.0), oak)]

    lx = top_width / 2.0 - leg_inset_x
    ly = top_depth / 2.0 - leg_inset_y
    for i, (sx, sy) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]):
        objs.append(_box("%s_Leg_%d" % (name, i + 1),
                         (leg_size, leg_size, leg_height),
                         (ox + sx * lx, oy + sy * ly, oz + leg_height / 2.0), oak))

    return objs
