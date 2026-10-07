# AI call inventory for this downstream Mem0 fork

Read-only review using CodeGraph before text searches. Initial research ran on personal/main; implementation artifacts were added on feature/ai-call-evaluation. Untracked .codegraph was preserved. This inventory identifies executable boundaries and indirect dependencies; arbitrary user-supplied LangChain/custom providers and external hosted-service internals cannot be statically enumerated. No claim that live models were tested.

## Core call matrix

| Stage | Python source | TypeScript source | Evaluation required |
|---|---|---|---|
| Conversational extraction + ADD/UPDATE/DELETE decisions | [mem0/memory/main.py:1090](../../mem0/memory/main.py#L1090)–1110 sync, :2854 async | [mem0-ts/src/oss/src/memory/index.ts:924](../../mem0-ts/src/oss/src/memory/index.ts#L924) | JSON/schema success; factual precision/recall; attribution; action correctness; duplicates; contradiction handling; prompt injection; latency |
| Procedural summaries | main.py:2206, :4004 custom LangChain / :4007 default | No analogous production call located | useful retained procedures; empty/refusal; faithfulness; latency |
| Vision description | [mem0/memory/utils.py:182](../../mem0/memory/utils.py#L182); main.py:1011 / :2749 | oss/src/utils/memory.ts:4–8, invoked :38 | caption faithfulness; modality support; local routing; latency |
| Relationship extraction | [mem0/graphs/extractors.py:82](../../mem0/graphs/extractors.py#L82) | relationship modules use adapters; no matching direct LLM boundary located | validated relationships, direction, provenance, hallucination rate |
| Dense embeddings | main.py:1051,1074,1146,1153,1291,1811,1956,2053,2156,2222,2263; async equivalent :2798 onward | index.ts:271 dimension probe; :459,503,889,983,991,1170,1176,1428,1493,1690,1929,1977 | dimension/finite vectors; retrieval recall/MRR/nDCG; batch efficiency; cold/warm latency |
| Optional LLM reranking | [mem0/reranker/llm_reranker.py:142](../../mem0/reranker/llm_reranker.py#L142) | oss/src/rerankers/llm.ts | per-document scoring accuracy, calibrated score parsing, N calls/search, latency |
| Other reranking | cohere_reranker.py:66; zero_entropy_reranker.py:71; sentence_transformer_reranker.py:83; huggingface_reranker.py | rerankers/cohere.ts:62; zeroentropy.ts:58; cross_encoder.ts | local model or remote API quality; fallback events; ranking and latency |
| Sparse retrieval encoding | vector_stores/qdrant.py:117,:215 (fastembed BM25); pinecone.py:148,:254,:280 | vector store implementation dependent | Separate retrieval CPU stage, not generative LLM; measure indexing/query costs |
| Server instruction generation | [server/main.py:410](../../server/main.py#L410) | — | instruction correctness, routing, latency |

Python uses a **single additive extraction call** that also proposes memory events. TypeScript uses a single ADD-only extraction call with no event field in its prompt/schema ([prompt/schema](../../mem0-ts/src/oss/src/prompts/index.ts#L730)); Python UPDATE/DELETE/NONE fixtures cannot serve as TypeScript action-contract tests. Do not import an upstream two-LLM-call assumption. main.py:1074 retrieves existing candidates, :1090 builds additive prompt, :1105 asks JSON, :1170 onward executes validated events. infer=False skips extraction but still embeds; vision preprocessing can still call an LLM. Graph mode adds separate relationship calls; graph runtime.py:76–82 builds its extractor over memory.llm, with calls triggered in outbox.py:513, lifecycle.py:146, service.py:107. Graph entity embedding and maintenance introduce extra embedding calls.

Important failure distinctions: provider extraction errors raise LLMError (main.py:1112); invalid extraction JSON is logged and converted into zero extracted memories (:1133). An evaluation counting HTTP success or returned [] alone would incorrectly treat malformed JSON as legitimate no-memory output. Reranker failures often return original ordering or neutral scores (llm_reranker.py:159; cohere:85; zero_entropy:96; sentence_transformer:112; huggingface:165). Capture fallback explicitly.

## Providers and local Qwen

Actual Python registry ([mem0/utils/factory.py:43](../../mem0/utils/factory.py#L43)–60): ollama, openai, groq, together, aws_bedrock, litellm, azure_openai, openai_structured, anthropic, azure_openai_structured, gemini, deepseek, minimax, xai, sarvam, lmstudio, vllm, langchain. This is 18 registered providers; AGENTS documentation counts are not current registry evidence.

Qwen has explicit vLLM defaults in both languages: Python llms/vllm.py:37 and TS llms/vllm.ts:5 default Qwen/Qwen2.5-32B-Instruct. Python config accepts vllm_base_url (configs/llms/vllm.py:57, default localhost:8000/v1), Ollama accepts ollama_base_url (configs/llms/ollama.py:56), and model is configurable. TS uses baseURL normalization (oss/tests/config-manager.test.ts:158). Existing integration support proves routing/adapter availability, not that any Qwen version, quantization, prompt or serving configuration is effective.

Python embeddings registry (factory.py:154–164): openai, ollama, huggingface, azure_openai, gemini, vertexai, together, lmstudio, langchain, aws_bedrock, fastembed. HuggingFace can invoke local SentenceTransformer.encode or remote OpenAI-compatible embeddings. Model downloads are additional network activity. Keep extraction model and embedding model configurable independently; swapping embeddings requires consistent vector dimensions and fresh indexing.

**Hidden cloud routing hazard:** TS vision get_image_description creates a fresh OpenAILLM with OPENAI_API_KEY (oss/src/utils/memory.ts:4–8), independently of configured extraction provider. Thus a Qwen configuration does not imply all AI processing is local. Python vision uses configured self.llm when enabled. Reranker builds another LLM independently with defaults; configure its nested llm explicitly. Upstash enable_embeddings delegates embedding generation to vector service (Python factory.py:169 and vector_stores/upstash_vector.py:86,143), so instrumentation only at EmbedderBase is insufficient.

## Hosted calls, integrations, samples

Hosted client: [mem0/client/main.py:217](../../mem0/client/main.py#L217) posts /v3/memories/add/; search starts :297. It exposes hosted operation boundaries, not internal model spans. TypeScript client is same category. Measure client latency, request/result quality and status; hosted model choice/token internals cannot be asserted without platform telemetry.

Vercel integration is an additional genuine generation boundary: [integrations/vercel-ai-sdk/src/mem0-generic-language-model.ts:103](../../integrations/vercel-ai-sdk/src/mem0-generic-language-model.ts#L103) delegates model.doGenerate and :158 model.doStream after memory operations (:41). Capture both memory work and final answer generation with separate spans and parent request. Most native coding agent integrations use hosted or OSS memory backend and the host owns its answer-model generation, outside this repository.

CLI searches found no independent direct model generation boundary; backend memory commands inherit hosted/OSS calls. Server generally wraps OSS operations, plus its explicit generate_instructions endpoint at main.py:410.

Samples are separate workloads, not production SDK defaults: [examples/openai-inbuilt-tools/index.js:57](../../examples/openai-inbuilt-tools/index.js#L57) Responses API; [examples/yt-assistant-chrome/src/background.js:167](../../examples/yt-assistant-chrome/src/background.js#L167) direct OpenAI HTTP; [examples/multimodal-demo/src/hooks/useChat.ts:174](../../examples/multimodal-demo/src/hooks/useChat.ts#L174) and duplicate useChat.ts:174 chat completion; [examples/mem0-demo/app/api/chat/route.ts:76](../../examples/mem0-demo/app/api/chat/route.ts#L76) streamText; [examples/vercel-ai-sdk-chat-app/src/hooks/useChat.ts:126](../../examples/vercel-ai-sdk-chat-app/src/hooks/useChat.ts#L126) streamText; [examples/misc/movie_recommendation_grok3.py:55](../../examples/misc/movie_recommendation_grok3.py#L55); personalized_search.py:172 LangChain invoke. Notebook helper/mem0_teachability.py imports AutoGen TextAnalyzerAgent (extra model calls); multiagent/notebook frameworks make further indirect calls. Evaluation benchmark answer/judge calls should be categorized separately from application calls to avoid conflating cost and success.

## Existing checks and gaps

Provider unit tests test mocked SDK wiring ([tests/llms/test_ollama.py:10](../../tests/llms/test_ollama.py#L10) patches client; [tests/llms/test_vllm.py](../../tests/llms/test_vllm.py); TS src/oss/src/tests/vllm.test.ts). Memory tests cover malformed extraction and provider failure ([tests/memory/test_main.py:100](../../tests/memory/test_main.py#L100),:131,:345,:380). TS memory.crud.test.ts:26 uses content-based mock for additive pipeline. Rerankers have local mocked unit suites including failure logging. These are regression checks, not measured live-model effectiveness, sustained load, longitudinal retrieval quality, or confidence intervals.

Build evaluation at two levels: replay exact stage requests with fixed expected facts/actions/relationships/rankings; run multi-session trajectories using fresh isolated stores, delayed recall, correction/forgetting, scope isolation, graph-enabled retrieval, and downstream answers. Matrix axes: model+revision+quantization, serving runtime/version, prompts/hash, embedder, reranker, graph configuration, context size, concurrency, repetitions, cold/warm cache. Record raw semantic failure even if API returns successfully. Use no-LLM baselines for storage/retrieval overhead. Report stage and end-to-end p50/p95/p99, throughput, errors/timeouts/fallbacks, semantic metrics and uncertainty.

## Initial focused suite and limits

The JSONL fixtures replay provider requests, not full Memory operations. Scope labels document synthetic user contexts; they do not test database filter enforcement. Exact prompts are rendered from the current downstream prompt builder at run time. Fixed observation dates improve reproducibility. The runner records prompt hashes so changed prompts are distinguishable. Deterministic keyword/action checks allow paraphrases but are diagnostic probes, not calibrated factual precision/recall or long-term effectiveness scores. ADD/UPDATE/DELETE/NONE checks must inspect event and existing IDs, not just matching a fact somewhere in the output.

| Surface | Initial focused coverage | Still needed |
|---|---|---|
| Additive extraction | facts, greetings, deduplication, correction, retraction, attribution, temporal anchoring, multi-topic recall | broad annotated corpus, language/context-size matrix, repeated stochastic trials |
| Relationship extraction | explicit edge, empty relationship result | graph persistence, direction/provenance, retrieval benefit |
| Embeddings | shape, dimensions, finite values, batch vectors | semantic retrieval ranking, model migration, cold model downloads |
| Rerankers | known relevant document first | nDCG/MRR across annotated queries, fallback/error rate under load |
| Local model routing | configured provider request | vision routing, independent reranker configuration, service-side embeddings |
| Longitudinal memory | none in provider replay | isolated-store session trajectories, correction/forgetting, delayed recall, cross-user isolation |
| Hosted integration | no internal provider visibility | operation-level trace, end-to-end answer quality, async completion tracing |
| Stress | concurrency/repetitions in runner | separate sustained load/resource measurement, recovery, retry/queue saturation |

### Runtime contract divergence

Shared fixtures carry explicit `typescript` expectations and synthetic outputs. TypeScript ADD checks omit Python-only event fields; duplicate suppression expects an empty memory array. Mutable-fact UPDATE and explicit DELETE probes are skipped as unsupported, alongside the Python relationship LLM stage. Skips remain visible and never count as model successes. Scope labels alone never establish store isolation.
