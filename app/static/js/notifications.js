(function () {
    let listNode = document.getElementById("notification-list");
    const emptyState = document.getElementById("notifications-empty-state");
    const totalNode = document.getElementById("notification-total");
    const invitationNode = document.getElementById("invitation-total");
    const messageNode = document.getElementById("message-total");
    const mentionNode = document.getElementById("mention-total");
    const summaryNode = document.getElementById("notification-summary");
    const navBadgeHost = document.querySelector(".nav-link-with-badge");
    const csrfInput = document.querySelector('input[name="csrf_token"]');

    function parseServerDate(value) {
        if (typeof value !== "string") {
            return new Date(value);
        }
        const hasTimezone = /([zZ]|[+\-]\d{2}:\d{2})$/.test(value);
        return new Date(hasTimezone ? value : `${value}Z`);
    }

    function formatDate(value) {
        const date = parseServerDate(value);
        if (Number.isNaN(date.getTime())) {
            return value || "";
        }
        return date.toLocaleString([], {
            year: "numeric",
            month: "numeric",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        });
    }

    function ensureList() {
        if (listNode) {
            return listNode;
        }
        if (!emptyState || !emptyState.parentElement) {
            return null;
        }
        const newList = document.createElement("div");
        newList.className = "notification-list";
        newList.id = "notification-list";
        emptyState.replaceWith(newList);
        listNode = newList;
        return newList;
    }

    function updateSummary() {
        const cards = document.querySelectorAll(".notification-card");
        const total = cards.length;
        const invitationTotal = document.querySelectorAll('.notification-card[data-notification-kind="invitation"]').length;
        const mentionTotal = document.querySelectorAll('.notification-card[data-notification-kind="mention"]').length;
        const messageTotal = total - invitationTotal - mentionTotal;

        if (totalNode) {
            totalNode.textContent = String(total);
        }
        if (invitationNode) {
            invitationNode.textContent = String(invitationTotal);
        }
        if (messageNode) {
            messageNode.textContent = String(messageTotal);
        }
        if (mentionNode) {
            mentionNode.textContent = String(mentionTotal);
        }
        if (summaryNode) {
            summaryNode.textContent = `${total} unread`;
        }
    }

    function updateNavBadge(count) {
        if (!navBadgeHost) {
            return;
        }
        let badge = navBadgeHost.querySelector(".nav-badge");
        if (count > 0) {
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "nav-badge";
                navBadgeHost.appendChild(badge);
            }
            badge.textContent = String(count);
            return;
        }
        if (badge) {
            badge.remove();
        }
    }

    function buildInvitationActions(invitationId) {
        const wrapper = document.createElement("div");
        wrapper.className = "notification-actions";
        const csrfToken = csrfInput ? csrfInput.value : "";

        const acceptForm = document.createElement("form");
        acceptForm.method = "post";
        acceptForm.action = `/invitations/${invitationId}/accept`;

        if (csrfToken) {
            const tokenInput = document.createElement("input");
            tokenInput.type = "hidden";
            tokenInput.name = "csrf_token";
            tokenInput.value = csrfToken;
            acceptForm.appendChild(tokenInput);
        }

        const acceptButton = document.createElement("button");
        acceptButton.type = "submit";
        acceptButton.className = "secondary-button";
        acceptButton.textContent = "Accept";
        acceptForm.appendChild(acceptButton);

        const declineForm = document.createElement("form");
        declineForm.method = "post";
        declineForm.action = `/invitations/${invitationId}/decline`;
        if (csrfToken) {
            const tokenInput = document.createElement("input");
            tokenInput.type = "hidden";
            tokenInput.name = "csrf_token";
            tokenInput.value = csrfToken;
            declineForm.appendChild(tokenInput);
        }

        const declineButton = document.createElement("button");
        declineButton.type = "submit";
        declineButton.className = "ghost-button";
        declineButton.textContent = "Decline";
        declineForm.appendChild(declineButton);

        wrapper.append(acceptForm, declineForm);
        return wrapper;
    }

    function createNotificationCard(item) {
        const card = document.createElement("article");
        card.className = "notification-card";
        card.dataset.notificationKind = item.kind;
        if (item.kind === "invitation" && item.invitation_id) {
            card.dataset.invitationId = String(item.invitation_id);
        }
        if (item.kind === "message" && item.message_id) {
            card.dataset.messageId = String(item.message_id);
        }
        if (item.kind === "mention" && item.mention_id) {
            card.dataset.mentionId = String(item.mention_id);
        }

        const avatar = document.createElement("div");
        avatar.className = "notification-avatar";
        avatar.textContent = (item.sender_username || "?").slice(0, 1).toUpperCase();

        const content = document.createElement("div");
        content.className = "notification-content";

        const row = document.createElement("div");
        row.className = "notification-row";
        const heading = document.createElement("div");
        heading.className = "notification-heading";
        const author = document.createElement("strong");
        author.textContent = item.sender_username;
        const room = document.createElement("span");
        room.className = "notification-room";
        room.textContent = item.room_name;
        heading.append(author, room);

        const meta = document.createElement("div");
        meta.className = "notification-meta";
        const badge = document.createElement("span");
        badge.className = "room-badge";
        if (item.kind === "invitation") {
            badge.textContent = "INVITE";
        } else if (item.kind === "mention") {
            badge.textContent = "MENTION";
        } else {
            badge.textContent = item.is_private ? "DM" : "ROOM";
        }
        const time = document.createElement("span");
        time.textContent = formatDate(item.created_at);
        meta.append(badge, time);

        row.append(heading, meta);

        const preview = document.createElement("p");
        preview.className = "notification-preview";
        preview.textContent = item.preview || "";

        content.append(row, preview);

        if (item.kind === "invitation" && item.invitation_id) {
            content.appendChild(buildInvitationActions(item.invitation_id));
        } else if (item.room_id) {
            const openLink = document.createElement("a");
            openLink.className = "notification-open-link";
            openLink.href = `/rooms/${item.room_id}`;
            openLink.textContent = "Open chat";
            content.appendChild(openLink);
        }

        card.append(avatar, content);
        return card;
    }

    function prependNotification(item) {
        const targetList = ensureList();
        if (!targetList) {
            return;
        }

        if (item.kind === "invitation" && item.invitation_id) {
            const existingInvite = targetList.querySelector(`[data-invitation-id="${item.invitation_id}"]`);
            if (existingInvite) {
                return;
            }
        }
        if (item.kind === "message" && item.message_id) {
            const existingMessage = targetList.querySelector(`[data-message-id="${item.message_id}"]`);
            if (existingMessage) {
                return;
            }
        }
        if (item.kind === "mention" && item.mention_id) {
            const existingMention = targetList.querySelector(`[data-mention-id="${item.mention_id}"]`);
            if (existingMention) {
                return;
            }
        }

        targetList.prepend(createNotificationCard(item));
        updateSummary();
        const total = document.querySelectorAll(".notification-card").length;
        updateNavBadge(total);
    }

    window.addEventListener("app:notification-item", function (event) {
        const payload = event.detail || {};
        prependNotification(payload);
    });

    window.addEventListener("app:notification-count", function (event) {
        const payload = event.detail || {};
        const count = payload && typeof payload.count === "number" ? payload.count : 0;
        updateNavBadge(count);
    });
})();
