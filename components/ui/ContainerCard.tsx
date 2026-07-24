"use client";

import React, { useState } from "react";
import axios from "axios";

type Props = {
  containerId: string;
  initial?: {
    ip?: string;
    ipv6?: string;
    ssh_port?: number;
    ssh_password?: string;
    os?: string;
    package?: any;
    last_sync_msg?: string;
  };
  userEmail?: string;
};

export default function ContainerCard({ containerId, initial = {}, userEmail }: Props) {
  const [rec, setRec] = useState(initial);
  const [loading, setLoading] = useState(false);

  async function sync() {
    setLoading(true);
    try {
      const r = await axios.get(`/api/containers/${containerId}/sync`);
      setRec(r.data || {});
    } catch (e) {
      console.error(e);
      alert("同步失败，请检查后端日志。");
    } finally {
      setLoading(false);
    }
  }

  async function resetPassword() {
    if (!confirm("确定要重置该容器的 SSH 密码并发邮件通知用户吗？")) return;
    setLoading(true);
    try {
      const payload: any = {};
      if (userEmail) payload.email = userEmail;
      const r = await axios.post(`/api/containers/${containerId}/reset-password`, payload);
      const updated = r.data.container || r.data;
      setRec({ ...updated, last_sync_msg: "*请重置SSH密码，查收邮件" });
      alert("重置成功，已向用户发送邮件（若配置正确）。");
    } catch (e: any) {
      console.error(e);
      alert("重置失败：" + (e?.response?.data?.error || e.message));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container-card border p-4 rounded">
      <h3 className="text-lg font-semibold">容器：{containerId}</h3>
      <ul className="mt-2 text-sm">
        <li>IP: {rec.ip ?? "-"}</li>
        <li>IPv6: {rec.ipv6 ?? "-"}</li>
        <li>SSH 端口: {rec.ssh_port ?? "-"}</li>
        <li>
          SSH 密码: {rec.ssh_password ? <span style={{ color: "#c53030" }}>{rec.ssh_password}</span> : "-"}
        </li>
        <li>操作系统: {rec.os ?? "-"}</li>
        <li>绑定套餐: {rec.package ? JSON.stringify(rec.package) : "-"}</li>
      </ul>
      <div className="mt-2">最近同步: {rec.last_sync_msg ?? "—"}</div>
      <div className="mt-3">
        <button className="btn mr-2" onClick={sync} disabled={loading}>
          同步信息
        </button>
        <button className="btn btn-danger" onClick={resetPassword} disabled={loading}>
          {loading ? "处理中..." : "重置SSH密码"}
        </button>
      </div>
    </div>
  );
}
