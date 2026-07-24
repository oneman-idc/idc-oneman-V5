import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from sqlalchemy.ext.asyncio import AsyncSession

from .settings import get


class MailDeliveryError(RuntimeError):
    pass


def valid_address(value: str) -> bool:
    address = parseaddr(value)[1]
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address))


async def send_mail(db: AsyncSession, recipient: str, subject: str, text: str) -> None:
    host = (await get(db, "smtp_host")).strip()
    port_text = await get(db, "smtp_port", "587")
    username = (await get(db, "smtp_username")).strip()
    password = await get(db, "smtp_password")
    sender = (await get(db, "smtp_from") or username).strip()
    mode = (await get(db, "smtp_security", "starttls")).strip().lower()

    if not host or not sender:
        raise MailDeliveryError("SMTP 尚未配置")
    if mode not in {"ssl", "starttls", "plain"}:
        raise MailDeliveryError("SMTP 加密方式必须是 ssl、starttls 或 plain")
    if not valid_address(sender) or not valid_address(recipient):
        raise MailDeliveryError("SMTP 发件人或收件人地址无效")
    try:
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise MailDeliveryError("SMTP 端口无效") from exc

    message = EmailMessage()
    message["From"] = formataddr(("VPS-ONE", parseaddr(sender)[1]))
    message["To"] = parseaddr(recipient)[1]
    message["Subject"] = subject
    message.set_content(text, charset="utf-8")

    def deliver() -> None:
        context = ssl.create_default_context()
        client_class = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
        kwargs = {"host": host, "port": port, "timeout": 20}
        if mode == "ssl":
            kwargs["context"] = context
        try:
            with client_class(**kwargs) as client:
                if mode != "ssl":
                    client.ehlo()
                if mode == "starttls":
                    client.starttls(context=context)
                    client.ehlo()
                if username:
                    client.login(username, password)
                refused = client.send_message(message)
                if refused:
                    raise MailDeliveryError("SMTP 服务器拒绝了收件人")
        except MailDeliveryError:
            raise
        except smtplib.SMTPAuthenticationError as exc:
            raise MailDeliveryError("SMTP 认证失败，请检查账号或授权码") from exc
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise MailDeliveryError(f"SMTP 投递失败：{exc.__class__.__name__}") from exc

    await asyncio.to_thread(deliver)
