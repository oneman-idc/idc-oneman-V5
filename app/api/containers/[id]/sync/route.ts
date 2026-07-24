import { NextResponse } from "next/server";

const CLICD_BASE = process.env.CLICD_BASE || "http://51.159.67.180:8999";
const CLICD_API_KEY = process.env.CLICD_API_KEY || "";

function clicdHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${CLICD_API_KEY}`,
  };
}

export async function GET(request: Request, { params }: { params: { id: string } }) {
  const containerId = params.id;
  const url = `${CLICD_BASE}/api/v1/containers/${containerId}`;
  try {
    const res = await fetch(url, { headers: clicdHeaders(), cache: "no-store" });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      return NextResponse.json({ error: "clicd fetch failed", details: txt }, { status: res.status });
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch (err: any) {
    return NextResponse.json({ error: "request failed", message: String(err) }, { status: 500 });
  }
}
