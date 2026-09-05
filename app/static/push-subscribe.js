// Enable/disable browser push notifications from the profile page.
// External file (not inline) so it needs no CSP nonce; the one non-secret
// value it needs -- the VAPID public key -- comes off the button's own
// data attribute rather than being baked into this file, since the same
// static asset is served (and cached) for every user.
(function () {
  const button = document.getElementById("push-toggle");
  if (!button || !("serviceWorker" in navigator) || !("PushManager" in window)) {
    if (button) button.hidden = true;
    return;
  }

  function urlBase64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = window.atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  function setLabel(subscribed) {
    button.textContent = subscribed ? "Disable browser notifications" : "Enable browser notifications";
    button.dataset.subscribed = subscribed ? "1" : "";
  }

  async function currentSubscription() {
    const registration = await navigator.serviceWorker.register("/static/sw.js");
    return registration.pushManager.getSubscription();
  }

  currentSubscription()
    .then(function (sub) {
      setLabel(!!sub);
    })
    .catch(function () {
      button.hidden = true;
    });

  button.addEventListener("click", async function () {
    button.disabled = true;
    try {
      const registration = await navigator.serviceWorker.register("/static/sw.js");
      const existing = await registration.pushManager.getSubscription();

      if (existing) {
        await fetch("/notifications/push/unsubscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: existing.endpoint }),
        });
        await existing.unsubscribe();
        setLabel(false);
        return;
      }

      const permission = await Notification.requestPermission();
      if (permission !== "granted") return;

      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(button.dataset.vapidKey),
      });
      await fetch("/notifications/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription.toJSON()),
      });
      setLabel(true);
    } catch (e) {
      // A denied permission, an offline POST, or a browser that lied about
      // supporting the Push API -- leave the button in its last-known
      // state rather than claiming success.
    } finally {
      button.disabled = false;
    }
  });
})();
