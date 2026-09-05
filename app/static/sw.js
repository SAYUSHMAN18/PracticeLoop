// PracticeLoop's push service worker. Deliberately minimal: no offline
// caching, no fetch interception -- its only job is turning a push event
// into a real OS/browser notification, and routing a click on that
// notification back into the app.

self.addEventListener("push", function (event) {
  let data = { title: "PracticeLoop", body: "", link: "/dashboard" };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    // A push with a body that isn't JSON still shows *something* rather
    // than silently dropping the notification.
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "PracticeLoop", {
      body: data.body || "",
      icon: "/static/icon.svg",
      data: { link: data.link || "/dashboard" },
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/dashboard";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windowClients) {
      for (const client of windowClients) {
        if (client.url.endsWith(link) && "focus" in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(link);
    })
  );
});
