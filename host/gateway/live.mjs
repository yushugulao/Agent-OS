// SPDX-License-Identifier: Apache-2.0

import {createServer as createViteServer} from "vite";
import {createLiveSource} from "./live-source.mjs";
import {createGatewayServer} from "./server.mjs";

const gatewayPort = Number(process.env.HOST_GATEWAY_PORT || 8787);
const dashboardPort = Number(process.env.HOST_DASHBOARD_PORT || 5173);
const withDashboard = process.env.HOST_LIVE_DASHBOARD !== "0";
const keepOpen = withDashboard && process.env.HOST_LIVE_KEEP_OPEN !== "0";
const liveSource = createLiveSource().start();
const gateway = await createGatewayServer({
  port: gatewayPort,
  liveSource,
});
await gateway.listen();

console.log("host:live gateway=http://127.0.0.1:%d command=\"%s\" bench=%s",
            gatewayPort, liveSource.config.command,
            liveSource.config.runBench ? "on" : "off");

let vite = null;
if (withDashboard) {
  process.env.VITE_GATEWAY_URL = `http://127.0.0.1:${gatewayPort}`;
  vite = await createViteServer({
    configFile: "host/dashboard/vite.config.mjs",
    server: {
      host: "127.0.0.1",
      port: dashboardPort,
    },
  });
  await vite.listen();
  console.log("host:live dashboard=http://127.0.0.1:%d", dashboardPort);
}

function shutdown(code = 0) {
  Promise.all([
    vite ? vite.close() : Promise.resolve(),
    gateway.close(),
  ]).finally(() => process.exit(code));
}

process.on("SIGINT", () => shutdown(130));
process.on("SIGTERM", () => shutdown(143));

const summary = await liveSource.waitUntilDone();
console.log("host:live done status=%s events=%d final=%s llm=%s",
            summary.status, summary.totalEvents,
            summary.finalStatus || "none", summary.llmMode || "none");

if (summary.status !== "done" || summary.finalStatus !== "RECOVERED") {
  if (summary.error)
    console.error("host:live error=%s", summary.error);
  shutdown(1);
} else {
  if (keepOpen) {
    console.log("host:live keep-open=on, dashboard remains available. Press Ctrl+C to stop.");
  } else {
    shutdown(0);
  }
}
