(function () {
    if (typeof io === "undefined") {
        return;
    }

    const navBadgeHost = document.querySelector(".nav-link-with-badge");

    function getBadgeNode() {
        if (!navBadgeHost) {
            return null;
        }
        let badge = navBadgeHost.querySelector(".nav-badge");
        if (!badge) {
            badge = document.createElement("span");
            badge.className = "nav-badge";
            navBadgeHost.appendChild(badge);
        }
        return badge;
    }

    function readBadgeCount() {
        if (!navBadgeHost) {
            return 0;
        }
        const badge = navBadgeHost.querySelector(".nav-badge");
        if (!badge) {
            return 0;
        }
        const parsed = Number.parseInt(badge.textContent || "0", 10);
        return Number.isNaN(parsed) ? 0 : parsed;
    }

    function updateBadgeCount(count) {
        if (!navBadgeHost) {
            return;
        }
        if (count <= 0) {
            const existing = navBadgeHost.querySelector(".nav-badge");
            if (existing) {
                existing.remove();
            }
            return;
        }
        const badge = getBadgeNode();
        if (badge) {
            badge.textContent = String(count);
        }
    }

    function bumpBadgeCount() {
        updateBadgeCount(readBadgeCount() + 1);
    }

    if (!window.__appSocket) {
        window.__appSocket = io({
            transports: ["polling"],
            upgrade: false,
        });
    }
    const socket = window.__appSocket;

    if (window.__liveNotificationHandlersAttached) {
        return;
    }
    window.__liveNotificationHandlersAttached = true;

    socket.on("notification_item", function (payload) {
        bumpBadgeCount();
        window.dispatchEvent(
            new CustomEvent("app:notification-item", {
                detail: payload || {},
            })
        );
    });

    socket.on("notification_count", function (payload) {
        const count = payload && typeof payload.count === "number" ? payload.count : 0;
        updateBadgeCount(count);
        window.dispatchEvent(
            new CustomEvent("app:notification-count", {
                detail: { count: count },
            })
        );
    });

    socket.on("user_profile_updated", function (payload) {
        window.dispatchEvent(
            new CustomEvent("app:user-profile-updated", {
                detail: payload || {},
            })
        );
    });
})();
