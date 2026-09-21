/* BarberHub — a small client for the v1 API.
 *
 * No framework and no build step on purpose: this is a Python repository, and
 * a node_modules tree would cost more than it buys for six screens. Hash
 * routing, one render function per view, and a fetch wrapper that refreshes
 * tokens is the whole architecture.
 */

const API = "/api/v1";

/* ------------------------------------------------------------------ state */

const store = {
  get access() { return localStorage.getItem("bh.access"); },
  get refresh() { return localStorage.getItem("bh.refresh"); },
  get user() {
    try { return JSON.parse(localStorage.getItem("bh.user") || "null"); }
    catch { return null; }
  },
  signIn({ access_token, refresh_token, user }) {
    localStorage.setItem("bh.access", access_token);
    localStorage.setItem("bh.refresh", refresh_token);
    localStorage.setItem("bh.user", JSON.stringify(user));
  },
  signOut() {
    ["bh.access", "bh.refresh", "bh.user"].forEach((k) => localStorage.removeItem(k));
  },
};

/* -------------------------------------------------------------- transport */

class ApiError extends Error {
  constructor(status, payload) {
    const body = payload?.error;
    super(body?.message || "Something went wrong");
    this.status = status;
    this.code = body?.code || "UNKNOWN";
    this.details = body?.details || {};
  }
}

async function request(path, { method = "GET", body, auth = false, retry = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && store.access) headers.Authorization = `Bearer ${store.access}`;

  const response = await fetch(API + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  // One transparent refresh attempt. A second 401 means the refresh chain is
  // gone too, so the session is genuinely over.
  if (response.status === 401 && auth && retry && store.refresh) {
    if (await refreshSession()) {
      return request(path, { method, body, auth, retry: false });
    }
    store.signOut();
  }

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

async function refreshSession() {
  try {
    const tokens = await request("/auth/refresh", {
      method: "POST",
      body: { refresh_token: store.refresh },
    });
    localStorage.setItem("bh.access", tokens.access_token);
    localStorage.setItem("bh.refresh", tokens.refresh_token);
    return true;
  } catch {
    return false;
  }
}

/* ----------------------------------------------------------------- helpers */

const el = (html) => {
  const wrap = document.createElement("div");
  wrap.innerHTML = html.trim();
  return wrap;
};

const escape = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const money = (value) =>
  `${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })} ₸`;

const initials = (first = "", last = "") =>
  `${first[0] || ""}${last[0] || ""}`.toUpperCase() || "?";

const dayLabel = (date) => {
  const today = new Date();
  const diff = Math.round((date - stripTime(today)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
};

const stripTime = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const when = (iso, timezone) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
    ...(timezone ? { timeZone: timezone } : {}),
  });

const view = document.getElementById("view");

function render(html) {
  view.innerHTML = html;
  window.scrollTo(0, 0);
}

function banner(message, kind = "error") {
  return `<div class="banner banner--${kind}">${escape(message)}</div>`;
}

function skeletons(count = 3) {
  return `<div class="stack">${'<div class="skeleton"></div>'.repeat(count)}</div>`;
}

function empty(icon, title, note = "") {
  return `<div class="empty">
    <div class="empty__icon" aria-hidden="true">${icon}</div>
    <div class="empty__title">${escape(title)}</div>
    ${note ? `<div>${escape(note)}</div>` : ""}
  </div>`;
}

function chevron() {
  return `<span class="row-link__chevron" aria-hidden="true">›</span>`;
}

/* -------------------------------------------------------------------- nav */

function paintChrome() {
  const route = location.hash || "#/";
  document.querySelectorAll(".tabbar__item").forEach((tab) => {
    const active = route === tab.dataset.route || (tab.dataset.route === "#/" && route.startsWith("#/shop"));
    tab.setAttribute("aria-current", active ? "page" : "false");
  });

  const account = document.getElementById("account");
  const user = store.user;
  account.innerHTML = user
    ? `<button class="btn btn--ghost" data-go="#/account">${escape(user.first_name)}</button>`
    : `<button class="btn btn--ghost" data-go="#/login">Sign in</button>`;
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest(".tabbar__item");
  if (tab) location.hash = tab.dataset.route;

  const go = event.target.closest("[data-go]");
  if (go) location.hash = go.dataset.go;
});

/* ------------------------------------------------------------------ views */

async function shopsView() {
  render(`
    <h1 class="page-title">Find a barber</h1>
    <p class="page-sub">Real availability, booked in two taps.</p>
    <form class="search" id="search">
      <input class="input" name="q" placeholder="Shop name or city" autocomplete="off" />
      <button class="btn btn--primary" type="submit">Search</button>
    </form>
    <div id="results">${skeletons()}</div>
  `);

  const results = document.getElementById("results");
  const load = async (q = "") => {
    results.innerHTML = skeletons();
    try {
      const params = new URLSearchParams({ limit: "20", sort: "-rating" });
      if (q) params.set("q", q);
      const page = await request(`/barbershops?${params}`);
      results.innerHTML = page.items.length
        ? page.items.map(shopRow).join("")
        : empty("🔍", "Nothing found", "Try a different name or city.");
    } catch (error) {
      results.innerHTML = banner(error.message);
    }
  };

  document.getElementById("search").addEventListener("submit", (event) => {
    event.preventDefault();
    load(new FormData(event.target).get("q").trim());
  });

  load();
}

function shopRow(shop) {
  const rating = shop.reviews_count
    ? `<span class="rating">★ <strong>${Number(shop.rating).toFixed(1)}</strong> · ${shop.reviews_count} reviews</span>`
    : `<span class="rating">New</span>`;
  return `<button class="row-link" data-go="#/shop/${shop.id}">
    <span class="avatar" aria-hidden="true">${escape(shop.name[0])}</span>
    <span class="row-link__main">
      <span class="row-link__title">${escape(shop.name)}</span>
      <span class="row-link__meta">${escape(shop.city)} · ${rating}</span>
    </span>
    ${chevron()}
  </button>`;
}

async function shopView(shopId) {
  render(skeletons(4));
  let shop, barbers, services;
  try {
    [shop, barbers, services] = await Promise.all([
      request(`/barbershops/${shopId}`),
      request(`/barbershops/${shopId}/barbers`),
      request(`/barbershops/${shopId}/services`),
    ]);
  } catch (error) {
    return render(banner(error.message));
  }

  const closures = await request(`/barbershops/${shopId}/closures?upcoming_only=true`).catch(() => []);

  render(`
    <h1 class="page-title">${escape(shop.name)}</h1>
    <p class="page-sub">${escape(shop.address)}, ${escape(shop.city)}</p>
    ${shop.description ? `<div class="card"><div class="card__body">${escape(shop.description)}</div></div>` : ""}
    ${closures.length ? banner(`Closed ${closures.map((c) => c.start_date === c.end_date ? c.start_date : `${c.start_date} – ${c.end_date}`).join(", ")}`, "info") : ""}

    <div class="section-label">Choose a service</div>
    <div class="pills" id="services" role="group">
      ${services.map((s) => `
        <button class="pill" type="button" aria-pressed="false"
                data-service="${s.id}" data-duration="${s.duration_minutes}" data-price="${s.price}">
          ${escape(s.name)} · ${money(s.price)}
        </button>`).join("")}
    </div>

    <div class="section-label">Choose a barber</div>
    <div id="barbers" class="stack">
      ${barbers.map(barberRow).join("") || empty("💈", "No barbers yet")}
    </div>

    <div id="schedule"></div>
  `);

  wireBooking({ shop, barbers, services });
}

function barberRow(barber) {
  const rating = barber.reviews_count
    ? `★ ${Number(barber.rating).toFixed(1)} · ${barber.reviews_count} reviews`
    : "New";
  return `<button class="row-link" type="button" data-barber="${barber.id}" aria-pressed="false">
    <span class="avatar" aria-hidden="true">${escape(initials(barber.first_name, barber.last_name))}</span>
    <span class="row-link__main">
      <span class="row-link__title">${escape(barber.first_name)} ${escape(barber.last_name)}</span>
      <span class="row-link__meta">${escape(rating)}${barber.experience_years ? ` · ${barber.experience_years} yrs` : ""}</span>
    </span>
  </button>`;
}

/* The booking flow lives in one place because each choice narrows the next:
   service sets the slot width, barber and date fetch the slots. Keeping the
   selection in one object avoids three sources of truth disagreeing. */
function wireBooking({ shop, barbers, services }) {
  const choice = { service: null, barber: null, date: stripTime(new Date()), slot: null };

  const schedule = document.getElementById("schedule");

  document.getElementById("services").addEventListener("click", (event) => {
    const pill = event.target.closest("[data-service]");
    if (!pill) return;
    choice.service = { id: pill.dataset.service, duration: pill.dataset.duration, price: pill.dataset.price, name: pill.textContent.trim() };
    document.querySelectorAll("[data-service]").forEach((p) =>
      p.setAttribute("aria-pressed", String(p === pill))
    );
    choice.slot = null;
    refresh();
  });

  document.getElementById("barbers").addEventListener("click", (event) => {
    const row = event.target.closest("[data-barber]");
    if (!row) return;
    choice.barber = barbers.find((b) => b.id === row.dataset.barber);
    document.querySelectorAll("[data-barber]").forEach((r) =>
      r.setAttribute("aria-pressed", String(r === row))
    );
    choice.slot = null;
    refresh();
  });

  async function refresh() {
    if (!choice.service || !choice.barber) {
      schedule.innerHTML = "";
      paintActionBar(null);
      return;
    }

    const days = Array.from({ length: 7 }, (_, i) => {
      const d = new Date(choice.date);
      d.setDate(stripTime(new Date()).getDate() + i);
      return d;
    });

    schedule.innerHTML = `
      <div class="section-label">Pick a time</div>
      <div class="pills" id="days">
        ${days.map((d) => `
          <button class="pill" type="button" data-date="${isoDate(d)}"
                  aria-pressed="${isoDate(d) === isoDate(choice.date)}">${dayLabel(d)}</button>`).join("")}
      </div>
      <div id="slots" style="margin-top:14px">${skeletons(1)}</div>
    `;

    document.getElementById("days").addEventListener("click", (event) => {
      const pill = event.target.closest("[data-date]");
      if (!pill) return;
      const [y, m, d] = pill.dataset.date.split("-").map(Number);
      choice.date = new Date(y, m - 1, d);
      choice.slot = null;
      refresh();
    });

    const slotsBox = document.getElementById("slots");
    try {
      const data = await request(
        `/barbers/${choice.barber.id}/available-slots?date=${isoDate(choice.date)}&service_id=${choice.service.id}`
      );
      slotsBox.innerHTML = data.slots.length
        ? `<div class="slots">${data.slots
            .map((s) => `<button class="slot" type="button" data-slot="${s.start}" aria-pressed="false">${s.start_local}</button>`)
            .join("")}</div>`
        : empty("🚫", "Nothing free that day", "Try another date or barber.");

      slotsBox.addEventListener("click", (event) => {
        const slot = event.target.closest("[data-slot]");
        if (!slot) return;
        choice.slot = { start: slot.dataset.slot, label: slot.textContent.trim() };
        slotsBox.querySelectorAll("[data-slot]").forEach((s) =>
          s.setAttribute("aria-pressed", String(s === slot))
        );
        paintActionBar(choice);
      });
    } catch (error) {
      slotsBox.innerHTML = banner(error.message);
    }

    paintActionBar(choice.slot ? choice : null);
  }

  function paintActionBar(ready) {
    document.getElementById("actionbar")?.remove();
    if (!ready) return;

    const bar = el(`
      <div class="actionbar" id="actionbar">
        <div class="actionbar__inner">
          <div class="actionbar__summary">
            <strong>${escape(dayLabel(choice.date))}, ${escape(choice.slot.label)}</strong>
            ${escape(choice.barber.first_name)} · ${escape(money(choice.service.price))}
          </div>
          <button class="btn btn--primary" id="confirm">Book</button>
        </div>
      </div>
    `).firstElementChild;
    document.body.appendChild(bar);

    bar.querySelector("#confirm").addEventListener("click", async (event) => {
      if (!store.access) return (location.hash = "#/login");
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Booking…";
      try {
        await request("/appointments", {
          method: "POST",
          auth: true,
          body: {
            barber_id: choice.barber.id,
            service_id: choice.service.id,
            start_time: choice.slot.start,
          },
        });
        bar.remove();
        location.hash = "#/bookings";
      } catch (error) {
        button.disabled = false;
        button.textContent = "Book";
        // SLOT_TAKEN is the interesting one: someone won the race, so the
        // honest response is to show the fresh list rather than a dead end.
        schedule.querySelector("#slots").innerHTML = banner(error.message);
        if (error.code === "SLOT_TAKEN") refresh();
      }
    });
  }
}

async function bookingsView() {
  if (!store.access) return signInPrompt("See your bookings");

  render(`<h1 class="page-title">Bookings</h1>${skeletons()}`);
  try {
    const page = await request("/appointments/me?limit=50", { auth: true });
    render(`
      <h1 class="page-title">Bookings</h1>
      ${page.items.length
        ? `<div class="stack">${page.items.map(appointmentCard).join("")}</div>`
        : empty("📅", "No bookings yet", "Find a barber and pick a time.")}
    `);
    view.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-cancel]");
      if (!button) return;
      button.disabled = true;
      try {
        await request(`/appointments/${button.dataset.cancel}/cancel`, {
          method: "PATCH", auth: true, body: {},
        });
        bookingsView();
      } catch (error) {
        button.disabled = false;
        button.closest(".card").insertAdjacentHTML("afterbegin", banner(error.message));
      }
    });
  } catch (error) {
    render(`<h1 class="page-title">Bookings</h1>${banner(error.message)}`);
  }
}

function appointmentCard(appointment) {
  const open = ["PENDING", "CONFIRMED"].includes(appointment.status);
  return `<div class="card"><div class="card__body">
    <div style="display:flex;align-items:start;gap:12px">
      <div style="flex:1">
        <div class="row-link__title">${escape(appointment.service.name)}</div>
        <div class="row-link__meta">${escape(when(appointment.start_time))}</div>
        <div class="row-link__meta">with ${escape(appointment.barber.first_name)} ${escape(appointment.barber.last_name)}</div>
      </div>
      <div style="text-align:right">
        <div class="price">${escape(money(appointment.total_price))}</div>
        <div style="margin-top:6px"><span class="badge badge--${appointment.status.toLowerCase()}">${escape(appointment.status.replace("_", " "))}</span></div>
      </div>
    </div>
    ${open ? `<div style="margin-top:14px"><button class="btn btn--danger btn--block" data-cancel="${appointment.id}">Cancel booking</button></div>` : ""}
  </div></div>`;
}

function signInPrompt(what) {
  render(`
    ${empty("🔐", `Sign in to ${what.toLowerCase()}`)}
    <button class="btn btn--primary btn--block" data-go="#/login">Sign in</button>
    <div style="height:10px"></div>
    <button class="btn btn--secondary btn--block" data-go="#/register">Create an account</button>
  `);
}

function authView(mode) {
  const registering = mode === "register";
  render(`
    <h1 class="page-title">${registering ? "Create an account" : "Sign in"}</h1>
    <p class="page-sub">${registering ? "Takes a moment. No card needed." : "Welcome back."}</p>
    <form id="auth" class="card"><div class="card__body">
      <div id="auth-error"></div>
      ${registering ? `
        <div class="field"><label class="field__label" for="first">First name</label>
          <input class="input" id="first" name="first_name" required autocomplete="given-name" /></div>
        <div class="field"><label class="field__label" for="last">Last name</label>
          <input class="input" id="last" name="last_name" required autocomplete="family-name" /></div>` : ""}
      <div class="field"><label class="field__label" for="email">Email</label>
        <input class="input" id="email" name="email" type="email" required autocomplete="email" /></div>
      <div class="field"><label class="field__label" for="password">Password</label>
        <input class="input" id="password" name="password" type="password" required
               autocomplete="${registering ? "new-password" : "current-password"}" /></div>
      ${registering ? `<p class="row-link__meta">At least 8 characters, mixing letters and digits.</p>` : ""}
      <div style="margin-top:16px">
        <button class="btn btn--primary btn--block" type="submit">${registering ? "Create account" : "Sign in"}</button>
      </div>
    </div></form>
    <div style="margin-top:12px;text-align:center">
      <button class="btn btn--ghost" data-go="${registering ? "#/login" : "#/register"}">
        ${registering ? "I already have an account" : "Create an account instead"}
      </button>
    </div>
  `);

  document.getElementById("auth").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    const errorBox = document.getElementById("auth-error");
    button.disabled = true;
    errorBox.innerHTML = "";
    try {
      const body = Object.fromEntries(new FormData(event.target));
      const session = await request(registering ? "/auth/register" : "/auth/login", {
        method: "POST", body,
      });
      store.signIn(session);
      location.hash = "#/bookings";
    } catch (error) {
      const fields = error.details?.fields;
      errorBox.innerHTML = banner(fields?.length ? fields[0].message : error.message);
      button.disabled = false;
    }
  });
}

function accountView() {
  const user = store.user;
  if (!user) return signInPrompt("manage your account");

  render(`
    <h1 class="page-title">Account</h1>
    <div class="card"><div class="card__body">
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">
        <span class="avatar" aria-hidden="true">${escape(initials(user.first_name, user.last_name))}</span>
        <div>
          <div class="row-link__title">${escape(user.first_name)} ${escape(user.last_name)}</div>
          <div class="row-link__meta">${escape(user.email)}</div>
        </div>
      </div>
      <dl class="meta-grid">
        <dt>Role</dt><dd>${escape(user.role.replace("_", " ").toLowerCase())}</dd>
        <dt>Joined</dt><dd>${escape(new Date(user.created_at).toLocaleDateString())}</dd>
      </dl>
    </div></div>
    <div style="margin-top:14px">
      <button class="btn btn--secondary btn--block" id="signout">Sign out</button>
    </div>
  `);

  document.getElementById("signout").addEventListener("click", async () => {
    await request("/auth/logout", { method: "POST", body: { refresh_token: store.refresh } }).catch(() => {});
    store.signOut();
    location.hash = "#/";
  });
}

/* ----------------------------------------------------------------- router */

function route() {
  document.getElementById("actionbar")?.remove();
  paintChrome();

  const hash = location.hash || "#/";
  const shop = hash.match(/^#\/shop\/([0-9a-f-]+)$/i);

  if (shop) return shopView(shop[1]);
  if (hash === "#/bookings") return bookingsView();
  if (hash === "#/account") return accountView();
  if (hash === "#/login") return authView("login");
  if (hash === "#/register") return authView("register");
  return shopsView();
}

window.addEventListener("hashchange", route);
route();
