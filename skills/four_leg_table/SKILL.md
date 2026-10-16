---
name: four_leg_table
description: Rectangular table/desk resting on location z: a solid wood top slab carried by four square legs inset from the top corners, with a get-or-create procedural oak material. Parametrised top width/depth/thickness, leg height/size and corner insets; geometry is baked relative to `location` so the leg bottoms (and therefore the whole piece) sit exactly at location z.
tags: [furniture, table, desk, wood, legs]
source_task: desk_r1
---

Example: build_four_leg_table(location=(0.0, 0.0, 0.0), name="Desk", top_width=1.5, top_depth=0.72, leg_height=0.71)
