// SPDX-License-Identifier: Apache-2.0

import {createServer} from "node:http";
import {buildReplayEvents, defaultFixtures} from "./replay-data.mjs";

function sendJson(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": "no-store",
  });
  res.end(data);
}

function eventView(entry) {
  return {
    line: entry.lineNumber,
    type: entry.type,
    known: entry.known,
    fields: entry.fields,
    raw: entry.raw,
  };
}

function createReplaySource(replay) {
  return {
    mode: "replay",
    getFiles() {
      return replay.files.map((item) => ({
        name: item.name,
        summary: item.summary,
      }));
    },
    getEvents() {
      return replay.events;
    },
    getSummary() {
      return {
        ...replay.summary,
        mode: "replay",
        status: "ready",
      };
    },
    isDone() {
      return true;
    },
    subscribe() {
      return () => {};
    },
    close() {
      return Promise.resolve();
    },
  };
}

export async function createGatewayServer(options = {}) {
  const port = Number(options.port || process.env.HOST_GATEWAY_PORT || 8787);
  const source = options.liveSource || createReplaySource(
    await buildReplayEvents(options.files || defaultFixtures,
                            options.llmConfig || {},
                            options.fetchImpl || globalThis.fetch));

  const server = createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (req.method === "OPTIONS") {
      res.writeHead(204, {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "GET, OPTIONS",
        "access-control-allow-headers": "content-type",
      });
      res.end();
      return;
    }

    if (url.pathname === "/health") {
      const summary = source.getSummary();
      sendJson(res, 200, {
        ok: true,
        mode: source.mode,
        status: summary.status,
        events: summary.totalEvents,
        final: summary.finalStatus,
        llm: summary.llmMode,
      });
      return;
    }

    if (url.pathname === "/api/replay") {
      const summary = source.getSummary();
      sendJson(res, 200, {
        mode: source.mode,
        files: source.getFiles(),
        summary,
        events: source.getEvents().map(eventView),
      });
      return;
    }

    if (url.pathname === "/events") {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-store",
        "connection": "keep-alive",
        "access-control-allow-origin": "*",
      });

      let index = 0;
      const intervalMs = Number(url.searchParams.get("interval") || 180);
      const events = source.getEvents();
      const flushAvailable = () => {
        while (index < events.length) {
          res.write(`data: ${JSON.stringify(eventView(events[index]))}\n\n`);
          index++;
        }
      };
      const timer = setInterval(() => {
        flushAvailable();
        if (index >= events.length) {
          if (!source.isDone())
            return;
          res.write("event: done\ndata: {}\n\n");
          clearInterval(timer);
          res.end();
          return;
        }
      }, intervalMs);

      const unsubscribe = source.subscribe((message) => {
        if (message.kind === "event")
          return;
        if (message.kind === "done") {
          clearInterval(timer);
          flushAvailable();
          res.write("event: done\ndata: {}\n\n");
          res.end();
        }
      });

      req.on("close", () => {
        clearInterval(timer);
        unsubscribe();
      });
      return;
    }

    sendJson(res, 404, {error: "not_found"});
  });

  return {
    port,
    server,
    listen() {
      return new Promise((resolve) => {
        server.listen(port, "127.0.0.1", () => resolve(this));
      });
    },
    source,
    close() {
      return Promise.all([
        new Promise((resolve, reject) => {
          server.close((error) => error ? reject(error) : resolve());
        }),
        source.close(),
      ]);
    },
  };
}
