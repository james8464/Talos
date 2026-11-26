/** The overlay document. It stays inline so the overlay module needs no
 * bundler HTML loader and its tests need no plugin. */
export const OVERLAY_HTML = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Talos highlight</title>
    <style>
      html,
      body {
        margin: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: transparent;
        user-select: none;
        -webkit-user-select: none;
      }
      #ring {
        position: absolute;
        border: 3px solid #5eead4;
        border-radius: 10px;
        box-shadow:
          0 0 0 2px rgba(0, 0, 0, 0.35),
          0 0 18px rgba(94, 234, 212, 0.8);
        box-sizing: border-box;
        pointer-events: none;
      }
      #label {
        position: absolute;
        max-width: 320px;
        padding: 4px 8px;
        border-radius: 6px;
        background: #0f172a;
        color: #e2e8f0;
        font:
          12px/1.4 -apple-system,
          BlinkMacSystemFont,
          "Segoe UI",
          sans-serif;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        pointer-events: none;
      }
    </style>
  </head>
  <body>
    <div id="ring"></div>
    <div id="label"></div>
    <script>
      function draw() {
        var ring = document.getElementById("ring");
        var label = document.getElementById("label");
        var target;
        try {
          target = JSON.parse(
            decodeURIComponent(window.location.hash.slice(1)),
          );
        } catch {
          return;
        }
        ring.style.left = target.x + "px";
        ring.style.top = target.y + "px";
        ring.style.width = Math.max(1, target.width) + "px";
        ring.style.height = Math.max(1, target.height) + "px";
        var text = typeof target.label === "string" ? target.label : "";
        label.textContent = text;
        label.style.display = text ? "block" : "none";
        label.style.left = target.x + "px";
        label.style.top = Math.max(0, target.y - 26) + "px";
      }
      draw();
      window.addEventListener("hashchange", draw);
    </script>
  </body>
</html>
`;
