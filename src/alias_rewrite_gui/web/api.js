export function waitForPywebview() {
  return new Promise((resolve) => {
    if (window.pywebview?.api) {
      resolve(true);
      return;
    }
    let resolved = false;
    const finish = (value) => {
      if (resolved) return;
      resolved = true;
      resolve(value);
    };
    window.addEventListener("pywebviewready", () => finish(true), { once: true });
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (window.pywebview?.api) {
        window.clearInterval(timer);
        finish(true);
      } else if (Date.now() - started > 10000) {
        window.clearInterval(timer);
        finish(false);
      }
    }, 100);
  });
}

export async function callApi(name, payload = {}) {
  if (!(await waitForPywebview())) {
    throw new Error("pywebview API is not available");
  }
  const fn = window.pywebview.api[name];
  if (!fn) {
    throw new Error(`pywebview API method is missing: ${name}`);
  }
  return await fn(payload);
}

export async function chooseDirectory(kind) {
  if (!(await waitForPywebview())) return "";
  return await window.pywebview.api.choose_directory(kind);
}

export async function chooseFile(kind) {
  if (!(await waitForPywebview())) return "";
  return await window.pywebview.api.choose_file(kind);
}

function currentViewportSize() {
  const visualViewport = window.visualViewport;
  const visualWidth = Number(visualViewport?.width) || 0;
  const visualHeight = Number(visualViewport?.height) || 0;
  return {
    width: Math.max(1, Math.round(visualWidth || window.innerWidth || document.documentElement.clientWidth || 0)),
    height: Math.max(1, Math.round(visualHeight || window.innerHeight || document.documentElement.clientHeight || 0)),
  };
}

export async function expandWindow(minHeight, minWidth = 940) {
  if (!(await waitForPywebview())) return false;
  const { width: currentWidth, height: currentHeight } = currentViewportSize();
  return await window.pywebview.api.expand_window(currentWidth, currentHeight, minHeight, minWidth);
}

export async function syncMinWindowSize(size, resizeToMinHeight = false, resizeToMinWidth = false) {
  if (!(await waitForPywebview())) return false;
  const { width: currentWidth, height: currentHeight } = currentViewportSize();
  return await window.pywebview.api.sync_min_window_size(
    size.width,
    size.height,
    currentWidth,
    currentHeight,
    resizeToMinHeight,
    resizeToMinWidth,
  );
}

export async function syncMainWindowGeometry(geometry) {
  if (!(await waitForPywebview())) return false;
  const viewport = currentViewportSize();
  return await window.pywebview.api.sync_main_window_geometry({
    ...geometry,
    current_width: viewport.width,
    current_height: viewport.height,
  });
}
