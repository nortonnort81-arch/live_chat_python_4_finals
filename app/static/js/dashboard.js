(function () {
    const peopleSearch = document.getElementById("people-search");
    const roomSearch = document.getElementById("room-search");
    const privateChatInput = document.getElementById("private-chat-username");
    const peopleFeedback = document.getElementById("people-search-feedback");
    const roomFeedback = document.getElementById("room-search-feedback");
    const messageComposer = document.getElementById("message-input");

    function attachFilter(input, selector, attributeName, feedbackNode, emptyText, allText) {
        if (!input) {
            return;
        }

        const nodes = Array.from(document.querySelectorAll(selector));
        const total = nodes.length;

        input.addEventListener("input", function () {
            const query = input.value.trim().toLowerCase();
            let visibleCount = 0;
            nodes.forEach(function (node) {
                const value = (node.getAttribute(attributeName) || "").toLowerCase();
                const isVisible = !query || value.includes(query);
                node.hidden = !isVisible;
                if (isVisible) {
                    visibleCount += 1;
                }
            });

            if (feedbackNode) {
                if (!query) {
                    feedbackNode.textContent = allText;
                } else if (!visibleCount) {
                    feedbackNode.textContent = emptyText;
                } else {
                    feedbackNode.textContent = `${visibleCount} result${visibleCount === 1 ? "" : "s"} for "${query}"`;
                }
            }
        });
    }

    function buildAvatarNode(profileName, avatarUrl, username) {
        if (avatarUrl) {
            const imageNode = document.createElement("img");
            imageNode.className = "person-avatar-image";
            imageNode.src = avatarUrl;
            imageNode.alt = `${profileName || username || "User"} avatar`;
            return imageNode;
        }
        const initialSource = profileName || username || "?";
        const initialNode = document.createElement("span");
        initialNode.className = "person-avatar-initial";
        initialNode.textContent = initialSource.slice(0, 1).toUpperCase();
        return initialNode;
    }

    function updatePersonCardProfile(payload) {
        const userId = payload && payload.user_id;
        if (!userId) {
            return;
        }

        const card = document.querySelector(`.person-card[data-user-id="${userId}"]`);
        if (!card) {
            return;
        }

        const username = payload.username || "";
        const profileName = payload.profile_name || username;
        const avatarUrl = payload.avatar_url || "";

        const nameNode = card.querySelector(".profile-display-name");
        if (nameNode && profileName) {
            nameNode.textContent = profileName;
        }

        const avatarNode = card.querySelector(".person-avatar");
        if (avatarNode) {
            avatarNode.innerHTML = "";
            avatarNode.appendChild(buildAvatarNode(profileName, avatarUrl, username));
        }
    }

    attachFilter(
        peopleSearch,
        ".person-card",
        "data-user-name",
        peopleFeedback,
        "No people match that search",
        "Showing everyone"
    );
    attachFilter(
        roomSearch,
        ".room-item",
        "data-room-name",
        roomFeedback,
        "No rooms match that search",
        "Showing all rooms"
    );

    if (!privateChatInput) {
        if (!roomSearch && !messageComposer) {
            return;
        }
    }

    document.querySelectorAll(".quick-chat-button").forEach(function (button) {
        button.addEventListener("click", function () {
            const username = button.getAttribute("data-chat-username");
            if (!username) {
                return;
            }

            privateChatInput.value = username;
            privateChatInput.focus();
            privateChatInput.scrollIntoView({ behavior: "smooth", block: "center" });
        });
    });

    // Add lightweight keyboard shortcuts to speed up navigation.
    window.addEventListener("keydown", function (event) {
        if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) {
            return;
        }

        const activeTag = document.activeElement ? document.activeElement.tagName : "";
        const isTypingInField = activeTag === "INPUT" || activeTag === "TEXTAREA";

        if (event.key === "/" && roomSearch && !isTypingInField) {
            event.preventDefault();
            roomSearch.focus();
            roomSearch.select();
            return;
        }

        if ((event.key === "g" || event.key === "G") && messageComposer && !isTypingInField) {
            event.preventDefault();
            messageComposer.focus();
        }
    });

    window.addEventListener("app:user-profile-updated", function (event) {
        updatePersonCardProfile(event.detail || {});
    });
})();
