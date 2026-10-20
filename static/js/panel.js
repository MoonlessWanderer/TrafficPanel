(() => {
  const page = document.body.dataset.page;
  const timezone = document.body.dataset.timezone || "Asia/Tehran";
  const $ = (selector, root = document) => root.querySelector(selector);
  const params = () => new URLSearchParams(location.search);

  function cookie(name) {
    const item = document.cookie.split("; ").find((part) => part.startsWith(name + "="));
    return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
  }

  function toast(message) {
    const node = $("#toast");
    if (!node) return;
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => node.classList.remove("show"), 3200);
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const method = (options.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
      headers["X-CSRF-Token"] = cookie("vps_csrf");
      if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, { ...options, headers });
    const body = await response.json().catch(() => null);
    if (response.status === 401 && page !== "login") {
      location.href = "/login";
      throw new Error("unauthorized");
    }
    if (!response.ok || !body || body.ok === false) {
      const error = new Error(body?.error?.message || "The request could not be completed.");
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function confirmBox(title, message, okLabel = "Confirm", danger = false) {
    let finish;
    const pending = new Promise((resolve) => {
      finish = resolve;
    });
    const modal = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").textContent = message;
    $("#modal-ok").textContent = okLabel;
    $("#modal-ok").classList.toggle("danger", danger);
    $("#modal-cancel").textContent = "Cancel";
    modal.hidden = false;
    const close = (value) => {
      modal.hidden = true;
      $("#modal-ok").onclick = null;
      $("#modal-cancel").onclick = null;
      finish(value);
    };
    $("#modal-ok").onclick = () => close(true);
    $("#modal-cancel").onclick = () => close(false);
    return pending;
  }

  function formBox(title, fields) {
    const form = document.createElement("form");
    fields.forEach((field) => {
      const label = document.createElement("label");
      label.textContent = field.label;
      const input = document.createElement("input");
      input.name = field.name;
      input.value = field.value || "";
      if (field.required) input.required = true;
      label.append(input);
      form.append(label);
    });
    const modal = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").replaceChildren(form);
    $("#modal-ok").textContent = "Save";
    $("#modal-ok").classList.remove("danger");
    $("#modal-cancel").textContent = "Cancel";
    modal.hidden = false;
    return new Promise((resolve) => {
      const close = (value) => {
        modal.hidden = true;
        $("#modal-ok").onclick = null;
        $("#modal-cancel").onclick = null;
        resolve(value);
      };
      $("#modal-cancel").onclick = () => close(null);
      $("#modal-ok").onclick = () => {
        const values = {};
        fields.forEach((field) => {
          values[field.name] = form.elements[field.name].value.trim();
        });
        if (fields.some((field) => field.required && !values[field.name])) return;
        close(values);
      };
    });
  }

  function showKey(title, key, notice) {
    const modal = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = `<p>${notice}</p><div class="key-box" id="key-value"></div>`;
    $("#key-value").textContent = key;
    $("#modal-ok").textContent = "Copy key";
    $("#modal-ok").classList.remove("danger");
    $("#modal-cancel").textContent = "Close";
    modal.hidden = false;
    $("#modal-ok").onclick = async () => {
      try {
        await navigator.clipboard.writeText(key);
        toast("Key copied.");
      } catch {
        toast("Automatic copy failed. Copy the key manually.");
      }
    };
    $("#modal-cancel").onclick = () => {
      modal.hidden = true;
    };
  }

  function formatWhen(value) {
    if (!value) return "—";
    return new Date(value).toLocaleString("en-GB", {
      timeZone: timezone,
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatTick(value) {
    if (!value) return "";
    return new Date(value).toLocaleString("en-GB", {
      timeZone: timezone,
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatUnit(value, unit) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    const sign = value < 0 ? -1 : 1;
    let amount = Math.abs(value);
    if (unit === "percent") return `${amount.toFixed(1)}%`;
    const base = unit === "bps" ? 1000 : 1000;
    let units = unit === "bps" ? ["bps", "Kbps", "Mbps", "Gbps"] : ["B", "KB", "MB", "GB", "TB"];
    if (unit === "bps") amount *= 8;
    let index = 0;
    while (amount >= base && index < units.length - 1) {
      amount /= base;
      index += 1;
    }
    const shown = index === 0 ? Math.round(sign * amount) : (sign * amount).toFixed(2);
    return `${shown} ${units[index]}`;
  }

  function statusPill(status, stale) {
    const labels = { online: "Online", offline: "Offline", disabled: "Disabled" };
    const extra = stale ? " · delayed" : "";
    return `<span class="pill ${status}">${labels[status] || status}${extra}</span>`;
  }

  function setState(text, isError = false) {
    const node = $("#state");
    if (!node) return;
    node.hidden = !text;
    node.textContent = text || "";
    node.classList.toggle("error", isError);
  }

  function queryString() {
    return params().toString();
  }

  function syncTimeForm() {
    const form = $("#time-form");
    if (!form) return;
    const current = params();
    form.preset.value = current.get("preset") || "24h";
    form.classList.toggle("custom", form.preset.value === "custom");
    form.preset.onchange = () => form.classList.toggle("custom", form.preset.value === "custom");
    form.onsubmit = (event) => {
      event.preventDefault();
      const next = params();
      next.set("preset", form.preset.value);
      if (form.preset.value === "custom") {
        if (!form.from.value || !form.to.value) {
          toast("Enter the start and end of the custom range.");
          return;
        }
        next.set("from", new Date(form.from.value).toISOString());
        next.set("to", new Date(form.to.value).toISOString());
      } else {
        next.delete("from");
        next.delete("to");
      }
      next.delete("page");
      history.replaceState(null, "", `${location.pathname}?${next.toString()}`);
      load();
    };
  }

  const actionLabels = {
    admin_bootstrapped: "Admin created",
    login: "Sign in",
    logout: "Sign out",
    login_failed: "Failed sign-in",
    node_created: "Node added",
    node_updated: "Node updated",
    node_enabled: "Node enabled",
    node_disabled: "Node disabled",
    node_deleted: "Node deleted",
    node_key_rotated: "Key rotated",
    node_key_revoked: "Key revoked",
    node_auth_failed: "Node authentication failed",
    group_created: "Group added",
    group_updated: "Group updated",
    group_deleted: "Group deleted",
    group_members_added: "Member added",
    group_member_removed: "Member removed",
    group_members_transferred: "Members transferred",
    alert_opened: "Alert opened",
    alert_resolved: "Alert closed",
    settings_updated: "Settings changed",
    panel_restarted: "Panel restarted",
  };
  const alertLabels = {
    offline: "Connection lost",
    high_cpu: "High CPU",
    high_ram: "High RAM",
    disk_full: "Disk full",
    traffic_spike: "Unusual traffic",
    auth_error: "Authentication error",
    ingest_error: "Ingest error",
  };

  const charts = {};
  function chartColors() {
    const style = getComputedStyle(document.documentElement);
    return {
      ink: style.getPropertyValue("--ink").trim(),
      muted: style.getPropertyValue("--muted").trim(),
      line: style.getPropertyValue("--line").trim(),
    };
  }

  function chartYScale(source, yUnit, colors) {
    const unit = source[0]?.unit || yUnit;
    const values = source.flatMap((item) => item.data || []).filter((value) => typeof value === "number" && !Number.isNaN(value));
    const scale = {
      grid: { color: colors.line },
      ticks: {
        color: colors.muted,
        maxTicksLimit: 6,
        callback: (value) => formatUnit(value, unit),
      },
    };
    if (values.length) return scale;
    scale.min = 0;
    if (unit === "percent") {
      scale.max = 100;
      scale.ticks.stepSize = 25;
    } else {
      scale.max = 1;
      scale.ticks.stepSize = 1;
    }
    return scale;
  }

  function drawChart(id, labels, datasets, yUnit) {
    const canvas = document.getElementById(id);
    if (!canvas || !window.Chart) return;
    if (charts[id]) charts[id].destroy();
    const palette = ["#1d4e89", "#0f6e62", "#8a5a00", "#9d3434", "#5c4d7a", "#2f6f4e", "#8c4a32", "#3d6b8a"];
    const visible = datasets.filter((item) => (yUnit === "bps" ? item.unit === "bps" : item.unit !== "bps"));
    const source = visible.length ? visible : datasets;
    const colors = chartColors();
    charts[id] = new Chart(canvas, {
      type: "line",
      data: {
        labels: labels.map((item) => formatTick(item)),
        datasets: source.map((item, index) => ({
          label: item.label,
          data: item.data,
          borderColor: item.color || palette[index % palette.length],
          backgroundColor: "transparent",
          spanGaps: false,
          pointRadius: 0,
          borderWidth: 2,
          tension: 0.25,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        rtl: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom", rtl: false, labels: { color: colors.muted, boxWidth: 12, padding: 14, font: { family: "system-ui, Segoe UI, sans-serif" } } },
          tooltip: {
            callbacks: {
              label(context) {
                const unit = source[context.datasetIndex]?.unit || yUnit;
                return `${context.dataset.label}: ${formatUnit(context.parsed.y, unit)}`;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: colors.muted, maxTicksLimit: 4, maxRotation: 0, autoSkip: true, font: { family: "system-ui, Segoe UI, sans-serif", size: 11 } } },
          y: chartYScale(source, yUnit, colors),
        },
      },
    });
  }

  function drawBar(id, labels, data) {
    const canvas = document.getElementById(id);
    if (!canvas || !window.Chart) return;
    if (charts[id]) charts[id].destroy();
    const colors = chartColors();
    charts[id] = new Chart(canvas, {
      type: "bar",
      data: { labels, datasets: [{ label: "Traffic share", data, backgroundColor: "#0f6e62", borderRadius: 6, maxBarThickness: 42 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        rtl: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y.toFixed(1)}%` } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: colors.muted, font: { family: "system-ui, Segoe UI, sans-serif" } } },
          y: { grid: { color: colors.line }, ticks: { color: colors.muted, callback: (value) => `${value}%` } },
        },
      },
    });
  }

  function card(label, value, hint) {
    const valueHtml = value != null && String(value).includes("<") ? value : `<bdi>${value ?? "—"}</bdi>`;
    const hintHtml = !hint ? "" : (String(hint).includes("<") ? `<small class="stat-hint">${hint}</small>` : `<small class="stat-hint"><bdi>${hint}</bdi></small>`);
    return `<article class="stat"><span class="stat-label">${label}</span><strong class="stat-value">${valueHtml}</strong>${hintHtml}</article>`;
  }

  function feedItem(text, when) {
    return `<div class="feed-item"><span>${text}</span><time>${formatWhen(when)}</time></div>`;
  }

  function textOf(block, field, fallback = "—") {
    if (!block || block.has_data === false) return fallback;
    return block[field]?.text || fallback;
  }

  async function loadDashboard() {
    setState("Loading…");
    const query = queryString();
    const [summary, traffic, resources] = await Promise.all([
      api(`/api/v1/dashboard/summary?${query}`),
      api(`/api/v1/charts/traffic?${query}`),
      api(`/api/v1/charts/resources?${query}`),
    ]);
    const data = summary.data;
    const trafficBlock = data.traffic;
    const resourcesBlock = data.resources;
    $("#cards").innerHTML = [
      card("Nodes", data.nodes.total, ""),
      card("Online", data.nodes.online, ""),
      card("Offline", data.nodes.offline, ""),
      card("Alerting", data.nodes.alerting, "Open alerts"),
      card("Groups", data.groups.total, ""),
      card("RX", textOf(trafficBlock, "rx_text"), ""),
      card("TX", textOf(trafficBlock, "tx_text"), ""),
      card("Total usage", textOf(trafficBlock, "total_text"), ""),
      card("Average CPU", resourcesBlock.cpu_avg == null ? "—" : `${resourcesBlock.cpu_avg.toFixed(1)}%`, resourcesBlock.cpu_max == null ? "" : `Max <bdi>${resourcesBlock.cpu_max.toFixed(1)}%</bdi>`),
      card("Average RAM", resourcesBlock.ram_percent_avg == null ? "—" : `${resourcesBlock.ram_percent_avg.toFixed(1)}%`, resourcesBlock.ram_percent_max == null ? "" : `Max <bdi>${resourcesBlock.ram_percent_max.toFixed(1)}%</bdi>`),
    ].join("");
    drawChart("traffic-chart", traffic.data.labels, traffic.data.datasets, "bytes");
    drawChart("resource-chart", resources.data.labels, resources.data.datasets, "percent");
    $("#top-nodes").innerHTML = data.top_nodes.length
      ? `<div class="table-wrap"><table><tbody>${data.top_nodes.map((item) => `<tr><td><a href="/nodes/${item.node_id}">${item.name}</a></td><td>${statusPill(item.status)}</td><td class="num">${item.total_text?.text || "—"}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">No data has been recorded for this range.</p>`;
    $("#top-groups").innerHTML = data.top_groups.length
      ? `<div class="table-wrap"><table><tbody>${data.top_groups.map((item) => `<tr><td><a href="/groups/${item.id}">${item.name}</a></td><td class="num">${item.total_text?.text || "—"}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">No data has been recorded for this range.</p>`;
    $("#dash-alerts").innerHTML = data.alerts.length
      ? data.alerts.map((item) => feedItem(item.message, item.opened_at)).join("")
      : `<p class="empty">No open alerts in this view.</p>`;
    $("#dash-activity").innerHTML = data.activity.length
      ? data.activity.map((item) => feedItem(actionLabels[item.action] || item.action, item.created_at)).join("")
      : `<p class="empty">No activity in this range.</p>`;
    setState(data.nodes.total ? "" : "No nodes have been added yet.");
  }

  function readFilterForm(form) {
    const next = params();
    new FormData(form).forEach((value, key) => {
      if (value === "") next.delete(key);
      else next.set(key, value);
    });
    next.delete("page");
    history.replaceState(null, "", `${location.pathname}?${next.toString()}`);
  }

  function fillForm(form) {
    const current = params();
    form.querySelectorAll("input, select").forEach((field) => {
      if (field.name && current.has(field.name)) field.value = current.get(field.name);
    });
  }

  async function loadNodes() {
    if (!loadNodes.ready) {
      const groups = await api("/api/v1/groups");
      const select = $("#group-filter");
      groups.data.forEach((group) => {
        const option = document.createElement("option");
        option.value = group.id;
        option.textContent = group.name;
        select.appendChild(option);
      });
      const form = $("#node-filters");
      fillForm(form);
      const advanced = ["traffic_min", "traffic_max", "cpu_min", "cpu_max", "ram_min", "ram_max", "disk_min", "disk_max"];
      const trafficKind = params().get("traffic");
      if (advanced.some((key) => params().get(key)) || (trafficKind && trafficKind !== "total")) $("#more-filters").open = true;
      form.onsubmit = (event) => {
        event.preventDefault();
        readFilterForm(form);
        renderNodes();
      };
      $("#new-node").onclick = async () => {
        const values = await formBox("New node", [
          { name: "name", label: "Name", required: true },
          { name: "description", label: "Description" },
        ]);
        if (!values) return;
        try {
          const result = await api("/api/v1/nodes", {
            method: "POST",
            body: JSON.stringify({ name: values.name, description: values.description }),
          });
          showKey("Node added.", result.data.node_key, result.data.key_notice);
          renderNodes();
        } catch (error) {
          toast(error.message);
        }
      };
      loadNodes.ready = true;
    }
    await renderNodes();
  }

  async function renderNodes() {
    setState("Loading…");
    const result = await api(`/api/v1/nodes?${queryString()}`);
    const rows = $("#node-rows");
    if (!result.data.length) {
      rows.innerHTML = "";
      setState(result.meta.total ? "Nothing matches this filter." : "No nodes have been added yet.");
    } else {
      setState("");
      rows.innerHTML = result.data.map((node) => `
        <tr>
          <td><a href="/nodes/${node.node_id}?${queryString()}">${node.name}</a></td>
          <td class="ltr">${node.node_id}</td>
          <td>${statusPill(node.status, node.stale)}</td>
          <td class="num">${node.range_traffic?.total_text?.text || "—"}</td>
          <td class="num">${node.cpu_percent == null ? "—" : node.cpu_percent.toFixed(1) + "%"}</td>
          <td class="num">${node.ram.percent == null ? "—" : node.ram.percent.toFixed(1) + "%"}</td>
          <td>${node.last_seen_at ? formatWhen(node.last_seen_at) : "No report has been received yet."}</td>
        </tr>`).join("");
    }
    const pages = Math.max(1, Math.ceil(result.meta.total / result.meta.page_size));
    $("#pager").innerHTML = `Page ${result.meta.page} of ${pages}`;
  }

  async function loadNode() {
    const nodeId = document.body.dataset.nodeId;
    setState("Loading…");
    const [node, stats, traffic, resources, samples] = await Promise.all([
      api(`/api/v1/nodes/${nodeId}`),
      api(`/api/v1/nodes/${nodeId}/stats?${queryString()}`),
      api(`/api/v1/charts/traffic?${queryString()}&node_ids=${nodeId}`),
      api(`/api/v1/charts/resources?${queryString()}&node_ids=${nodeId}`),
      api(`/api/v1/nodes/${nodeId}/samples?${queryString()}`),
    ]);
    const info = node.data;
    $("#node-title").textContent = info.name;
    $("#node-sub").textContent = `${info.node_id} · Version ${info.agent_version || "—"} · ${info.os_name || ""}`;
    const block = stats.data.traffic;
    const res = stats.data.resources;
    $("#node-cards").innerHTML = [
      card("Status", statusPill(info.status, info.stale), info.last_seen_at ? formatWhen(info.last_seen_at) : "No report has been received yet."),
      card("Key", info.key_active ? `<span class="token">${info.key_prefix}</span>` : "Revoked", info.key_active ? "Key prefix" : ""),
      card("RX", textOf(block, "rx_text"), textOf(block, "rx_bps_avg_text")),
      card("TX", textOf(block, "tx_text"), textOf(block, "tx_bps_avg_text")),
      card("CPU", res.cpu_avg == null ? "—" : `${res.cpu_avg.toFixed(1)}%`, res.cpu_max == null ? "" : `Max <bdi>${res.cpu_max.toFixed(1)}%</bdi>`),
      card("RAM", res.ram_percent_avg == null ? "—" : `${res.ram_percent_avg.toFixed(1)}%`, info.ram.used_text?.text || ""),
      card("Disk", info.disk.percent == null ? "—" : `${info.disk.percent.toFixed(1)}%`, `<bdi>${info.disk.used_text?.text || "—"}</bdi> of <bdi>${info.disk.total_text?.text || "—"}</bdi>`),
      card("Uptime", info.uptime_seconds == null ? "—" : `<bdi>${Math.floor(info.uptime_seconds / 3600)}</bdi> h`, ""),
    ].join("");
    let view = "volume";
    const paint = () => drawChart("traffic-chart", traffic.data.labels, traffic.data.datasets, view === "speed" ? "bps" : "bytes");
    paint();
    $("#traffic-view").onclick = (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      view = button.dataset.view;
      $("#traffic-view").querySelectorAll("button").forEach((item) => item.classList.toggle("on", item === button));
      paint();
    };
    drawChart("resource-chart", resources.data.labels, resources.data.datasets, "percent");
    $("#iface-rows").innerHTML = stats.data.interfaces.length
      ? stats.data.interfaces.map((item) => `<tr><td class="ltr">${item.name}</td><td class="num">${item.rx_text?.text || "—"}</td><td class="num">${item.tx_text?.text || "—"}</td><td class="num">${item.rx_bps_text?.text || "—"}</td><td class="num">${item.tx_bps_text?.text || "—"}</td><td class="num">${item.rx_errors ?? "—"} / ${item.tx_errors ?? "—"}</td></tr>`).join("")
      : `<tr><td colspan="6">No data has been recorded for this range.</td></tr>`;
    $("#sample-rows").innerHTML = samples.data.length
      ? samples.data.map((item) => `<tr><td>${formatWhen(item.collected_at)}</td><td class="num">${item.cpu_percent.toFixed(1)}%</td><td class="num">${item.ram_percent ?? "—"}%</td><td class="num">${item.disk_percent ?? "—"}%</td><td class="num ltr">${item.load_1.toFixed(2)}</td><td class="num">${Math.floor(item.uptime_seconds / 3600)}h</td></tr>`).join("")
      : `<tr><td colspan="6">No data has been recorded for this range.</td></tr>`;
    setState("");
    $("#toggle-node").textContent = info.is_enabled ? "Disable" : "Enable";
    $("#toggle-node").onclick = async () => {
      const path = info.is_enabled ? "disable" : "enable";
      await api(`/api/v1/nodes/${nodeId}/${path}`, { method: "POST" });
      toast(info.is_enabled ? "Node disabled." : "Node enabled.");
      load();
    };
    $("#rotate-key").onclick = async () => {
      if (!await confirmBox("Rotate key", "The current key will be revoked. The new key is shown only once.", "Rotate", true)) return;
      const result = await api(`/api/v1/nodes/${nodeId}/rotate-key`, { method: "POST" });
      showKey("The new key is ready.", result.data.node_key, result.data.key_notice);
    };
    $("#revoke-key").onclick = async () => {
      if (!await confirmBox("Revoke key", "Revoke this node key?", "Revoke", true)) return;
      await api(`/api/v1/nodes/${nodeId}/revoke-key`, { method: "POST" });
      toast("Node key revoked.");
      load();
    };
    $("#delete-node").onclick = async () => {
      if (!await confirmBox("Delete node", "Delete this node and its history?", "Delete", true)) return;
      await api(`/api/v1/nodes/${nodeId}`, { method: "DELETE" });
      location.href = "/nodes";
    };
  }

  async function loadGroups() {
    setState("Loading…");
    const result = await api("/api/v1/groups");
    $("#group-rows").innerHTML = result.data.length
      ? result.data.map((group) => `<tr><td><a href="/groups/${group.id}?${queryString()}"><span class="pill" style="border-inline-start:4px solid ${group.color}">${group.name}</span></a></td><td>${group.members}</td><td>${group.online}</td><td>${group.offline}</td><td>${group.disabled}</td></tr>`).join("")
      : "";
    setState(result.data.length ? "" : "No groups have been created yet.");
    $("#new-group").onclick = async () => {
      const values = await formBox("New group", [
        { name: "name", label: "Name", required: true },
        { name: "description", label: "Description" },
        { name: "color", label: "Color", value: "#0f6e6b" },
      ]);
      if (!values) return;
      try {
        await api("/api/v1/groups", {
          method: "POST",
          body: JSON.stringify({ name: values.name, description: values.description, color: values.color || "#0f6e6b", tags: [] }),
        });
        toast("Group added.");
        load();
      } catch (error) {
        toast(error.message);
      }
    };
  }

  async function loadGroup() {
    const groupId = document.body.dataset.groupId;
    setState("Loading…");
    const [stats, traffic] = await Promise.all([
      api(`/api/v1/groups/${groupId}/stats?${queryString()}`),
      api(`/api/v1/charts/traffic?${queryString()}&group_ids=${groupId}`),
    ]);
    const data = stats.data;
    $("#group-title").textContent = data.group.name;
    $("#group-sub").textContent = data.group.description || "Stats are calculated from the current members.";
    const trafficBlock = data.traffic;
    const resources = data.resources;
    $("#group-cards").innerHTML = [
      card("Online", data.counts.online, `of ${data.counts.total}`),
      card("Offline", data.counts.offline, ""),
      card("RX", textOf(trafficBlock, "rx_text"), textOf(trafficBlock, "rx_bps_avg_text", "")),
      card("TX", textOf(trafficBlock, "tx_text"), textOf(trafficBlock, "tx_bps_max_text", "")),
      card("CPU", resources.cpu_avg == null ? "—" : `${resources.cpu_avg.toFixed(1)}%`, resources.cpu_max == null ? "" : `Max ${resources.cpu_max.toFixed(1)}%`),
      card("RAM", resources.ram_percent_avg == null ? "—" : `${resources.ram_percent_avg.toFixed(1)}%`, ""),
      card("Disk capacity", data.disk.total_text?.text || "—", data.disk.used_text ? `Used ${data.disk.used_text.text}` : ""),
    ].join("");
    drawChart("traffic-chart", traffic.data.labels, traffic.data.datasets, "bytes");
    drawBar("share-chart", data.ranking.map((item) => item.name), data.ranking.map((item) => item.share_percent || 0));
    $("#rank-rows").innerHTML = data.ranking.length
      ? data.ranking.map((item) => `<tr><td><a href="/nodes/${item.node_id}">${item.name}</a></td><td>${statusPill(item.status)}</td><td class="num">${item.total_text?.text || "—"}</td><td class="num">${item.share_percent == null ? "—" : item.share_percent.toFixed(1) + "%"}</td><td class="num">${item.cpu_avg == null ? "—" : item.cpu_avg.toFixed(1) + "%"}</td><td><button class="btn ghost" type="button" data-remove="${item.node_id}">Remove</button></td></tr>`).join("")
      : `<tr><td colspan="6">No data has been recorded for this range.</td></tr>`;
    $("#rank-rows").onclick = async (event) => {
      const button = event.target.closest("[data-remove]");
      if (!button) return;
      await api(`/api/v1/groups/${groupId}/members/${button.dataset.remove}`, { method: "DELETE" });
      toast("Node removed from the group.");
      load();
    };
    setState("");
    $("#add-member").onclick = async () => {
      const values = await formBox("Add node", [{ name: "node_id", label: "Node ID", required: true }]);
      if (!values) return;
      try {
        await api(`/api/v1/groups/${groupId}/members`, { method: "POST", body: JSON.stringify({ node_ids: [values.node_id] }) });
        toast("Node added to the group.");
        load();
      } catch (error) {
        toast(error.message);
      }
    };
    $("#edit-group").onclick = async () => {
      const values = await formBox("Edit group", [
        { name: "name", label: "Name", value: data.group.name, required: true },
        { name: "description", label: "Description", value: data.group.description || "" },
      ]);
      if (!values) return;
      await api(`/api/v1/groups/${groupId}`, { method: "PATCH", body: JSON.stringify(values) });
      toast("Group updated.");
      load();
    };
    $("#delete-group").onclick = async () => {
      if (!await confirmBox("Delete group", "Members are kept. Delete this group?", "Delete", true)) return;
      await api(`/api/v1/groups/${groupId}`, { method: "DELETE" });
      location.href = "/groups";
    };
  }

  async function loadCompare() {
    const form = $("#compare-form");
    const select = $("#compare-select");
    async function fill() {
      select.innerHTML = "";
      if (form.kind.value === "nodes") {
        const result = await api("/api/v1/nodes?page_size=100");
        result.data.forEach((node) => {
          const option = document.createElement("option");
          option.value = node.node_id;
          option.textContent = node.name;
          select.appendChild(option);
        });
      } else {
        const result = await api("/api/v1/groups");
        result.data.forEach((group) => {
          const option = document.createElement("option");
          option.value = group.id;
          option.textContent = group.name;
          select.appendChild(option);
        });
      }
    }
    form.kind.onchange = fill;
    await fill();
    form.onsubmit = async (event) => {
      event.preventDefault();
      const selected = [...select.selectedOptions].map((option) => option.value);
      if (selected.length < 2) {
        toast("Select at least two items.");
        return;
      }
      const next = params();
      if (form.kind.value === "nodes") {
        next.delete("group_ids");
        next.delete("node_ids");
        selected.forEach((id) => next.append("node_ids", id));
      } else {
        next.delete("node_ids");
        next.delete("group_ids");
        selected.forEach((id) => next.append("group_ids", id));
      }
      history.replaceState(null, "", `${location.pathname}?${next.toString()}`);
      setState("Loading…");
      try {
        const path = form.kind.value === "nodes" ? "/api/v1/compare/nodes" : "/api/v1/compare/groups";
        const result = await api(`${path}?${next.toString()}`);
        drawChart("compare-chart", result.data.chart.labels, result.data.chart.datasets, "bytes");
        const rows = result.data.items.map((item) => {
          if (item.node) return `<tr><td>${item.node.name}</td><td class="num">${item.traffic.total_text?.text || "—"}</td><td class="num">${item.resources.cpu_avg == null ? "—" : item.resources.cpu_avg.toFixed(1) + "%"}</td></tr>`;
          return `<tr><td>${item.group.name}</td><td class="num">${item.traffic.total_text?.text || "—"}</td><td class="num">${item.resources.cpu_avg == null ? "—" : item.resources.cpu_avg.toFixed(1) + "%"}</td></tr>`;
        }).join("");
        $("#compare-table").innerHTML = `<table><thead><tr><th>Name</th><th>Traffic</th><th>CPU</th></tr></thead><tbody>${rows}</tbody></table>`;
        setState(result.data.items.some((item) => item.traffic.has_data) ? "" : "No data has been recorded for this range.");
      } catch (error) {
        setState(error.message, true);
      }
    };
  }

  async function loadAlerts() {
    const form = $("#alert-filters");
    fillForm(form);
    const link = $("#export-link");
    if (link) link.href = `/api/v1/export/traffic.csv?${queryString()}`;
    form.onsubmit = (event) => {
      event.preventDefault();
      readFilterForm(form);
      renderAlerts();
    };
    await renderAlerts();
  }

  async function renderAlerts() {
    setState("Loading…");
    const result = await api(`/api/v1/alerts?${queryString()}`);
    $("#alert-rows").innerHTML = result.data.length
      ? result.data.map((item) => `<tr><td>${formatWhen(item.opened_at)}</td><td>${item.node_name || "—"}</td><td>${alertLabels[item.type] || item.type}</td><td>${item.message}</td><td><span class="pill ${item.status}">${item.status === "open" ? "Open" : "Closed"}</span></td><td>${item.status === "open" ? `<button class="btn ghost" type="button" data-resolve="${item.id}">Close</button>` : (item.resolution || "")}</td></tr>`).join("")
      : "";
    setState(result.data.length ? "" : "No alerts in this view.");
    $("#alert-rows").onclick = async (event) => {
      const button = event.target.closest("[data-resolve]");
      if (!button) return;
      await api(`/api/v1/alerts/${button.dataset.resolve}/resolve`, { method: "POST" });
      toast("Alert closed.");
      renderAlerts();
    };
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  }

  async function loadSettings() {
    setState("Loading…");
    const result = await api("/api/v1/settings");
    const startup = $("#startup");
    startup.hidden = false;
    startup.innerHTML = `<header><h2>Startup</h2></header><p class="muted">These values are in config.py. Restart the panel after editing that file.</p><dl class="startup-list">${result.data.startup.map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("")}</dl>`;
    const form = $("#settings-form");
    form.innerHTML = result.data.groups.map((group) => `
      <section class="card">
        <header><h2>${escapeHtml(group.title)}</h2></header>
        <div class="settings-grid">
          ${group.fields.map((field) => `<label>${escapeHtml(field.label)}<input name="${escapeHtml(field.key)}" value="${escapeHtml(field.value)}" inputmode="${field.type === "str" ? "text" : "decimal"}"><small class="muted">${escapeHtml(field.help)}</small></label>`).join("")}
        </div>
      </section>`).join("") + `<button class="btn settings-save" type="submit">Save settings</button>`;
    form.onsubmit = async (event) => {
      event.preventDefault();
      const values = {};
      new FormData(form).forEach((value, key) => {
        values[key] = value;
      });
      try {
        await api("/api/v1/settings", { method: "PUT", body: JSON.stringify({ values }) });
        if (values.timezone) document.body.dataset.timezone = values.timezone;
        toast("Settings saved.");
      } catch (error) {
        toast(error.message);
      }
    };
    setState("");
  }

  async function load() {
    try {
      if (page === "dashboard") await loadDashboard();
      else if (page === "nodes") await loadNodes();
      else if (page === "node") await loadNode();
      else if (page === "groups") await loadGroups();
      else if (page === "group") await loadGroup();
      else if (page === "compare") await loadCompare();
      else if (page === "alerts") await loadAlerts();
      else if (page === "settings") await loadSettings();
    } catch (error) {
      if (error.message !== "unauthorized") setState(error.message, true);
    }
  }

  if (page === "login") {
    $("#login-form").onsubmit = async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      $("#login-error").hidden = true;
      try {
        await api("/api/v1/auth/login", {
          method: "POST",
          body: JSON.stringify({ username: form.username.value, password: form.password.value }),
        });
        location.href = "/";
      } catch (error) {
        $("#login-error").hidden = false;
        $("#login-error").textContent = error.message;
      }
    };
    return;
  }

  document.querySelectorAll(".sidebar a").forEach((link) => {
    if (link.dataset.nav === page || (page === "node" && link.dataset.nav === "nodes") || (page === "group" && link.dataset.nav === "groups")) {
      link.setAttribute("aria-current", "page");
    }
  });
  const menuButton = $("#menu-btn");
  const backdrop = $("#backdrop");
  const setMenu = (open) => {
    $("#sidebar").classList.toggle("open", open);
    if (backdrop) backdrop.hidden = !open;
    if (menuButton) menuButton.setAttribute("aria-expanded", open ? "true" : "false");
  };
  if (menuButton) menuButton.onclick = () => setMenu(!$("#sidebar").classList.contains("open"));
  if (backdrop) backdrop.onclick = () => setMenu(false);
  const storedTheme = localStorage.getItem("vps-theme");
  if (storedTheme) document.documentElement.dataset.theme = storedTheme;
  else if (window.matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark";
  const themeButton = $("#theme-btn");
  const paintTheme = () => {
    themeButton.textContent = document.documentElement.dataset.theme === "dark" ? "Light mode" : "Dark mode";
  };
  paintTheme();
  function restyleCharts() {
    const colors = chartColors();
    Object.values(charts).forEach((chart) => {
      ["x", "y"].forEach((axis) => {
        const scale = chart.options.scales?.[axis];
        if (!scale) return;
        if (scale.ticks) scale.ticks.color = colors.muted;
        if (scale.grid && scale.grid.display !== false) scale.grid.color = colors.line;
      });
      if (chart.options.plugins?.legend?.labels) chart.options.plugins.legend.labels.color = colors.muted;
      chart.update();
    });
  }
  themeButton.onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("vps-theme", next);
    paintTheme();
    restyleCharts();
  };
  $("#logout-btn").onclick = async () => {
    await api("/api/v1/auth/logout", { method: "POST" });
    location.href = "/login";
  };
  const restart = $("#restart-btn");
  if (restart) {
    restart.onclick = async () => {
      if (!await confirmBox("Restart panel", "The panel will restart and connections will drop briefly.", "Restart", true)) return;
      try {
        await api("/api/v1/system/restart", { method: "POST" });
        toast("The panel is restarting.");
      } catch (error) {
        toast(error.message);
      }
    };
  }
  syncTimeForm();
  load();
})();
