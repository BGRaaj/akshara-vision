# Akshara Vision: Documentation Hub

Welcome to the official documentation hub for Akshara Vision. This document serves as a centralized index and architectural guide for setting up, configuring, and operating the system.

---

## 1. System Architecture & Workflow

Akshara Vision implements a structured, hybrid pipeline that coordinates local layout segmentation with advanced language model restoration.

```
+---------------------------------------------------------------------------------+
|                                1. INPUT STAGE                                   |
|              Scanned Page Images, Multi-page PDFs, or Archive Bundles           |
+---------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                         2. LOCAL GEOMETRY SEGMENTATION                          |
|    Heuristic / ML parser extracts layout bounding boxes (BBoxes) & page zones   |
+---------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                           3. READING-ORDER SORTING                              |
|   Reconstructs multi-column flows & page hierarchy, preventing mixed segments   |
+---------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                        4. HYBRID MODEL ROUTING LAYER                            |
|  Directs text blocks and page images to either:                                 |
|  - Local Offline LLMs (Ollama, LM Studio, llama.cpp)                            |
|  - Cloud Multimodal APIs (Gemini, OpenAI, Anthropic)                            |
+---------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                         5. BLOCK-GUIDED RESTORATION                             |
|  LLM processes segment-by-segment using structured [BLOCK x] context prompts    |
+---------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                       6. QUALITY ASSURANCE & ASSET CROPS                        |
|  Vision model verifies figure crops; CSV tables are validated and normalized    |
+---------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                              7. EXPORTERS LAYER                                 |
|            Assembles final deliverables: MD, DOCX, EPUB, and PDF                |
+---------------------------------------------------------------------------------+
```

---

## 2. Documentation Directory

Refer to the following sections for formal guides on each subsystem:

### Core Configuration
* **[Onboarding & Setup](docs/onboarding.md)**: Guided initialization (`akv i`), keyboard-first terminal controls, and system diagnosis (`akv doctor`).
* **[Supported Models & API Keys](docs/models.md)**: Configuration details for setting up local runtimes (Ollama, LM Studio, llama.cpp) and cloud provider base URLs/API keys.
* **[Profiles Manager](docs/profiles.md)**: TOML profiles specification for portable defaults, output destinations, and run locks.

### Document Pipeline
* **[Execution Workflows](docs/workflows.md)**: Staged processing, resumable checkpoints, batch input discovery, and visual overlays (`akv compare`).
* **[Document Intelligence](docs/document-intelligence.md)**: Bounding boxes, layout trees, reading order, and page segment classifications.
* **[Instruction Presets](docs/instructions.md)**: Customizing language model correction and translation rules safely.

### Interfaces & Formats
* **[Grounded Document Chat](docs/chat.md)**: Conducting semantic Q&A over run manifests and auditing sources with page-level visual rechecks.
* **[Inputs and Exporters](docs/inputs-outputs.md)**: Specifications for input file formats and output delivery configurations.
* **[Privacy & Security](docs/privacy.md)**: Local metadata storage, sandbox isolation, and API key environment safety.
