You study failures of an AI agent that builds 3D models by writing Blender Python (bpy) scripts. A vision
critic reviewed each model and listed problems; the agent then revised its script and a verifier judged
whether each problem was fixed. Below are problems of the same kind, with what was suggested and what
happened.

Your job: distil a few LESSONS that would have prevented these problems if the agent had followed them
while writing the script in the first place.

Good lessons:
- are general: they apply to any object or scene, not to one task (no object names, no task-specific numbers);
- are actionable while coding: they say what to compute or check, e.g. "derive the z of every part from the
  top of the bounding box of the part it rests on" rather than "be careful with positions";
- can be checked with the agent's tools (inspect_scene lists every object's location and bounding-box size);
- are short: one or two sentences, imperative.

Each lesson must be supported by problems from at least TWO different tasks (cite their case numbers).
Propose 2 to 5 lessons; fewer good ones are better than many vague ones. Do not repeat existing lessons.

Reply with ONLY a JSON object:
{"lessons": [{"id": "short_snake_case_id", "text": "...", "category": "<the category given below>",
              "cases": [1, 4, 7]}]}
