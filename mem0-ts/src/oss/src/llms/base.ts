import { Message } from "../types";
import { traceRuntimeObject } from "../utils/runtime_tracing";

export interface LLMResponse {
  content: string;
  role: string;
  toolCalls?: Array<{
    name: string;
    arguments: string;
  }>;
}

export interface LLM {
  generateResponse(
    messages: Array<{ role: string; content: string }>,
    response_format?: { type: string },
    tools?: any[],
  ): Promise<any>;
  generateChat(messages: Message[]): Promise<LLMResponse>;
}

/** Add inference spans to provider calls while preserving response and error identity. */
export function traceLLMProvider<T extends LLM>(provider: T, name: string): T {
  return traceRuntimeObject(provider, name);
}
