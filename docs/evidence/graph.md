# Grafo do turno

Gerado por `build_graph(...).get_graph().draw_mermaid()` (Task 22). `llm` -> `tools` repete até `max_rounds` (4; 2 sob custo); `guard` volta a `llm` 1x (retry do guard).

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	llm(llm)
	tools(tools)
	guard(guard)
	__end__([<p>__end__</p>]):::last
	__start__ --> llm;
	guard -.-> __end__;
	guard -.-> llm;
	llm -.-> __end__;
	llm -.-> guard;
	llm -.-> tools;
	tools --> llm;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
