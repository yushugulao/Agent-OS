#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DUAL_LOG_DIR="${DUAL_LOG_DIR:-/tmp/agentos-dual-platform}"
STATE_DIR="${STATE_DIR:-${DUAL_LOG_DIR}/agentos-state}"
OUT_DIR="${OUT_DIR:-${DUAL_LOG_DIR}/agentos-reader-live}"
RESULT_DIR="${RESULT_DIR:-${ROOT_DIR}/results/latest}"
PORT="${PORT:-8767}"
LLM_RELAY_MODE="${LLM_RELAY_MODE:-cloud}"
host_run_result_name="$(PYTHONPATH="${ROOT_DIR}/host_tools" "${PYTHON_BIN}" -c '
from dual_state_evidence_contract import RUN_RESULT_WORK_FILES
print(RUN_RESULT_WORK_FILES["agentos"])
')"
HOST_RUN_RESULT="${HOST_RUN_RESULT:-${DUAL_LOG_DIR}/${host_run_result_name}}"

if [ ! -d "${STATE_DIR}" ]; then
	echo "[reader] 找不到状态目录：${STATE_DIR}" >&2
	echo "[reader] 请先运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

if [ ! -f "${STATE_DIR}/rp_agentos_mainflow" ]; then
	echo "[reader] AgentOS 状态不完整，缺少：${STATE_DIR}/rp_agentos_mainflow" >&2
	echo "[reader] 请重新运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

if [ -L "${HOST_RUN_RESULT}" ] || [ ! -f "${HOST_RUN_RESULT}" ]; then
	echo "[reader] 找不到独立 Host 运行回执：${HOST_RUN_RESULT}" >&2
	echo "[reader] 请重新运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

mkdir -p "${OUT_DIR}"
rm -rf "${OUT_DIR}/dual-results" "${OUT_DIR}/dual-results.html" "${OUT_DIR}/reader-url-list.txt"
if [ -f "${RESULT_DIR}/monitor.html" ]; then
	if ! "${PYTHON_BIN}" "${ROOT_DIR}/host_tools/result_bundle_contract.py" \
		--result-dir "${RESULT_DIR}"; then
		echo "[reader] 现有结果包未通过来源与完整性校验，拒绝提供过期或伪造证据。" >&2
		echo "[reader] 请重新运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
		exit 1
	fi
	mkdir -p "${OUT_DIR}/dual-results"
	cp -R "${RESULT_DIR}/." "${OUT_DIR}/dual-results/"
	cat >"${OUT_DIR}/reader-url-list.txt" <<EOF
AgentOS 运行 URL 清单

1. 本地结果首页
   http://127.0.0.1:${PORT}/
2. 双目标结果入口
   http://127.0.0.1:${PORT}/dual-results.html
3. 运行导览页
   http://127.0.0.1:${PORT}/dual-results/reader-guide.html
4. 结果核验表
   http://127.0.0.1:${PORT}/dual-results/reader-checklist.html
5. 测试入口说明
   http://127.0.0.1:${PORT}/dual-results/test-suite.html
6. 实验场景说明
   http://127.0.0.1:${PORT}/dual-results/experiment-design.html
7. 运行观测面板
   http://127.0.0.1:${PORT}/dual-results/monitor.html
8. 图表索引页
   http://127.0.0.1:${PORT}/dual-results/index.html
9. 证据索引页
   http://127.0.0.1:${PORT}/dual-results/evidence-map.html
10. 文件查询实测统计 CSV
   http://127.0.0.1:${PORT}/dual-results/experiments/experiment-stats.csv
11. AgentOS Compare
   http://127.0.0.1:${PORT}/compare.html
12. LLM Relay
   http://127.0.0.1:${PORT}/llm.html
13. Run Detail
   http://127.0.0.1:${PORT}/run.html
14. Evidence
   http://127.0.0.1:${PORT}/evidence.html
15. Artifacts
   http://127.0.0.1:${PORT}/artifacts.html
EOF
	cat >"${OUT_DIR}/dual-results.html" <<EOF
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 运行 URL 清单</title>
  <style>
    body { font-family: Arial, "Microsoft YaHei", sans-serif; margin: 0; color: #1f2937; background: #f7f9fb; line-height: 1.7; }
    header { background: #fff; border-bottom: 1px solid #d8dee6; padding: 30px 42px 20px; }
    main { max-width: 1120px; margin: 0 auto; padding: 24px 42px 42px; }
    h1 { margin: 0 0 10px; font-size: 28px; }
    h2 { margin-top: 26px; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    a { display: block; color: #075985; text-decoration: none; border: 1px solid #d8dee6; background: #fff; padding: 10px 12px; }
    .primary a { border-color: #94a3b8; }
    .hint { color: #52616f; }
    @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } main, header { padding-left: 18px; padding-right: 18px; } }
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 运行 URL 清单</h1>
    <p class="hint">这些页面来自 ${RESULT_DIR}，已复制到当前 本地结果服务目录。建议先打开运行导览页，再进入完整科研平台页面。</p>
  </header>
  <main>
    <h2>建议查看顺序</h2>
    <div class="grid primary">
      <a href="dual-results/reader-guide.html">1. 运行导览页</a>
      <a href="dual-results/reader-checklist.html">2. 结果核验表</a>
      <a href="dual-results/test-suite.html">3. 测试入口说明</a>
      <a href="dual-results/experiment-design.html">4. 实验场景说明</a>
      <a href="dual-results/monitor.html">5. 运行观测面板</a>
      <a href="dual-results/index.html">6. 图表索引页</a>
      <a href="dual-results/evidence-map.html">7. 证据索引页</a>
      <a href="dual-results/experiments/experiment-stats.csv">8. 文件查询实测统计 CSV</a>
      <a href="compare.html">9. AgentOS Compare</a>
      <a href="llm.html">10. LLM Relay</a>
    </div>
    <h2>科研平台页面</h2>
    <div class="grid">
      <a href="index.html">本地结果首页</a>
      <a href="run.html">Run Detail</a>
      <a href="evidence.html">Evidence</a>
      <a href="artifacts.html">Artifacts</a>
      <a href="delivery.html">Delivery</a>
      <a href="provenance.html">Provenance</a>
    </div>
    <h2>可下载结果</h2>
    <div class="grid">
      <a href="dual-results/report.md">Markdown 报告</a>
      <a href="dual-results/summary.csv">CSV 明细</a>
      <a href="dual-results/runner-sweep.csv">Runner 成组数据</a>
      <a href="dual-results/experiments/experiment-stats.csv">文件查询实测统计 CSV</a>
      <a href="dual-results/experiments/mechanism-notes.csv">机制说明 CSV</a>
      <a href="reader-url-list.txt">纯文本 URL 清单</a>
    </div>
  </main>
</body>
</html>
EOF
else
	echo "[reader] 未找到双目标结果页：${RESULT_DIR}/monitor.html" >&2
	echo "[reader] 本地结果服务仍会启动；如需观测面板，请先运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
fi
echo "[reader] 状态目录：${STATE_DIR}"
echo "[reader] 输出目录：${OUT_DIR}"
echo "[reader] 页面地址：http://127.0.0.1:${PORT}/"
echo "[reader] Host LLM Relay 自动模式：${LLM_RELAY_MODE}"
if [ -f "${OUT_DIR}/dual-results.html" ]; then
	echo "[reader] 双目标结果入口：http://127.0.0.1:${PORT}/dual-results.html"
	echo "[reader] 运行导览页：http://127.0.0.1:${PORT}/dual-results/reader-guide.html"
	echo "[reader] 结果核验表：http://127.0.0.1:${PORT}/dual-results/reader-checklist.html"
	echo "[reader] 测试入口说明：http://127.0.0.1:${PORT}/dual-results/test-suite.html"
	echo "[reader] 实验场景说明：http://127.0.0.1:${PORT}/dual-results/experiment-design.html"
	echo "[reader] 运行观测面板：http://127.0.0.1:${PORT}/dual-results/monitor.html"
	echo "[reader] 图表索引页：http://127.0.0.1:${PORT}/dual-results/index.html"
	echo "[reader] 证据索引页：http://127.0.0.1:${PORT}/dual-results/evidence-map.html"
	echo "[reader] 纯文本 URL 清单：http://127.0.0.1:${PORT}/reader-url-list.txt"
fi
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/plain_ucore_reader.py" \
	--state-dir "${STATE_DIR}" \
	--out-dir "${OUT_DIR}" \
	--host-platform-alignment "${DUAL_LOG_DIR}/host-platform-alignment.json" \
	--host-test-alignment "${DUAL_LOG_DIR}/host-test-alignment.json" \
	--host-surface-alignment "${DUAL_LOG_DIR}/host-surface-alignment.json" \
	--seeded-action-state "${DUAL_LOG_DIR}/seeded-action-state.json" \
	--host-run-result "${HOST_RUN_RESULT}" \
	--expected-target agentos \
	--serve \
	--auto-run-llm-relay \
	--llm-relay-mode "${LLM_RELAY_MODE}" \
	--port "${PORT}"
