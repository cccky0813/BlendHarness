import bpy
import bmesh


def build_low_poly_tree(location=(0.0, 0.0, 0.0), name='Tree',
                        trunk_radius=0.18, trunk_height=1.8, trunk_verts=6,
                        foliage_radius=0.9, foliage_height=1.4,
                        layers=3, cone_verts=6):
    'Stylized tree; the trunk bottom sits exactly at location z.'
    lx, ly, lz = location
    objs = []

    def get_mat(mname, color, roughness=0.9):
        if mname in bpy.data.materials:
            return bpy.data.materials[mname]
        m = bpy.data.materials.new(mname)
        m.use_nodes = True
        b = m.node_tree.nodes['Principled BSDF']
        b.inputs['Base Color'].default_value = (color[0], color[1], color[2], 1.0)
        b.inputs['Roughness'].default_value = roughness
        return m

    def cone(oname, center, r1, r2, depth, n, material):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=n,
                              radius1=r1, radius2=r2, depth=depth)
        for v in bm.verts:
            v.co.x += center[0]
            v.co.y += center[1]
            v.co.z += center[2]
        me = bpy.data.meshes.new(oname)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(oname, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(material)
        objs.append(ob)
        return ob

    m_trunk = get_mat('Trunk', (0.38, 0.22, 0.08))
    m_foliage = get_mat('Foliage', (0.15, 0.48, 0.10))

    cone(name + '_Trunk', (lx, ly, lz + trunk_height * 0.5),
         trunk_radius, trunk_radius, trunk_height, trunk_verts, m_trunk)

    steps = layers - 1 if layers > 1 else 1
    for j in range(layers):
        t = j / float(steps)
        fr = foliage_radius * (1.0 - 0.5 * t)
        fh = foliage_height * (1.0 - 0.36 * t)
        fz = trunk_height * (0.833 + 0.722 * t)
        cone(name + '_Foliage_' + str(j), (lx, ly, lz + fz),
             fr, 0.0, fh, cone_verts, m_foliage)

    return objs
