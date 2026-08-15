export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // =====================================================
    // API REQUESTS → FLASK /api/*
    // =====================================================
    if (url.pathname.startsWith("/api/")) {

      // IMPORTANT:
      // We will put your public Flask/VPS address here
      // once we confirm the current address.
      const BACKEND_URL = "YOUR_FLASK_PUBLIC_URL";

      const backendUrl =
        BACKEND_URL.replace(/\/$/, "") +
        url.pathname +
        url.search;

      const backendRequest = new Request(
        backendUrl,
        {
          method: request.method,
          headers: request.headers,
          body:
            request.method === "GET" ||
            request.method === "HEAD"
              ? undefined
              : request.body
        }
      );

      return fetch(backendRequest);
    }

    // =====================================================
    // EVERYTHING ELSE → GITHUB STATIC WEBSITE
    // =====================================================
    return env.ASSETS.fetch(request);
  }
};
