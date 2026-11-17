import { goto } from '$app/navigation';

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail: any = null) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'offbook' };
  // a FormData body (file upload) must set its own multipart Content-Type, boundary included
  const form = body instanceof FormData;
  if (body !== undefined && !form) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : form ? body : JSON.stringify(body) });
  if (r.status === 401 && !path.startsWith('/api/login')) {
    const next = location.pathname + location.search;
    await goto(`/login?next=${encodeURIComponent(next)}`);
    throw new ApiError(401, 'Not logged in');
  }
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) {
    const detail = data?.detail;
    if (r.status === 409 && detail?.code === 'no_profile') await goto('/who');
    const msg = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ')
      : detail?.message ?? r.statusText;
    throw new ApiError(r.status, msg || `HTTP ${r.status}`, detail);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body: unknown = {}) => request<T>('POST', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, form),
  del: <T>(path: string) => request<T>('DELETE', path)
};

export function safeNext(path: string | null): string {
  return path && path.startsWith('/') && !['/', '\\'].includes(path[1] ?? '') ? path : '/';
}
