Revise an existing Blender model.

Original request:
{request}

Current working script (it runs without errors):
```python
{script}
```

A visual review of renders of this model found these problems:
{issues}

Fix these problems, keeping everything that is already good: make ALL the fixes in ONE edit_script call
(a list of exact old_string -> new_string replacements). Then check with inspect_scene, and only then reply
with a short summary.