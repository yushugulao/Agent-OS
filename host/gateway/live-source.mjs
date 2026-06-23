// SPDX-License-Identifier: Apache-2.0

import {spawn} from "node:child_process";
import {EventEmitter} from "node:events";
import {generateLlmAnalysis} from "./llm.mjs";
import {parseAgentOsLine, summarizeParsedEntries} from "./parser.mjs";

function parseBool(value, fallback) {
  if (value === undefined || value === "")
    return fallback;
  return !["0", "false", "no", "off"].includes(String(value).toLowerCase());
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

function windowsPathToWsl(path) {
  const match = String(path).match(/^([A-Za-z]):\\(.*)$/);
  if (!match)
    return path;
  const drive = match[1].toLowerCase();
  const rest = match[2].replace(/\\/g, "/");
  return `/mnt/${drive}/${rest}`;
}

function defaultLiveCommand(env) {
  if (env.HOST_LIVE_COMMAND)
    return env.HOST_LIVE_COMMAND;
  if (process.platform === "win32") {
    const cwd = env.HOST_LIVE_CWD || process.cwd();
    return `wsl -e bash -lc "cd ${shellQuote(windowsPathToWsl(cwd))} && make qemu"`;
  }
  return "make qemu";
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

export function liveConfigFromEnv(env = process.env) {
  return {
    command: defaultLiveCommand(env),
    cwd: env.HOST_LIVE_CWD || process.cwd(),
    bootWaitMs: Number(env.HOST_LIVE_BOOT_WAIT_MS || 8000),
    commandGapMs: Number(env.HOST_LIVE_COMMAND_GAP_MS || 250),
    timeoutMs: Number(env.HOST_LIVE_TIMEOUT_MS || 120000),
    runBench: parseBool(env.HOST_LIVE_RUN_BENCH, true),
    autoExitQemu: parseBool(env.HOST_LIVE_AUTO_EXIT_QEMU, true),
  };
}

export function createLiveSource(options = {}) {
  const config = {
    ...liveConfigFromEnv(),
    ...options,
  };
  const emitter = new EventEmitter();
  const entries = [];
  const events = [];
  let child = null;
  let lineNumber = 0;
  let done = false;
  let status = "idle";
  let errorMessage = "";
  let timeout = null;
  let stdoutBuffer = "";
  let stderrBuffer = "";
  let commandIndex = 0;
  let llmEvent = null;
  const commands = ["labdemo", ...(config.runBench ? ["labbench"] : [])];

  function emit(kind) {
    emitter.emit("message", {kind});
  }

  function addEntry(entry) {
    entries.push(entry);
    if (entry.kind === "event") {
      events.push(entry);
      emit("event");
    }
  }

  function handleLine(line) {
    const text = line.replace(/\r$/, "");
    if (!text)
      return;
    lineNumber++;
    const entry = parseAgentOsLine(text, lineNumber);
    addEntry(entry);
    if (text.includes("labdemo: passed") && commandIndex < commands.length)
      scheduleNextCommand();
    if (text.includes("labbench: passed"))
      finish("completed");
    if (!config.runBench && text.includes("labdemo: passed"))
      finish("completed");
  }

  function consumeChunk(name, chunk) {
    let buffer = name === "stderr" ? stderrBuffer : stdoutBuffer;
    buffer += chunk.toString("utf8");
    const lines = buffer.split(/\n/);
    buffer = lines.pop() || "";
    for (const line of lines)
      handleLine(line);
    if (name === "stdout" && commandIndex === 0 &&
        (buffer.includes("$ ") || buffer.includes("init: starting sh")))
      scheduleNextCommand();
    if (name === "stderr")
      stderrBuffer = buffer;
    else
      stdoutBuffer = buffer;
  }

  function writeToQemu(text) {
    if (!child || !child.stdin || child.stdin.destroyed)
      return;
    child.stdin.write(text);
  }

  function scheduleNextCommand() {
    const command = commands[commandIndex++];
    if (!command)
      return;
    setTimeout(() => writeToQemu(`${command}\n`), config.commandGapMs);
  }

  async function finish(reason) {
    if (done)
      return;
    done = true;
    status = reason === "completed" ? "done" : reason;
    if (timeout)
      clearTimeout(timeout);
    try {
      llmEvent = await generateLlmAnalysis(entries, config.llmConfig || {},
                                           config.fetchImpl || globalThis.fetch);
      events.push(llmEvent);
    } catch (error) {
      errorMessage = error.message;
      status = "error";
    }
    if (config.autoExitQemu)
      writeToQemu("\x01x");
    emit("done");
  }

  return {
    mode: "live",
    config,
    start() {
      if (child)
        return this;
      status = "running";
      child = spawn(config.command, {
        cwd: config.cwd,
        env: process.env,
        shell: true,
        stdio: ["pipe", "pipe", "pipe"],
      });
      child.stdout.on("data", (chunk) => consumeChunk("stdout", chunk));
      child.stderr.on("data", (chunk) => consumeChunk("stderr", chunk));
      child.on("error", (error) => {
        errorMessage = error.message;
        finish("error");
      });
      child.on("exit", (code, signal) => {
        if (!done) {
          errorMessage = `qemu exited before completion code=${code} signal=${signal}`;
          finish("error");
        }
      });
      setTimeout(() => scheduleNextCommand(), config.bootWaitMs);
      timeout = setTimeout(() => {
        errorMessage = `live timeout after ${config.timeoutMs}ms`;
        finish("timeout");
      }, config.timeoutMs);
      return this;
    },
    getFiles() {
      return [{
        name: "qemu-live",
        summary: summarizeParsedEntries(entries),
      }];
    },
    getEvents() {
      return events;
    },
    getSummary() {
      const finalEvent = events.findLast((entry) => entry.type === "FINAL");
      return {
        mode: "live",
        status,
        error: errorMessage || null,
        totalEvents: events.length,
        parsedEvents: events.filter((entry) => entry.type !== "LLM_ANALYSIS").length,
        finalStatus: finalEvent ? finalEvent.fields.status : null,
        llmMode: llmEvent ? llmEvent.fields.mode : null,
      };
    },
    isDone() {
      return done;
    },
    subscribe(callback) {
      emitter.on("message", callback);
      return () => emitter.off("message", callback);
    },
    waitUntilDone() {
      if (done)
        return Promise.resolve(this.getSummary());
      return new Promise((resolve) => {
        const unsubscribe = this.subscribe((message) => {
          if (message.kind === "done") {
            unsubscribe();
            resolve(this.getSummary());
          }
        });
      });
    },
    close() {
      if (timeout)
        clearTimeout(timeout);
      if (child && !child.killed) {
        if (config.autoExitQemu)
          writeToQemu("\x01x");
        child.kill();
      }
      return Promise.resolve();
    },
    eventView,
  };
}
