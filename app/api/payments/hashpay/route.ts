import { NextResponse } from "next/server";
import nodemailer from "nodemailer";

const CLICD_BASE = process.env.CLICD_BASE || "http://51.159.67.180:8999";
const CLICD_API_KEY = process.env.CLICD_API_KEY || "";
const SMTP_HOST = process.env.SMTP_HOST || "smtp";
const SMTP_PORT = process.env.SMTP_PORT ? Number(process.env.SMTP_PORT) : 25;
const SMTP_USER = process.env.SMTP_USER || "";
const SMTP_PASS = process.env.SMTP_PASS || "";

function clicdHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${CLICD_API_KEY}`,
  };
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    // Expecting payload from HashPay-mock: { event, data: { order_id, amount, status, metadata } }
    const event = body.event;
    const data = body.data || {};

    if (event !== 'payment.paid' || data.status !== 'paid') {
      return NextResponse.json({ ok: false, message: 'ignored event' });
    }

    // Simulate container creation by calling CLICD API create endpoint (if available)
    // We POST minimal payload; adapt if your CLICD expects other fields.
    const createUrl = `${CLICD_BASE}/api/v1/containers`;
    const createBody = {
      name: `order-${data.order_id}`,
      plan: 'default',
      metadata: data.metadata || {},
    };

    const res = await fetch(createUrl, { method: 'POST', headers: clicdHeaders(), body: JSON.stringify(createBody) });
    const respText = await res.text();
    let respJson: any = {};
    try { respJson = JSON.parse(respText); } catch (e) { respJson = { raw: respText }; }

    // If email provided in metadata, send notification (using smtp4dev listening on smtp:25)
    const userEmail = (data.metadata && data.metadata.user_email) || null;
    if (userEmail) {
      try {
        const transporter = nodemailer.createTransport({ host: SMTP_HOST, port: SMTP_PORT, secure: false });
        const mailBody = `您的订单 ${data.order_id} 已支付，容器已创建。\n\nCLICD response:\n${JSON.stringify(respJson, null, 2)}`;
        await transporter.sendMail({ from: 'noreply@example.com', to: userEmail, subject: '容器已创建', text: mailBody });
      } catch (e) {
        console.error('mail send failed', e);
      }
    }

    return NextResponse.json({ ok: true, created: respJson, raw: respText });
  } catch (err: any) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 500 });
  }
}
