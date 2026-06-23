// SPDX-License-Identifier: Apache-2.0

import {createServer as createViteServer} from "vite";
import {createLiveSource} from "./live-source.mjs";
import {createGatewayServer} from "./server.mjs";

const gatewayPort = Number(process.env.HOST_GATEWAY_PORT || 8787);
const dashboardPort = Number(process.env.HOST_DASHBOARD_PORT || 5173);
const mode = process.env.HOST_GATEWAY_MODE || "replay";
const liveSource = mode === "live" ? createLiveSource().start() : null;
const gateway = await createGatewayServer({
  port: gatewayPort,
  liveSource,
});
await gateway.listen();

const summary = gateway.source.getSummary();
console.log("host:gateway mode=%s url=http://127.0.0.1:%d events=%d final=%s llm=%s",
            gateway.source.mode, gatewayPort, summary.totalEvents,
            summary.finalStatus || "none", summary.llmMode || "pending");

process.env.VITE_GATEWAY_URL = `http://127.0.0.1:${gatewayPort}`;
const vite = await createViteServer({
  configFile: "host/dashboard/vite.config.mjs",
  server: {
    host: "127.0.0.1",
    port: dashboardPort,
  },
});
await vite.listen();

console.log("host:dashboard url=http://127.0.0.1:%d", dashboardPort);

function shutdown() {
  Promise.all([
    vite.close(),
    gateway.close(),
  ]).finally(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
