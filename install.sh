#!/bin/sh
set -eu

REPO_URL="${REPO_URL:-https://github.com/oneman-idc/idc-oneman-V5.git}"
GITHUB_PROXY="${GITHUB_PROXY:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/vps-one}"
VPS_ONE_PORT="${VPS_ONE_PORT:-9080}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${VPS_ONE_PORT}}"
USE_CN_MIRROR="${USE_CN_MIRROR:-auto}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-}"

log() { printf '\n[VPS-ONE] %s\n' "$*"; }
fail() { printf '\n[VPS-ONE] 错误：%s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" -eq 0 ] || fail "请使用 root 执行：sudo sh install.sh"

if [ "$USE_CN_MIRROR" = "auto" ]; then
  if curl -fsS --connect-timeout 3 --max-time 5 https://github.com >/dev/null 2>&1; then USE_CN_MIRROR=0; else USE_CN_MIRROR=1; fi
fi
if [ "$USE_CN_MIRROR" = "1" ]; then
  : "${PIP_INDEX_URL:=https://pypi.tuna.tsinghua.edu.cn/simple}"
  : "${DOCKER_REGISTRY:=docker.m.daocloud.io/}"
fi

install_packages() {
  if have apt-get; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -o Acquire::Retries=3
    apt-get install -y --no-install-recommends ca-certificates curl git openssl tar
  elif have dnf; then
    dnf -y install ca-certificates curl git openssl tar
  elif have yum; then
    yum -y install ca-certificates curl git openssl tar
  else
    fail "仅支持 Debian/Ubuntu/CentOS/RHEL 系 Linux"
  fi
}

install_docker() {
  have docker && docker compose version >/dev/null 2>&1 && return
  log "安装 Docker 与 Compose"
  if have apt-get; then
    apt-get update -o Acquire::Retries=3
    apt-get install -y docker.io docker-compose-plugin 2>/dev/null || apt-get install -y docker.io docker-compose
  elif have dnf; then
    dnf -y install docker docker-compose-plugin 2>/dev/null || dnf -y install docker docker-compose
  else
    yum -y install docker docker-compose-plugin 2>/dev/null || yum -y install docker docker-compose
  fi
  systemctl enable --now docker 2>/dev/null || service docker start
  if ! docker compose version >/dev/null 2>&1 && have docker-compose; then
    docker_compose() { docker-compose "$@"; }
  fi
  docker compose version >/dev/null 2>&1 || have docker-compose || fail "Docker Compose 安装失败，请检查系统软件源"
}

docker_compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi
}

prepare_source() {
  if [ -f "./docker-compose.yml" ] && [ -f "./Dockerfile" ]; then
    INSTALL_DIR=$(pwd)
    return
  fi
  mkdir -p "$INSTALL_DIR"
  if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
    url="$REPO_URL"
    [ -n "$GITHUB_PROXY" ] && url="${GITHUB_PROXY%/}/$REPO_URL"
    log "下载源码：$url"
    if ! git clone --depth=1 "$url" "$INSTALL_DIR"; then
      archive="https://github.com/oneman-idc/idc-oneman-V5/archive/refs/heads/main.tar.gz"
      [ -n "$GITHUB_PROXY" ] && archive="${GITHUB_PROXY%/}/$archive"
      log "Git 拉取失败，尝试源码压缩包"
      tmp="$(mktemp -d)"
      if curl -fL --retry 4 --connect-timeout 10 "$archive" -o "$tmp/source.tar.gz" && tar -xzf "$tmp/source.tar.gz" -C "$tmp"; then
        cp -a "$tmp"/oneman-idc-main/. "$INSTALL_DIR"/
        rm -rf "$tmp"
      else
        rm -rf "$tmp"
        fail "源码下载失败。可设置 GITHUB_PROXY=https://ghfast.top，或上传源码包后在源码目录内执行 install.sh。"
      fi
    fi
  fi
  cd "$INSTALL_DIR"
}

write_env() {
  if [ -f .env ]; then
    log "保留现有 .env，不覆盖密钥与配置"
    return
  fi
  secret=$(openssl rand -hex 32)
  master=$(openssl rand -hex 32)
  umask 077
  cat > .env <<EOF
SECRET_KEY=$secret
MASTER_KEY=$master
DATABASE_URL=sqlite+aiosqlite:////app/data/vps-one.sqlite
BASE_URL=$BASE_URL
DEBUG=false
VPS_ONE_PORT=$VPS_ONE_PORT
PYTHON_IMAGE=${DOCKER_REGISTRY}python:3.12-slim
PIP_INDEX_URL=$PIP_INDEX_URL
EOF
}

wait_ready() {
  log "等待服务健康检查"
  i=0
  while [ "$i" -lt 90 ]; do
    if curl -fsS --connect-timeout 2 "http://127.0.0.1:${VPS_ONE_PORT}/healthz" >/dev/null 2>&1; then
      printf '\n安装完成：%s/install\n后台配置：%s/admin/settings\n日志：cd %s && docker compose logs -f --tail=200\n' "$BASE_URL" "$BASE_URL" "$INSTALL_DIR"
      return
    fi
    printf '.'; i=$((i+1)); sleep 2
  done
  docker_compose logs --tail=150 || true
  fail "服务未通过健康检查"
}

install_packages
install_docker
prepare_source
write_env
log "构建并启动 VPS-ONE"
docker_compose build --pull --build-arg "PYTHON_IMAGE=${DOCKER_REGISTRY}python:3.12-slim" --build-arg "PIP_INDEX_URL=$PIP_INDEX_URL"
docker_compose up -d --remove-orphans
wait_ready
