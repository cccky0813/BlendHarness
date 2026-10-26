You are continuing a Blender scene that the user is building over several turns.
The current working script runs without errors and is shown at the end of your context: it IS the scene.

{context}

New request (turn {turn}):
{request}

Rules for this turn:
1. Change only what the new request asks for. Every other object keeps its position, size, shape and material.
2. Keep the names of existing objects. A renamed object counts as removed and re-added.
3. Give new objects meaningful names that say what they are (e.g. Well_Bucket, not Cylinder.004).
4. Prefer edit_script (all replacements in one call) over rewriting the whole script.
5. Check the result with inspect_scene: the requested change happened and nothing else moved.
6. Then call declare_changes with the exact names of the objects you added, modified and removed,
   and only then reply with a short summary.
