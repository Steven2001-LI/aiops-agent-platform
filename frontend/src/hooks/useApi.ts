import { useState, useCallback, useRef, useMemo } from 'react';

const API_BASE = '/api/v1';

interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

interface UseApiReturn<T> extends ApiState<T> {
  get: (url: string) => Promise<T | null>;
  post: (url: string, body: Record<string, unknown>) => Promise<T | null>;
}

/**
 * Generic API hook — returns stable get/post functions and reactive data/loading/error state.
 *
 * Key design: get/post are stabilized via useRef so they never cause downstream
 * useCallback/useEffect churn. Only data/loading/error trigger re-renders.
 */
export function useApi<T = unknown>(): UseApiReturn<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // Keep request ID to avoid stale responses
  const reqIdRef = useRef(0);

  // Stable fetch core — never changes, so downstream deps are safe
  const fetchRef = useRef(async (url: string, options: RequestInit = {}): Promise<T | null> => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE}${url}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error ${response.status}: ${errorText}`);
      }

      const result = (await response.json()) as T;

      // Only update state if this is still the latest request
      if (reqId === reqIdRef.current) {
        setData(result);
      }
      return result;
    } catch (err) {
      const errorObj = err instanceof Error ? err : new Error('Unknown error');
      if (reqId === reqIdRef.current) {
        setError(errorObj);
      }
      console.warn('[API]', errorObj.message);
      return null;
    } finally {
      if (reqId === reqIdRef.current) {
        setLoading(false);
      }
    }
  });

  // Stable function references — memoized once, never change
  const get = useCallback(
    (url: string) => fetchRef.current(url, { method: 'GET' }),
    [] // ✅ empty deps = stable forever
  );

  const post = useCallback(
    (url: string, body: Record<string, unknown>) =>
      fetchRef.current(url, { method: 'POST', body: JSON.stringify(body) }),
    [] // ✅ empty deps = stable forever
  );

  // Return stable get/post + reactive state
  return useMemo(() => ({ data, loading, error, get, post }), [data, loading, error, get, post]);
}

// ============================================================
// Convenience hooks — stable callback references
// ============================================================

export function useIncidents() {
  const api = useApi<{
    items: Array<Record<string, unknown>>;
    total: number;
  }>();

  // Extract stable get/post from api
  const { get, post, data, loading, error } = api;

  const listIncidents = useCallback(
    (params?: { severity?: string; status?: string; page?: number; limit?: number }) => {
      const queryParams = new URLSearchParams();
      if (params?.severity) queryParams.append('severity', params.severity);
      if (params?.status) queryParams.append('status', params.status);
      if (params?.page) queryParams.append('page', String(params.page));
      if (params?.limit) queryParams.append('limit', String(params.limit));
      return get(`/incidents?${queryParams.toString()}`);
    },
    [get] // ✅ get is stable
  );

  const getIncident = useCallback(
    (id: string) => get(`/incidents/${id}`),
    [get]
  );

  const triggerIncident = useCallback(
    (body: { service: string; metric: string; severity: string; value?: number; threshold?: number }) =>
      post('/incidents/trigger', {
        ...body,
        value: body.value ?? 95,
        threshold: body.threshold ?? 80,
        operator: '>',
        source: 'frontend',
        labels: { tier: 'critical' },
        annotations: {},
      }),
    [post] // ✅ post is stable
  );

  // 人工审批恢复接口:批准补跑模拟收尾(→resolved),拒绝转人工升级(→escalated)
  const approveIncident = useCallback(
    (id: string, approver = 'admin', comment = '') =>
      post(`/incidents/${id}/approve`, { approver, comment }),
    [post]
  );

  const rejectIncident = useCallback(
    (id: string, approver = 'admin', comment = '') =>
      post(`/incidents/${id}/reject`, { approver, comment }),
    [post]
  );

  return {
    data,
    loading,
    error,
    listIncidents,
    getIncident,
    triggerIncident,
    approveIncident,
    rejectIncident,
  };
}

export function useAgents() {
  const { get, data, loading, error } = useApi<{
    items: Array<Record<string, unknown>>;
    total: number;
  }>();

  const listAgents = useCallback(() => get('/agents'), [get]);
  const getAgentStatus = useCallback((id: string) => get(`/agents/${id}/status`), [get]);

  return { data, loading, error, listAgents, getAgentStatus };
}

export function useEvaluations() {
  const { get, post, data, loading, error } = useApi<{
    items: Array<Record<string, unknown>>;
    total: number;
  }>();

  const listEvaluations = useCallback(() => get('/evaluations'), [get]);
  const getEvaluation = useCallback((id: string) => get(`/evaluations/${id}`), [get]);
  const runEvaluation = useCallback(
    (evalType: string) => post('/evaluations/run', { eval_type: evalType }),
    [post]
  );

  return { data, loading, error, listEvaluations, getEvaluation, runEvaluation };
}

export function useTopology() {
  const { get, data, loading, error } = useApi<{
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
  }>();

  const getTopology = useCallback(() => get('/topology'), [get]);

  return { data, loading, error, getTopology };
}

export function useMemory() {
  const { get, post, data, loading, error } = useApi<{
    results: Array<Record<string, unknown>>;
    total: number;
  }>();

  const searchMemory = useCallback(
    (query: string) => get(`/memory/search?query=${encodeURIComponent(query)}`),
    [get]
  );

  const storeMemory = useCallback(
    (content: string, memoryType: string = 'observation', tags: string[] = []) =>
      post('/memory/store', { content, memory_type: memoryType, tags }),
    [post]
  );

  return { data, loading, error, searchMemory, storeMemory };
}
