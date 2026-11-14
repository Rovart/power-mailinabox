/*
 * Enhanced Mail-in-a-Box control panel: the few interactive components the panel needs
 * (it replaced Bootstrap's JavaScript in v61.12). No dependencies.
 *
 * - Dropdown menus: a button with data-toggle="dropdown" opens the .dropdown-menu next
 *   to it. Escape and a click elsewhere close it; arrow keys move between its items.
 * - The sidebar drawer on narrow screens: data-toggle="sidebar" opens and closes it.
 * - Modal dialogs: PanelModal(element) wraps a <dialog class="modal">. Elements with
 *   data-dismiss="modal" close it. It triggers "modal:shown" and "modal:hidden" events
 *   on the dialog (with jQuery, if it is loaded, so that $(...).on() sees them).
 */
(function () {
	"use strict";

	function trigger(el, name) {
		if (window.jQuery)
			window.jQuery(el).trigger(name);
		else
			el.dispatchEvent(new CustomEvent(name));
	}

	function reduced_motion() {
		return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	}

	/* Dropdown menus */

	var open_menu = null;

	function menu_of(button) {
		var parent = button.closest(".dropdown") || button.parentElement;
		return parent ? parent.querySelector(".dropdown-menu") : null;
	}

	function menu_items(menu) {
		return Array.prototype.filter.call(
			menu.querySelectorAll(".dropdown-item:not(:disabled):not(.disabled)"),
			function (item) { return item.offsetParent !== null; });
	}

	function close_menu(focus_button) {
		if (!open_menu) return;
		var menu = open_menu.menu, button = open_menu.button;
		menu.classList.remove("show");
		button.setAttribute("aria-expanded", "false");
		open_menu = null;
		if (focus_button) button.focus();
	}

	function open(button, focus) {
		var menu = menu_of(button);
		if (!menu) return;
		close_menu(false);
		// Open upwards when there is no room below (e.g. the account menu at the bottom
		// of the sidebar).
		var rect = button.getBoundingClientRect();
		menu.classList.toggle("dropup", window.innerHeight - rect.bottom < 240 && rect.top > 240);
		menu.classList.add("show");
		button.setAttribute("aria-expanded", "true");
		open_menu = { button: button, menu: menu };
		if (focus) {
			var items = menu_items(menu);
			if (items.length) items[focus === "last" ? items.length - 1 : 0].focus();
		}
	}

	document.addEventListener("click", function (e) {
		var button = e.target.closest('[data-toggle="dropdown"], [data-bs-toggle="dropdown"]');
		if (button) {
			e.preventDefault();
			if (open_menu && open_menu.button === button)
				close_menu(false);
			else
				open(button, e.detail === 0 ? "first" : null); // keyboard "click": focus the first item
			return;
		}
		if (open_menu && (!open_menu.menu.contains(e.target) || e.target.closest(".dropdown-item")))
			close_menu(false);
	});

	document.addEventListener("keydown", function (e) {
		var button = e.target.closest && e.target.closest('[data-toggle="dropdown"], [data-bs-toggle="dropdown"]');
		if (button && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
			e.preventDefault();
			open(button, e.key === "ArrowDown" ? "first" : "last");
			return;
		}
		if (!open_menu) return;
		if (e.key === "Escape") {
			e.preventDefault();
			close_menu(true);
		} else if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Home" || e.key === "End") {
			var items = menu_items(open_menu.menu);
			if (!items.length) return;
			e.preventDefault();
			var i = items.indexOf(document.activeElement);
			if (e.key === "Home") i = 0;
			else if (e.key === "End") i = items.length - 1;
			else if (e.key === "ArrowDown") i = (i + 1) % items.length;
			else i = (i - 1 + items.length) % items.length;
			items[i].focus();
		} else if (e.key === "Tab") {
			close_menu(false);
		}
	});

	window.close_dropdowns = function () { close_menu(false); };

	/* Sidebar drawer */

	function set_sidebar(open) {
		document.body.classList.toggle("sidebar-open", open);
		document.querySelectorAll('[data-toggle="sidebar"][aria-expanded]').forEach(function (b) {
			b.setAttribute("aria-expanded", open ? "true" : "false");
		});
		var sidebar = document.getElementById("sidebar");
		if (open && sidebar) {
			var first = sidebar.querySelector(".nav-link");
			if (first) first.focus({ preventScroll: true });
		}
	}

	document.addEventListener("click", function (e) {
		if (e.target.closest('[data-toggle="sidebar"]')) {
			e.preventDefault();
			set_sidebar(!document.body.classList.contains("sidebar-open"));
		}
	});

	document.addEventListener("keydown", function (e) {
		if (e.key === "Escape" && document.body.classList.contains("sidebar-open") && !open_menu)
			set_sidebar(false);
	});

	window.close_sidebar = function () {
		if (document.body.classList.contains("sidebar-open")) set_sidebar(false);
	};

	/* Modal dialogs */

	function PanelModal(dialog) {
		this.dialog = dialog;
		var self = this;
		dialog.addEventListener("cancel", function (e) {
			// Escape: close with the exit transition.
			e.preventDefault();
			self.hide();
		});
		dialog.addEventListener("click", function (e) {
			// A click on the backdrop (outside the dialog's box) closes it.
			if (e.target === dialog) {
				var r = dialog.getBoundingClientRect();
				var inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
				if (!inside || (e.clientX === 0 && e.clientY === 0)) self.hide();
			}
			if (e.target.closest('[data-dismiss="modal"], [data-bs-dismiss="modal"]'))
				self.hide();
		});
		dialog.addEventListener("close", function () {
			dialog.classList.remove("closing");
			trigger(dialog, "modal:hidden");
		});
	}

	PanelModal.prototype.show = function () {
		var dialog = this.dialog;
		clearTimeout(this.timer);
		dialog.classList.remove("closing");
		if (!dialog.open) dialog.showModal();
		// Let the dialog lay itself out before moving the focus into it.
		requestAnimationFrame(function () { trigger(dialog, "modal:shown"); });
	};

	PanelModal.prototype.hide = function () {
		var dialog = this.dialog;
		if (!dialog.open || dialog.classList.contains("closing")) return;
		if (reduced_motion()) {
			dialog.close();
			return;
		}
		dialog.classList.add("closing");
		this.timer = setTimeout(function () { dialog.close(); }, 130);
	};

	window.PanelModal = PanelModal;
})();
