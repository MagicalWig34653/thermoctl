// Ausgabe der Bridge. Auf stdout steht ausschliesslich der Kopplungscode (einmalig); alles andere,
// auch die Meldungen von Matter.js, geht nach stderr.
export const warning = message => process.stderr.write(`${message}\n`);
export const info = message => process.stderr.write(`${message}\n`);

// Matter.js schreibt standardmaessig ueber console.info/debug nach stdout. Das Ziel der
// Standard-Logsenke wird auf stderr umgebogen.
export function routeMatterLogsToStderr(Logger) {
    Logger.destinations.default.write = text => { process.stderr.write(`${text}\n`); };
}
