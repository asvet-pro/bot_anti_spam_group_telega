"""VLESS-через-sing-box для обхода блокировок Telegram.

Если задан VLESS_URL, поднимает локальный SOCKS5-прокси (sing-box)
на 127.0.0.1:1080, через который бот ходит в api.telegram.org.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger("bot.proxy")

SOCKS_HOST = "127.0.0.1"
SOCKS_PORT = 1080
SINGBOX_CONFIG_PATH = Path("/app/sing-box.json")


@dataclass(frozen=True)
class VlessConfig:
    """Распарсенная VLESS-ссылка."""

    uuid: str
    host: str
    port: int
    network: str
    security: str
    flow: str | None
    sni: str | None
    fp: str | None
    pbk: str | None
    sid: str | None
    path: str | None
    host_header: str | None
    name: str | None


def parse_vless_url(url: str) -> VlessConfig:
    """Парсит vless://uuid@host:port?params#name.

    Поддерживает: type=tcp|ws, security=none|reality|tls, flow=xtls-rprx-vision,
    pbk, sid, sni, fp, path, host.
    """
    if not url.startswith("vless://"):
        raise ValueError("URL должен начинаться с vless://")
    u = urlsplit(url)
    if not u.username or not u.hostname or u.port is None:
        raise ValueError(f"Некорректный vless URL: {url!r}")
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    return VlessConfig(
        uuid=u.username,
        host=u.hostname,
        port=u.port,
        network=q.get("type", "tcp"),
        security=q.get("security", "none"),
        flow=q.get("flow") or None,
        sni=q.get("sni") or None,
        fp=q.get("fp") or None,
        pbk=q.get("pbk") or None,
        sid=q.get("sid") or None,
        path=unquote(q["path"]) if "path" in q else None,
        host_header=q.get("host") or None,
        name=unquote(u.fragment) if u.fragment else None,
    )


def build_singbox_config(cfg: VlessConfig) -> dict[str, Any]:
    """Собирает sing-box config: SOCKS5 inbound + VLESS outbound."""
    outbound: dict[str, Any] = {
        "type": "vless",
        "tag": "proxy",
        "server": cfg.host,
        "server_port": cfg.port,
        "uuid": cfg.uuid,
    }
    if cfg.flow:
        outbound["flow"] = cfg.flow

    if cfg.network == "ws":
        ws: dict[str, Any] = {"type": "ws"}
        if cfg.path:
            ws["path"] = cfg.path
        if cfg.host_header:
            ws["headers"] = {"Host": cfg.host_header}
        outbound["transport"] = ws
    elif cfg.network == "grpc":
        outbound["transport"] = {"type": "grpc", "service_name": cfg.path or ""}

    if cfg.security == "tls":
        tls: dict[str, Any] = {"enabled": True}
        if cfg.sni:
            tls["server_name"] = cfg.sni
        if cfg.fp:
            tls["utls"] = {"enabled": True, "fingerprint": cfg.fp}
        outbound["tls"] = tls
    elif cfg.security == "reality":
        if not (cfg.pbk and cfg.sid and cfg.sni):
            raise ValueError("security=reality требует pbk, sid, sni")
        outbound["tls"] = {
            "enabled": True,
            "server_name": cfg.sni,
            "utls": {"enabled": True, "fingerprint": cfg.fp or "chrome"},
            "reality": {
                "enabled": True,
                "public_key": cfg.pbk,
                "short_id": cfg.sid,
            },
        }

    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": SOCKS_HOST,
                "listen_port": SOCKS_PORT,
            }
        ],
        "outbounds": [outbound],
    }


async def _wait_for_socks(host: str, port: int, timeout: float = 10.0) -> None:
    """Ждёт, пока SOCKS5-порт начнёт принимать соединения."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            if asyncio.get_event_loop().time() >= deadline:
                raise TimeoutError(
                    f"sing-box SOCKS5 не поднялся на {host}:{port} за {timeout}s"
                ) from None
            await asyncio.sleep(0.2)


async def start_singbox(vless_url: str) -> asyncio.subprocess.Process:
    """Парсит VLESS, пишет sing-box config, запускает sing-box, ждёт SOCKS5."""
    sing_box_bin = shutil.which("sing-box")
    if not sing_box_bin:
        raise RuntimeError("sing-box не найден в PATH (должен быть установлен в образе)")

    cfg = parse_vless_url(vless_url)
    config = build_singbox_config(cfg)
    SINGBOX_CONFIG_PATH.write_text(json.dumps(config, indent=2))
    logger.info(
        "sing-box config: %s:%s via VLESS/%s/%s (name=%s)",
        cfg.host,
        cfg.port,
        cfg.network,
        cfg.security,
        cfg.name or "?",
    )

    proc = await asyncio.create_subprocess_exec(
        sing_box_bin,
        "run",
        "-c",
        str(SINGBOX_CONFIG_PATH),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def _log_stream() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            logger.info("sing-box: %s", line.decode(errors="replace").rstrip())

    asyncio.create_task(_log_stream())
    await _wait_for_socks(SOCKS_HOST, SOCKS_PORT)
    logger.info("sing-box SOCKS5 ready on %s:%s", SOCKS_HOST, SOCKS_PORT)
    return proc


async def stop_singbox(proc: asyncio.subprocess.Process) -> None:
    """Шлёт SIGTERM и ждёт завершения."""
    if proc.returncode is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (ProcessLookupError, asyncio.TimeoutError):
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass


def socks_url() -> str:
    """Возвращает SOCKS5 URL для aiohttp-socks."""
    return f"socks5://{SOCKS_HOST}:{SOCKS_PORT}"


def vless_url_from_env() -> str | None:
    """Читает VLESS_URL. Пусто/None → прокси не нужен."""
    raw = os.getenv("VLESS_URL", "").strip()
    return raw or None
