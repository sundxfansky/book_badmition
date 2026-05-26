import type { Env } from "../types";

export class AdminAuthDO implements DurableObject {
  private ctx: DurableObjectState;

  constructor(ctx: DurableObjectState, _env: Env) {
    this.ctx = ctx;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/password-set") {
      const has = await this.ctx.storage.get<string>("password_hash");
      return json({ password_set: !!has });
    }
    if (path === "/set-password" && request.method === "POST") {
      const { password } = (await request.json()) as { password: string };
      return json(await this.setPassword(password));
    }
    if (path === "/verify" && request.method === "POST") {
      const { password } = (await request.json()) as { password: string };
      const ok = await this.verify(password);
      if (!ok) return json({ ok: false });
      const token = crypto.randomUUID();
      const sessions = (await this.ctx.storage.get<string[]>("sessions")) || [];
      sessions.push(token);
      if (sessions.length > 100) sessions.splice(0, sessions.length - 100);
      await this.ctx.storage.put("sessions", sessions);
      return json({ ok: true, token });
    }
    if (path === "/check-session" && request.method === "POST") {
      const { token } = (await request.json()) as { token: string };
      const sessions = (await this.ctx.storage.get<string[]>("sessions")) || [];
      return json({ ok: sessions.includes(token) });
    }
    return new Response("Not found", { status: 404 });
  }

  private async setPassword(password: string): Promise<{ ok: boolean }> {
    if (!password) return { ok: false };
    const existing = await this.ctx.storage.get<string>("password_hash");
    if (existing) return { ok: false };
    const salt = crypto.randomUUID();
    const hash = await hashPassword(password, salt);
    await this.ctx.storage.put("password_hash", hash);
    await this.ctx.storage.put("salt", salt);
    return { ok: true };
  }

  private async verify(password: string): Promise<boolean> {
    const hash = await this.ctx.storage.get<string>("password_hash");
    const salt = await this.ctx.storage.get<string>("salt");
    if (!hash || !salt) return false;
    const computed = await hashPassword(password, salt);
    return hash === computed;
  }
}

async function hashPassword(password: string, salt: string): Promise<string> {
  const data = new TextEncoder().encode(`${salt}:${password}`);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function json(data: any): Response {
  return new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });
}
