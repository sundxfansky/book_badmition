import { Hono } from "hono";
import { getCookie, setCookie } from "hono/cookie";
import type { Env } from "../types";

const admin = new Hono<{ Bindings: Env }>();
const COOKIE_NAME = "sundx_admin_session";

function getAuthStub(env: Env): DurableObjectStub {
  const id = env.ADMIN_AUTH.idFromName("singleton");
  return env.ADMIN_AUTH.get(id);
}

async function isAuthenticated(c: any): Promise<boolean> {
  const token = getCookie(c, COOKIE_NAME);
  if (!token) return false;
  const stub = getAuthStub(c.env);
  const resp = await stub.fetch(new Request("http://do/check-session", {
    method: "POST", body: JSON.stringify({ token }), headers: { "content-type": "application/json" },
  }));
  const data = (await resp.json()) as { ok: boolean };
  return data.ok;
}

admin.get("/", async (c) => {
  const authed = await isAuthenticated(c);
  const stub = getAuthStub(c.env);
  const pwResp = await stub.fetch(new Request("http://do/password-set"));
  const { password_set } = (await pwResp.json()) as { password_set: boolean };

  if (!password_set || !authed) {
    return c.html(loginPage(password_set));
  }
  return c.html(await adminDashboard(c.env));
});

admin.post("/login", async (c) => {
  const form = await c.req.parseBody();
  const password = String(form.password || "");
  const stub = getAuthStub(c.env);

  const pwResp = await stub.fetch(new Request("http://do/password-set"));
  const { password_set } = (await pwResp.json()) as { password_set: boolean };

  if (!password_set) {
    await stub.fetch(new Request("http://do/set-password", {
      method: "POST", body: JSON.stringify({ password }), headers: { "content-type": "application/json" },
    }));
  }

  const verifyResp = await stub.fetch(new Request("http://do/verify", {
    method: "POST", body: JSON.stringify({ password }), headers: { "content-type": "application/json" },
  }));
  const result = (await verifyResp.json()) as { ok: boolean; token?: string };
  if (!result.ok) return c.html(loginPage(true, "密码错误"));

  setCookie(c, COOKIE_NAME, result.token!, { path: "/sundx", httpOnly: true, sameSite: "Lax" });
  return c.redirect("/sundx");
});

admin.post("/stop", async (c) => {
  if (!(await isAuthenticated(c))) return c.redirect("/sundx");
  const form = await c.req.parseBody();
  const clientId = String(form.client_id || "default");
  const id = c.env.BOOKING_CLIENT.idFromName(clientId);
  const stub = c.env.BOOKING_CLIENT.get(id);
  await stub.fetch(new Request("http://do/api/stop", { method: "POST", body: "{}", headers: { "content-type": "application/json" } }));
  return c.redirect("/sundx");
});

admin.post("/start", async (c) => {
  if (!(await isAuthenticated(c))) return c.redirect("/sundx");
  const form = await c.req.parseBody();
  const clientId = String(form.client_id || "default");
  const id = c.env.BOOKING_CLIENT.idFromName(clientId);
  const stub = c.env.BOOKING_CLIENT.get(id);
  await stub.fetch(new Request("http://do/api/start", { method: "POST", body: "{}", headers: { "content-type": "application/json" } }));
  return c.redirect("/sundx");
});

admin.post("/export", async (c) => {
  if (!(await isAuthenticated(c))) return c.text("Unauthorized", 401);
  const clients = await getActiveClients(c.env);
  const tasks: any[] = [];
  for (const clientId of clients) {
    const id = c.env.BOOKING_CLIENT.idFromName(clientId);
    const stub = c.env.BOOKING_CLIENT.get(id);
    const resp = await stub.fetch(new Request("http://do/api/status"));
    const status = (await resp.json()) as Record<string, any>;
    tasks.push({ client_id: clientId, ...status });
  }
  return c.json({ exported_at: new Date().toISOString(), tasks });
});

admin.post("/upload-requests", async (c) => {
  if (!(await isAuthenticated(c))) return c.redirect("/sundx");
  const form = await c.req.parseBody();
  const file = form.file;
  if (file && typeof file === "object" && "text" in file) {
    const content = await (file as File).text();
    await c.env.REQUEST_STORE.put("request_template:default", content);
  }
  return c.redirect("/sundx");
});

async function getActiveClients(env: Env): Promise<string[]> {
  const raw = await env.ADMIN_KV.get("active_clients");
  return raw ? JSON.parse(raw) : [];
}

async function adminDashboard(env: Env): Promise<string> {
  const clients = await getActiveClients(env);
  const rows: string[] = [];
  for (const clientId of clients) {
    const id = env.BOOKING_CLIENT.idFromName(clientId);
    const stub = env.BOOKING_CLIENT.get(id);
    try {
      const resp = await stub.fetch(new Request("http://do/api/status"));
      const status = (await resp.json()) as any;
      const running = status.running ? "运行中" : "已停止";
      const lastLog = status.logs?.length ? status.logs[status.logs.length - 1] : "-";
      rows.push(`<tr>
        <td>${esc(clientId)}</td><td>${running}</td><td>${esc(lastLog)}</td>
        <td>
          <form method="POST" action="/sundx/start" style="display:inline"><input type="hidden" name="client_id" value="${esc(clientId)}"><button type="submit">启动</button></form>
          <form method="POST" action="/sundx/stop" style="display:inline"><input type="hidden" name="client_id" value="${esc(clientId)}"><button type="submit">停止</button></form>
        </td>
      </tr>`);
    } catch { /* skip */ }
  }

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Admin</title>
<style>body{font-family:system-ui;max-width:1200px;margin:0 auto;padding:20px}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:8px;text-align:left}th{background:#f5f5f5}button{padding:4px 12px;cursor:pointer}h2{margin-top:30px}</style>
</head><body>
<h1>Admin Dashboard</h1>
<h2>任务列表</h2>
<table><tr><th>Client ID</th><th>状态</th><th>最新日志</th><th>操作</th></tr>${rows.join("")}</table>
<h2>上传 request.txt</h2>
<form method="POST" action="/sundx/upload-requests" enctype="multipart/form-data">
<input type="file" name="file" accept=".txt,.json"><button type="submit">上传</button>
</form>
<h2>导出所有任务</h2>
<form method="POST" action="/sundx/export"><button type="submit">导出 JSON</button></form>
</body></html>`;
}

function loginPage(passwordSet: boolean, error?: string): string {
  const title = passwordSet ? "管理员登录" : "设置管理员密码";
  const errorHtml = error ? `<p style="color:red">${esc(error)}</p>` : "";
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title}</title>
<style>body{font-family:system-ui;max-width:400px;margin:100px auto;padding:20px}input{width:100%;padding:8px;margin:8px 0;box-sizing:border-box}button{width:100%;padding:10px;background:#333;color:#fff;border:none;cursor:pointer}</style>
</head><body>
<h2>${title}</h2>${errorHtml}
<form method="POST" action="/sundx/login">
<input type="password" name="password" placeholder="密码" required>
<button type="submit">${passwordSet ? "登录" : "设置密码"}</button>
</form></body></html>`;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export default admin;
