import nodemailer from "nodemailer";
import { NextResponse } from "next/server";

const CLICD_BASE = process.env.CLICD_BASE || "http://51.159.67.180:8999";
const CLICD_API_KEY = process.env.CLICD_API_KEY || "";
const SMTP_HOST = process.env.SMTP_HOST || "";
const SMTP_PORT = process.env.SMTP_PORT ? Number(process.env.SMTP_PORT) : 587;
const SMTP_USER = process.env.SMTP_USER || "";
const SMTP_PASS = process.env.SMTP_PASS || "";

function clicdHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${CLICD_API_KEY}`,
  };
}

export async function POST(request: Request, { params }: { params: { id: string } }) {
  const containerId = params.id;
  const body = await request.json().catch(() => ({}));
  const userEmail = body.email || null;

  const resetUrl = `${CLICD_BASE}/api/v1/containers/${containerId}/reset-password`;
  try {
    const res = await fetch(resetUrl, { method: "POST", headers: clicdHeaders() });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      return NextResponse.json({ error: "clicd reset failed", details: txt }, { status: res.status });
    }
    const data = await res.json();
    const newPassword = data.ssh_password || data.new_password || data.password || null;

    // send email if we have recipient and SMTP configured
    if (userEmail && SMTP_HOST && SMTP_USER && SMTP_PASS) {
      try {
        const transporter = nodemailer.createTransport({
          host: SMTP_HOST,
          port: SMTP_PORT,
          secure: SMTP_PORT === 465,
          auth: { user: SMTP_USER, pass: SMTP_PASS },
        });

        const mailBody = `容器 ID: ${containerId}\nIP: ${data.ip || "-"}\nIPv6: ${data.ipv6 || "-"}\nSSH 端口: ${data.ssh_port || "-"}\nSSH 密码: ${newPassword || "已重置（请在控制台查看）"}\n操作系统: ${data.os || data.os_name || "-"}\n\n*请重置SSH密码，查收邮件\n`;

        await transporter.sendMail({
          from: SMTP_USER,
          to: userEmail,
          subject: `容器 ${containerId} - SSH 密码已重置`,
          text: mailBody,
        });
      } catch (e: any) {
        // return 207 to indicate partial success
        return NextResponse.json({ ok: true, container: data, email_warning: String(e) }, { status: 207 });
      }
    }

    return NextResponse.json({ ok: true, container: data });
  } catch (err: any) {
    return NextResponse.json({ error: "request failed", message: String(err) }, { status: 500 });
  }
}
