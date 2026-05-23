(function () {
    const configNode = document.getElementById("chat-config");
    if (!configNode || typeof io === "undefined") {
        return;
    }

    const config = JSON.parse(configNode.textContent);
    const stream = document.getElementById("chat-stream");
    const form = document.getElementById("message-form");
    const input = document.getElementById("message-input");
    const typingBanner = document.getElementById("typing-banner");
    const composerCounter = document.getElementById("composer-counter");
    const mediaInput = document.getElementById("media-input");
    const mediaUploadTrigger = document.getElementById("media-upload-trigger");
    const uploadStatus = document.getElementById("upload-status");
    const roomMemberActions = document.getElementById("room-member-actions");
    const roomMemberEmptyState = document.getElementById("room-member-empty-state");
    const typingUsers = new Map();
    let typingTimer = null;
    let isTyping = false;

    const socket = io({
        transports: ["polling"],
        upgrade: false,
    });

    function parseServerDate(value) {
        if (typeof value !== "string") {
            return new Date(value);
        }
        const hasTimezone = /([zZ]|[+\-]\d{2}:\d{2})$/.test(value);
        return new Date(hasTimezone ? value : `${value}Z`);
    }

    function formatTimestamp(value) {
        const date = parseServerDate(value);
        return date.toLocaleString([], {
            year: "numeric",
            month: "numeric",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        });
    }

    function updateTypingBanner() {
        if (!typingUsers.size) {
            typingBanner.textContent = "";
            return;
        }

        const names = Array.from(typingUsers.values());
        typingBanner.textContent = `${names.join(", ")} typing...`;
    }

    function receiptLabel(message) {
        if (!message.read_by || !message.read_by.length) {
            return "Delivered";
        }

        const others = message.read_by.filter((entry) => entry.user_id !== config.currentUserId);
        if (!others.length) {
            return "Sent";
        }

        return `Read by ${others.map((entry) => entry.username).join(", ")}`;
    }

    function buildMessageContent(message) {
        if (message.message_type === "image") {
            const image = document.createElement("img");
            image.className = "message-media message-image";
            image.src = message.content;
            image.alt = `Image shared by ${message.user.username}`;
            image.loading = "lazy";
            return image;
        }

        if (message.message_type === "video") {
            const video = document.createElement("video");
            video.className = "message-media message-video";
            video.src = message.content;
            video.controls = true;
            video.preload = "metadata";
            return video;
        }

        const body = document.createElement("p");
        body.className = "message-body";
        body.textContent = message.content;
        return body;
    }

    function buildMessageElement(message) {
        const wrapper = document.createElement("article");
        wrapper.className = `message${message.user.id === config.currentUserId ? " own" : ""}`;
        wrapper.dataset.messageId = String(message.id);

        const header = document.createElement("div");
        header.className = "message-header";

        const author = document.createElement("span");
        author.className = "message-author";
        author.textContent = message.user.username;

        const time = document.createElement("span");
        time.className = "message-time";
        time.textContent = formatTimestamp(message.created_at);

        const readers = document.createElement("div");
        readers.className = "message-readers";
        readers.textContent = receiptLabel(message);

        header.append(author, time);
        wrapper.append(header, buildMessageContent(message), readers);
        return wrapper;
    }

    function appendMessage(message) {
        stream.appendChild(buildMessageElement(message));
        stream.scrollTop = stream.scrollHeight;
    }

    function updateComposerCounter() {
        if (!composerCounter || !input) {
            return;
        }

        composerCounter.textContent = `${input.value.length} / 1000`;
    }

    function setUploadStatus(message, isError) {
        if (!uploadStatus) {
            return;
        }

        uploadStatus.textContent = message || "";
        uploadStatus.classList.toggle("is-error", Boolean(isError));
    }

    function renderMessages(messages) {
        stream.innerHTML = "";

        if (!messages.length) {
            const empty = document.createElement("p");
            empty.className = "muted-text";
            empty.textContent = "No messages yet. Start the conversation.";
            stream.appendChild(empty);
            return;
        }

        messages.forEach(appendMessage);
    }

    function updateReceipts(messageIds, username) {
        messageIds.forEach((messageId) => {
            const messageNode = stream.querySelector(`[data-message-id="${messageId}"] .message-readers`);
            if (!messageNode) {
                return;
            }

            const currentText = messageNode.textContent || "";
            if (currentText.includes(username)) {
                return;
            }

            if (currentText === "Delivered" || currentText === "Sent") {
                messageNode.textContent = `Read by ${username}`;
                return;
            }

            messageNode.textContent = `${currentText}, ${username}`;
        });
    }

    function updateUserStatus(userId, isOnline) {
        const label = document.querySelector(`.person-card[data-user-id="${userId}"] .status-label`);
        if (!label) {
            return;
        }

        label.textContent = isOnline ? "Online" : "Offline";
        label.classList.toggle("is-online", isOnline);
    }

    function updateMemberManagement(members) {
        if (!roomMemberActions) {
            return;
        }

        const csrfInput = document.querySelector('input[name="csrf_token"]');
        const csrfToken = csrfInput ? csrfInput.value : "";
        const removableMembers = (members || []).filter(function (member) {
            return member.user_id !== config.currentUserId;
        });

        roomMemberActions.innerHTML = "";

        removableMembers.forEach(function (member) {
            const formNode = document.createElement("form");
            formNode.method = "post";
            formNode.action = `/rooms/${config.roomId}/members/${member.user_id}/remove`;

            if (csrfToken) {
                const csrfNode = document.createElement("input");
                csrfNode.type = "hidden";
                csrfNode.name = "csrf_token";
                csrfNode.value = csrfToken;
                formNode.appendChild(csrfNode);
            }

            const buttonNode = document.createElement("button");
            buttonNode.className = "ghost-button";
            buttonNode.type = "submit";
            buttonNode.textContent = `Remove ${member.username}`;
            formNode.appendChild(buttonNode);
            roomMemberActions.appendChild(formNode);
        });

        if (roomMemberEmptyState) {
            roomMemberEmptyState.hidden = removableMembers.length > 0;
        }
    }

    function emitTypingStart() {
        if (isTyping) {
            return;
        }

        isTyping = true;
        socket.emit("typing_start", { room_id: config.roomId });
    }

    function emitTypingStop() {
        if (!isTyping) {
            return;
        }

        isTyping = false;
        socket.emit("typing_stop", { room_id: config.roomId });
    }

    renderMessages(config.initialMessages || []);
    updateComposerCounter();

    socket.on("connect", function () {
        if (config.isBlockedRoom) {
            return;
        }

        socket.emit("join_room", { room_id: config.roomId });
        socket.emit("mark_read", { room_id: config.roomId });
    });

    socket.on("receive_message", function (message) {
        if (message.room_id !== config.roomId) {
            return;
        }

        const emptyState = stream.querySelector(".muted-text");
        if (emptyState) {
            emptyState.remove();
        }

        appendMessage(message);
        typingUsers.delete(message.user.id);
        updateTypingBanner();

        if (message.user.id !== config.currentUserId) {
            socket.emit("mark_read", { room_id: config.roomId });
        }
    });

    socket.on("typing_indicator", function (payload) {
        if (payload.room_id !== config.roomId) {
            return;
        }

        if (payload.is_typing) {
            typingUsers.set(payload.user_id, payload.username);
        } else {
            typingUsers.delete(payload.user_id);
        }

        updateTypingBanner();
    });

    socket.on("message_read", function (payload) {
        if (payload.room_id !== config.roomId) {
            return;
        }

        updateReceipts(payload.message_ids, payload.username);
    });

    socket.on("user_status", function (payload) {
        updateUserStatus(payload.user_id, payload.is_online);
    });

    socket.on("room_members_updated", function (payload) {
        if (!payload || payload.room_id !== config.roomId) {
            return;
        }
        updateMemberManagement(payload.members);
    });

    socket.on("error", function (payload) {
        if (payload && payload.message) {
            window.alert(payload.message);
        }
    });

    async function uploadMedia(file) {
        if (!file || config.isBlockedRoom) {
            return;
        }

        const formData = new FormData();
        formData.append("media", file);

        if (mediaUploadTrigger) {
            mediaUploadTrigger.disabled = true;
        }
        if (mediaInput) {
            mediaInput.disabled = true;
        }

        setUploadStatus(`Uploading ${file.name}...`, false);

        try {
            const response = await fetch(`/rooms/${config.roomId}/media`, {
                method: "POST",
                body: formData,
                credentials: "same-origin",
            });

            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {
                payload = null;
            }

            if (!response.ok) {
                throw new Error((payload && payload.message) || "Unable to upload media.");
            }

            setUploadStatus(`${file.name} sent`, false);
            window.setTimeout(function () {
                setUploadStatus("", false);
            }, 2400);
        } catch (error) {
            setUploadStatus(error.message || "Unable to upload media.", true);
        } finally {
            if (mediaInput) {
                mediaInput.value = "";
                mediaInput.disabled = config.isBlockedRoom;
            }
            if (mediaUploadTrigger) {
                mediaUploadTrigger.disabled = config.isBlockedRoom;
            }
        }
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();

        if (config.isBlockedRoom) {
            return;
        }

        const content = input.value.trim();
        if (!content) {
            return;
        }

        socket.emit("send_message", {
            room_id: config.roomId,
            content: content,
            message_type: "text",
        });

        input.value = "";
        updateComposerCounter();
        emitTypingStop();
        input.focus();
    });

    input.addEventListener("input", function () {
        updateComposerCounter();
        const value = input.value.trim();

        if (!value) {
            emitTypingStop();
            return;
        }

        emitTypingStart();
        clearTimeout(typingTimer);
        typingTimer = window.setTimeout(emitTypingStop, 1200);
    });

    if (mediaUploadTrigger && mediaInput) {
        mediaUploadTrigger.addEventListener("click", function () {
            if (!config.isBlockedRoom) {
                mediaInput.click();
            }
        });

        mediaInput.addEventListener("change", function () {
            const file = mediaInput.files && mediaInput.files[0];
            if (file) {
                uploadMedia(file);
            }
        });
    }

    window.addEventListener("beforeunload", emitTypingStop);
})();
