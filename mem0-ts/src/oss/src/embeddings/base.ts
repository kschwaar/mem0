import { traceRuntimeObject } from "../utils/runtime_tracing";

export interface Embedder {
  embed(
    text: string,
    memoryAction?: "add" | "update" | "search",
  ): Promise<number[]>;
  embedBatch(
    texts: string[],
    memoryAction?: "add" | "update" | "search",
  ): Promise<number[][]>;
}

/** Add inference spans to embedding calls while keeping vectors out of metadata. */
export function traceEmbedderProvider<T extends Embedder>(
  provider: T,
  name: string,
): T {
  return traceRuntimeObject(provider, name);
}
