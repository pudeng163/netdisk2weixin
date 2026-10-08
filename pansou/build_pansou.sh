#!/usr/bin/env bash
# PanSou 多平台交叉编译脚本
# 用法： bash build_pansou.sh
set -e

cd "$(dirname "$0")"

GO=/usr/local/go/bin/go
OUT=dist
mkdir -p "$OUT"

# 平台列表： os arch
TARGETS=(
  "linux   amd64"
  "linux   arm64"
  "windows amd64"
  "windows arm64"
  "darwin  amd64"
  "darwin  arm64"
  "android arm64"
)

build() {
  local os="$1" arch="$2"
  local name="pansou-${os}-${arch}"
  [ "$os" = "windows" ] && name="${name}.exe"

  echo ">> 编译 ${name}"
  GOOS="$os" GOARCH="$arch" GOTOOLCHAIN=local CGO_ENABLED=0 \
    "$GO" build -ldflags="-s -w" -o "${OUT}/${name}" .
}

for t in "${TARGETS[@]}"; do
  build $t
done

# 额外生成 Windows 版默认名，保持与原 start_pansou.ps1 兼容
cp "${OUT}/pansou-windows-amd64.exe" "pansou.exe" 2>/dev/null || true

echo ""
echo "✅ 编译完成，产物在 dist/："
ls -lh "${OUT}/"
