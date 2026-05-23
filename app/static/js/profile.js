(function () {
    const profileCard = document.getElementById("profile-card");
    if (!profileCard) {
        return;
    }

    const userId = Number.parseInt(profileCard.dataset.profileUserId || "", 10);
    const username = profileCard.dataset.profileUsername || "";
    const nameNode = document.getElementById("profile-name");
    const avatarWrap = document.getElementById("profile-avatar-wrap");
    const bioNode = document.getElementById("profile-bio");

    function buildAvatarNode(profileName, avatarUrl) {
        if (avatarUrl) {
            const imageNode = document.createElement("img");
            imageNode.className = "profile-avatar-preview";
            imageNode.src = avatarUrl;
            imageNode.alt = `${profileName || username || "User"} avatar`;
            return imageNode;
        }

        const fallbackNode = document.createElement("div");
        fallbackNode.className = "profile-avatar-fallback";
        const initialSource = profileName || username || "?";
        fallbackNode.textContent = initialSource.slice(0, 1).toUpperCase();
        return fallbackNode;
    }

    window.addEventListener("app:user-profile-updated", function (event) {
        const payload = event.detail || {};
        if (!userId || payload.user_id !== userId) {
            return;
        }

        const profileName = payload.profile_name || payload.username || username;
        if (nameNode) {
            nameNode.textContent = profileName;
        }

        if (bioNode) {
            bioNode.textContent = payload.bio || "This user has not added a bio yet.";
        }

        if (avatarWrap) {
            avatarWrap.innerHTML = "";
            avatarWrap.appendChild(buildAvatarNode(profileName, payload.avatar_url || ""));
        }
    });
})();
