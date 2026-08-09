/* Shared, side-effect-free frontend primitives. Keep all rendered API data escaped. */
const esc = value => String(value ?? "").replace(
  /[&<>"']/g,
  character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character],
);

const money = value => new Intl.NumberFormat("bs-BA", {
  style: "currency",
  currency: "EUR",
}).format(value || 0);

const sourceLinks = values => (values || []).flatMap((value, index) => {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol)
      ? [`<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer" style="color:var(--green)">source ${index + 1} ↗</a>`]
      : [];
  } catch {
    return [];
  }
}).join(" · ");

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw;
    try {
      const parsed = JSON.parse(raw);
      const detail = parsed.detail;
      message = typeof detail === "string"
        ? detail
        : detail?.message
          ? `${detail.message}: ${(detail.blockers || []).join("; ")}`
          : raw;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
