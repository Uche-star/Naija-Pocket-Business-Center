export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // =====================================================
    // API REQUESTS → RENDER / FLASK /api/*
    // =====================================================
    if (url.pathname.startsWith("/api/")) {

      const BACKEND_URL =
        "https://naija-pocket-business-center.onrender.com";

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
