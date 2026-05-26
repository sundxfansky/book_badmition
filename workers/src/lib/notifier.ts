export async function sendWechatNotification(
  webhookUrl: string,
  message: string
): Promise<string> {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const resp = await fetch(webhookUrl, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ msgtype: "text", text: { content: message } }),
      });
      const payload = (await resp.json()) as { errcode?: number; errmsg?: string };
      if (payload.errcode === 0) return "企业微信通知已发送";
      if (attempt < 3) continue;
      return `企业微信通知失败：${payload.errmsg || resp.status}`;
    } catch (e: any) {
      if (attempt < 3) continue;
      return `企业微信通知失败：${e.message || "网络错误"}`;
    }
  }
  return "企业微信通知最终失败";
}
