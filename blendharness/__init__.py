"""BlendHarness: a self-evolving multi-agent harness for Blender.

Layers (each one may only import from the layers above it):

    core/       generic harness mechanism, knows nothing about Blender
                llm, runtime (agent loop), context_engine, tools (Tool type), tracing
    sandbox/    process-isolated headless Blender
    memory/     long-term memory (the skill library)
    tools/      domain tools the agents call (Blender scripts, scene, render, skills)
    agents/     agent configurations: prompts + tools + context policy + completion check
    workflow/   orchestration: build -> critique -> revise -> verify -> learn skills
"""

__version__ = "0.1.0"
