/** @param {string | undefined} dashboardUrl @param {string | undefined} nodeEnv */
export function shouldUseSecureCookie(dashboardUrl, nodeEnv) {
  if (!dashboardUrl) {
    return nodeEnv === "production";
  }

  try {
    const url = new URL(dashboardUrl);
    if (url.protocol === "http:" && isLoopbackHost(url.hostname)) {
      return false;
    }
    return true;
  } catch {
    return nodeEnv === "production";
  }
}

/** @param {string} hostname */
function isLoopbackHost(hostname) {
  const host = hostname.toLowerCase();
  if (host === "localhost" || host === "[::1]") {
    return true;
  }
  const octets = host.split(".");
  return (
    octets.length === 4 &&
    octets[0] === "127" &&
    octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255)
  );
}

/**
 * @param {string | undefined} dashboardUrl
 * @param {string | undefined} nodeEnv
 * @returns {{ httpOnly: true, secure: boolean, sameSite: "lax", path: "/", maxAge: number }}
 */
export function refreshCookieOptions(dashboardUrl, nodeEnv) {
  return {
    httpOnly: true,
    secure: shouldUseSecureCookie(dashboardUrl, nodeEnv),
    sameSite: "lax",
    path: "/",
    maxAge: 30 * 24 * 60 * 60,
  };
}
