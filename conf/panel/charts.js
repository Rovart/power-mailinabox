/*
 * Power Mail-in-a-Box control panel: small SVG charts for the dashboard (no
 * dependencies). Colors come from CSS tokens (--viz-1, --viz-2... in panel.css), so the
 * light and dark themes each use their own validated steps.
 *
 * PanelCharts.columns(element, options): columns over time, stacked when there are
 *   several series. options = {
 *     buckets: [ISO start time, ...], bucket_seconds: 3600 | 86400,
 *     series: [{label, values: [number | null, ...], color: "var(--viz-1)"}],
 *     unit: "failed logins" (for the tooltip and the table), empty: "text when all zero"
 *   }
 *   Bars are at most 24px wide, with a 2px gap between stacked segments and a rounded
 *   top. Hovering (or focusing) a column shows every series at that time. A legend is
 *   shown for two or more series, and a "Show as a table" view is always there.
 * PanelCharts.sparkline(element, values): a small trend line for a stat tile.
 * PanelCharts.compact(n): 1,284 / 12.9K / 4.2M.
 */
(function () {
	"use strict";

	var SVG = "http://www.w3.org/2000/svg";

	function el(name, attrs, parent) {
		var node = document.createElementNS(SVG, name);
		for (var k in attrs) node.setAttribute(k, attrs[k]);
		if (parent) parent.appendChild(node);
		return node;
	}

	function html(tag, cls, text) {
		var node = document.createElement(tag);
		if (cls) node.className = cls;
		if (text !== undefined && text !== null) node.textContent = text;
		return node;
	}

	function compact(n) {
		if (n === null || n === undefined) return "–";
		var a = Math.abs(n);
		if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
		if (a >= 1e4) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "K";
		return Math.round(n).toLocaleString();
	}

	function nice_max(v) {
		// The top of the axis: 2, 4, 6, 8 or 10 times a power of ten, so that the
		// middle tick is a whole number too.
		if (v <= 2) return 2;
		var p = Math.pow(10, Math.floor(Math.log10(v)));
		var steps = [2, 4, 6, 8, 10];
		for (var i = 0; i < steps.length; i++)
			if (steps[i] * p >= v) return steps[i] * p;
		return 20 * p;
	}

	function time_label(iso, bucket_seconds, long) {
		var d = new Date(iso);
		if (bucket_seconds < 86400) {
			var t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
			return long ? d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) + ", " + t : t;
		}
		return d.toLocaleDateString([], long ? { weekday: "short", day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
	}

	function range_label(iso, bucket_seconds) {
		var start = new Date(iso), end = new Date(start.getTime() + bucket_seconds * 1000);
		if (bucket_seconds < 86400)
			return time_label(iso, bucket_seconds, true) + " – " + end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
		return time_label(iso, bucket_seconds, true);
	}

	function columns(container, options) {
		container.innerHTML = "";
		container.classList.add("viz");
		var series = options.series.filter(function (s) { return s; });
		var n = options.buckets.length;
		var totals = [];
		var any_data = false;
		for (var i = 0; i < n; i++) {
			var t = null;
			series.forEach(function (s) {
				if (s.values[i] !== null && s.values[i] !== undefined) t = (t || 0) + s.values[i];
			});
			totals.push(t);
			if (t) any_data = true;
		}

		if (series.length > 1) {
			var legend = html("div", "viz-legend");
			series.forEach(function (s) {
				var item = html("span", "viz-legend-item");
				var key = html("span", "viz-key");
				key.style.background = s.color;
				item.appendChild(key);
				item.appendChild(document.createTextNode(s.label));
				legend.appendChild(item);
			});
			container.appendChild(legend);
		}

		var plot = html("div", "viz-plot");
		container.appendChild(plot);
		var tooltip = html("div", "viz-tooltip");
		tooltip.setAttribute("role", "status");
		plot.appendChild(tooltip);

		function draw() {
			var old = plot.querySelector("svg");
			if (old) old.remove();
			var width = Math.max(plot.clientWidth, 240), height = 190;
			var m = { top: 10, right: 6, bottom: 24, left: 40 };
			var w = width - m.left - m.right, h = height - m.top - m.bottom;
			var max = nice_max(Math.max.apply(null, totals.map(function (v) { return v || 0; })));
			var svg = el("svg", { width: width, height: height, role: "img", "aria-label": options.title || "" });
			plot.insertBefore(svg, tooltip);
			var g = el("g", { transform: "translate(" + m.left + "," + m.top + ")" }, svg);

			// Gridlines and y ticks: 0, half and the top.
			[0, max / 2, max].forEach(function (v) {
				var y = h - (v / max) * h;
				el("line", { x1: 0, x2: w, y1: y, y2: y, class: v === 0 ? "viz-baseline" : "viz-grid" }, g);
				var label = el("text", { x: -8, y: y, dy: "0.32em", "text-anchor": "end", class: "viz-tick" }, g);
				label.textContent = compact(v);
			});

			var slot = w / n;
			var bar = Math.max(2, Math.min(24, slot * 0.62));
			var every = Math.ceil(n / Math.max(1, Math.floor(w / 64)));
			var columns_g = el("g", {}, g);
			for (var i = 0; i < n; i++) {
				var x = i * slot + (slot - bar) / 2;
				var col = el("g", { class: "viz-col" }, columns_g);
				var y = h;
				var drawn = series.map(function (s) { return s.values[i] || 0; });
				var top_index = -1;
				drawn.forEach(function (v, k) { if (v > 0) top_index = k; });
				drawn.forEach(function (v, k) {
					if (v <= 0) return;
					var seg = Math.max(1, (v / max) * h);
					var bottom = y, top = y - seg;
					// A 2px surface gap separates a segment from the one above it.
					if (k < top_index) top = Math.min(bottom - 1, top + 2);
					var r = k === top_index ? Math.min(4, bar / 2, bottom - top) : 0;
					var d = "M" + x + "," + bottom +
						"V" + (top + r) +
						(r ? "Q" + x + "," + top + " " + (x + r) + "," + top : "") +
						"H" + (x + bar - r) +
						(r ? "Q" + (x + bar) + "," + top + " " + (x + bar) + "," + (top + r) : "") +
						"V" + bottom + "Z";
					var path = el("path", { d: d, class: "viz-bar" }, col);
					path.style.fill = series[k].color;
					y = bottom - seg;
				});
				if (i % every === 0 || i === n - 1 && n <= 8) {
					var xl = el("text", { x: i * slot + slot / 2, y: h + 16, "text-anchor": "middle", class: "viz-tick" }, g);
					xl.textContent = time_label(options.buckets[i], options.bucket_seconds);
				}
				var hit = el("rect", { x: i * slot, y: 0, width: slot, height: h, class: "viz-hit", tabindex: "-1" }, col);
				hit.dataset.index = i;
			}

			function show(i, rect) {
				columns_g.querySelectorAll(".viz-col").forEach(function (c, k) { c.classList.toggle("dim", k !== i); });
				tooltip.textContent = "";
				tooltip.appendChild(html("div", "viz-tooltip-title", range_label(options.buckets[i], options.bucket_seconds)));
				var rows = series.length > 1 ? series.slice().reverse() : series;
				rows.forEach(function (s) {
					var row = html("div", "viz-tooltip-row");
					var key = html("span", "viz-line-key");
					key.style.background = s.color;
					row.appendChild(key);
					var v = s.values[i];
					row.appendChild(html("strong", null, v === null || v === undefined ? "no data" : v.toLocaleString()));
					row.appendChild(html("span", null, s.label));
					tooltip.appendChild(row);
				});
				if (series.length > 1 && totals[i] !== null)
					tooltip.appendChild(html("div", "viz-tooltip-total", "Total " + totals[i].toLocaleString()));
				tooltip.classList.add("show");
				var left = m.left + i * slot + slot / 2;
				var tw = tooltip.offsetWidth;
				left = Math.min(Math.max(left - tw / 2, 0), width - tw);
				tooltip.style.transform = "translate(" + left + "px, 0)";
			}

			function hide() {
				columns_g.querySelectorAll(".viz-col").forEach(function (c) { c.classList.remove("dim"); });
				tooltip.classList.remove("show");
			}

			svg.addEventListener("pointermove", function (e) {
				var r = svg.getBoundingClientRect();
				var i = Math.floor((e.clientX - r.left - m.left) / slot);
				if (i >= 0 && i < n) show(i); else hide();
			});
			svg.addEventListener("pointerleave", hide);

			// Keyboard: the chart is one stop; arrow keys move between columns.
			svg.setAttribute("tabindex", "0");
			var focused = n - 1;
			svg.addEventListener("focus", function () { show(focused); });
			svg.addEventListener("blur", hide);
			svg.addEventListener("keydown", function (e) {
				if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
					e.preventDefault();
					focused = Math.min(n - 1, Math.max(0, focused + (e.key === "ArrowRight" ? 1 : -1)));
					show(focused);
				}
			});
		}

		draw();
		if (!any_data && options.empty) {
			var note = html("div", "viz-empty", options.empty);
			plot.appendChild(note);
		}
		if (window.ResizeObserver) {
			var last_width = plot.clientWidth;
			new ResizeObserver(function () {
				if (Math.abs(plot.clientWidth - last_width) > 4) {
					last_width = plot.clientWidth;
					draw();
				}
			}).observe(plot);
		}

		// The same data as a table.
		var details = html("details", "viz-table");
		details.appendChild(html("summary", null, "Show as a table"));
		var wrap = html("div", "table-responsive");
		var table = html("table", "table table-sm mb-0");
		var thead = html("thead"), tr = html("tr");
		tr.appendChild(html("th", null, options.bucket_seconds < 86400 ? "Hour" : "Day"));
		series.forEach(function (s) { var th = html("th", "text-end", s.label); tr.appendChild(th); });
		if (series.length > 1) tr.appendChild(html("th", "text-end", "Total"));
		thead.appendChild(tr);
		table.appendChild(thead);
		var tbody = html("tbody");
		for (var r = n - 1; r >= 0; r--) {
			var row = html("tr");
			row.appendChild(html("td", "text-nowrap", range_label(options.buckets[r], options.bucket_seconds)));
			series.forEach(function (s) {
				var v = s.values[r];
				row.appendChild(html("td", "text-end", v === null || v === undefined ? "–" : v.toLocaleString()));
			});
			if (series.length > 1)
				row.appendChild(html("td", "text-end", totals[r] === null ? "–" : totals[r].toLocaleString()));
			tbody.appendChild(row);
		}
		table.appendChild(tbody);
		wrap.appendChild(table);
		details.appendChild(wrap);
		container.appendChild(details);
	}

	function sparkline(container, values) {
		container.innerHTML = "";
		var w = 96, h = 28, pts = values.map(function (v) { return v || 0; });
		if (!pts.length) return;
		var max = Math.max.apply(null, pts) || 1;
		var step = pts.length > 1 ? w / (pts.length - 1) : w;
		var d = pts.map(function (v, i) {
			return (i ? "L" : "M") + (i * step).toFixed(1) + "," + (h - 3 - (v / max) * (h - 6)).toFixed(1);
		}).join("");
		var svg = el("svg", { width: w, height: h, viewBox: "0 0 " + w + " " + h, "aria-hidden": "true", class: "viz-spark" }, container);
		el("path", { d: d, class: "viz-spark-line" }, svg);
		var last = pts.length - 1;
		el("circle", { cx: last * step, cy: h - 3 - (pts[last] / max) * (h - 6), r: 3, class: "viz-spark-dot" }, svg);
	}

	window.PanelCharts = { columns: columns, sparkline: sparkline, compact: compact };
})();
