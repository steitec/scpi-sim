// Loads Pyodide, the scpi-sim wheel and web/py/demo.py, and wraps the Demo object.
// Everything comes from this site: ./pyodide/ and ./py/ (see web/build_site.py).

const base = new URL(".", import.meta.url);

async function fetchOk(path) {
  const response = await fetch(new URL(path, base));
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response;
}

export async function loadSim(onStatus) {
  if (typeof WebAssembly !== "object") throw new Error("this browser has no WebAssembly");

  onStatus("loading the Python runtime…");
  const { loadPyodide } = await import("./pyodide/pyodide.mjs");
  const pyodide = await loadPyodide({ indexURL: new URL("pyodide/", base).href });

  onStatus("loading scpi-sim…");
  await pyodide.loadPackage("pyyaml", { messageCallback: () => {} });
  const manifest = await (await fetchOk("py/manifest.json")).json();
  await pyodide.loadPackage(new URL(`py/${manifest.wheel}`, base).href, { messageCallback: () => {} });
  for (const name of ["demo.py", "bench.yaml"]) {
    const data = new Uint8Array(await (await fetchOk(`py/${name}`)).arrayBuffer());
    pyodide.FS.writeFile(name, data);
  }

  onStatus("wiring the bench…");
  const demo = pyodide.runPython('import sys; sys.path.insert(0, "."); from demo import Demo; Demo("bench.yaml")');

  // Python dict -> plain JS object; the proxy is freed right away
  const js = (proxy) => {
    const value = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    return value;
  };
  return {
    version: manifest.version,
    send: async (target, line) => js(demo.send(target, line)),
    inject: async (fault) => js(demo.inject(fault)),
    reset: async () => js(demo.reset()),
    state: () => js(demo.state()),
  };
}
