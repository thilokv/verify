/**
 * Zod schemas for every API response the dashboard consumes.
 *
 * These parse at the boundary rather than casting. The backend is a separate
 * runtime that ships on its own schedule, so a response shape can change
 * without the compiler noticing — `as Valuation` would hide exactly that and
 * fail later, deep in a component, as an undefined read.
 */
import { z } from "zod";

export const propertySchema = z.object({
  id: z.number(),
  title: z.string(),
  property_type: z.string().nullable(),
  config: z.string().nullable(),
  location_name: z.string().nullable(),
  city: z.string().nullable(),
  price_inr: z.number().nullable(),
  area_sqft: z.number().nullable(),
  possession: z.string().nullable(),
  facing: z.string().nullable(),
  rera_id: z.string().nullable(),
  khata: z.string().nullable(),
  is_verified: z.boolean(),
  verification_note: z.string().nullable(),
  risk_status: z.string().nullable(),
  description: z.string().nullable(),
  amenities: z.array(z.unknown()),
});
export type Property = z.infer<typeof propertySchema>;

/**
 * A search hit is NOT a full property row. `/search` serialises through
 * PropertyMatch, which omits `risk_status` — so that field is relaxed here
 * rather than in `propertySchema`, which stays strict for `/properties`.
 */
export const matchSchema = propertySchema.extend({
  risk_status: z.string().nullish(),
  score: z.number().nullable().optional(),
  why: z.string().default(""),
  concern: z.string().default(""),
});
export type Match = z.infer<typeof matchSchema>;

export const searchResponseSchema = z.object({
  query: z.string(),
  parsed_constraints: z.record(z.string(), z.unknown()),
  semantic: z.boolean(),
  rationale_source: z.string(),
  relaxed_locality: z.boolean().default(false),
  notice: z.string().nullable().optional(),
  count: z.number(),
  matches: z.array(matchSchema),
});
export type SearchResponse = z.infer<typeof searchResponseSchema>;

export const sunlightSchema = z.object({
  facing: z.string(),
  annual_direct_sun_hours: z.number(),
  mean_daylight_hours: z.number(),
  peak_exposure: z.string(),
  summer_daylight_hours: z.number(),
  winter_daylight_hours: z.number(),
  note: z.string(),
  method: z.string(),
});
export type Sunlight = z.infer<typeof sunlightSchema>;

export const adjustmentSchema = z.object({
  factor: z.string(),
  percent: z.number(),
  amount_inr: z.number(),
  why: z.string(),
});

export const comparableSchema = z.object({
  id: z.number(),
  title: z.string(),
  locality: z.string().nullable(),
  price_inr: z.number(),
  area_sqft: z.number(),
  rate_per_sqft: z.number(),
});

export const valuationSchema = z.object({
  property_id: z.number(),
  title: z.string(),
  locality: z.string().nullable().optional(),
  estimate_inr: z.number().nullable(),
  estimate_display: z.string().optional(),
  listed_price_inr: z.number().nullable(),
  listed_display: z.string().optional(),
  gap_percent: z.number().nullable().optional(),
  verdict: z.string().nullable().optional(),
  rate_per_sqft: z.number().optional(),
  base_inr: z.number().optional(),
  adjustments: z.array(adjustmentSchema),
  projected_3yr_growth_percent: z.number().optional(),
  confidence: z.enum(["none", "low", "medium", "high"]),
  comparables: z.array(comparableSchema),
  comparable_count: z.number().optional(),
  same_locality_count: z.number().optional(),
  sunlight: sunlightSchema,
  is_estimate: z.boolean(),
  basis: z.string(),
});
export type Valuation = z.infer<typeof valuationSchema>;

export const renovationLineSchema = z.object({
  scope: z.string(),
  label: z.string(),
  treated_sqft: z.number(),
  cost_inr: z.number(),
  cost_display: z.string(),
  range_inr: z.tuple([z.number(), z.number()]),
  recovery_percent: z.number(),
  value_added_inr: z.number(),
  net_inr: z.number(),
  working_days: z.number(),
  note: z.string(),
});

export const renovationSchema = z.object({
  area_sqft: z.number(),
  grade: z.string(),
  lines: z.array(renovationLineSchema),
  total_cost_inr: z.number(),
  total_cost_display: z.string(),
  range_display: z.string(),
  estimated_value_added_inr: z.number(),
  estimated_value_added_display: z.string(),
  net_inr: z.number(),
  net_display: z.string(),
  pays_for_itself: z.boolean(),
  estimated_weeks: z.number(),
  recommendation: z.string(),
  is_estimate: z.boolean(),
  disclaimer: z.string(),
});
export type Renovation = z.infer<typeof renovationSchema>;
export type RenovationLine = z.infer<typeof renovationLineSchema>;

export const scopeSchema = z.object({
  key: z.string(),
  label: z.string(),
  rate_range_per_sqft: z.tuple([z.number(), z.number()]),
  recovery_percent: z.number(),
  working_days: z.number(),
  note: z.string(),
});
export type Scope = z.infer<typeof scopeSchema>;

export const scopesResponseSchema = z.object({
  scopes: z.array(scopeSchema),
  styles: z.array(z.string()),
  rooms: z.array(z.string()),
});

export const stagingSchema = z.object({
  room: z.string(),
  style: z.string(),
  prompt: z.string(),
  image: z.string().nullable(),
  rendered: z.boolean(),
  provider_configured: z.boolean(),
  status: z.string(),
  suggested_scopes: z.array(z.string()),
  costing: renovationSchema.nullable(),
  honesty_note: z.string(),
});
export type Staging = z.infer<typeof stagingSchema>;

export const issueSchema = z.object({
  code: z.string(),
  severity: z.string(),
  detail: z.string(),
});

export const documentCheckSchema = z.object({
  document_type: z.string(),
  verdict: z.enum(["PASSED", "PENDING", "RED_FLAGGED"]),
  issues: z.array(issueSchema),
  extracted: z.record(z.string(), z.unknown()),
  disclaimer: z.string().optional(),
});
export type DocumentCheck = z.infer<typeof documentCheckSchema>;

export const healthSchema = z.object({
  status: z.string(),
  vector_backend: z.string(),
  embedding_provider: z.string(),
  llm_provider: z.string(),
  semantic_search: z.boolean(),
  auth_required: z.boolean(),
  documents_encrypted: z.boolean(),
  verification_gate: z.boolean(),
  whatsapp_outbound: z.string(),
  warning: z.string().optional(),
  auth_warning: z.string().optional(),
});
export type Health = z.infer<typeof healthSchema>;

export const agentStatusSchema = z.object({
  running: z.boolean(),
  active_watches: z.number(),
  last_run: z.record(z.string(), z.unknown()).nullable(),
  whatsapp: z.string().optional(),
  suppressed_total: z.number().optional(),
});
export type AgentStatus = z.infer<typeof agentStatusSchema>;

export const notificationSchema = z.object({
  id: z.number(),
  title: z.string(),
  body: z.string(),
  severity: z.string(),
  status: z.string(),
  channel: z.string(),
  read_at: z.string().nullish(),
  created_at: z.string().nullish(),
});
export type Notification = z.infer<typeof notificationSchema>;

export const notificationsSchema = z.object({
  count: z.number(),
  notifications: z.array(notificationSchema),
});
