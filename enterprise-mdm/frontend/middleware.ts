import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const hasAccessToken = request.cookies.has("access_token");
  const hasRefreshToken = request.cookies.has("refresh_token");
  const hasCsrfToken = request.cookies.has("csrf_token");

  const isAuthenticated = hasAccessToken || hasRefreshToken || hasCsrfToken;

  if (!isAuthenticated) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("from", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/settings/:path*",
    "/admin/:path*",
    "/users/:path*",
    // Explicit (non-wildcard) entries only — /organizations/invitations/accept
    // must stay public, it's the unauthenticated invite-acceptance flow.
    "/organizations",
    "/organizations/:id",
    "/organizations/:id/members",
  ],
};
