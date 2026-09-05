/**
 * Typed API client.
 *
 * Every call parses its response through a Zod schema. A shape the backend
 * did not promise fails here, with the field named, rather than three
 * components later as `Cannot read properties of undefined`.
 */
import { z } from "zod";
import {
  agentStatusSchema,
  documentCheckSchema,
  notificationsSchema,
  healthSchema,
  propertySchema,
  renovationSchema,
  scopesResponseSchema,
  searchResponseSchema,
  stagingSchema,
  sunlightSchema,
  valuationSchema,
} from "@/types";
import type {
  AgentStatus,
  DocumentCheck,
  Health,
  Property,
  Renovation,
  SearchResponse,
  Staging,
  Sunlight,
  Valuation,
} from "@/types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    // A dead backend is the single most common failure in local development,
    // and "Failed to fetch" tells the user nothing they can act on.
    throw new ApiError(
      "Could not reach the API. Is the backend running on :8732?",
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail === "string"
      ) {
        detail = (body as { detail: string }).detail;
      }
    } catch {
      /* response had no JSON body; the status line is all we have */
    }
    throw new ApiError(detail, response.status);
  }

  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    throw new ApiError(
      `Unexpected response from ${path}: ${first?.path.join(".") ?? "?"} ${
        first?.message ?? "did not match"
      }`,
      response.status,
    );
  }
  return parsed.data;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: (): Promise<Health> => request("/api/v1/health", healthSchema),

  search: (prompt: string, limit = 6): Promise<SearchResponse> =>
    request(
      "/api/v1/search",
      searchResponseSchema,
      json({ user_prompt: prompt, limit }),
    ),

  properties: (limit = 50): Promise<{ count: number; properties: Property[] }> =>
    request(
      `/api/v1/properties?limit=${limit}`,
      propertySchema
        .array()
        .transform((properties) => ({ count: properties.length, properties }))
        .or(
          // The endpoint returns {count, properties}; accept it directly too.
          searchResponseSchema.pick({ count: true }).extend({
            properties: propertySchema.array(),
          }),
        ),
    ),

  valuation: (propertyId: number): Promise<Valuation> =>
    request(`/api/v1/properties/${propertyId}/valuation`, valuationSchema),

  sunlight: (facing: string): Promise<Sunlight> =>
    request(`/api/v1/sunlight?facing=${encodeURIComponent(facing)}`, sunlightSchema),

  renovationScopes: () =>
    request("/api/v1/renovation/scopes", scopesResponseSchema),

  renovationEstimate: (
    scopes: readonly string[],
    areaSqft: number,
    grade: string,
  ): Promise<Renovation> => {
    const query = scopes
      .map((s) => `scopes=${encodeURIComponent(s)}`)
      .join("&");
    return request(
      `/api/v1/renovation/estimate?${query}&area_sqft=${areaSqft}&grade=${encodeURIComponent(grade)}`,
      renovationSchema,
      { method: "POST" },
    );
  },

  stage: (
    room: string,
    style: string,
    areaSqft: number | null,
    file: File | null,
  ): Promise<Staging> => {
    const form = new FormData();
    form.set("room", room);
    form.set("style", style);
    if (areaSqft !== null) form.set("area_sqft", String(areaSqft));
    if (file) form.set("file", file);
    return request("/api/v1/renovation/stage", stagingSchema, {
      method: "POST",
      body: form,
    });
  },

  agentStatus: (): Promise<AgentStatus> =>
    request("/api/v1/agent/status", agentStatusSchema),

  notifications: (subject: string) =>
    request(
      `/api/v1/notifications?subject=${encodeURIComponent(subject)}`,
      notificationsSchema,
    ),

  createWatch: (
    subject: string,
    kind: string,
    criteria: Record<string, string>,
  ) => {
    const extra = Object.entries(criteria)
      .map(([k, v]) => `&${k}=${encodeURIComponent(v)}`)
      .join("");
    return request(
      `/api/v1/watches?subject=${encodeURIComponent(subject)}&kind=${kind}&channel=inapp${extra}`,
      z.object({ id: z.number(), kind: z.string() }).passthrough(),
      { method: "POST" },
    );
  },

  checkDocument: (text: string): Promise<DocumentCheck> =>
    request("/api/v1/documents/check", documentCheckSchema, json({ text })),
};

/** Format rupees the way India reads them: lakh and crore, never thousands. */
export function rupees(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const lakh = value / 1e5;
  if (lakh >= 100) {
    return `₹${(lakh / 100).toFixed(2).replace(/\.00$/, "")} Cr`;
  }
  if (lakh >= 1) return `₹${Math.round(lakh)} L`;
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}
