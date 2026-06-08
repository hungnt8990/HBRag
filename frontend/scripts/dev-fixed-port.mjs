import { spawn } from "node:child_process";
import net from "node:net";

const PORT = 3000;

function canConnect(host) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host, port: PORT });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", (error) => {
      socket.destroy();
      if (error.code === "ECONNREFUSED") {
        resolve(false);
        return;
      }
      reject(error);
    });
  });
}

async function assertPortAvailable() {
  if ((await canConnect("127.0.0.1")) || (await canConnect("::1"))) {
    throw new Error(`Port ${PORT} is already in use. Stop the existing dev server first.`);
  }

  return new Promise((resolve, reject) => {
    const server = net.createServer();

    server.once("error", (error) => {
      if (error.code === "EADDRINUSE") {
        reject(new Error(`Port ${PORT} is already in use. Stop the existing dev server first.`));
        return;
      }
      reject(error);
    });

    server.once("listening", () => {
      server.close(resolve);
    });

    server.listen(PORT);
  });
}

try {
  await assertPortAvailable();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}

const next = spawn("next", ["dev", "--port", String(PORT)], {
  stdio: "inherit",
  shell: process.platform === "win32",
});

next.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
