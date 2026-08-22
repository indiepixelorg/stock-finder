const DAY_MILLISECONDS = 86_400_000;

function validIsoDate(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return null;
  const [, year, month, day] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return date.toISOString().slice(0, 10) === text ? text : null;
}

export function parseDate(value) {
  if (value instanceof Date && Number.isFinite(value.valueOf())) return value.toISOString().slice(0, 10);
  const text = String(value ?? "").trim();
  if (!text) return null;
  const iso = validIsoDate(text.slice(0, 10));
  if (iso) return iso;
  let match = /^(\d{2})\/(\d{2})\/(\d{4})/.exec(text);
  if (match) return validIsoDate(`${match[3]}-${match[1]}-${match[2]}`);
  match = /^(\d{4})\/(\d{2})\/(\d{2})/.exec(text);
  return match ? validIsoDate(`${match[1]}-${match[2]}-${match[3]}`) : null;
}

export function epochDay(isoDate) {
  return Math.floor(Date.parse(`${isoDate}T00:00:00Z`) / DAY_MILLISECONDS);
}

export function daysBetween(earlier, later) {
  return epochDay(later) - epochDay(earlier);
}

export function addDays(isoDate, days) {
  return new Date((epochDay(isoDate) + days) * DAY_MILLISECONDS).toISOString().slice(0, 10);
}

export function todayLocal() {
  const now = new Date();
  const year = String(now.getFullYear()).padStart(4, "0");
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
