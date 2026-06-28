#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DUAL_LOG_DIR="${DUAL_LOG_DIR:-/tmp/agentos-dual-platform}"
STATE_DIR="${STATE_DIR:-${DUAL_LOG_DIR}/agentos-state}"
OUT_DIR="${OUT_DIR:-${DUAL_LOG_DIR}/agentos-reader-live}"
RESULT_DIR="${RESULT_DIR:-${ROOT_DIR}/results/latest}"
PORT="${PORT:-8767}"

if [ ! -d "${STATE_DIR}" ]; then
	echo "[demo-reader] 找不到状态目录：${STATE_DIR}" >&2
	echo "[demo-reader] 请先运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

if [ ! -f "${STATE_DIR}/rp_agentos_mainflow" ]; then
	echo "[demo-reader] AgentOS 状态不完整，缺少：${STATE_DIR}/rp_agentos_mainflow" >&2
	echo "[demo-reader] 请重新运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

mkdir -p "${OUT_DIR}"
rm -rf "${OUT_DIR}/dual-results" "${OUT_DIR}/dual-results.html"
if [ -f "${RESULT_DIR}/monitor.html" ]; then
	mkdir -p "${OUT_DIR}/dual-results"
	cp -R "${RESULT_DIR}/." "${OUT_DIR}/dual-results/"
	cat >"${OUT_DIR}/dual-results.html" <<EOF
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 双目标结果入口</title>
  <style>
    body { font-family: Arial, "Microsoft YaHei", sans-serif; margin: 32px; color: #1f2937; line-height: 1.7; }
    a { display: inline-block; margin: 8px 12px 8px 0; color: #075985; text-decoration: none; border: 1px solid #d8dee6; padding: 8px 12px; }
  </style>
</head>
<body>
  <h1>AgentOS 双目标结果入口</h1>
  <p>这些页面来自 ${RESULT_DIR}，已复制到当前 Reader 服务目录。录屏时可以先打开运行观测面板，再返回 Reader 首页查看完整科研平台页面。</p>
  <p>
    <a href="dual-results/monitor.html">运行观测面板</a>
    <a href="dual-results/demo-guide.html">演示导览页</a>
    <a href="dual-results/index.html">图表索引页</a>
    <a href="dual-results/report.md">Markdown 报告</a>
    <a href="dual-results/summary.csv">CSV 明细</a>
  </p>
</body>
</html>
EOF
else
	echo "[demo-reader] 未找到双目标结果页：${RESULT_DIR}/monitor.html" >&2
	echo "[demo-reader] Reader 仍会启动；如需观测面板，请先运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
fi
echo "[demo-reader] 状态目录：${STATE_DIR}"
echo "[demo-reader] 输出目录：${OUT_DIR}"
echo "[demo-reader] 页面地址：http://127.0.0.1:${PORT}/"
if [ -f "${OUT_DIR}/dual-results.html" ]; then
	echo "[demo-reader] 双目标结果入口：http://127.0.0.1:${PORT}/dual-results.html"
	echo "[demo-reader] 运行观测面板：http://127.0.0.1:${PORT}/dual-results/monitor.html"
	echo "[demo-reader] 演示导览页：http://127.0.0.1:${PORT}/dual-results/demo-guide.html"
fi
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/plain_ucore_reader.py" \
	--state-dir "${STATE_DIR}" \
	--out-dir "${OUT_DIR}" \
	--host-platform-alignment "${DUAL_LOG_DIR}/host-platform-alignment.json" \
	--host-test-alignment "${DUAL_LOG_DIR}/host-test-alignment.json" \
	--host-surface-alignment "${DUAL_LOG_DIR}/host-surface-alignment.json" \
	--seeded-action-state "${DUAL_LOG_DIR}/seeded-action-state.json" \
	--serve \
	--port "${PORT}"
