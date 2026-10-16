// Begrenzt gleichzeitige Abrufe (viele Verlaufskarten auf einer Seite laden nicht alle auf einmal).

export function createLimiter(max: number) {
  let active = 0;
  const waiting: (() => void)[] = [];
  // Der freiwerdende Platz wird direkt an den nächsten Wartenden übergeben.
  const release = () => {
    const next = waiting.shift();
    if (next) next();
    else active -= 1;
  };
  return {
    async run<T>(task: () => Promise<T>): Promise<T> {
      if (active >= max) await new Promise<void>((resolve) => waiting.push(resolve));
      else active += 1;
      try {
        return await task();
      } finally {
        release();
      }
    },
    get active() {
      return active;
    },
    get waiting() {
      return waiting.length;
    },
  };
}
