import { Hono } from "hono";
import type { Env } from "../types";

const api = new Hono<{ Bindings: Env }>();

function getClientStub(c: any): DurableObjectStub {
  const clientId = c.req.header("x-client-id") || "default";
  const id = c.env.BOOKING_CLIENT.idFromName(clientId);
  return c.env.BOOKING_CLIENT.get(id);
}

api.get("/metadata", async (c) => {
  const stub = getClientStub(c);
  const resp = await stub.fetch(new Request("http://do/api/metadata"));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.get("/status", async (c) => {
  const stub = getClientStub(c);
  const resp = await stub.fetch(new Request("http://do/api/status"));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.get("/export", async (c) => {
  const stub = getClientStub(c);
  const resp = await stub.fetch(new Request("http://do/api/export"));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/preview", async (c) => {
  const stub = getClientStub(c);
  const body = await c.req.text();
  const resp = await stub.fetch(new Request("http://do/api/preview", { method: "POST", body, headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/save", async (c) => {
  const stub = getClientStub(c);
  const body = await c.req.text();
  const resp = await stub.fetch(new Request("http://do/api/save", { method: "POST", body, headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/import", async (c) => {
  const stub = getClientStub(c);
  const body = await c.req.text();
  const resp = await stub.fetch(new Request("http://do/api/import", { method: "POST", body, headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/start", async (c) => {
  const stub = getClientStub(c);
  const body = await c.req.text();
  const resp = await stub.fetch(new Request("http://do/api/start", { method: "POST", body, headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/stop", async (c) => {
  const stub = getClientStub(c);
  const resp = await stub.fetch(new Request("http://do/api/stop", { method: "POST", body: "{}", headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/clear-logs", async (c) => {
  const stub = getClientStub(c);
  const resp = await stub.fetch(new Request("http://do/api/clear-logs", { method: "POST", body: "{}", headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

api.post("/site-status", async (c) => {
  const stub = getClientStub(c);
  const body = await c.req.text();
  const resp = await stub.fetch(new Request("http://do/api/site-status", { method: "POST", body, headers: { "content-type": "application/json" } }));
  return new Response(resp.body, { headers: { "content-type": "application/json" } });
});

export default api;
