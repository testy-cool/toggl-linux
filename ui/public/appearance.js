// Apply the native host's validated preference before styles or React paint.
(() => {
  const theme = new URLSearchParams(location.hash.slice(1)).get("theme");
  document.documentElement.dataset.theme = theme === "grok-night" ? theme : "gruvbox";
})();
