export interface BrowserTracingConfig {
  enabled: boolean;
  endpoint: string;
}

export function configureBrowserTracing(config?: Partial<BrowserTracingConfig>): BrowserTracingConfig;
export function traceBrowserInference<T>(
  metadata: { provider: string; model: string; operation: string },
  operation: () => Promise<T>,
): Promise<T>;
