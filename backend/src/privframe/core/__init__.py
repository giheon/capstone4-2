"""PrivFrame 2.0 core (ADR-0005): domain, graphs, agents and their tools.

core never imports privframe.harness or privframe.service; models, prompts and tool implementations
come in through profiles (configs/pipeline) and LangGraph Runtime context.
"""
