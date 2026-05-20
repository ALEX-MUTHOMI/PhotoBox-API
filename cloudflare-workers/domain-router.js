/**
 * Cloudflare Worker: Edge Subdomain & Custom Domain Resolver.
 * Resolves custom domain hostnames to tenant workspace IDs in <5ms.
 */
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const hostname = url.hostname.toLowerCase();

    // Check Cloudflare KV Edge Cache
    let workspaceId = await env.DOMAIN_KV.get(hostname);

    if (!workspaceId && !hostname.endsWith("photobox.io")) {
      // Edge miss: Resolve via Origin API
      const apiLookup = await fetch(`${env.ORIGIN_API}/api/v1/core/resolve-domain/?domain=${encodeURIComponent(hostname)}`, {
        headers: { "X-Worker-Secret": env.WORKER_SHARED_SECRET }
      });
      if (apiLookup.ok) {
        const data = await apiLookup.json();
        workspaceId = data.workspace_id || "";
        if (workspaceId) {
          // Cache for 1 hour at edge
          await env.DOMAIN_KV.put(hostname, workspaceId, { expirationTtl: 3600 });
        }
      }
    }

    // Forward request downstream with resolved tenant context
    const modifiedRequest = new Request(request);
    if (workspaceId) {
      modifiedRequest.headers.set("X-Tenant-Workspace-ID", workspaceId);
    }

    return fetch(modifiedRequest);
  }
};
