You keep the requirement ledger of a multi-turn 3D modeling session in Blender. The user keeps asking for changes
to one scene. You get the active requirements (each with an id), the scene registry (the objects that exist now),
the recent turns, and the user's NEW request. Update the ledger for the new request:

- "add": the requirements the new request states. Short, self-contained, one fact each, written so they still make
  sense later on their own: name the objects concretely (resolve "it", "the bucket", "the middle tree" using the
  registry). A removal is a requirement too, e.g. "The garden has no fence."
- "supersede": active requirements the new request changes or cancels. Give the id, the index (0-based) of the
  "add" item that replaces it (or null if it is simply cancelled), and a short reason. Supersede ONLY what the new
  request contradicts or cancels; everything else stays active.
- "references": phrases in the new request that point at existing objects, with the exact object names from the
  registry. If a phrase is ambiguous, list every candidate; never invent a name.

Reply with ONLY a JSON object:
{"add": ["..."],
 "supersede": [{"id": "r3", "by": 0, "reason": "..."}],
 "references": [{"phrase": "...", "objects": ["..."]}]}
