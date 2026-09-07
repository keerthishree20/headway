export type Vehicle = {
  id: string;
  label: string | null;
  route_id: string | null;
  trip_id: string | null;
  direction_id: number;
  lat: number;
  lon: number;
  bearing: number | null;
  speed: number | null;
  ts: number | null;
  stop_id: string | null;
  status: "incoming_at" | "stopped_at" | "in_transit_to" | null;
  occupancy: string | null;
};

export type BunchPair = {
  pair_id: string;
  route_id: string;
  direction_id: number;
  vehicles: [string, string];
  distance_m: number;
  /** The distance this route-direction was actually judged against. */
  threshold_m: number;
  lat: number;
  lon: number;
};

export type StaleVehicle = {
  id: string;
  route_id: string | null;
  age_sec: number;
  lat: number;
  lon: number;
};

export type RouteStat = {
  route_id: string;
  vehicles: number;
  bunched: number;
  stale: number;
  // Present only once the route has enough samples to have learned its own
  // spacing; until then it is judged against the global fallback.
  threshold_m?: number;
  normal_spacing_m?: number | null;
  samples?: number;
  learned?: boolean;
};

export type Metrics = {
  active: number;
  routes: number;
  bunched_pairs: number;
  bunched_vehicles: number;
  stale: number;
  moving: number;
  avg_speed_mps: number;
  fleet_zscore: number | null;
  bunching: BunchPair[];
  stale_vehicles: StaleVehicle[];
  worst_routes: RouteStat[];
  /** Route-directions that have learned their own bunching threshold. */
  learned_routes: number;
};

export type LiveEvent = {
  ts: number;
  kind:
    | "bunching_started"
    | "bunching_cleared"
    | "vehicle_stale"
    | "vehicle_recovered"
    | "fleet_anomaly"
    | "feed_error";
  severity: "ok" | "warn" | "alert";
  route_id: string | null;
  message: string;
  detail: Record<string, unknown>;
};

export type HistoryPoint = {
  ts: number;
  feed_ts: number | null;
  active: number;
  bunched_pairs: number;
  stale: number;
  avg_speed_mps: number;
};

export type FeedStatus = {
  connected: boolean;
  source: string;
  url: string;
  simulated: boolean;
  last_ok: number | null;
  last_error: { ts: number; error: string } | null;
  consecutive_failures: number;
};

export type FeedConfig = {
  bunch_distance_m: number;
  stale_after_sec: number;
  poll_interval_sec: number;
  adaptive_bunching: boolean;
  bunch_ratio: number;
  bunch_distance_min_m: number;
  bunch_distance_max_m: number;
  map_center: [number, number];
  map_zoom: number;
};

/** What the server actually sent this client, and what it held back. */
export type FilterInfo = {
  routes: string[] | null;
  bbox: [number, number, number, number] | null;
  vehicles_sent: number;
  vehicles_total: number;
};

/** What this client asks the server to send. */
export type FeedFilter = {
  routes: string[] | null;
  bbox: [number, number, number, number] | null;
};

export type ReplayMessage = {
  type: "replay";
  ts: number;
  vehicles: Vehicle[];
  metrics: Metrics | null;
  history: HistoryPoint[];
  events: LiveEvent[];
  alerts: LiveEvent[];
  status: FeedStatus;
  config: FeedConfig;
  filter: FilterInfo;
};

export type TickMessage = {
  type: "tick";
  ts: number;
  feed_ts: number | null;
  feed_age_sec: number | null;
  vehicles: Vehicle[];
  metrics: Metrics;
  events: LiveEvent[];
  alerts: LiveEvent[];
  status: FeedStatus;
  filter: FilterInfo;
};

export type StatusMessage = {
  type: "status";
  ts: number;
  status: FeedStatus;
  events: LiveEvent[];
  alerts: LiveEvent[];
};

export type ServerMessage = ReplayMessage | TickMessage | StatusMessage;
