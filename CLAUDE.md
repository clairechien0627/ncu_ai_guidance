# Report Agent

RAG-first multi-agent research assistant for academic PDF analysis and student-facing research guidance.

## Tech Stack

* FastAPI
* LangGraph
* PostgreSQL
* Qdrant
* Azure OpenAI
* Hybrid RAG (dense + sparse + rerank + HyDE)

## Architecture

* orchestrator_agent owns user interaction
* specialist agents are tools, not separate assistants
* retrieval_agent handles focused document QA
* question_agent handles tutoring and question generation
* research_agent handles multi-step synthesis workflows

## Core Principles

* retrieval quality determines response quality
* evidence is more important than fluency
* never fabricate unsupported conclusions
* explicitly state uncertainty when evidence is insufficient

## Behavior Rules

* tasks requiring 3+ steps must start with planning
* do not modify code before reading existing implementations
* changes must remain minimally scoped
* unverified behavior is not considered complete
* retrieval should be iterative, not repetitive
* when context becomes insufficient, say so explicitly

## Retrieval Rules

* prefer targeted retrieval before broad synthesis
* avoid semantically identical repeated queries
* each retrieval iteration should refine scope or perspective
* escalate to research workflow only when retrieval is insufficient

## Development Rules

* inspect routing flow before modifying agent behavior
* inspect prompt stack interactions before changing prompts
* avoid hidden coupling between agents
* prefer explicit contracts and traceable reasoning

## Important Directories

* `agents/` → orchestration and specialist agents
* `agents/research/` → LangGraph research workflow
* `tools/` → RAG tools and retrieval logic
* `services/` → extraction and background pipelines
* `prompts/` → modular runtime prompt stacks
* `prompting/` → prompt registry and loading system
