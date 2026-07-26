from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
import httpx


CLICD_USER_AGENT = "VPS-ONE/1.0"


class CLICDError(RuntimeError):
    pass


def expiration_date(value: date | datetime | str) -> str:
    """Return the date-only format required by CLICD's expiration parser."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).date()
        else:
            value = value.date()
    elif isinstance(value, str):
        raw = value.strip()
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                value = date.fromisoformat(raw)
            except ValueError as exc:
                raise CLICDError("CLICD 到期日期必须是有效的 ISO 8601 日期") from exc
    if not isinstance(value, date):
        raise CLICDError("CLICD 到期日期类型无效")
    if value <= datetime.now(timezone.utc).date():
        raise CLICDError("CLICD 到期日期必须晚于今天")
    return value.isoformat()


def expiration_datetime(value: Any) -> datetime | None:
    """Normalize a CLICD expiration value to a naive UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def unwrap_data(result: Any) -> Any:
    value = result
    for _ in range(4):
        if not isinstance(value, dict):
            break
        nested = value.get("data") or value.get("container") or value.get("result")
        if not isinstance(nested, dict):
            break
        value = nested
    return value


def container_status(result: Any) -> str:
    value = unwrap_data(result)
    raw = str((value.get("status") or value.get("state") or value.get("power_status") or "unknown") if isinstance(value, dict) else "unknown").lower()
    if raw in {"running", "started", "online", "up", "active"}:
        return "running"
    if raw in {"stopped", "stop", "offline", "down", "inactive", "exited"}:
        return "stopped"
    if raw in {"starting", "stopping", "restarting", "creating", "provisioning", "pending"}:
        return raw
    return "unknown"


def extract_access(result: Any) -> dict[str, str]:
    aliases = {
        "username": ("username", "user_name", "sub_username", "sub_user_name"),
        "password": ("password", "initial_password", "sub_password", "login_password"),
        "access_code": ("access_code", "code", "login_code"),
        "management_url": ("management_url", "access_url", "login_url", "panel_url", "url"),
    }
    candidates: list[dict[str, Any]] = []

    def visit(value: Any, depth: int = 0):
        if depth > 5:
            return
        if isinstance(value, dict):
            candidates.append(value)
            for key, child in value.items():
                if key in {"sub_user", "subUser", "sub_user_info", "credentials", "access", "data", "container", "result"}:
                    visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:10]:
                visit(child, depth + 1)

    visit(result)
    output: dict[str, str] = {}
    for target, keys in aliases.items():
        for item in candidates:
            found = next((item.get(key) for key in keys if item.get(key) not in {None, ""}), None)
            if found is not None:
                output[target] = str(found)
                break
    return output


def container_items(result: Any) -> list[dict[str, Any]]:
    """Normalize CLICD list responses without losing nested public address data."""
    data = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(data, dict):
        data = data.get("items") or data.get("containers") or data.get("data") or []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def normalize_virtualization(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"lxc", "kvm"} else ""


def enabled_image_items(result: Any, requested_type: str) -> list[dict[str, Any]]:
    """Normalize CLICD enabled-image responses and preserve the requested runtime."""
    data = result
    for _ in range(5):
        if isinstance(data, list):
            break
        if not isinstance(data, dict):
            return []
        nested = next((data[key] for key in ("data", "items", "images", "result", "templates") if key in data and data[key] is not None), None)
        if nested is None:
            return []
        data = nested
    if not isinstance(data, list):
        return []
    images: list[dict[str, Any]] = []
    for image in data:
        if not isinstance(image, dict):
            continue
        image_id = image.get("id") or image.get("template_id") or image.get("slug")
        if image_id is None or image_id == "":
            continue
        image_type = normalize_virtualization(image.get("type") or image.get("virtualization")) or requested_type
        if image_type != requested_type:
            continue
        images.append({**image, "id": str(image_id), "type": image_type})
    return images


def container_details(result: Any) -> dict[str, Any]:
    value = unwrap_data(result)
    if not isinstance(value, dict):
        return {}
    operating_system = str(value.get("template_name") or value.get("template") or value.get("image_name") or value.get("image") or "")
    virtualization = normalize_virtualization(value.get("virtualization") or value.get("type")) or normalize_virtualization(operating_system.split("-", 1)[0])
    public = value.get("public_ipv4s") or value.get("public_ips") or []
    public_ip = ""
    if isinstance(public, list) and public:
        first = public[0]
        public_ip = str(first.get("address") or first.get("ip") or "") if isinstance(first, dict) else str(first)
    elif isinstance(public, str):
        public_ip = public
    expires_at = expiration_datetime(value.get("expires_at") or value.get("expiry") or value.get("expiration_at"))
    details = {
        "id": str(value.get("uuid") or value.get("id") or value.get("container_id") or ""),
        "name": str(value.get("name") or value.get("container_name") or ""),
        "virtualization": virtualization,
        "status": container_status(value),
        "ip": public_ip or str(value.get("public_ip") or value.get("public_ipv4") or value.get("ip") or ""),
        "ipv6": str(value.get("ipv6") or value.get("ipv6_address") or ""),
        "ssh_port": int(value.get("ssh_port") or 22),
        "ssh_password": str(value.get("ssh_password") or value.get("password") or ""),
        "operating_system": operating_system,
    }
    if expires_at:
        details["expires_at"] = expires_at
    return details


def reset_password_value(result: Any) -> str:
    if not isinstance(result, dict) or result.get("success") is not True:
        raise CLICDError(str(result.get("message") if isinstance(result, dict) else "CLICD 重置 SSH 密码失败"))
    value = unwrap_data(result)
    password = value.get("password") or value.get("ssh_password") if isinstance(value, dict) else ""
    if not password:
        raise CLICDError("CLICD 未返回新的 SSH 密码")
    return str(password)


def error_message(response: httpx.Response) -> str:
    try:
        detail = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(detail, dict):
        value = detail.get("message") or detail.get("detail") or detail.get("error")
        if isinstance(value, dict):
            value = value.get("message") or value.get("detail") or value
        return str(value or detail)
    return str(detail)


class CLICD:
    def __init__(self, base_url: str, token: str):
        if not base_url or not token:
            raise CLICDError("CLICD 尚未配置")
        self.base = base_url.rstrip("/")
        self.headers = {"X-API-Key": token, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": CLICD_USER_AGENT}
        self.timeout = httpx.Timeout(connect=5, read=30, write=15, pool=5)

    async def request(self, method: str, path: str, data: dict[str, Any] | None = None, params: dict[str, Any] | None = None):
        transport = httpx.AsyncHTTPTransport(retries=2 if method.upper() in {"GET", "HEAD"} else 0)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=transport) as client:
                response = await client.request(method, self.base + "/api/v1" + path, headers=self.headers, json=data, params=params)
        except httpx.TimeoutException as exc:
            raise CLICDError("CLICD 请求超时，请检查服务连通性") from exc
        except httpx.RequestError as exc:
            raise CLICDError(f"CLICD 网络错误：{exc.__class__.__name__}") from exc
        if response.is_error:
            raise CLICDError(f"CLICD 请求失败：{response.status_code} · {error_message(response)[:800]}")
        try:
            result = response.json() if response.content else {}
        except ValueError as exc:
            raise CLICDError("CLICD 返回了无效的 JSON 数据") from exc
        if isinstance(result, dict) and result.get("success") is False:
            raise CLICDError(str(result.get("message") or "CLICD 操作失败"))
        return result

    async def test(self):
        return await self.request("GET", "/dashboard")

    async def dashboard(self):
        return await self.request("GET", "/dashboard")

    async def containers(self):
        return await self.request("GET", "/containers")

    async def find_by_name(self, name: str) -> dict[str, Any] | None:
        return next((item for item in container_items(await self.containers()) if item.get("name") == name), None)

    async def create_sub_user(self, container_name: str) -> dict[str, str]:
        result = await self.request("POST", "/sub-user/create", {"container_name": container_name})
        credentials = extract_access(result)
        if not credentials.get("username") or not credentials.get("password") or not credentials.get("access_code"):
            raise CLICDError("CLICD 子用户创建响应缺少登录凭据")
        credentials.setdefault("management_url", f"{self.base}/login?code={credentials['access_code']}")
        return credentials

    async def reset_password(self, instance_id: str, password: str) -> str:
        return reset_password_value(await self.request("POST", f"/containers/{instance_id}/reset-password", {"password": password}))

    async def templates(self, virtualization: str = ""):
        selected_type = normalize_virtualization(virtualization)
        if virtualization and not selected_type:
            raise CLICDError("CLICD 镜像类型必须是 LXC 或 KVM")
        requested_types = [selected_type] if selected_type else ["lxc", "kvm"]
        images: list[dict[str, Any]] = []
        errors: list[str] = []
        succeeded = 0
        seen: set[tuple[str, str]] = set()
        for image_type in requested_types:
            try:
                response = await self.request("GET", "/images/enabled", params={"type": image_type})
            except CLICDError as exc:
                errors.append(f"{image_type.upper()}: {exc}")
                continue
            succeeded += 1
            for image in enabled_image_items(response, image_type):
                key = (image["type"], image["id"])
                if key not in seen:
                    seen.add(key)
                    images.append(image)
        if not succeeded and errors:
            raise CLICDError("；".join(errors))
        return {"data": images, "errors": errors}

    async def host_info(self):
        return await self.request("GET", "/host-info")

    async def routing(self):
        return await self.request("GET", "/routing")

    async def tasks(self):
        return await self.request("GET", "/tasks")

    async def snapshots_overview(self):
        return await self.request("GET", "/snapshots")

    async def security_summary(self):
        return await self.request("GET", "/security/summary")

    async def audit_logs(self):
        return await self.request("GET", "/audit-logs")

    async def create(self, payload: dict[str, Any]):
        return await self.request("POST", "/containers", payload)

    async def delete(self, instance_id: str):
        return await self.request("DELETE", f"/containers/{instance_id}/delete")

    async def update_resource_limit(self, instance_id: str, payload: dict[str, Any]):
        return await self.request("PUT", f"/containers/{instance_id}/resource-limit", payload)

    async def update_traffic_limit(self, instance_id: str, payload: dict[str, Any]):
        return await self.request("PUT", f"/containers/{instance_id}/traffic-limit", payload)

    async def update_expiry(self, instance_id: str, expires_at: date | datetime | str):
        return await self.request("PUT", f"/containers/{instance_id}/expiry", {"expires_at": expiration_date(expires_at)})

    async def get(self, instance_id: str):
        return await self.request("GET", f"/containers/{instance_id}")

    async def status(self, instance_id: str) -> str:
        return container_status(await self.get(instance_id))

    async def start(self, instance_id: str):
        return await self.action(instance_id, "start")

    async def stop(self, instance_id: str):
        return await self.action(instance_id, "stop")

    async def restart(self, instance_id: str):
        return await self.action(instance_id, "restart")

    async def usage(self, instance_id: str):
        return await self.request("GET", f"/containers/{instance_id}/usage")

    async def action(self, instance_id: str, action: str, data: dict[str, Any] | None = None):
        allowed = {"start", "stop", "restart", "reset-password", "reinstall"}
        if action not in allowed:
            raise CLICDError("不允许的实例操作")
        return await self.request("POST", f"/containers/{instance_id}/{action}", data or {})

    async def snapshots(self, instance_id: str):
        return await self.request("GET", f"/containers/{instance_id}/snapshots")

    async def create_snapshot(self, instance_id: str, name: str):
        return await self.request("POST", f"/containers/{instance_id}/snapshots", {"name": name})

    async def add_port(self, instance_id: str, payload: dict[str, Any]):
        return await self.request("POST", f"/containers/{instance_id}/port-mappings", payload)

    async def firewall(self, instance_id: str, payload: dict[str, Any] | None = None):
        method = "PUT" if payload is not None else "GET"
        return await self.request(method, f"/containers/{instance_id}/firewall", payload)

    async def ssh_ticket(self, instance_id: str):
        return await self.request("POST", "/ssh-ticket", {"container_id": instance_id})

    async def vnc_ticket(self, container_name: str) -> str:
        result = unwrap_data(await self.request("POST", "/vnc-ticket", {"container_name": container_name}))
        ticket = str(result.get("ticket") or "") if isinstance(result, dict) else ""
        if not ticket:
            raise CLICDError("CLICD 未返回 WebVNC 票据")
        return ticket

    def vnc_websocket_url(self, container_name: str) -> str:
        parsed = urlparse(self.base)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = parsed.path.rstrip("/") + "/api/vnc"
        return urlunparse((scheme, parsed.netloc, path, "", urlencode({"container": container_name}), ""))


def plan_payload(plan, order_no: str, expires_at: date | datetime | str) -> dict[str, Any]:
    assign_nat = bool(plan.assign_nat)
    port_mapping_count = max(2, min(64, int(plan.port_mapping_count or 2))) if assign_nat else 0
    return {
        "name": f"vps-{order_no.lower()}",
        "virtualization": plan.virtualization,
        "template_id": plan.clicd_image,
        "vcpu": plan.cpu,
        "ram_mb": plan.memory_mb,
        "disk_gb": plan.disk_gb,
        "assign_nat": assign_nat,
        "port_mapping_count": port_mapping_count,
        "assign_ipv4": plan.assign_ipv4,
        "ipv4_count": plan.ipv4_count,
        "public_ipv4s": [],
        "assign_ipv6": plan.assign_ipv6,
        "ipv6_count": plan.ipv6_count,
        "ipv6_addresses": [],
        "ssh_auth_mode": "auto_password",
        "ssh_password": "",
        "ssh_public_key": "",
        "expires_at": expiration_date(expires_at),
        "network_down_mbps": plan.network_down_mbps,
        "network_up_mbps": plan.network_up_mbps,
        "io_read_mbps": plan.io_read_mbps,
        "io_write_mbps": plan.io_write_mbps,
    }
