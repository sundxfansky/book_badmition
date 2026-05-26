import { Hono } from "hono";
import type { Env } from "./types";
import apiRoutes from "./routes/api";
import adminRoutes from "./routes/admin";

export { BookingClientDO } from "./durable-objects/booking-client";
export { AdminAuthDO } from "./durable-objects/admin-auth";

const app = new Hono<{ Bindings: Env }>();

app.route("/api", apiRoutes);
app.route("/sundx", adminRoutes);

export default app;
